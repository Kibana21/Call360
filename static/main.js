const $ = (id) => document.getElementById(id);
const btn = $("toggle");
const statusEl = $("status");
const transcript = $("transcript");

let scripts = [];
let selected = null;
let answers = {};
let micCtx, playerCtx, micStream, recorderNode, playerNode;
let line = null;      // pre-connected websocket for the selected script
let callLine = null;  // the line currently in a call
let botUntil = 0;     // when queued bot audio finishes playing (for the echo gate)
const ECHO_GATE_RMS = 0.02;
const inCall = () => !!callLine;
let recorder, recChunks = [], callId = null;
let current = { user: null, bot: null };
let timerId, startedAt, speakTimer;

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtTime = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

function setStatus(text, live = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("live", live);
}

// ---------- Rendering ----------
function renderScripts() {
  $("scripts").innerHTML = scripts
    .map(
      (s) => `<button class="script ${s.id === selected?.id ? "active" : ""}" data-id="${s.id}" ${inCall() ? "disabled" : ""}>
        <div class="t">${esc(s.title)}</div>
        <div class="c">${esc(s.profile.client_name)} · ${esc(s.channel)}</div>
        <div class="tags">${s.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>
      </button>`
    )
    .join("");
  document.querySelectorAll(".script").forEach((el) => (el.onclick = () => selectScript(el.dataset.id)));
}

function renderProfile() {
  const p = selected.profile;
  const facts = [
    ["Policy", p.policy_name],
    ["Premium", `${p.premium} · ${p.frequency}`],
    ["Term", p.term],
    ["Agent", p.agent_name],
    ["Channel", selected.channel],
    ["Purchased", p.purchase_date],
    ["Application", p.application_no],
    ["DOB", p.dob],
  ];
  if (p.language) facts.push(["Language", p.language]);
  if (p.replacing) facts.push(["Replacing", p.replacing]);
  $("profile").innerHTML = `
    <div>
      <div class="name">${esc(p.client_name)}</div>
      <div class="meta">Age ${esc(p.age)} · ${esc(selected.product)} · ${esc(p.insurer)}</div>
    </div>
    ${p.selected_client ? `<div class="badge-sc">SELECTED CLIENT</div>` : "<div></div>"}
    <div class="facts">${facts.map(([k, v]) => `<div class="fact"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("")}</div>
    <div class="focus"><b>Call objective:</b> ${esc(selected.focus)}</div>
    <div class="tips"><span class="lbl">Role-play ideas</span>${selected.roleplay_tips.map((t) => `<span class="tip">${esc(t)}</span>`).join("")}</div>`;
}

function renderChecklist(live = inCall()) {
  const all = selected.sections.flatMap((s) => s.questions);
  const nextId = all.find((q) => !answers[q.id])?.id;
  $("checklist").innerHTML = selected.sections
    .map(
      (sec) => `<div class="sec"><div class="sec-name">${esc(sec.name)}</div>${sec.questions
        .map((q) => {
          const a = answers[q.id];
          const cls = a ? a.flag : live && q.id === nextId ? "current" : "";
          const ans = a ? `<div class="ans">${esc(a.answer)}${a.note ? ` · ${esc(a.note)}` : ""}</div>` : "";
          return `<div class="q ${cls}"><div class="dot"></div><div><div class="qt">${esc(q.text)}${q.missell ? '<span class="ms-tag">MIS-SELL</span>' : ""}</div>${ans}</div></div>`;
        })
        .join("")}</div>`
    )
    .join("");
  const vals = Object.values(answers);
  $("cnt-done").textContent = `${vals.length}/${all.length} answered`;
  $("cnt-ok").textContent = vals.filter((a) => a.flag === "ok").length;
  $("cnt-warn").textContent = vals.filter((a) => a.flag === "concern").length;
  $("cnt-bad").textContent = vals.filter((a) => a.flag === "red_flag").length;
  $("bar").style.width = `${all.length ? (100 * vals.length) / all.length : 0}%`;
}

function showOutcome(r) {
  const el = $("outcome");
  if (!r || !r.outcome) { el.className = ""; return; }
  const label = { PASS: "PASS: verified", REVIEW: "REVIEW: follow-up needed", ESCALATE: "ESCALATE: compliance review" }[r.outcome] || r.outcome;
  el.className = `show ${r.outcome}`;
  el.innerHTML = `<div class="big">${esc(label)}</div>
    <div class="ms">${r.missell_suspected ? "⚠ Mis-selling suspected" : "No mis-selling indicators"}</div>
    ${r.path ? `<div class="file">Result saved: responses/${esc(r.path)}</div>` : ""}`;
}

function selectScript(id) {
  if (inCall()) return;
  selected = scripts.find((s) => s.id === id) || scripts[0];
  answers = {};
  showOutcome(null);
  $("replay").className = "";
  renderScripts();
  renderProfile();
  renderChecklist();
  ensureLine();
}

// ---------- Recorded calls ----------
async function loadCalls() {
  try {
    const calls = await (await fetch("/api/calls")).json();
    $("calls").innerHTML = calls.length
      ? calls
          .map((c) => {
            const d = new Date(c.started_at);
            const when = `${d.toLocaleDateString([], { day: "numeric", month: "short" })} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
            return `<button class="script callitem" data-id="${esc(c.call_id)}" ${inCall() ? "disabled" : ""}>
              <div><div class="t">${esc(c.client_name)}</div><div class="c">${esc(c.survey_title)}</div>
              <div class="when">${when} · ${fmtTime(c.duration_s || 0)}${c.audio_url ? " · 🎧" : ""}</div></div>
              <span class="chip ${c.outcome || "NONE"}">${esc(c.outcome || "INCOMPLETE")}</span>
            </button>`;
          })
          .join("")
      : `<div class="none">No calls recorded yet.</div>`;
    document.querySelectorAll(".callitem").forEach((el) => (el.onclick = () => openCall(el.dataset.id)));
  } catch (e) {
    $("calls").innerHTML = `<div class="none">Couldn't load calls.</div>`;
  }
}

