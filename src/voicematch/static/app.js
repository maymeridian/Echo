import { yin } from "./pitch.js";

// The browser's only audio jobs are capturing the microphone and turning any
// clip into 16 kHz mono WAV; all measurement happens on the server.

const SR = 16000;
const MAX_SECONDS = 30;
const MAX_TRIM_SECONDS = 600; // longer clips can be opened, then trimmed to the line
const $ = (selector) => document.querySelector(selector);

let targets = [];
let current = null; // selected target
let takes = []; // summaries for the selected target, oldest first
let shown = null; // full take currently displayed
let session = null; // { kind: "take" | "target", recorder, timer }

// ---------- server ----------

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let message = res.statusText;
    try {
      message = (await res.json()).detail || message;
    } catch {}
    throw new Error(message);
  }
  return res.status === 204 ? null : res.json();
}

const postWav = (path, wav) =>
  api(path, { method: "POST", headers: { "Content-Type": "audio/wav" }, body: wav });

// ---------- audio ----------

function encodeWav(samples) {
  const view = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const text = (offset, s) => [...s].forEach((ch, i) => view.setUint8(offset + i, ch.charCodeAt(0)));
  text(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, SR, true);
  view.setUint32(28, SR * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([view], { type: "audio/wav" });
}

async function decodeMono(blob, maxSeconds) {
  // decodeAudioData resamples to the context's rate, so a 16 kHz context
  // hands back 16 kHz audio whatever the file was.
  const ctx = new OfflineAudioContext(1, 1, SR);
  let decoded;
  try {
    decoded = await ctx.decodeAudioData(await blob.arrayBuffer());
  } catch {
    throw new Error("Couldn't read that audio.");
  }
  if (decoded.duration > maxSeconds) {
    throw new Error(`That clip is ${Math.round(decoded.duration)} s long — the limit is ${maxSeconds} s.`);
  }
  const mono = new Float32Array(decoded.length);
  for (let c = 0; c < decoded.numberOfChannels; c++) {
    const channel = decoded.getChannelData(c);
    for (let i = 0; i < mono.length; i++) mono[i] += channel[i] / decoded.numberOfChannels;
  }
  return mono;
}

const toWav = async (blob) => encodeWav(await decodeMono(blob, MAX_SECONDS + 1));

class Recorder {
  async start(onLevel, onPitch) {
    // The browser's call-quality processing reshapes the voice, which is the
    // thing being measured, so all of it is off.
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
    });
    this.chunks = [];
    this.media = new MediaRecorder(this.stream);
    this.media.ondataavailable = (e) => e.data.size && this.chunks.push(e.data);
    this.media.start();

    this.ctx = new AudioContext();
    const analyser = this.ctx.createAnalyser();
    analyser.fftSize = 2048;
    this.ctx.createMediaStreamSource(this.stream).connect(analyser);
    const buffer = new Float32Array(analyser.fftSize);
    const tick = () => {
      if (this.media.state !== "recording") return;
      analyser.getFloatTimeDomainData(buffer);
      const peak = buffer.reduce((m, x) => Math.max(m, Math.abs(x)), 0);
      onLevel(peak);
      if (onPitch && peak > 0) {
        const frame = buffer.map((x) => x / peak);
        onPitch(yin(frame, this.ctx.sampleRate)?.f0 ?? null);
      }
      requestAnimationFrame(tick);
    };
    tick();
  }

  stop() {
    return new Promise((resolve) => {
      this.media.onstop = () => {
        this.stream.getTracks().forEach((track) => track.stop());
        this.ctx.close();
        resolve(new Blob(this.chunks, { type: this.media.mimeType }));
      };
      this.media.stop();
    });
  }
}

// ---------- recording flow ----------

function setStatus(message, isError = false) {
  $("#status").textContent = message;
  $("#status").classList.toggle("error", isError);
}

// ---------- live pitch while recording a take ----------

const LIVE_SECONDS = 5;
let live = null; // { profile, points: [{t, f0}], started }

