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

function pcm16ToFloat(bytes) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const output = new Float32Array(Math.floor(bytes.byteLength / 2));
    for (let index = 0; index < output.length; index += 1) {
        const sample = view.getInt16(index * 2, true);
        output[index] = sample < 0 ? sample / 0x8000 : sample / 0x7fff;
    }
    return output;
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

// Prebuffer once at the beginning of a response. After that, PCM flows through
// one WebSocket so network message boundaries never become avatar silence.
const AVATAR_UPLOAD_CHUNK_MS = 600;
const AVATAR_START_BUFFER_MS = 1200;
const AVATAR_IDLE_FLUSH_MS = 1200;
const AVATAR_MIN_FINAL_CHUNK_MS = 160;
const AVATAR_STREAM_SAMPLE_RATE = 16000;

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
        this.playbackMode = options.playbackMode || 'avatar';
        // 'engine': the GPU engine owns the ElevenLabs socket; this client only
        // streams the microphone and receives state events over one WebSocket.
        this.engineMode = this.playbackMode !== 'browser'
            && this.conversation_transport !== 'legacy';
        this.muted = Boolean(options.muted);
        this.generation = Number(options.generation || 0);
        this.uploadQueue = Promise.resolve();
        this.generationBarrier = Promise.resolve();
        this.pcmRemainder = new Uint8Array(0);
        this.avatarFlushTimer = null;
        this.avatarAudioStarted = false;
        this.avatarSocket = null;
        this.avatarSocketReady = false;
        this.avatarStreamQueue = Promise.resolve();
        this.playbackGain = null;
        this.playbackSources = new Set();
        this.nextPlaybackTime = 0;
        this.playbackIdleTimer = null;
        this.responseComplete = false;
        this.stopped = false;
        this.uploadAbortController = null;
    }

    async start() {
        this.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
        if (this.engineMode) {
            await this.connectEngineSocket();
            await this.startMic();
        } else {
            await this.startMic();
            await this.connectSocket();
            if (this.playbackMode !== 'browser') await this.connectAvatarStream();
        }
        this.onState?.('listening');
    }

    connectEngineSocket() {
        return new Promise((resolve, reject) => {
            const url = new URL('/media/api/elevenlabs/stream', window.location.href);
            url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const socket = new WebSocket(url);
            this.socket = socket;
            socket.binaryType = 'arraybuffer';
            let ready = false;
            const timeout = setTimeout(() => {
                if (!ready) {
                    socket.close();
                    reject(new Error('Avatar conversation connection timed out.'));
                }
            }, 8000);
            socket.onopen = () => socket.send(JSON.stringify({
                type: 'init',
                token: this.control_token,
                mode: 'conversation',
                language: this.language,
            }));
            socket.onmessage = (event) => {
                if (typeof event.data !== 'string') return;
                let data;
                try { data = JSON.parse(event.data); } catch { return; }
                if (data.type === 'ready') {
                    ready = true;
                    clearTimeout(timeout);
                    resolve();
                    return;
                }
                if (data.type === 'state') {
                    this.onState?.(data.state);
                    return;
                }
                if (data.type === 'error') {
                    if (!ready) {
                        clearTimeout(timeout);
                        socket.close();
                        reject(new Error(data.message || 'Avatar conversation failed.'));
                    } else if (!this.stopped) {
                        this.onError?.(data.message || 'Avatar conversation error.');
                    }
                }
            };
            socket.onerror = () => {
                if (!ready) {
                    clearTimeout(timeout);
                    reject(new Error('Avatar conversation connection failed.'));
                }
            };
            socket.onclose = () => {
                if (!ready) {
                    clearTimeout(timeout);
                    reject(new Error('Avatar conversation connection closed.'));
                    return;
                }
                if (!this.stopped) this.onError?.('Voice session disconnected.');
            };
        });
    }

    connectAvatarStream() {
        if (this.outputRate !== AVATAR_STREAM_SAMPLE_RATE) {
            console.warn(`Avatar PCM WebSocket requires ${AVATAR_STREAM_SAMPLE_RATE} Hz; using HTTP fallback for ${this.outputRate} Hz audio.`);
            return Promise.resolve(false);
        }
        return new Promise((resolve) => {
            const url = new URL('/media/api/elevenlabs/stream', window.location.href);
            url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const socket = new WebSocket(url);
            this.avatarSocket = socket;
            socket.binaryType = 'arraybuffer';
            let settled = false;
            let wasReady = false;
            const finish = (ready) => {
                if (settled) return;
                settled = true;
                clearTimeout(timeout);
                resolve(ready);
            };
            const timeout = setTimeout(() => {
                socket.close();
                finish(false);
            }, 5000);
            socket.onopen = () => socket.send(JSON.stringify({
                type: 'init',
                token: this.control_token,
            }));
            socket.onmessage = (event) => {
                if (typeof event.data !== 'string') return;
                let data;
                try { data = JSON.parse(event.data); } catch { return; }
                if (data.type === 'ready') {
                    wasReady = true;
                    this.avatarSocketReady = true;
                    finish(true);
                    return;
                }
                if (data.type === 'error') {
                    console.warn('Avatar PCM stream error:', data.code, data.message);
                    if (!wasReady) {
                        socket.close();
                        finish(false);
                    }
                }
            };
            socket.onerror = () => finish(false);
            socket.onclose = () => {
                this.avatarSocketReady = false;
                if (this.avatarSocket === socket) this.avatarSocket = null;
                finish(false);
                if (wasReady && !this.stopped) this.onError?.('Avatar audio stream disconnected.');
            };
        });
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
        if (data.type === 'agent_response') {
            this.responseComplete = false;
            if (this.playbackMode !== 'browser') this.onState?.('speaking');
        }
        if (data.type === 'audio' && data.audio_event?.audio_base_64) {
            this.responseComplete = false;
            this.onState?.('speaking');
            this.enqueueAudio(bytesFromBase64(data.audio_event.audio_base_64));
        }
        if (data.type === 'agent_response_complete') {
            this.responseComplete = true;
            if (this.playbackMode === 'browser') {
                this.finishBrowserPlayback();
            } else {
                this.flushAvatarAudio();
            }
        }
        if (data.type === 'interruption') this.interrupt();
    }

    enqueueAudio(bytes) {
        if (this.playbackMode === 'browser') {
            this.enqueueBrowserAudio(bytes);
            return;
        }

        if (this.avatarSocketReady) {
            this.enqueueAvatarStreamAudio(bytes);
            return;
        }

        this.enqueueAvatarFallbackAudio(bytes);
    }

    enqueueAvatarStreamAudio(bytes) {
        const combined = new Uint8Array(this.pcmRemainder.byteLength + bytes.byteLength);
        combined.set(this.pcmRemainder);
        combined.set(bytes, this.pcmRemainder.byteLength);
        const startBufferBytes = Math.round(this.outputRate * AVATAR_START_BUFFER_MS / 1000) * 2;

        if (!this.avatarAudioStarted && combined.byteLength < startBufferBytes) {
            this.pcmRemainder = combined;
        } else {
            const generation = this.generation;
            const firstMessage = !this.avatarAudioStarted;
            this.avatarAudioStarted = true;
            this.pcmRemainder = new Uint8Array(0);
            this.queueAvatarStreamMessage(generation, () => {
                if (firstMessage) this.avatarSocket.send(JSON.stringify({
                    type: 'start',
                    generation,
                    sample_rate: this.outputRate,
                }));
                this.avatarSocket.send(combined);
            });
        }

    }

    queueAvatarStreamMessage(generation, callback) {
        const generationBarrier = this.generationBarrier;
        this.avatarStreamQueue = this.avatarStreamQueue.then(async () => {
            await generationBarrier;
            if (this.stopped || generation !== this.generation || !this.avatarSocketReady || !this.avatarSocket) return;
            callback();
        }).catch((error) => {
            if (!this.stopped && generation === this.generation) this.onError?.(error.message);
        });
    }

    enqueueAvatarFallbackAudio(bytes) {

        const combined = new Uint8Array(this.pcmRemainder.byteLength + bytes.byteLength);
        combined.set(this.pcmRemainder);
        combined.set(bytes, this.pcmRemainder.byteLength);
        const frameBytes = Math.max(2, Math.round(this.outputRate * 0.02) * 2);
        const targetChunkBytes = Math.max(
            frameBytes,
            Math.floor((this.outputRate * AVATAR_UPLOAD_CHUNK_MS / 1000 * 2) / frameBytes) * frameBytes,
        );
        const startBufferBytes = Math.max(
            targetChunkBytes,
            Math.floor((this.outputRate * AVATAR_START_BUFFER_MS / 1000 * 2) / frameBytes) * frameBytes,
        );
        let offset = 0;
        if (this.avatarAudioStarted || combined.byteLength >= startBufferBytes) {
            this.avatarAudioStarted = true;
            while (combined.byteLength - offset >= targetChunkBytes) {
                this.enqueueAvatarUpload(
                    combined.slice(offset, offset + targetChunkBytes),
                    this.generation,
                    this.generationBarrier,
                );
                offset += targetChunkBytes;
            }
        }
        this.pcmRemainder = combined.slice(offset);
        clearTimeout(this.avatarFlushTimer);
        this.avatarFlushTimer = setTimeout(() => this.flushAvatarAudio(), AVATAR_IDLE_FLUSH_MS);
    }

    flushAvatarAudio() {
        clearTimeout(this.avatarFlushTimer);
        this.avatarFlushTimer = null;
        if (this.playbackMode === 'browser') return;
        if (this.avatarSocketReady) {
            this.flushAvatarStreamAudio();
            return;
        }
        this.flushAvatarFallbackAudio();
    }

    flushAvatarStreamAudio() {
        const generation = this.generation;
        const finalBytes = this.pcmRemainder.slice(0, this.pcmRemainder.byteLength - (this.pcmRemainder.byteLength % 2));
        this.pcmRemainder = new Uint8Array(0);

        if (!this.avatarAudioStarted && finalBytes.byteLength) {
            this.avatarAudioStarted = true;
            this.queueAvatarStreamMessage(generation, () => {
                this.avatarSocket.send(JSON.stringify({
                    type: 'start',
                    generation,
                    sample_rate: this.outputRate,
                }));
                this.avatarSocket.send(finalBytes);
            });
        }
        if (this.avatarAudioStarted) {
            this.queueAvatarStreamMessage(generation, () => {
                this.avatarSocket.send(JSON.stringify({ type: 'end', generation }));
            });
        }
        this.avatarAudioStarted = false;
    }

    flushAvatarFallbackAudio() {
        const frameBytes = Math.max(2, Math.round(this.outputRate * 0.02) * 2);
        const completeBytes = Math.floor(this.pcmRemainder.byteLength / frameBytes) * frameBytes;
        let finalChunk = this.pcmRemainder.slice(0, completeBytes);
        this.pcmRemainder = new Uint8Array(0);
        this.avatarAudioStarted = false;
        if (finalChunk.byteLength) {
            const minimumFinalBytes = Math.max(
                frameBytes,
                Math.ceil((this.outputRate * AVATAR_MIN_FINAL_CHUNK_MS / 1000 * 2) / frameBytes) * frameBytes,
            );
            if (finalChunk.byteLength < minimumFinalBytes) {
                const paddedChunk = new Uint8Array(minimumFinalBytes);
                paddedChunk.set(finalChunk);
                finalChunk = paddedChunk;
            }
            this.enqueueAvatarUpload(finalChunk, this.generation, this.generationBarrier);
        }
    }

    enqueueAvatarUpload(bytes, generation, generationBarrier) {
        this.uploadQueue = this.uploadQueue.then(async () => {
            await generationBarrier;
            if (this.stopped || generation !== this.generation) return;
            const controller = new AbortController();
            this.uploadAbortController = controller;
            const form = new FormData();
            form.append('generation', String(generation));
            form.append('file', wavBlob(bytes, this.outputRate), 'agent.wav');
            const response = await fetch('/media/api/elevenlabs/audio', {
                method: 'POST',
                headers: { 'X-LiveTalking-Token': this.control_token },
                body: form,
                signal: controller.signal,
            });
            if (this.uploadAbortController === controller) this.uploadAbortController = null;
            if (this.stopped || generation !== this.generation || response.status === 409) return;
            if (!response.ok) throw new Error(`Avatar audio HTTP ${response.status}`);
        }).catch((error) => {
            this.uploadAbortController = null;
            if (error.name === 'AbortError' || this.stopped || generation !== this.generation) return;
            this.onError?.(error.message);
        });
    }

    enqueueBrowserAudio(bytes) {
        if (!this.context || this.stopped) return;
        clearTimeout(this.playbackIdleTimer);
        this.playbackIdleTimer = null;
        const samples = pcm16ToFloat(bytes);
        if (!samples.length) return;

        const buffer = this.context.createBuffer(1, samples.length, this.outputRate);
        buffer.copyToChannel(samples, 0);
        const source = this.context.createBufferSource();
        const generation = this.generation;
        source.buffer = buffer;
        source.connect(this.playbackGain);
        this.playbackSources.add(source);

        const startAt = Math.max(this.context.currentTime, this.nextPlaybackTime);
        this.nextPlaybackTime = startAt + buffer.duration;
        source.onended = () => {
            this.playbackSources.delete(source);
            if (generation !== this.generation) return;
            if (this.playbackSources.size === 0) {
                this.playbackIdleTimer = setTimeout(() => {
                    this.playbackIdleTimer = null;
                    this.finishBrowserPlayback(true);
                }, 250);
            }
        };
        source.start(startAt);
    }

    finishBrowserPlayback(force = false) {
        if ((force || this.responseComplete) && this.playbackSources.size === 0 && !this.stopped) {
            this.nextPlaybackTime = this.context?.currentTime || 0;
            this.onState?.('listening');
        }
    }

    async startMic() {
        this.context = new AudioContext({ latencyHint: 'interactive' });
        await this.context.resume();
        if (this.playbackMode === 'browser') {
            this.playbackGain = this.context.createGain();
            this.playbackGain.gain.value = this.muted ? 0 : 1;
            this.playbackGain.connect(this.context.destination);
        }
        this.source = this.context.createMediaStreamSource(this.stream);

        if (this.context.audioWorklet && typeof AudioWorkletNode !== 'undefined') {
            try {
                await this.context.audioWorklet.addModule(new URL('./pcm-capture-processor.js', import.meta.url));
                this.processor = new AudioWorkletNode(this.context, 'pcm-capture-processor', {
                    numberOfInputs: 1,
                    numberOfOutputs: 0,
                    channelCount: 1,
                    processorOptions: { targetSampleRate: this.inputRate },
                });
                this.processor.port.onmessage = (event) => {
                    if (this.socket?.readyState !== WebSocket.OPEN) return;
                    if (this.engineMode) {
                        this.socket.send(event.data);
                        return;
                    }
                    this.socket.send(JSON.stringify({ user_audio_chunk: base64FromBytes(new Uint8Array(event.data)) }));
                };
                this.source.connect(this.processor);
                return;
            } catch (error) {
                console.warn('AudioWorklet unavailable, using ScriptProcessor fallback.', error);
            }
        }

        this.processor = this.context.createScriptProcessor(4096, 1, 1);
        this.processor.onaudioprocess = (event) => {
            if (this.socket?.readyState !== WebSocket.OPEN) return;
            const samples = resample(event.inputBuffer.getChannelData(0), this.context.sampleRate, this.inputRate);
            const pcm = floatToPcm16(samples);
            if (this.engineMode) {
                this.socket.send(pcm.buffer);
                return;
            }
            this.socket.send(JSON.stringify({ user_audio_chunk: base64FromBytes(pcm) }));
        };
        this.source.connect(this.processor);
        this.processor.connect(this.context.destination);
    }

    setMuted(muted) {
        this.muted = Boolean(muted);
        if (this.playbackGain) this.playbackGain.gain.value = this.muted ? 0 : 1;
    }

    sendText(text) {
        if (this.engineMode) {
            if (this.socket?.readyState === WebSocket.OPEN) {
                this.socket.send(JSON.stringify({ type: 'user_message', text }));
            }
            return;
        }
        this.interrupt();
        this.socket?.send(JSON.stringify({ type: 'user_message', text }));
    }

    interrupt() {
        if (this.engineMode) {
            if (this.socket?.readyState === WebSocket.OPEN) {
                this.socket.send(JSON.stringify({ type: 'interrupt' }));
            }
            return;
        }
        this.generation += 1;
        clearTimeout(this.avatarFlushTimer);
        this.avatarFlushTimer = null;
        this.avatarAudioStarted = false;
        this.pcmRemainder = new Uint8Array(0);
        this.responseComplete = false;
        this.uploadAbortController?.abort();
        this.uploadAbortController = null;
        if (this.playbackMode === 'browser') {
            clearTimeout(this.playbackIdleTimer);
            this.playbackIdleTimer = null;
            for (const source of this.playbackSources) {
                try { source.stop(); } catch {}
            }
            this.playbackSources.clear();
            this.nextPlaybackTime = this.context?.currentTime || 0;
            this.onState?.('listening');
            return;
        }
        const interruptedGeneration = this.generation;
        this.generationBarrier = fetch('/media/api/elevenlabs/interrupt', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-LiveTalking-Token': this.control_token },
            body: JSON.stringify({ generation: interruptedGeneration }),
        }).then((response) => {
            if (!response.ok) throw new Error(`Avatar interrupt HTTP ${response.status}`);
        }).catch((error) => {
            if (!this.stopped && interruptedGeneration === this.generation) this.onError?.(error.message);
        });
    }

    stop() {
        this.stopped = true;
        if (this.processor?.port) this.processor.port.onmessage = null;
        this.processor?.disconnect();
        this.source?.disconnect();
        this.stream?.getTracks().forEach((track) => track.stop());
        if (this.engineMode) {
            if (this.socket?.readyState === WebSocket.OPEN) {
                try { this.socket.send(JSON.stringify({ type: 'end' })); } catch {}
            }
            this.context?.close().catch(() => {});
            this.socket?.close();
            return;
        }
        this.uploadAbortController?.abort();
        this.uploadAbortController = null;
        clearTimeout(this.avatarFlushTimer);
        this.avatarFlushTimer = null;
        this.avatarAudioStarted = false;
        clearTimeout(this.playbackIdleTimer);
        this.playbackIdleTimer = null;
        for (const playbackSource of this.playbackSources) {
            try { playbackSource.stop(); } catch {}
        }
        this.playbackSources.clear();
        this.context?.close().catch(() => {});
        this.socket?.close();
        this.avatarSocketReady = false;
        this.avatarSocket?.close();
        this.avatarSocket = null;
        if (this.playbackMode === 'browser') return;
        fetch('/media/api/elevenlabs/end', {
            method: 'POST', keepalive: true, headers: { 'X-LiveTalking-Token': this.control_token },
        }).catch(() => {});
    }
}