async function openCall(id) {
  if (inCall()) return;
  const m = await (await fetch(`/api/calls/${encodeURIComponent(id)}`)).json();
  selected = scripts.find((s) => s.id === m.survey_id) || scripts[0];
  answers = {};
  for (const a of m.answers || []) answers[a.question_id] = a;
  renderScripts();
  renderProfile();
  renderChecklist(false);
  showOutcome(m.result);
  transcript.innerHTML = m.transcript?.length
    ? m.transcript
        .map((l) => `<div class="bubble ${l.speaker === "client" ? "user" : "bot"}"><span class="ts">${fmtTime(l.t)}</span>${esc(l.text)}</div>`)
        .join("")
    : `<div class="empty">No transcript was captured for this call.</div>`;
  const player = $("player");
  if (m.audio_url) {
    player.src = m.audio_url;
    $("replay-lbl").textContent = "Call recording";
    player.style.display = "";
  } else {
    player.removeAttribute("src");
    $("replay-lbl").textContent = "Audio not available for this call (transcript only)";
    player.style.display = "none";
  }
  $("replay").className = "show";
  $("timer").textContent = fmtTime(m.duration_s || 0);
  setStatus(`Viewing recorded call · ${m.client_name}`);
  ensureLine();
}

// ---------- Transcript ----------
function appendText(who, text, final) {
  $("empty")?.remove();
  const other = who === "user" ? "bot" : "user";
  current[other] = null;
  if (!current[who]) {
    current[who] = document.createElement("div");
    current[who].className = `bubble ${who}`;
    transcript.appendChild(current[who]);
  }
  if (final) {
    current[who].textContent = text; // final event carries the full utterance
    current[who] = null;
  } else {
    current[who].textContent += text;
  }
  transcript.scrollTop = transcript.scrollHeight;
}

function speaking() {
  $("avatar").classList.add("speaking");
  clearTimeout(speakTimer);
  speakTimer = setTimeout(() => $("avatar").classList.remove("speaking"), 400);
}

