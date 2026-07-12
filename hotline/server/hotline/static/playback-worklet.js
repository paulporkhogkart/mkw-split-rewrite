// int16 8 kHz frames in (via port) -> sample-repeat x6 to 48 kHz out.
// A downstream BiquadFilter (3.4 kHz lowpass) smooths the steps.
class PlaybackWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];
    this.cur = null;
    this.idx = 0;
    this.rep = 0;
    this.port.onmessage = (e) => {
      this.queue.push(new Int16Array(e.data));
      if (this.queue.length > 25) this.queue.shift(); // cap ~0.5 s
    };
  }
  process(_inputs, outputs) {
    const out = outputs[0][0];
    for (let i = 0; i < out.length; i++) {
      if (!this.cur || this.idx >= this.cur.length) {
        this.cur = this.queue.shift() || null;
        this.idx = 0;
      }
      out[i] = this.cur ? this.cur[this.idx] / 32768 : 0;
      this.rep = (this.rep + 1) % 6;
      if (this.rep === 0 && this.cur) this.idx++;
    }
    return true;
  }
}
registerProcessor("playback-worklet", PlaybackWorklet);
