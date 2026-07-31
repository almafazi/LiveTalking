class PcmCaptureProcessor extends AudioWorkletProcessor {
    constructor(options) {
        super();
        this.targetSampleRate = options.processorOptions?.targetSampleRate || 16000;
        this.sourceToTargetRatio = sampleRate / this.targetSampleRate;
        this.nextSourcePosition = 0;
        this.pendingInput = new Float32Array(0);
        this.chunkSamples = Math.max(1, Math.round(this.targetSampleRate * 0.02));
        this.outputChunk = new Int16Array(this.chunkSamples);
        this.outputOffset = 0;
    }

    emitSample(value) {
        const sample = Math.max(-1, Math.min(1, value));
        this.outputChunk[this.outputOffset] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
        this.outputOffset += 1;
        if (this.outputOffset !== this.outputChunk.length) return;

        this.port.postMessage(this.outputChunk.buffer, [this.outputChunk.buffer]);
        this.outputChunk = new Int16Array(this.chunkSamples);
        this.outputOffset = 0;
    }

    process(inputs) {
        const input = inputs[0]?.[0];
        if (!input?.length) return true;

        const combined = new Float32Array(this.pendingInput.length + input.length);
        combined.set(this.pendingInput);
        combined.set(input, this.pendingInput.length);

        while (this.nextSourcePosition + 1 < combined.length) {
            const left = Math.floor(this.nextSourcePosition);
            const fraction = this.nextSourcePosition - left;
            const value = combined[left] * (1 - fraction) + combined[left + 1] * fraction;
            this.emitSample(value);
            this.nextSourcePosition += this.sourceToTargetRatio;
        }

        const consumed = Math.min(Math.floor(this.nextSourcePosition), combined.length);
        this.pendingInput = combined.slice(consumed);
        this.nextSourcePosition -= consumed;
        return true;
    }
}

registerProcessor('pcm-capture-processor', PcmCaptureProcessor);
