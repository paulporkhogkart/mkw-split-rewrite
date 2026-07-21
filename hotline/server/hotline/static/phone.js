/* pork phone page. State is driven by the events WS (line_state) plus what this
   page knows it did (its own lease). All audio (call + sfx) rides one
   AudioContext through one gain node so the output device + volume apply to
   everything. */
(() => {
  const $ = (id) => document.getElementById(id);
  const btn = $("callbtn"), caption = $("caption"),
        pill = $("pill"), pillText = $("pill-text"), dot = $("dot"),
        micSel = $("mic-sel"), spkSel = $("spk-sel"),
        micTest = $("mic-test"), spkTest = $("spk-test"),
        vol = $("vol"), micLevel = $("mic-level");

  const store = {
    get input()  { return localStorage.getItem("pp.input") || ""; },
    set input(v) { localStorage.setItem("pp.input", v); },
    get output() { return localStorage.getItem("pp.output") || ""; },
    set output(v){ localStorage.setItem("pp.output", v); },
    get volume() { return +(localStorage.getItem("pp.volume") ?? 80); },
    set volume(v){ localStorage.setItem("pp.volume", String(v)); },
  };

  // ---- audio graph ---------------------------------------------------------
  let ctx = null, gain = null, playNode = null, fallbackEl = null;
  const sfxBuf = {};   // name -> AudioBuffer
  let ringLoop = null; // AudioBufferSourceNode while ringing

  async function ensureCtx() {
    if (ctx) { if (ctx.state === "suspended") await ctx.resume(); return; }
    ctx = new AudioContext({ sampleRate: 48000 });
    gain = new GainNode(ctx, { gain: store.volume / 100 });
    await ctx.audioWorklet.addModule("/static/capture-worklet.js");
    await ctx.audioWorklet.addModule("/static/playback-worklet.js");
    if (typeof ctx.setSinkId === "function") {
      gain.connect(ctx.destination);
      if (store.output) await ctx.setSinkId(store.output).catch(() => {});
    } else {
      // no AudioContext.setSinkId: route through an <audio> element instead
      const dest = new MediaStreamAudioDestinationNode(ctx);
      gain.connect(dest);
      fallbackEl = new Audio();
      fallbackEl.srcObject = dest.stream;
      fallbackEl.play().catch(() => {});
      if (store.output && fallbackEl.setSinkId)
        await fallbackEl.setSinkId(store.output).catch(() => {});
    }
    for (const name of ["ringing", "hangup", "ringtone"]) {
      const resp = await fetch(`/static/sfx/${name}.wav`);
      sfxBuf[name] = await ctx.decodeAudioData(await resp.arrayBuffer());
    }
  }

  function playSfx(name, { loop = false } = {}) {
    if (!ctx || !sfxBuf[name]) return null;
    const src = new AudioBufferSourceNode(ctx, { buffer: sfxBuf[name], loop });
    src.connect(gain);
    src.start();
    return src;
  }
  function stopRingback() { try { ringLoop?.stop(); } catch {} ringLoop = null; }

  async function setOutput(id) {
    store.output = id;
    if (!ctx) return;
    if (typeof ctx.setSinkId === "function") await ctx.setSinkId(id).catch(() => {});
    else if (fallbackEl?.setSinkId) await fallbackEl.setSinkId(id).catch(() => {});
  }

  // ---- devices -------------------------------------------------------------
  async function refreshDevices() {
    const devs = await navigator.mediaDevices.enumerateDevices();
    fill(micSel, devs.filter(d => d.kind === "audioinput"), store.input);
    fill(spkSel, devs.filter(d => d.kind === "audiooutput"), store.output);
    // hide the output row entirely where selection isn't supported (Firefox)
    spkSel.parentElement.hidden = !devs.some(d => d.kind === "audiooutput");
  }
  function fill(sel, devs, saved) {
    sel.innerHTML = "";
    for (const d of devs) {
      const o = document.createElement("option");
      o.value = d.deviceId;
      o.textContent = d.label || (sel === micSel ? "microphone" : "speaker");
      sel.appendChild(o);
    }
    if (saved && [...sel.options].some(o => o.value === saved)) sel.value = saved;
  }
  navigator.mediaDevices.addEventListener?.("devicechange", refreshDevices);

  function micConstraints() {
    return { audio: {
      channelCount: 1, echoCancellation: true, noiseSuppression: true,
      ...(store.input ? { deviceId: { ideal: store.input } } : {}) } };
  }

  // ---- mic test (meter only, mic held only while testing) ------------------
  let testStream = null, meterRaf = 0;
  micTest.addEventListener("click", async () => {
    if (testStream) return stopMicTest();
    await ensureCtx();
    testStream = await navigator.mediaDevices.getUserMedia(micConstraints())
      .catch(() => null);
    if (!testStream) return;
    micTest.textContent = "Stop"; micTest.classList.add("on");
    await refreshDevices();   // labels appear once permission is granted
    const src = ctx.createMediaStreamSource(testStream);
    const an = new AnalyserNode(ctx, { fftSize: 512 });
    src.connect(an);
    const buf = new Uint8Array(an.fftSize);
    (function tick() {
      an.getByteTimeDomainData(buf);
      let peak = 0;
      for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
      micLevel.style.width = Math.min(100, (peak / 128) * 140) + "%";
      meterRaf = requestAnimationFrame(tick);
    })();
  });
  function stopMicTest() {
    cancelAnimationFrame(meterRaf);
    micLevel.style.width = "0";
    testStream?.getTracks().forEach(t => t.stop());
    testStream = null;
    micTest.textContent = "Test"; micTest.classList.remove("on");
  }

  spkTest.addEventListener("click", async () => {
    await ensureCtx();
    playSfx("ringtone");
  });

  micSel.addEventListener("change", () => { store.input = micSel.value; });
  spkSel.addEventListener("change", () => setOutput(spkSel.value));
  vol.value = store.volume;
  vol.addEventListener("input", () => {
    store.volume = +vol.value;
    if (gain) gain.gain.value = vol.value / 100;
  });

  // ---- call machine --------------------------------------------------------
  // page states: idle | calling (claim+ws setup) | ringing | oncall | busy | unplugged
  let page = "idle", lease = null, audioWs = null, callStream = null;
  let line = { state: "idle", since: 0 };   // latest broadcast
  let timerIv = 0, captionTimeout = 0;

  function render() {
    clearInterval(timerIv); timerIv = 0;
    if (page === "ringing") {
      pill.hidden = false; dot.className = "dot cadence"; pillText.textContent = "ringing…";
      btn.className = "callbtn red"; caption.textContent = "hang up";
    } else if (page === "oncall") {
      pill.hidden = false; dot.className = "dot green";
      btn.className = "callbtn red"; caption.textContent = "hang up";
      const t0 = Date.now();
      const tick = () => {
        const s = Math.floor((Date.now() - t0) / 1000 + oncallOffset);
        pillText.textContent =
          `on call · ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
      };
      tick(); timerIv = setInterval(tick, 1000);
    } else if (page === "busy") {
      pill.hidden = false; dot.className = "dot"; pillText.textContent = "line busy";
      btn.className = "callbtn off"; caption.textContent = "wait for their call to end";
    } else if (page === "unplugged") {
      pill.hidden = false; dot.className = "dot"; pillText.textContent = "phone unplugged";
      btn.className = "callbtn off"; caption.textContent = "not taking calls right now";
    } else { // idle
      pill.hidden = false; dot.className = "dot"; pillText.textContent = "idle";
      btn.className = "callbtn";
      if (!captionTimeout) caption.textContent = "press to call";
    }
  }
  let oncallOffset = 0;

  function toIdle(cap) {
    page = "idle"; lease = null;
    if (cap) {   // transient caption ("no answer"), fades back
      caption.textContent = cap;
      clearTimeout(captionTimeout);
      captionTimeout = setTimeout(() => {
        captionTimeout = 0;
        if (page === "idle") render();
      }, 4000);
    }
    render();
  }

  function endCallCleanup() {
    stopRingback();
    audioWs?.close(); audioWs = null;
    callStream?.getTracks().forEach(t => t.stop()); callStream = null;
  }

  async function startCall() {
    page = "calling"; btn.className = "callbtn off"; caption.textContent = "";
    try {
      await ensureCtx();
      callStream = await navigator.mediaDevices.getUserMedia(micConstraints());
      await refreshDevices();
      const r = await fetch("/call/claim", { method: "POST" });
      if (!r.ok) { endCallCleanup(); lease = null; return syncFromLine(); }
      lease = (await r.json()).lease_id;

      // capture chain: mic -> 2x lowpass 3400 -> capture worklet -> ws
      const src = ctx.createMediaStreamSource(callStream);
      const lp1 = new BiquadFilterNode(ctx, { type: "lowpass", frequency: 3400 });
      const lp2 = new BiquadFilterNode(ctx, { type: "lowpass", frequency: 3400 });
      const cap = new AudioWorkletNode(ctx, "capture-worklet");
      src.connect(lp1).connect(lp2).connect(cap);
      playNode = new AudioWorkletNode(ctx, "playback-worklet",
                                      { outputChannelCount: [1] });
      const lp3 = new BiquadFilterNode(ctx, { type: "lowpass", frequency: 3400 });
      playNode.connect(lp3).connect(gain);

      const proto = location.protocol === "https:" ? "wss" : "ws";
      audioWs = new WebSocket(
        `${proto}://${location.host}/ws/audio?lease=${encodeURIComponent(lease)}`);
      audioWs.binaryType = "arraybuffer";
      audioWs.onmessage = (e) => playNode.port.postMessage(e.data);
      cap.port.onmessage = (e) => {
        if (audioWs && audioWs.readyState === 1) audioWs.send(e.data);
      };
      audioWs.onclose = () => { if (page === "ringing" || page === "oncall") hangup(false); };

      await new Promise((res, rej) => {
        audioWs.onopen = res; audioWs.onerror = rej;
      });
      const rr = await fetch(`/call/ring?lease=${encodeURIComponent(lease)}`,
                             { method: "POST" });
      if (!rr.ok) { endCallCleanup(); lease = null; return syncFromLine(); }
      page = "ringing";
      ringLoop = playSfx("ringing", { loop: true });
      render();
    } catch {
      endCallCleanup();
      toIdle();
    }
  }

  async function hangup(tellServer = true) {
    const l = lease;
    endCallCleanup();
    if (tellServer && l)
      fetch(`/call/hangup?lease=${encodeURIComponent(l)}`, { method: "POST" })
        .catch(() => {});
    playSfx("hangup");
    toIdle(page === "ringing" ? undefined : undefined);
    // toIdle's caption comes from line events; explicit outcomes handled in onLine
  }

  btn.addEventListener("click", () => {
    if (page === "idle") startCall();
    else if (page === "ringing" || page === "oncall") hangup(true);
    // busy / unplugged / calling: inert
  });

  // ---- events feed ---------------------------------------------------------
  function syncFromLine() {
    if (lease) return;   // my own flow drives the UI while I hold the lease
    if (line.state === "idle") { page = "idle"; render(); }
    else if (line.state === "unplugged") { page = "unplugged"; render(); }
    else { page = "busy"; render(); }
  }

  function onLine(ev) {
    const prev = line; line = ev;
    if (lease) {
      // my lease: server-side transitions I care about
      if (ev.state === "oncall" && page === "ringing") {
        stopRingback(); oncallOffset = Math.max(0, (Date.now() / 1000) - ev.since);
        page = "oncall"; render();
      } else if (ev.state === "idle") {
        const wasRinging = page === "ringing";
        endCallCleanup(); playSfx("hangup");
        toIdle(wasRinging ? "no answer" : undefined);
      }
      return;
    }
    if ((prev.state === "idle") !== (ev.state === "idle")) syncFromLine();
    else syncFromLine();
  }

  function connectEvents() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/events?feed=rt`);
    ws.onmessage = (e) => {
      let ev; try { ev = JSON.parse(e.data); } catch { return; }
      if (ev.type === "line_state") onLine(ev);
    };
    ws.onclose = () => setTimeout(connectEvents, 2000);
  }

  // ---- boot ----------------------------------------------------------------
  refreshDevices();
  render();
  connectEvents();
})();
