// 48 kHz float in -> every 6th sample -> 160-sample int16 chunks (20 ms @ 8 kHz).
// A BiquadFilter (3.4 kHz lowpass) runs upstream in the graph as the anti-alias filter.
class CaptureWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Int16Array(160);
    this.n = 0;
    this.phase = 0; // decimation phase across process() calls
  }
  process(inputs) {
    const ch = inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      if (this.phase === 0) {
        const s = Math.max(-1, Math.min(1, ch[i]));
        this.buf[this.n++] = (s * 32767) | 0;
        if (this.n === 160) {
          this.port.postMessage(this.buf.buffer.slice(0));
          this.n = 0;
        }
      }
      this.phase = (this.phase + 1) % 6;
    }
    return true;
  }
}
registerProcessor("capture-worklet", CaptureWorklet);
