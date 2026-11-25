// AudioWorklet: plays Int16 PCM (AudioContext runs at 24 kHz) from a ring buffer.
// Post an ArrayBuffer to enqueue audio, or {cmd: "clear"} to flush (barge-in).
class PCMPlayer extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(24000 * 180); // up to 3 min buffered
    this.write = 0;
    this.read = 0;
    this.port.onmessage = (e) => {
      if (e.data && e.data.cmd === "clear") {
        this.read = this.write;
        return;
      }
      const samples = new Int16Array(e.data);
      for (let i = 0; i < samples.length; i++) {
        this.buffer[this.write] = samples[i] / 32768;
        this.write = (this.write + 1) % this.buffer.length;
        if (this.write === this.read) this.read = (this.read + 1) % this.buffer.length;
      }
    };
  }
  process(_inputs, outputs) {
    const out = outputs[0][0];
    for (let i = 0; i < out.length; i++) {
      if (this.read !== this.write) {
        out[i] = this.buffer[this.read];
        this.read = (this.read + 1) % this.buffer.length;
      } else {
        out[i] = 0;
      }
    }
    return true;
  }
}
registerProcessor("pcm-player", PCMPlayer);
