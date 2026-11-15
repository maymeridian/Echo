// The browser's only audio jobs are capturing the microphone and turning any
// clip into 16 kHz mono WAV; all measurement happens on the server.

const SR = 16000;
const MAX_SECONDS = 30;
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

async function toWav(blob) {
  // decodeAudioData resamples to the context's rate, so a 16 kHz context
  // hands back 16 kHz audio whatever the file was.
  const ctx = new OfflineAudioContext(1, 1, SR);
  let decoded;
  try {
    decoded = await ctx.decodeAudioData(await blob.arrayBuffer());
  } catch {
    throw new Error("Couldn't read that audio.");
  }
  if (decoded.duration > MAX_SECONDS + 1) {
    throw new Error(`That clip is ${Math.round(decoded.duration)} s long — trim it to under ${MAX_SECONDS} s.`);
  }
  const mono = new Float32Array(decoded.length);
  for (let c = 0; c < decoded.numberOfChannels; c++) {
    const channel = decoded.getChannelData(c);
    for (let i = 0; i < mono.length; i++) mono[i] += channel[i] / decoded.numberOfChannels;
  }
  return encodeWav(mono);
}

class Recorder {
  async start(onLevel) {
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
    analyser.fftSize = 1024;
    this.ctx.createMediaStreamSource(this.stream).connect(analyser);
    const buffer = new Float32Array(analyser.fftSize);
    const tick = () => {
      if (this.media.state !== "recording") return;
      analyser.getFloatTimeDomainData(buffer);
      onLevel(buffer.reduce((peak, x) => Math.max(peak, Math.abs(x)), 0));
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

async function startRecording(kind) {
  const recorder = new Recorder();
  try {
    await recorder.start((peak) => ($("#level").style.width = `${Math.min(100, peak * 140)}%`));
  } catch {
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
  $("#level").style.width = "0";
  renderControls();
  const blob = await recorder.stop();
  if (kind === "take") await submitTake(blob);
  else await createTarget(blob);
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

async function createTarget(blob) {
  const name = $("#new-name").value.trim();
  setStatus("Adding clip…");
  try {
    const target = await postWav(`/api/targets?name=${encodeURIComponent(name)}`, await toWav(blob));
    targets.push(target);
    $("#new-name").value = "";
    setStatus("");
    await select(target);
  } catch (err) {
    setStatus(err.message, true);
  }
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
  $("#target-line").hidden = !target.words?.length;
  $("#target-line").textContent = target.words?.length ? `“${target.words.join(" ")}”` : "";
  try {
    takes = await api(`/api/targets/${target.id}/takes`);
    if (takes.length) shown = await api(`/api/targets/${target.id}/takes/${takes.at(-1).id}`);
  } catch (err) {
    setStatus(err.message, true);
  }
  renderResult();
  renderHistory();
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
  if (file) await createTarget(file);
};
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
window.addEventListener("resize", drawContour);

try {
  targets = await api("/api/targets");
  await select(targets.at(-1) ?? null);
} catch (err) {
  setStatus(err.message, true);
}