// ---------- Pre-connected line ----------
// The Gemini Live session is opened as soon as a script is selected, so Start is instant.
function openLine(surveyId) {
  closeLine();
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws?survey=${encodeURIComponent(surveyId)}`);
  ws.binaryType = "arraybuffer";
  const l = { ws, surveyId, closing: false, isReady: false };
  l.ready = new Promise((resolve, reject) => { l.resolve = resolve; l.reject = reject; });
  l.ready.catch(() => {});
  ws.onmessage = (e) => onLineMessage(l, e);
  ws.onerror = () => l.reject(new Error("Could not connect to the server"));
  ws.onclose = () => onLineClosed(l);
  line = l;
  return l;
}

function closeLine() {
  if (line && line !== callLine) {
    line.closing = true;
    line.ws.close();
  }
  line = null;
}

function ensureLine() {
  if (inCall() || !selected) return;
  if (line && line.surveyId === selected.id && line.ws.readyState <= WebSocket.OPEN) return;
  openLine(selected.id);
}

function onLineClosed(l) {
  l.reject(new Error("Line closed"));
  if (l === callLine) { stop(false); return; }
  if (l === line) line = null;
  // Idle line dropped (server idle timeout / network): quietly re-open it.
  if (!l.closing) setTimeout(ensureLine, 1500);
}

function onLineMessage(l, e) {
  if (!(e.data instanceof ArrayBuffer)) {
    const msg = JSON.parse(e.data);
    if (msg.type === "ready") {
      l.isReady = true;
      l.resolve();
      if (!inCall() && !statusEl.textContent.startsWith("Viewing") && !statusEl.textContent.startsWith("Call saved"))
        setStatus("Line ready");
      return;
    }
    if (l !== callLine) return;
    handleCallMessage(msg);
    return;
  }
  if (l !== callLine) return;
  // Bot audio: PCM16 @ 24 kHz
  const ms = (e.data.byteLength / 2 / 24000) * 1000;
  botUntil = Math.max(performance.now(), botUntil) + ms;
  playerNode.port.postMessage(e.data, [e.data]);
  speaking();
}

function handleCallMessage(msg) {
  if (msg.type === "session") callId = msg.call_id;
  else if (msg.type === "user" || msg.type === "bot") appendText(msg.type, msg.text, msg.final);
  else if (msg.type === "interrupted") { playerNode.port.postMessage({ cmd: "clear" }); botUntil = 0; }
  else if (msg.type === "turn_complete") current = { user: null, bot: null };
  else if (msg.type === "answer") {
    answers[msg.question_id] = msg;
    renderChecklist();
  } else if (msg.type === "done") {
    showOutcome(msg.result || {});
    setStatus("Survey complete: you can end the call", true);
  } else if (msg.type === "error") setStatus(`Error: ${msg.text}`);
}

// Echo gate: while Mei Ling is talking, only pass the mic through if the client is clearly
// speaking (barge-in). Quiet residual echo / room noise is replaced with silence.
function sendMic(buf) {
  if (!callLine || callLine.ws.readyState !== WebSocket.OPEN) return;
  if (performance.now() < botUntil + 200) {
    const s = new Int16Array(buf);
    let sum = 0;
    for (let i = 0; i < s.length; i++) sum += s[i] * s[i];
    if (Math.sqrt(sum / s.length) / 32768 < ECHO_GATE_RMS) buf = new ArrayBuffer(buf.byteLength);
  }
  callLine.ws.send(buf);
}

// ---------- Call ----------
async function setupAudio() {
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });

  playerCtx = new AudioContext({ sampleRate: 24000 });
  await playerCtx.audioWorklet.addModule("/static/pcm-player.js");
  playerNode = new AudioWorkletNode(playerCtx, "pcm-player");
  playerNode.connect(playerCtx.destination);

  // Call recording: mix Mei Ling's audio + the client's mic into one track.
  const mix = playerCtx.createMediaStreamDestination();
  playerNode.connect(mix);
  playerCtx.createMediaStreamSource(micStream).connect(mix);
  const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
  recorder = new MediaRecorder(mix.stream, mimeType ? { mimeType } : undefined);
  recChunks = [];
  recorder.ondataavailable = (e) => e.data.size && recChunks.push(e.data);

  micCtx = new AudioContext({ sampleRate: 16000 });
  await micCtx.audioWorklet.addModule("/static/pcm-recorder.js");
  recorderNode = new AudioWorkletNode(micCtx, "pcm-recorder");
  micCtx.createMediaStreamSource(micStream).connect(recorderNode);
  recorderNode.port.onmessage = (e) => sendMic(e.data);
}

async function start() {
  btn.disabled = true;
  answers = {};
  callId = null;
  botUntil = 0;
  showOutcome(null);
  $("replay").className = "";
  $("player").pause();
  transcript.innerHTML = "";
  current = { user: null, bot: null };

  ensureLine();
  const l = line;
  if (!l.isReady) setStatus("Connecting…");
  await setupAudio(); // runs while the line finishes connecting
  await Promise.race([l.ready, new Promise((_, rej) => setTimeout(() => rej(new Error("Server not responding")), 15000))]);

  callLine = l;
  l.ws.send(JSON.stringify({ type: "start" }));
  recorder.start(1000);
  setStatus("● Live call", true);
  btn.textContent = "End call";
  btn.classList.add("end");
  btn.disabled = false;
  $("rec").classList.add("on");
  startedAt = Date.now();
  timerId = setInterval(() => ($("timer").textContent = fmtTime((Date.now() - startedAt) / 1000)), 500);
  renderScripts();
  renderChecklist();
  loadCalls();
}

function closeAudio() {
  micStream?.getTracks().forEach((t) => t.stop());
  if (micCtx && micCtx.state !== "closed") micCtx.close();
  if (playerCtx && playerCtx.state !== "closed") playerCtx.close();
  micCtx = playerCtx = micStream = null;
}

function saveRecording() {
  const id = callId;
  const rec = recorder;
  recorder = null;
  if (!rec || rec.state === "inactive") { closeAudio(); return; }
  rec.onstop = async () => {
    closeAudio();
    if (!id || !recChunks.length) return;
    setStatus("Saving recording…");
    try {
      const blob = new Blob(recChunks, { type: "audio/webm" });
      const r = await fetch(`/api/calls/${encodeURIComponent(id)}/audio`, { method: "POST", body: blob });
      setStatus(r.ok ? "Call saved: see Recorded calls" : "Recording upload failed");
    } catch {
      setStatus("Recording upload failed");
    }
    loadCalls();
  };
  rec.stop();
}

function stop(sendStop = true) {
  const l = callLine;
  if (!l) return;
  callLine = null;
  l.closing = true;
  if (sendStop && l.ws.readyState === WebSocket.OPEN) l.ws.send(JSON.stringify({ type: "stop" }));
  if (l.ws.readyState <= WebSocket.OPEN) l.ws.close();
  if (line === l) line = null;
  clearInterval(timerId);
  $("rec").classList.remove("on");
  saveRecording();
  btn.textContent = "Start call";
  btn.classList.remove("end");
  btn.disabled = false;
  renderScripts();
  if (selected) renderChecklist(false);
  setTimeout(loadCalls, 800);
  setTimeout(ensureLine, 1000); // pre-connect the next call straight away
}

btn.onclick = () =>
  inCall()
    ? stop()
    : start().catch((err) => {
        setStatus(`Error: ${err.message}`);
        btn.disabled = false;
        if (callLine) stop(false);
        else { recorder = null; closeAudio(); }
      });

// ---------- Boot ----------
fetch("/api/surveys")
  .then((r) => r.json())
  .then((data) => {
    scripts = data;
    selectScript(scripts[0].id);
    loadCalls();
  })
  .catch((err) => setStatus(`Failed to load scripts: ${err.message}`));