async function startLive() {
  let profile = null;
  try {
    profile = await api(`/api/targets/${current.id}/profile`);
  } catch {}
  live = { profile, points: [], started: performance.now() };
  $("#live").hidden = false;
  $("#live-text").textContent = "Speak…";
}

function onLivePitch(f0) {
  if (!live) return;
  const t = (performance.now() - live.started) / 1000;
  live.points.push({ t, f0 });
  live.points = live.points.filter((p) => p.t > t - LIVE_SECONDS);
  const recent = live.points.filter((p) => p.f0 && p.t > t - 0.5).map((p) => p.f0).sort((a, b) => a - b);
  const target = live.profile?.median_f0;
  if (recent.length >= 5) {
    const now = recent[Math.floor(recent.length / 2)];
    let text = `You: ${Math.round(now)} Hz`;
    if (target) {
      const st = 12 * Math.log2(now / target);
      const advice = Math.abs(st) < 1 ? "on target" : st > 0 ? `go lower (${st.toFixed(1)} st)` : `go higher (${(-st).toFixed(1)} st)`;
      text += ` · target about ${Math.round(target)} Hz — ${advice}`;
    }
    $("#live-text").textContent = text;
  }
  drawLive(t);
}

function drawLive(now) {
  const canvas = $("#live-pitch");
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (!w) return;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const g = canvas.getContext("2d");
  g.scale(dpr, dpr);
  const css = getComputedStyle(document.documentElement);
  const color = (name) => css.getPropertyValue(name).trim();
  const p = live.profile;
  const voiced = live.points.filter((pt) => pt.f0).map((pt) => pt.f0);
  // Pitch is heard in ratios, so the axis is logarithmic, around the target.
  const centre = p?.median_f0 ?? (voiced.length ? voiced[voiced.length - 1] : 150);
  let lo = centre / 2;
  let hi = centre * 2;
  for (const f of voiced) {
    lo = Math.min(lo, f / 1.1);
    hi = Math.max(hi, f * 1.1);
  }
  const y = (hz) => h - 6 - (Math.log(hz / lo) / Math.log(hi / lo)) * (h - 12);
  const x = (t) => ((t - (now - LIVE_SECONDS)) / LIVE_SECONDS) * w;
  if (p?.low && p?.high) {
    g.fillStyle = color("--track");
    g.fillRect(0, y(p.high), w, y(p.low) - y(p.high));
    g.strokeStyle = color("--target");
    g.lineWidth = 2;
    g.beginPath();
    g.moveTo(0, y(p.median_f0));
    g.lineTo(w, y(p.median_f0));
    g.stroke();
  }
  g.fillStyle = color("--accent");
  for (const pt of live.points) {
    if (pt.f0) g.fillRect(x(pt.t) - 1.5, y(pt.f0) - 1.5, 3, 3);
  }
}

function stopLive() {
  live = null;
  $("#live").hidden = true;
}

async function startRecording(kind) {
  const recorder = new Recorder();
  if (kind === "take") await startLive();
  try {
    await recorder.start(
      (peak) => ($("#level").style.width = `${Math.min(100, peak * 140)}%`),
      kind === "take" ? onLivePitch : null,
    );
  } catch {
    stopLive();
    setStatus("Couldn't open the microphone — allow access for this page and try again.", true);
    return;
  }
  const started = performance.now();
  const timer = setInterval(() => {
    const seconds = (performance.now() - started) / 1000;
    setStatus(`Recording… ${seconds.toFixed(0)} s`);
    if (seconds >= MAX_SECONDS) stopRecording();
  }, 250);
  session = { kind, recorder, timer };
  setStatus("Recording…");
  renderControls();
}

async function stopRecording() {
  const { kind, recorder, timer } = session;
  session = null;
  clearInterval(timer);
  stopLive();
  $("#level").style.width = "0";
  renderControls();
  const blob = await recorder.stop();
  if (kind === "take") await submitTake(blob);
  else await openTrim(blob);
}

const toggleRecording = (kind) => (session ? stopRecording() : startRecording(kind));

