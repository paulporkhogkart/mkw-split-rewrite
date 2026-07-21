/* pork phone page. State is driven by the events WS (line_state) plus what this
   page knows it did (its own lease). All audio (call + sfx) rides one
   AudioContext through one gain node so the output device + volume apply to
   everything. */
(() => {
  const $ = (id) => document.getElementById(id);
  const btn = $("callbtn"), caption = $("caption"),
        pill = $("pill"), pillText = $("pill-text"), dot = $("dot"),
        micSel = $("mic-sel"), spkSel = $("spk-sel"),
        micListen = $("mic-listen"), spkTest = $("spk-test"),
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
    for (const name of ["ringback", "busy", "dialtone", "clunk"]) {
      const resp = await fetch(`/static/sfx/${name}.wav`);
      sfxBuf[name] = await ctx.decodeAudioData(await resp.arrayBuffer());
    }
  }

  // ---- DTMF dialling theatre ----------------------------------------------
  // A rapid random number dialled before the line is actually claimed: pure
  // audio set dressing. Standard DTMF pairs, scheduled sample-accurately; the
  // button icon recoils once per digit in step with the tones.
  const DTMF = { 1: [697, 1209], 2: [697, 1336], 3: [697, 1477],
                 4: [770, 1209], 5: [770, 1336], 6: [770, 1477],
                 7: [852, 1209], 8: [852, 1336], 9: [852, 1477],
                 0: [941, 1336] };
  function dtmfTone(digit, at, dur) {
    for (const f of DTMF[digit]) {
      const o = new OscillatorNode(ctx, { frequency: f });
      const g = new GainNode(ctx, { gain: 0 });
      o.connect(g).connect(gain);
      g.gain.setValueAtTime(0, at);
      g.gain.linearRampToValueAtTime(0.12, at + 0.005);
      g.gain.setValueAtTime(0.12, at + dur - 0.005);
      g.gain.linearRampToValueAtTime(0, at + dur);
      o.start(at); o.stop(at + dur + 0.01);
    }
  }
  async function playDialSequence() {
    const digits = Array.from({ length: 8 }, () => (Math.random() * 10) | 0);
    const TONE = 0.07, GAP = 0.055;
    const t0 = ctx.currentTime + 0.08;
    digits.forEach((d, i) => {
      const at = t0 + i * (TONE + GAP);
      dtmfTone(d, at, TONE);
      setTimeout(() => {
        btn.classList.add("press");
        setTimeout(() => btn.classList.remove("press"), 65);
      }, Math.max(0, (at - ctx.currentTime) * 1000));
    });
    const total = 0.08 + digits.length * (TONE + GAP) + 0.15;
    await new Promise((r) => setTimeout(r, total * 1000));
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

  // ---- always-on level meter + mic monitor ("Listen") ----------------------
  // The meter holds its own mic stream for the whole page life (separate from
  // a call's stream, so it survives calls and mic swaps independently).
  let meterStream = null, meterSrc = null, meterRaf = 0, listening = false;

  // Returns true when the mic is live. This getUserMedia doubles as the page's
  // ONE permission prompt: the meter keeps its stream for the page's life, so
  // the grant stays active (even a one-time "allow this time" grant) and no
  // later request (calls, listen) ever prompts again.
  async function startMeter() {
    if (!ctx) return false;
    let stream;
    try {
      // acquire the new stream BEFORE dropping the old one: the grant never
      // hits a zero-track moment, and a failed swap keeps the old mic alive
      stream = await navigator.mediaDevices.getUserMedia(micConstraints());
    } catch { return false; }   // denied or no device: meter unchanged/flat
    cancelAnimationFrame(meterRaf); meterRaf = 0;
    micLevel.style.width = "0";
    meterSrc?.disconnect(); meterSrc = null;
    meterStream?.getTracks().forEach(t => t.stop());
    meterStream = stream;
    meterSrc = ctx.createMediaStreamSource(meterStream);
    const an = new AnalyserNode(ctx, { fftSize: 512 });
    meterSrc.connect(an);
    if (listening) meterSrc.connect(gain);
    const buf = new Uint8Array(an.fftSize);
    (function tick() {
      an.getByteTimeDomainData(buf);
      let peak = 0;
      for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
      micLevel.style.width = Math.min(100, (peak / 128) * 140) + "%";
      meterRaf = requestAnimationFrame(tick);
    })();
    return true;
  }

  micListen.addEventListener("click", async () => {
    if (ctx?.state === "suspended") await ctx.resume().catch(() => {});
    if (!meterSrc) await startMeter();
    if (!meterSrc) return;
    listening = !listening;
    if (listening) meterSrc.connect(gain);
    else meterSrc.disconnect(gain);
    micListen.textContent = listening ? "Stop" : "Listen";
    micListen.classList.toggle("on", listening);
  });

  spkTest.addEventListener("click", async () => {
    await ensureCtx();
    const src = playSfx("dialtone");
    if (!src) return;
    spkTest.disabled = true;
    spkTest.textContent = "Playing…";
    src.onended = () => {
      spkTest.textContent = "Test";
      if (micAccess === "granted") spkTest.disabled = false;
    };
  });

  micSel.addEventListener("change", () => {
    store.input = micSel.value;
    startMeter();   // re-arm the meter (and monitor, if listening) on the new mic
  });
  spkSel.addEventListener("change", () => setOutput(spkSel.value));
  vol.value = store.volume;
  vol.addEventListener("input", () => {
    store.volume = +vol.value;
    if (gain) gain.gain.value = vol.value / 100;
  });

  // ---- call machine --------------------------------------------------------
  // page states: idle | calling (claim+ws setup) | ringing | oncall | busy | unplugged
  let page = "idle", lease = null, audioWs = null, callStream = null;
  // nodes created fresh for the current call; disconnected + nulled in
  // endCallCleanup so a second call never doubles up on the first's graph
  let callSrc = null, callLp1 = null, callLp2 = null, callCap = null, callLp3 = null;
  let line = { state: "idle", since: 0 };   // latest broadcast
  let timerIv = 0, captionTimeout = 0;

  // ---- mic permission gate -------------------------------------------------
  // Without mic access the site cannot work at all: gate every control and say
  // so plainly. "pending" = prompt not yet answered, "denied" = blocked.
  let micAccess = "pending";

  function applyMicGate() {
    const blocked = micAccess !== "granted";
    for (const el of [micSel, spkSel, micListen, spkTest, vol]) el.disabled = blocked;
    if (blocked) {
      pill.hidden = true;
      btn.className = "callbtn off";
      caption.textContent = micAccess === "denied"
        ? "microphone access is blocked, allow it for this site and reload"
        : "allow microphone access to get started";
    }
  }

  function render() {
    if (micAccess !== "granted") return applyMicGate();
    clearInterval(timerIv); timerIv = 0;
    if (page === "dialling") {
      pill.hidden = false; dot.className = "dot"; pillText.textContent = "dialling…";
      btn.className = "callbtn dial"; caption.textContent = "dialling…";
    } else if (page === "ringing") {
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
    if (callCap) callCap.port.onmessage = null;
    callSrc?.disconnect(); callLp1?.disconnect(); callLp2?.disconnect();
    callCap?.disconnect(); playNode?.disconnect(); callLp3?.disconnect();
    callSrc = callLp1 = callLp2 = callCap = playNode = callLp3 = null;
  }

  async function startCall() {
    page = "dialling"; render();
    try {
      await ensureCtx();
      await playDialSequence();   // theatre first: also the double-tap guard
      callStream = await navigator.mediaDevices.getUserMedia(micConstraints());
      const r = await fetch("/call/claim", { method: "POST" });
      if (!r.ok) {
        endCallCleanup(); lease = null; page = "idle";
        playSfx("busy");   // dialled into an engaged line
        return syncFromLine();
      }
      lease = (await r.json()).lease_id;

      // capture chain: mic -> 2x lowpass 3400 -> capture worklet -> ws
      callSrc = ctx.createMediaStreamSource(callStream);
      callLp1 = new BiquadFilterNode(ctx, { type: "lowpass", frequency: 3400 });
      callLp2 = new BiquadFilterNode(ctx, { type: "lowpass", frequency: 3400 });
      callCap = new AudioWorkletNode(ctx, "capture-worklet");
      callSrc.connect(callLp1).connect(callLp2).connect(callCap);
      playNode = new AudioWorkletNode(ctx, "playback-worklet",
                                      { outputChannelCount: [1] });
      callLp3 = new BiquadFilterNode(ctx, { type: "lowpass", frequency: 3400 });
      playNode.connect(callLp3).connect(gain);

      const proto = location.protocol === "https:" ? "wss" : "ws";
      audioWs = new WebSocket(
        `${proto}://${location.host}/ws/audio?lease=${encodeURIComponent(lease)}`);
      audioWs.binaryType = "arraybuffer";
      audioWs.onmessage = (e) => playNode.port.postMessage(e.data);
      callCap.port.onmessage = (e) => {
        if (audioWs && audioWs.readyState === 1) audioWs.send(e.data);
      };
      audioWs.onclose = () => {
        if (page === "ringing" || page === "oncall")
          enterEnding("you were hung up on", "busy");
      };

      await new Promise((res, rej) => {
        audioWs.onopen = res; audioWs.onerror = rej;
      });
      const rr = await fetch(`/call/ring?lease=${encodeURIComponent(lease)}`,
                             { method: "POST" });
      if (!rr.ok) { endCallCleanup(); lease = null; page = "idle"; return syncFromLine(); }
      page = "ringing";
      ringLoop = playSfx("ringback", { loop: true });
      render();
    } catch {
      // a lease may already be ours here (claim succeeded, then worklet/WS
      // setup blew up) -- release it now instead of leaving it held until
      // the claim window times it out from under us
      const l = lease;
      endCallCleanup();
      if (l) fetch(`/call/hangup?lease=${encodeURIComponent(l)}`, { method: "POST" })
        .catch(() => {});
      toIdle();
    }
  }

  // End-of-call lockout: the outcome caption holds and the button stays dead
  // for exactly as long as the end sound plays, then the page resyncs.
  // "you hung up" rides the physical handset clunk; everything ended from the
  // far side ("you were hung up on", "no answer") rides the AU busy tone.
  function enterEnding(text, sfxName) {
    if (page === "ending") return;
    endCallCleanup();
    lease = null;
    page = "ending";
    clearTimeout(captionTimeout); captionTimeout = 0;
    const src = playSfx(sfxName);
    const ms = (sfxBuf[sfxName]?.duration ?? 1.5) * 1000 + 150;
    pill.hidden = false; dot.className = "dot"; pillText.textContent = "idle";
    btn.className = "callbtn off";
    caption.textContent = text;
    setTimeout(() => {
      if (page !== "ending") return;
      page = "idle";
      syncFromLine();   // catches line changes that happened during the lockout
    }, src ? ms : 300);
  }

  function hangup() {   // user-initiated: the physical clunk
    const l = lease;
    if (l) fetch(`/call/hangup?lease=${encodeURIComponent(l)}`, { method: "POST" })
      .catch(() => {});
    enterEnding("you hung up", "clunk");
  }

  btn.addEventListener("click", () => {
    if (micAccess !== "granted") { boot(); return; }   // re-prompt on tap
    if (page === "idle") startCall();
    else if (page === "ringing" || page === "oncall") hangup();
    // dialling / ending / busy / unplugged: inert
  });

  // ---- events feed ---------------------------------------------------------
  function syncFromLine() {
    if (lease) return;   // my own flow drives the UI while I hold the lease
    if (page === "ending" || page === "dialling") return;   // lockout/theatre first
    if (line.state === "idle") { page = "idle"; render(); }
    else if (line.state === "unplugged") { page = "unplugged"; render(); }
    else { page = "busy"; render(); }
  }

  function onLine(ev) {
    line = ev;
    if (lease) {
      // my lease: server-side transitions I care about
      if (ev.state === "oncall" && page === "ringing") {
        stopRingback(); oncallOffset = Math.max(0, (Date.now() / 1000) - ev.since);
        page = "oncall"; render();
      } else if (ev.state === "idle") {
        enterEnding(page === "ringing" ? "no answer" : "you were hung up on",
                    "busy");
      }
      return;
    }
    syncFromLine();
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
  // Ask for mic permission up front (Meet-style pre-join): device labels only
  // exist after a grant, and the pickers must work BEFORE the first call.
  // Returning visitors have a standing grant, so no prompt appears.
  let eventsStarted = false;
  async function boot() {
    applyMicGate();   // "allow microphone access" until the prompt resolves
    if (!eventsStarted) { eventsStarted = true; connectEvents(); }
    await ensureCtx().catch(() => {});
    micAccess = (await startMeter()) ? "granted" : "denied";
    applyMicGate();
    if (micAccess !== "granted") { watchPermission(); return; }
    render();
    await refreshDevices();   // labels exist now that the grant is live
  }
  // if the user unblocks the mic in browser settings, come back to life
  let watching = false;
  async function watchPermission() {
    if (watching) return;
    watching = true;
    try {
      const st = await navigator.permissions.query({ name: "microphone" });
      st.onchange = () => { if (st.state !== "denied") location.reload(); };
    } catch {}   // permissions API absent: the call-button tap re-prompts
  }
  // AudioContext may boot suspended (no user gesture yet): resume on first tap
  document.addEventListener("pointerdown", () => {
    if (ctx?.state === "suspended") ctx.resume().catch(() => {});
  }, { capture: true });
  boot();
})();
