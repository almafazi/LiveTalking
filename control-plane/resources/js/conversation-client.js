function bytesFromBase64(value) {
    const binary = atob(value);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    return bytes;
}

function base64FromBytes(bytes) {
    let binary = '';
    for (let index = 0; index < bytes.length; index += 0x8000) {
        binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
    }
    return btoa(binary);
}

function floatToPcm16(input) {
    const output = new Int16Array(input.length);
    for (let index = 0; index < input.length; index += 1) {
        const sample = Math.max(-1, Math.min(1, input[index]));
        output[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
    return new Uint8Array(output.buffer);
}

function resample(input, sourceRate, targetRate) {
    if (sourceRate === targetRate) return input;
    const output = new Float32Array(Math.max(1, Math.round(input.length * targetRate / sourceRate)));
    const scale = (input.length - 1) / Math.max(1, output.length - 1);
    for (let index = 0; index < output.length; index += 1) {
        const position = index * scale;
        const left = Math.floor(position);
        const right = Math.min(left + 1, input.length - 1);
        output[index] = input[left] * (1 - (position - left)) + input[right] * (position - left);
    }
    return output;
}

function wavBlob(pcmBytes, sampleRate) {
    const header = new ArrayBuffer(44);
    const view = new DataView(header);
    const write = (offset, text) => [...text].forEach((value, index) => view.setUint8(offset + index, value.charCodeAt(0)));
    write(0, 'RIFF'); view.setUint32(4, 36 + pcmBytes.byteLength, true); write(8, 'WAVE'); write(12, 'fmt ');
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    write(36, 'data'); view.setUint32(40, pcmBytes.byteLength, true);
    return new Blob([header, pcmBytes], { type: 'audio/wav' });
}

export class ConversationClient {
    constructor(options) {
        Object.assign(this, options);
        this.socket = null;
        this.stream = null;
        this.context = null;
        this.processor = null;
        this.source = null;
        this.outputRate = 24000;
        this.inputRate = 16000;
        this.generation = Number(options.generation || 0);
        this.uploadQueue = Promise.resolve();
        this.pcmRemainder = new Uint8Array(0);
        this.stopped = false;
    }

    async start() {
        this.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
        await this.connectSocket();
        await this.startMic();
        this.onState?.('listening');
    }

    connectSocket() {
        return new Promise((resolve, reject) => {
            const socket = new WebSocket(this.signed_url);
            this.socket = socket;
            let ready = false;
            socket.onopen = () => socket.send(JSON.stringify({
                type: 'conversation_initiation_client_data',
                conversation_config_override: { agent: { language: this.language } },
            }));
            socket.onmessage = (event) => {
                let data;
                try { data = JSON.parse(event.data); } catch { return; }
                if (data.type === 'conversation_initiation_metadata') {
                    const meta = data.conversation_initiation_metadata_event || {};
                    this.outputRate = Number(String(meta.agent_output_audio_format || '').replace('pcm_', '')) || 24000;
                    this.inputRate = Number(String(meta.user_input_audio_format || '').replace('pcm_', '')) || 16000;
                    ready = true;
                    resolve();
                    return;
                }
                this.handleEvent(data);
            };
            socket.onerror = () => { if (!ready) reject(new Error('ElevenLabs connection failed.')); };
            socket.onclose = () => { if (!this.stopped) this.onError?.('Voice session disconnected.'); };
        });
    }

    handleEvent(data) {
        if (data.type === 'ping') this.socket?.send(JSON.stringify({ type: 'pong', event_id: data.ping_event?.event_id }));
        if (data.type === 'user_transcript') this.onState?.('thinking');
        if (data.type === 'agent_response') this.onState?.('speaking');
        if (data.type === 'audio' && data.audio_event?.audio_base_64) {
            this.onState?.('speaking');
            this.enqueueAudio(bytesFromBase64(data.audio_event.audio_base_64));
        }
        if (data.type === 'interruption') this.interrupt();
    }

    enqueueAudio(bytes) {
        const combined = new Uint8Array(this.pcmRemainder.byteLength + bytes.byteLength);
        combined.set(this.pcmRemainder);
        combined.set(bytes, this.pcmRemainder.byteLength);
        const frameBytes = Math.max(2, Math.round(this.outputRate * 0.02) * 2);
        const completeBytes = Math.floor(combined.byteLength / frameBytes) * frameBytes;
        if (!completeBytes) {
            this.pcmRemainder = combined;
            return;
        }
        bytes = combined.slice(0, completeBytes);
        this.pcmRemainder = combined.slice(completeBytes);
        const generation = this.generation;
        this.uploadQueue = this.uploadQueue.then(async () => {
            if (this.stopped || generation !== this.generation) return;
            const form = new FormData();
            form.append('generation', String(generation));
            form.append('file', wavBlob(bytes, this.outputRate), 'agent.wav');
            const response = await fetch('/media/api/elevenlabs/audio', {
                method: 'POST', headers: { 'X-LiveTalking-Token': this.control_token }, body: form,
            });
            if (!response.ok) throw new Error(`Avatar audio HTTP ${response.status}`);
        }).catch((error) => this.onError?.(error.message));
    }

    async startMic() {
        this.context = new AudioContext({ latencyHint: 'interactive' });
        await this.context.resume();
        this.source = this.context.createMediaStreamSource(this.stream);
        this.processor = this.context.createScriptProcessor(4096, 1, 1);
        this.processor.onaudioprocess = (event) => {
            if (this.socket?.readyState !== WebSocket.OPEN) return;
            const samples = resample(event.inputBuffer.getChannelData(0), this.context.sampleRate, this.inputRate);
            this.socket.send(JSON.stringify({ user_audio_chunk: base64FromBytes(floatToPcm16(samples)) }));
        };
        this.source.connect(this.processor);
        this.processor.connect(this.context.destination);
    }

    sendText(text) {
        this.interrupt();
        this.socket?.send(JSON.stringify({ type: 'user_message', text }));
    }

    interrupt() {
        this.generation += 1;
        this.pcmRemainder = new Uint8Array(0);
        fetch('/media/api/elevenlabs/interrupt', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-LiveTalking-Token': this.control_token },
            body: JSON.stringify({ generation: this.generation }),
        }).catch(() => {});
    }

    stop() {
        this.stopped = true;
        this.processor?.disconnect();
        this.source?.disconnect();
        this.stream?.getTracks().forEach((track) => track.stop());
        this.context?.close().catch(() => {});
        this.socket?.close();
        fetch('/media/api/elevenlabs/end', {
            method: 'POST', keepalive: true, headers: { 'X-LiveTalking-Token': this.control_token },
        }).catch(() => {});
    }
}