async function submitTake(blob) {
  setStatus("Scoring…");
  try {
    const take = await postWav(`/api/targets/${current.id}/takes`, await toWav(blob));
    takes.push({ id: take.id, created: take.created, overall: take.overall });
    shown = take;
    setStatus("");
    renderResult();
    renderHistory();
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function createTarget(samples) {
  const name = $("#new-name").value.trim();
  setStatus("Adding clip: separating the voice from any music, which can take a little while…");
  try {
    const target = await postWav(`/api/targets?name=${encodeURIComponent(name)}`, encodeWav(samples));
    targets.push(target);
    $("#new-name").value = "";
    setStatus("");
    await select(target);
  } catch (err) {
    setStatus(err.message, true);
  }
}

// ---------- trimming a new clip to the line ----------

let trimming = null; // { samples }
let trimPlayer = null;

async function openTrim(blob) {
  let samples;
  try {
    samples = await decodeMono(blob, MAX_TRIM_SECONDS);
  } catch (err) {
    setStatus(err.message, true);
    return;
  }
  const seconds = samples.length / SR;
  trimming = { samples };
  for (const id of ["#trim-start", "#trim-end"]) $(id).max = seconds.toFixed(2);
  $("#trim-start").value = 0;
  $("#trim-end").value = Math.min(seconds, MAX_SECONDS).toFixed(2);
  $("#empty").hidden = true;
  $("#practice").hidden = true;
  $("#trim").hidden = false;
  setStatus("");
  renderTrim();
}

function trimRange() {
  let a = Number($("#trim-start").value);
  let b = Number($("#trim-end").value);
  if (b < a) [a, b] = [b, a];
  return [a, b];
}

function renderTrim() {
  if (!trimming) return;
  const [a, b] = trimRange();
  const length = b - a;
  const ok = length >= 0.5 && length <= MAX_SECONDS;
  $("#trim-len").textContent = ok ? `${length.toFixed(1)} s selected` : `${length.toFixed(1)} s — pick 0.5 to ${MAX_SECONDS} s`;
  $("#trim-len").classList.toggle("error", !ok);
  $("#trim-use").disabled = !ok;
  drawWave(a, b);
}

function drawWave(a, b) {
  const canvas = $("#trim-wave");
  const { samples } = trimming;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (!w) return;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const g = canvas.getContext("2d");
  g.scale(dpr, dpr);
  const css = getComputedStyle(document.documentElement);
  const seconds = samples.length / SR;
  const per = Math.max(1, Math.floor(samples.length / w));
  for (let x = 0; x < w; x++) {
    let lo = 0;
    let hi = 0;
    for (let i = x * per; i < Math.min(samples.length, (x + 1) * per); i++) {
      lo = Math.min(lo, samples[i]);
      hi = Math.max(hi, samples[i]);
    }
    const t = (x / w) * seconds;
    g.fillStyle = css.getPropertyValue(t >= a && t <= b ? "--accent" : "--line").trim();
    g.fillRect(x, h / 2 - (hi * h) / 2, 1, Math.max(1, ((hi - lo) * h) / 2));
  }
}

function stopTrimPlayback() {
  if (trimPlayer) {
    trimPlayer.close();
    trimPlayer = null;
  }
}

function playTrim() {
  stopTrimPlayback();
  const [a, b] = trimRange();
  const part = trimming.samples.subarray(Math.round(a * SR), Math.round(b * SR));
  const ctx = new AudioContext();
  const buffer = ctx.createBuffer(1, part.length, SR);
  buffer.copyToChannel(part, 0);
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(ctx.destination);
  source.onended = stopTrimPlayback;
  source.start();
  trimPlayer = ctx;
}

function closeTrim() {
  stopTrimPlayback();
  trimming = null;
  $("#trim").hidden = true;
  $("#empty").hidden = Boolean(current);
  $("#practice").hidden = !current;
}

async function useTrim() {
  const [a, b] = trimRange();
  const part = trimming.samples.slice(Math.round(a * SR), Math.round(b * SR));
  closeTrim();
  await createTarget(part);
}

function nameGiven() {
  if ($("#new-name").value.trim()) return true;
  setStatus("Name the clip first.", true);
  $("#new-name").focus();
  return false;
}

// ---------- rendering ----------

function renderControls() {
  const kind = session?.kind;
  $("#record").classList.toggle("live", kind === "take");
  $("#record").disabled = kind === "target";
  $("#record-label").textContent = kind === "take" ? "Stop" : "Record a take";
  $("#new-record").classList.toggle("live", kind === "target");
  $("#new-record").disabled = kind === "take";
  $("#new-record").textContent = kind === "target" ? "Stop" : "Record clip";
}

function renderTargets() {
  $("#targets").replaceChildren(
    ...targets.map((target) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = target.name;
      button.setAttribute("aria-current", String(target.id === current?.id));
      button.onclick = () => select(target);
      const li = document.createElement("li");
      li.append(button);
      return li;
    }),
  );
}

async function select(target) {
  current = target;
  shown = null;
  takes = [];
  renderTargets();
  $("#empty").hidden = Boolean(target);
  $("#practice").hidden = !target;
  if (!target) return;
  $("#target-name").textContent = target.name;
  $("#target-audio").src = `/api/targets/${target.id}/audio`;
  renderLine();
  try {
    takes = await api(`/api/targets/${target.id}/takes`);
    if (takes.length) shown = await api(`/api/targets/${target.id}/takes/${takes.at(-1).id}`);
  } catch (err) {
    setStatus(err.message, true);
  }
  renderResult();
  renderHistory();
}

function renderLine() {
  const words = current?.words ?? [];
  $("#target-line").textContent = words.length ? `“${words.join(" ")}”` : "No words recognised in this clip.";
  $("#target-line").classList.toggle("empty", !words.length);
  $("#edit-line").textContent = words.length ? "Edit" : "Add the line";
  $("#line-form").hidden = true;
  $("#edit-line").parentElement.hidden = false;
}

function editLine() {
  $("#line-input").value = (current.words ?? []).join(" ");
  $("#edit-line").parentElement.hidden = true;
  $("#line-form").hidden = false;
  $("#line-input").focus();
}

async function saveLine(event) {
  event.preventDefault();
  try {
    const meta = await api(`/api/targets/${current.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ line: $("#line-input").value }),
    });
    current.words = meta.words;
    targets = targets.map((t) => (t.id === meta.id ? { ...t, words: meta.words } : t));
    renderLine();
    setStatus("Line saved; it's used from the next take.");
  } catch (err) {
    setStatus(err.message, true);
  }
}

function renderResult() {
  $("#result").hidden = !shown;
  if (!shown) return;
  $("#overall").textContent = Math.round(shown.overall);
  $("#take-audio").src = `/api/targets/${current.id}/takes/${shown.id}/audio`;
  $("#check").hidden = !shown.accepted;

  const earlier = takes.filter((t) => t.created < shown.created).map((t) => t.overall);
  const best = Math.max(...earlier);
  if (!earlier.length) $("#score-note").textContent = "First take — this is your baseline.";
  else if (shown.overall > best) $("#score-note").textContent = `New best, up from ${Math.round(best)}.`;
  else $("#score-note").textContent = `Best before this: ${Math.round(best)}.`;

  $("#components").replaceChildren(
    ...shown.components.map((c) => {
      const li = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = c.label;
      const bar = document.createElement("span");
      bar.className = "bar";
      const fill = document.createElement("i");
      fill.style.width = `${c.score ?? 0}%`;
      bar.append(fill);
      const num = document.createElement("span");
      num.className = "num";
      num.textContent = c.score == null ? "–" : Math.round(c.score);
      const detail = document.createElement("span");
      detail.className = "detail";
      detail.textContent = c.detail;
      li.append(label, bar, num, detail);
      return li;
    }),
  );

  $("#hints-wrap").hidden = !shown.hints.length;
  $("#hints").replaceChildren(
    ...shown.hints.map((hint) => {
      const li = document.createElement("li");
      li.textContent = hint;
      return li;
    }),
  );
  drawContour();
}

function renderHistory() {
  $("#history").hidden = takes.length < 2;
  if (takes.length < 2) return;
  const best = Math.max(...takes.map((t) => t.overall));
  $("#history-note").textContent = `${takes.length} so far, best ${Math.round(best)}`;
  $("#takes").replaceChildren(
    ...takes.map((take) => {
      const button = document.createElement("button");
      button.type = "button";
      button.style.height = `${Math.max(4, take.overall)}%`;
      button.title = `${Math.round(take.overall)} — ${new Date(take.created).toLocaleString()}`;
      button.setAttribute("aria-current", String(take.id === shown?.id));
      button.onclick = async () => {
        shown = await api(`/api/targets/${current.id}/takes/${take.id}`);
        renderResult();
        renderHistory();
      };
      return button;
    }),
  );
}

function drawContour() {
  const canvas = $("#contour");
  if (!shown || !canvas.clientWidth) return;
  const { t, target, take } = shown.contour;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const g = canvas.getContext("2d");
  g.scale(dpr, dpr);
  const css = getComputedStyle(document.documentElement);
  const color = (name) => css.getPropertyValue(name).trim();
  g.font = "11px system-ui, sans-serif";
  g.fillStyle = color("--muted");

  const voiced = [...target, ...take].filter((v) => v != null);
  if (!voiced.length) {
    g.fillText("No pitch detected.", 8, 20);
    return;
  }
  // Pitch is heard in ratios, so the axis is logarithmic.
  const lo = Math.min(...voiced) / 1.08;
  const hi = Math.max(...voiced) * 1.08;
  const pad = { left: 40, right: 6, top: 6, bottom: 18 };
  const x = (time) => pad.left + (time / (t.at(-1) || 1)) * (w - pad.left - pad.right);
  const y = (hz) => pad.top + (1 - Math.log(hz / lo) / Math.log(hi / lo)) * (h - pad.top - pad.bottom);

  g.strokeStyle = color("--line");
  g.lineWidth = 1;
  for (const hz of [60, 80, 100, 125, 150, 200, 250, 300, 400, 500, 600]) {
    if (hz < lo || hz > hi) continue;
    g.beginPath();
    g.moveTo(pad.left, y(hz));
    g.lineTo(w - pad.right, y(hz));
    g.stroke();
    g.fillText(`${hz} Hz`, 0, y(hz) + 4);
  }
  g.fillText(`${t.at(-1).toFixed(1)} s`, w - pad.right - 28, h - 4);

  const line = (values, stroke) => {
    g.strokeStyle = stroke;
    g.lineWidth = 2;
    g.lineJoin = "round";
    g.beginPath();
    let pen = false;
    values.forEach((hz, i) => {
      if (hz == null) return (pen = false);
      if (pen) g.lineTo(x(t[i]), y(hz));
      else g.moveTo(x(t[i]), y(hz));
      pen = true;
    });
    g.stroke();
  };
  line(target, color("--target"));
  line(take, color("--accent"));
}

// ---------- wiring ----------

$("#record").onclick = () => toggleRecording("take");
$("#new-record").onclick = () => (session || nameGiven()) && toggleRecording("target");
$("#new-file").onclick = (e) => nameGiven() || e.preventDefault();
$("#new-file").onchange = async (e) => {
  const [file] = e.target.files;
  e.target.value = "";
  if (file) await openTrim(file);
};
$("#trim-start").oninput = renderTrim;
$("#trim-end").oninput = renderTrim;
$("#trim-play").onclick = playTrim;
$("#trim-use").onclick = useTrim;
$("#trim-cancel").onclick = closeTrim;
$("#edit-line").onclick = editLine;
$("#cancel-line").onclick = renderLine;
$("#line-form").onsubmit = saveLine;
$("#delete-target").onclick = async () => {
  if (!confirm(`Delete "${current.name}" and all its takes?`)) return;
  try {
    await api(`/api/targets/${current.id}`, { method: "DELETE" });
    targets = targets.filter((t) => t.id !== current.id);
    await select(null);
  } catch (err) {
    setStatus(err.message, true);
  }
};
window.addEventListener("resize", () => {
  drawContour();
  renderTrim();
});

try {
  targets = await api("/api/targets");
  await select(targets.at(-1) ?? null);
} catch (err) {
  setStatus(err.message, true);
}
