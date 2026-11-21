import { Ending, spoken, waitFor } from "./ending.js";
import { Listener, alignedShift, shift, track, typical } from "./formants.js";
import { yin } from "./pitch.js";

// The browser's jobs are capturing the microphone, playing clips, drawing, and
// turning any clip into 16 kHz mono WAV. Everything that is scored is measured
// on the server; the browser measures only what it shows while you speak (your
// pitch, pitch.js, and your resonance, formants.js) and hears when you have
// finished, so a take stops by itself (ending.js).
//
// The pages: home, a voice (its score, lines and recordings), a recording (its
// lines to pick from), a line (listen, record, improve), and a voice check's
// result. A voice check walks through the voice's ticked lines on the line
// page, one take each.

const SR = 16000;
const MAX_SECONDS = 30;
const MAX_TRIM_SECONDS = 600; // longer clips can be opened, then trimmed to the line
const LONG_SECONDS = 60; // uploads longer than this are split into lines on the server
const CONTEXT_SECONDS = 1.5; // shown either side of a line from a recording, to adjust its ends
const WARMUP_SECONDS = 120;
const PARTS = [
  ["pitch", "Pitch"],
  ["melody", "Melody"],
  ["resonance", "Resonance"],
  ["articulation", "Articulation"],
  ["rhythm", "Rhythm"],
];
const ALL_PARTS = [["identity", "Voice"], ...PARTS];
const WORD_PARTS = ["pitch", "melody", "resonance", "articulation"];
const $ = (selector) => document.querySelector(selector);

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children.filter((c) => c != null));
  return node;
}

// Per-viewer conveniences only; the page works the same without storage.
const stored = {
  get(key) {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key, value) {
    try {
      if (value == null || value === "") localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch {}
  },
};

let voices = []; // every voice, as listed
let voice = null; // the open voice, with its lines, recordings and checks
let targets = []; // every line, for "Continue" and "already added"
let sources = []; // every recording, to follow their progress
let current = null; // the open line
let profile = null; // its guide: duration, onset, contour, words, pitch band
let takes = []; // its takes' summaries, oldest first
let shown = null; // the take on screen
let source = null; // the open recording, with its lines once they're found
let session = null; // { kind: "take" | "target" | "warmup", recorder, timer, target, owner }
// While the microphone is opening for a take, a warm-up, a clip or a trainer
// attempt (which can take seconds: the browser may ask first), and while a take
// or attempt is being scored, nothing else may start: a second start would open
// a second recorder that nothing stops, and a stray take would be scored too.
let opening = null;
let measuring = false;
let page = 0; // goes up whenever a page is left, so work started on it can tell
let live = null; // { points: [{t, f0, f}], started, onset, done, listener, target }
let targetTrack = null; // the open line's F1-F3 through time, as the browser reads them: { id, ceiling, frames }
let check = null; // a voice check under way: { voiceId, voiceName, lines, index, results }
let selectedWord = null;
let speed = 1;
let playhead = null; // where playback is, on the target's timeline

const VIEWS = ["empty", "home", "voice", "trim", "source", "practice", "train", "check-result"];

function show(view) {
  for (const id of VIEWS) $(`#${id}`).hidden = id !== view;
  window.scrollTo(0, 0);
}

function setStatus(message, isError = false) {
  $("#status").textContent = message;
  $("#status").classList.toggle("error", isError);
}

const NOTES = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"];

// The nearest note name, such as G3 for 196 Hz.
function noteName(hz) {
  const n = Math.round(12 * Math.log2(hz / 440)) + 69;
  return `${NOTES[((n % 12) + 12) % 12]}${Math.floor(n / 12) - 1}`;
}

const partLabel = (key) => ALL_PARTS.find(([k]) => k === key)?.[1] ?? key;
const lineText = (t) => (t.words?.length ? t.words.join(" ") : t.name);
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const voiceOf = (id) => voices.find((v) => v.id === id);

function when(iso) {
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

// ---------- server ----------

// What went wrong, from an error response: the server's `detail` (a list of
// problems when FastAPI itself turned the request down), else its status.
async function problem(res) {
  try {
    const { detail } = await res.json();
    if (Array.isArray(detail)) return detail.map((d) => d?.msg ?? String(d)).join("; ") || res.statusText;
    return detail || res.statusText;
  } catch {
    return res.statusText || `Error ${res.status}`;
  }
}

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(await problem(res));
  return res.status === 204 ? null : res.json();
}

const send = (path, method, body) =>
  api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const postWav = (path, wav) =>
  api(path, { method: "POST", headers: { "Content-Type": "audio/wav" }, body: wav });

async function refreshLists() {
  [voices, targets, sources] = await Promise.all([api("/api/voices"), api("/api/targets"), api("/api/sources")]);
  renderSidebar();
}

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

// ---------- the microphone ----------

// The browser's call-quality processing reshapes the voice, which is the
// thing being measured, so all of it is off.
const MIC = { echoCancellation: false, noiseSuppression: false, autoGainControl: false };
const MIC_IDLE_SECONDS = 15 * 60;
const QUIET = 0.0002; // a microphone whose loudest sample stays under this is giving silence

// One stream, kept open between takes, and asked for by the id of the
// microphone chosen here. Two things go wrong otherwise:
// - A browser that asks every time the microphone is opened (Firefox, unless
//   told to remember) would ask at every take.
// - A browser told to remember stops asking which microphone, and hands over
//   the system's default. On a computer with virtual audio devices (streaming
//   and headset software add them) that is often one that hears nothing.
const mic = { stream: null, devices: [], timer: null, note: "", testing: null, opening: null };

const micLive = () => Boolean(mic.stream?.getAudioTracks().some((t) => t.readyState === "live"));
const micLabel = () => mic.stream?.getAudioTracks()[0]?.label || stored.get("echo.mic.label") || "the microphone";

function micProblem(err) {
  if (err?.name === "NotAllowedError" || err?.name === "SecurityError") {
    return "The browser is blocking the microphone for this page. Allow it (the microphone icon in the address bar), then try again.";
  }
  if (err?.name === "NotFoundError" || err?.name === "OverconstrainedError") return "No microphone was found. Plug one in, then try again.";
  if (err?.name === "NotReadableError" || err?.name === "AbortError") {
    return "The microphone didn't start: another app may be using it. Close that, or choose another microphone on the left.";
  }
  return "Couldn't open the microphone. Choose another one on the left, or allow it for this page, and try again.";
}

async function listMics() {
  try {
    const all = await navigator.mediaDevices.enumerateDevices();
    // Chrome also lists "default" and "communications", which are whatever the system points them at.
    mic.devices = all.filter((d) => d.kind === "audioinput" && d.deviceId && d.deviceId !== "default" && d.deviceId !== "communications");
  } catch {
    mic.devices = [];
  }
  renderMic();
}

// Has the browser been told to always allow the microphone here? Then it won't ask, or say which one it picked.
async function alwaysAllowed() {
  try {
    return (await navigator.permissions.query({ name: "microphone" })).state === "granted";
  } catch {
    return false; // this browser doesn't say
  }
}

// The microphone, opened once and shared. Asked for again while it is still
// opening (a browser prompt can stay up for a while), the same request answers.
function openMic() {
  clearTimeout(mic.timer);
  if (micLive()) return Promise.resolve(mic.stream);
  mic.opening ??= openMicNow().finally(() => (mic.opening = null));
  return mic.opening;
}

async function openMicNow() {
  const ask = (audio) => navigator.mediaDevices.getUserMedia({ audio });
  const wanted = stored.get("echo.mic");
  const name = stored.get("echo.mic.label");
  mic.note = "";
  let stream = null;
  if (wanted) {
    try {
      stream = await ask({ ...MIC, deviceId: { exact: wanted } });
    } catch (err) {
      if (err.name !== "OverconstrainedError" && err.name !== "NotFoundError") throw err;
    }
  }
  if (!stream) {
    // No choice yet, or the chosen microphone isn't there under that id: take what the browser gives.
    const allowed = await alwaysAllowed();
    const before = performance.now();
    stream = await ask(MIC);
    const asked = !allowed && performance.now() - before > 400; // the browser put its own question, with its own list
    await listMics();
    const got = stream.getAudioTracks()[0];
    const same = name && got.label !== name ? mic.devices.find((d) => d.label === name) : null;
    if (same && !asked) {
      // The chosen microphone under a new id (a browser hands out new ids when it forgets a site): switch to it.
      try {
        const again = await ask({ ...MIC, deviceId: { exact: same.deviceId } });
        stream.getTracks().forEach((t) => t.stop());
        stream = again;
      } catch {}
    } else if (wanted && name && got.label !== name && !asked) {
      mic.note = `“${name}” isn't connected. This is the browser's default instead.`;
    }
    const now = stream.getAudioTracks()[0];
    const id = now.getSettings().deviceId;
    // Remember it when it was chosen: in the browser's question just now, or here before.
    if (id && (asked || now.label === name)) {
      stored.set("echo.mic", id);
      stored.set("echo.mic.label", now.label);
    }
  }
  mic.stream = stream;
  stream.getAudioTracks()[0].addEventListener("ended", () => {
    if (mic.stream !== stream) return;
    mic.stream = null; // unplugged, or taken away by the browser
    renderMic();
  });
  await listMics(); // the names can be read now
  return stream;
}

// Let go of the microphone: at once, or after it has gone unused for a while.
function closeMic() {
  clearTimeout(mic.timer);
  if (session || drillRec || mic.testing) return restMic();
  mic.stream?.getTracks().forEach((t) => t.stop());
  mic.stream = null;
  renderMic();
}

function restMic() {
  clearTimeout(mic.timer);
  mic.timer = setTimeout(closeMic, MIC_IDLE_SECONDS * 1000);
}

// An audio graph on the microphone, for the level and the live readouts.
// Firefox won't join a microphone to a context running at another sample
// rate, so other rates are tried before giving up on the readouts.
function listenTo(stream) {
  const own = stream.getAudioTracks()[0]?.getSettings().sampleRate;
  for (const rate of [null, own, 48000, 44100, 32000, 16000, 96000]) {
    if (rate === undefined) continue;
    let ctx = null;
    try {
      ctx = rate ? new AudioContext({ sampleRate: rate }) : new AudioContext();
      const source = ctx.createMediaStreamSource(stream);
      if (ctx.state === "suspended") ctx.resume().catch(() => {});
      return { ctx, source };
    } catch {
      ctx?.close().catch(() => {});
    }
  }
  return null;
}

class Recorder {
  // `onEnd`: the recording stopped without being asked to, because the
  // microphone went away (unplugged, or switched off by the system).
  async start(onLevel, onPitch, onEnd = () => {}) {
    this.heard = 0; // the loudest sample so far
    this.ctx = null;
    this.stopping = false;
    this.stream = await openMic();
    this.chunks = [];
    try {
      this.media = new MediaRecorder(this.stream);
      this.media.ondataavailable = (e) => e.data.size && this.chunks.push(e.data);
      this.done = new Promise((resolve) => {
        this.media.onstop = () => {
          this.ctx?.close().catch(() => {});
          restMic(); // the stream stays open for the next take
          renderMic();
          resolve(new Blob(this.chunks, { type: this.media.mimeType }));
          if (!this.stopping) onEnd();
        };
      });
      this.media.start();
    } catch (err) {
      restMic();
      throw err;
    }
    renderMic();

    const wired = listenTo(this.stream);
    if (!wired) return; // the take still records; only the level and the live readouts are missing
    this.ctx = wired.ctx;
    const rate = this.ctx.sampleRate;
    const analyser = this.ctx.createAnalyser();
    // Pitch reads the latest 2048 samples at 44.1 and 48 kHz, as it was
    // checked, and about as long a stretch at higher rates (a 192 kHz audio
    // interface). The formant tracker reads 50 ms, which is more than that,
    // so the buffer is twice as long and `onPitch` is handed all of it too.
    const span = rate > 50000 ? 2 ** Math.ceil(Math.log2(rate * 0.04)) : 2048;
    analyser.fftSize = 2 * span;
    wired.source.connect(analyser);
    const buffer = new Float32Array(analyser.fftSize);
    const recent = buffer.subarray(buffer.length - span);
    const tick = () => {
      if (this.media.state !== "recording") return;
      analyser.getFloatTimeDomainData(buffer);
      const peak = recent.reduce((m, x) => Math.max(m, Math.abs(x)), 0);
      this.heard = Math.max(this.heard, peak);
      onLevel(peak, Math.sqrt(recent.reduce((sum, x) => sum + x * x, 0) / recent.length));
      if (onPitch && peak > 0) {
        const frame = recent.map((x) => x / peak);
        onPitch(yin(frame, rate)?.f0 ?? null, buffer, rate);
      }
      requestAnimationFrame(tick);
    };
    tick();
  }

  // True once it's clear the microphone is giving nothing at all.
  get silent() {
    return Boolean(this.ctx) && this.heard < QUIET;
  }

  // What was recorded. Safe to call more than once, and after the recording
  // has stopped by itself.
  stop() {
    this.stopping = true;
    if (this.media && this.media.state !== "inactive") this.media.stop();
    return this.done ?? Promise.resolve(new Blob());
  }
}

const noSound = () => `No sound is coming from “${micLabel()}”. Choose your microphone in the list on the left.`;

// A take, or a scored attempt in the trainer, stops by itself once you have
// finished speaking (ending.js), unless that is switched off in the sidebar.
// The button and Space still stop it at any moment.
const autoStop = () => stored.get("echo.autostop") !== "off";

function renderAutoStop() {
  $("#auto-stop").checked = autoStop();
  for (const note of document.querySelectorAll(".auto-note")) note.hidden = !autoStop();
  for (const note of document.querySelectorAll(".manual-note")) note.hidden = autoStop();
}

// The sidebar's microphone box: which one, a level to see that it hears you, and what's going on.
function renderMic() {
  const select = $("#mic-select");
  const busy = Boolean(session || drillRec);
  const chosen = stored.get("echo.mic") || "";
  const name = stored.get("echo.mic.label");
  const options = [el("option", { value: "", textContent: "The browser's choice" })];
  mic.devices.forEach((d, i) => options.push(el("option", { value: d.deviceId, textContent: d.label || `Microphone ${i + 1}` })));
  // The chosen one stays listed while the browser isn't naming its microphones (before it's allowed) or it's unplugged.
  if (chosen && !mic.devices.some((d) => d.deviceId === chosen)) options.push(el("option", { value: chosen, textContent: name || "The one you chose" }));
  select.replaceChildren(...options);
  select.value = chosen;
  select.disabled = busy || Boolean(mic.testing);
  $("#mic-test").textContent = mic.testing ? "Stop" : "Test";
  $("#mic-test").disabled = busy;
  $("#mic-off").hidden = !micLive() || busy || Boolean(mic.testing);
  $("#mic-box").classList.toggle("attention", Boolean(mic.note));
  const on = micLive() ? `On: ${mic.stream.getAudioTracks()[0].label || "the microphone"}. It stays on between takes, and turns off after 15 minutes unused.` : "";
  $("#mic-note").textContent = mic.note || on || (chosen ? "" : "If the browser picks the wrong one, choose yours here. Echo remembers it.");
}

async function chooseMic(id) {
  const device = mic.devices.find((d) => d.deviceId === id);
  stored.set("echo.mic", id);
  stored.set("echo.mic.label", id ? (device?.label ?? stored.get("echo.mic.label")) : "");
  mic.note = "";
  closeMic(); // let go of the other one
  if (id) await testMic(); // open it and show its level straight away
}

// Open the microphone for a few seconds and show its level; nothing is recorded.
async function testMic() {
  if (mic.testing) return stopMicTest();
  if (session || drillRec || opening) return;
  const recorder = new Recorder();
  mic.testing = { recorder, timer: null };
  renderMic();
  try {
    await recorder.start(
      (peak) => ($("#mic-level").style.width = `${Math.min(100, peak * 140)}%`),
      null,
      () => mic.testing?.recorder === recorder && stopMicTest(),
    );
  } catch (err) {
    mic.testing = null;
    mic.note = micProblem(err);
    renderMic();
    return;
  }
  if (mic.testing?.recorder !== recorder) return recorder.stop(); // stopped while it was opening
  mic.testing.timer = setTimeout(stopMicTest, 8000);
  mic.note = "Say something: the bar should move.";
  renderMic();
}

async function stopMicTest() {
  const test = mic.testing;
  if (!test) return;
  mic.testing = null;
  clearTimeout(test.timer);
  $("#mic-level").style.width = "0";
  mic.note = test.recorder.silent ? `No sound came from “${micLabel()}”. Try another one.` : "";
  if (test.recorder.media?.state === "recording") await test.recorder.stop();
  renderMic();
}

// ---------- shared pieces: a score ring and the scale from a stranger to the real person ----------

function ring(score) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 80 80");
  const track = document.createElementNS(ns, "circle");
  const fill = document.createElementNS(ns, "circle");
  for (const c of [track, fill]) {
    c.setAttribute("cx", "40");
    c.setAttribute("cy", "40");
    c.setAttribute("r", "34");
  }
  track.setAttribute("class", "ring-track");
  fill.setAttribute("class", "ring-fill");
  const circumference = 2 * Math.PI * 34;
  fill.setAttribute("stroke-dasharray", `${(circumference * Math.max(0, score)) / 100} ${circumference}`);
  svg.append(track, fill);
  return el("div", { className: "ring" }, svg);
}

function ladder(score, before = null) {
  const now = el("i", { className: "ladder-now", title: `Now: ${Math.round(score)}` });
  now.style.left = `${score}%`;
  const track = el("div", { className: "ladder-track" });
  if (before != null) {
    const mark = el("i", { className: "ladder-best", title: `Before: ${Math.round(before)}` });
    mark.style.left = `${before}%`;
    track.append(mark);
  }
  track.append(now);
  const marks = el("div", { className: "ladder-marks" });
  marks.innerHTML = '<span style="left: 20%">20<br>a stranger</span><span style="left: 90%">90<br>the real person</span>';
  return el("div", { className: "ladder" }, track, marks);
}

function bar(score) {
  const fill = el("i");
  fill.style.width = `${score ?? 0}%`;
  return el("span", { className: "bar" }, fill);
}

function sparkline(values) {
  const points = values.map((v, i) => [i, v]).filter(([, v]) => v != null);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 64 20");
  svg.setAttribute("class", "spark");
  svg.setAttribute("aria-hidden", "true");
  if (points.length >= 2) {
    const n = values.length - 1;
    const poly = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    poly.setAttribute("points", points.map(([i, v]) => `${((i / n) * 62 + 1).toFixed(1)},${(19 - (v / 100) * 18).toFixed(1)}`).join(" "));
    svg.append(poly);
  }
  return svg;
}

function delta(now, before, unit = "") {
  if (now == null || before == null) return "";
  const d = Math.round(now - before);
  return d > 0 ? `+${d}${unit}` : d < 0 ? `−${-d}${unit}` : `±0${unit}`;
}

// ---------- the sidebar: just the voices ----------

function renderSidebar() {
  const openId = voice?.id ?? current?.voice_id ?? source?.voice_id ?? check?.voiceId ?? null;
  $("#voices").replaceChildren(
    ...voices.map((v) => {
      const button = el(
        "button",
        { type: "button", className: "voice-btn" },
        el("span", { className: "name", textContent: v.name }),
        el("span", { className: "score", textContent: v.score == null ? "" : Math.round(v.score), title: "Voice score, from the last voice check" }),
        el("small", { textContent: v.line_count ? plural(v.line_count, "line") : "no lines yet" }),
      );
      button.setAttribute("aria-current", String(v.id === openId));
      button.onclick = () => openVoice(v.id);
      return el("li", {}, button);
    }),
  );
}

function toggleNewVoice(open) {
  $("#new-voice-form").hidden = !open;
  $("#new-voice").hidden = open;
  if (open) $("#new-voice-name").focus();
}

const isLink = (text) => /^https?:\/\/\S+$/i.test(text.trim());

// A name makes an empty voice; a link makes a voice from the video.
async function startVoice(text) {
  text = text.trim();
  if (!text) return;
  if (isLink(text)) await createFromLink(text);
  else await createVoice(text);
}

async function createFromLink(url) {
  try {
    const meta = await send("/api/sources/link", "POST", { url });
    await refreshLists();
    await openVoice(meta.voice_id);
    setStatus("Echo is getting the voice lines from the video. A long video takes a few minutes; you can leave this page meanwhile.");
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function createVoice(name) {
  try {
    const made = await send("/api/voices", "POST", { name });
    voices.push(made);
    renderSidebar();
    await openVoice(made.id);
    setStatus("Now add some lines: paste a YouTube link, upload a recording or record a clip.");
  } catch (err) {
    setStatus(err.message, true);
  }
}

function remember(view) {
  stored.set("echo.view", view);
}

function leave() {
  page++;
  stopPlayback();
  stopLinePlayback();
  // A take, a warm-up or a clip left part-way is kept nowhere: it belonged to
  // the page being left.
  if (session) stopRecording({ discard: true });
  if (drillRec) {
    // Leaving mid-word: stop the microphone, keep nothing.
    clearTimeout(drillRec.timer);
    drillRec.recorder.stop();
    drillRec = null;
    drillLive = null;
  }
  training = null;
  check = null;
  current = null;
  source = null;
  voice = null;
}

// ---------- home ----------

async function goHome() {
  leave();
  remember("home");
  renderSidebar();
  show(voices.length ? "home" : "empty");
  if (voices.length) renderHome();
}

function renderHome() {
  const recent = targets.filter((t) => t.last).sort((a, b) => (a.last < b.last ? 1 : -1))[0];
  $("#continue").hidden = !recent;
  if (recent) {
    const owner = voiceOf(recent.voice_id);
    const next = recent.focus ? ` Next: work on ${partLabel(recent.focus).toLowerCase()}.` : "";
    const go = el("button", { type: "button", className: "record", textContent: "Continue" });
    go.onclick = () => openLine(recent);
    $("#continue").replaceChildren(
      el(
        "div",
        { className: "continue-text" },
        el("p", { className: "eyebrow", textContent: owner ? `Last practised · ${owner.name}` : "Last practised" }),
        el("p", { className: "continue-line", textContent: `“${lineText(recent)}”` }),
        el("p", { className: "fine", textContent: `${plural(recent.takes, "take")}, best ${Math.round(recent.best)}, last ${Math.round(recent.last_score ?? recent.best)}.${next}` }),
      ),
      go,
    );
  }
  $("#voice-cards").replaceChildren(
    ...voices.map((v) => {
      const open = el("button", { type: "button", className: "btn", textContent: "Open" });
      open.onclick = () => openVoice(v.id);
      const score =
        v.score == null
          ? el("p", { className: "fine", textContent: v.line_count >= v.min_check_lines ? "No voice score yet: run a voice check." : `${plural(v.line_count, "line")}. Add ${v.min_check_lines - v.line_count} more for a voice check.` })
          : el("p", { className: "voice-card-score" }, el("b", { textContent: Math.round(v.score) }), ` voice score${v.previous != null ? `, ${delta(v.score, v.previous)} since the check before` : ""}`);
      return el(
        "div",
        { className: "card voice-card" },
        el("p", { className: "voice-card-name", textContent: v.name }),
        score,
        el("p", { className: "fine", textContent: `${plural(v.line_count, "line")}${v.recording_count ? ` from ${plural(v.recording_count, "recording")}` : ""}` }),
        open,
      );
    }),
  );
}

// ---------- a voice: its score, lines and recordings ----------

let voiceTab = "practise"; // which part of the voice's page is showing

function setVoiceTab(tab) {
  voiceTab = tab;
  for (const b of document.querySelectorAll("#voice-tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === tab));
  for (const pane of document.querySelectorAll("#voice .pane")) pane.hidden = pane.dataset.pane !== tab;
}

async function openVoice(id, tab = null) {
  leave();
  try {
    voice = await api(`/api/voices/${id}`); // a merged voice answers as the one it joined
  } catch (err) {
    setStatus(err.message, true);
    return goHome();
  }
  remember(`voice:${voice.id}`);
  renderSidebar();
  renderVoice();
  // A voice with no lines yet opens on "Add lines".
  setVoiceTab(tab ?? (voice.lines.length ? "practise" : "add"));
  show("voice");
}

async function refreshVoice() {
  if (!voice) return;
  try {
    const asked = voice.id;
    voice = await api(`/api/voices/${voice.id}`);
    if (voice.id !== asked) {
      // Its video was from a channel that already had a voice: they're one now.
      remember(`voice:${voice.id}`);
      voices = await api("/api/voices");
      setStatus(`This video is from “${voice.name}”, which already had a voice, so its lines joined that one.`);
    }
    voices = voices.map((v) => (v.id === voice.id ? { ...v, ...voice } : v));
    renderSidebar();
    if (!$("#voice").hidden) renderVoice();
  } catch {}
}

const STEPS = [
  ["downloading", "Download"],
  ["reading", "Read the audio"],
  ["transcribing", "Find the lines"],
  ["speakers", "Find who's speaking"],
  ["picking", "Pick the best lines"],
];

// While a video or recording is processed: where it's got to.
function renderProgress() {
  const busy = voice.recordings.filter(unfinished);
  $("#voice-progress").hidden = !busy.length;
  $("#voice-progress").replaceChildren(
    ...busy.map((s) => {
      const steps = STEPS.filter(([key]) => key !== "downloading" || s.link);
      const at = steps.findIndex(([key]) => key === s.status);
      const items = steps.map(([key, label], i) => {
        const pct = i === at && s.progress != null ? ` ${Math.round(s.progress * 100)}%` : "";
        return el("li", { className: at < 0 ? "" : i < at ? "done" : i === at ? "now" : "", textContent: `${label}${pct}` });
      });
      const share = at < 0 ? 0 : (at + (s.progress ?? 0.5)) / steps.length;
      const fill = el("i");
      fill.style.width = `${Math.round(share * 100)}%`;
      return el(
        "div",
        { className: "progress" },
        el("h3", { textContent: `Getting voice lines from “${s.name || "the video"}”` }),
        el("span", { className: "bar" }, fill),
        el("ol", { className: "progress-steps" }, ...items),
        el("p", { className: "fine", textContent: s.status === "queued" ? "Waiting to start…" : "A long video takes a few minutes. You can leave this page; it carries on." }),
      );
    }),
  );
}

// "▶ <channel> on YouTube: <title>", opening the video in a new tab.
function videoLink(s) {
  const site = /youtu\.?be/i.test(s.link) ? "YouTube" : "the web";
  const title = s.title || s.name;
  const a = el("a", { href: s.link, target: "_blank", rel: "noopener", className: "video-link", title: `${title}
${s.link}` });
  // The channel first: a long title is cut short at the end.
  a.textContent = `▶ ${s.channel ? `${s.channel} on ${site}` : site}: ${title}`;
  return a;
}

async function addRecordingLink(s) {
  const url = prompt("Paste the link to the video this recording came from:")?.trim();
  if (!url) return;
  setStatus("Looking the video up…");
  try {
    await send(`/api/sources/${s.id}`, "PATCH", { link: url });
    await refreshVoice();
    setStatus("Link added.");
  } catch (err) {
    setStatus(err.message, true);
  }
}

// Recordings with more than one speaker: who they are, and which one the voice is.
function renderSpeakers() {
  const multi = voice.recordings.filter((s) => s.status === "ready" && s.speakers?.length > 1);
  $("#speakers-card").hidden = !multi.length;
  $("#speakers-card").replaceChildren(
    ...multi.map((s) => {
      const rows = s.speakers.map((sp) => {
        const play = el("button", { type: "button", className: "play", textContent: "▶" });
        play.setAttribute("aria-label", `Play speaker ${sp.speaker + 1}`);
        play.onclick = () => playUrl(`/api/sources/${s.id}/audio?start=${sp.sample.start}&end=${sp.sample.end}`, play, "voice");
        const inUse = sp.speaker === s.speaker;
        const actions = el("span", { className: "speaker-actions" });
        if (inUse) actions.append(el("span", { className: "in-use", textContent: "This voice" }));
        else {
          const use = el("button", { type: "button", className: "btn small", textContent: "Use this one" });
          use.onclick = () => chooseSpeaker(s, sp.speaker, use);
          const split = el("button", { type: "button", className: "btn small", textContent: "Make a separate voice" });
          split.onclick = () => splitSpeaker(s, sp.speaker, split);
          actions.append(use, split);
        }
        const pitch = sp.pitch ? ` · about ${sp.pitch} Hz (${noteName(sp.pitch)})` : "";
        return el(
          "li",
          { className: `speaker${inUse ? " current" : ""}` },
          play,
          el(
            "span",
            { className: "vline-text" },
            el("span", { className: "name", textContent: `Speaker ${sp.speaker + 1} · ${plural(sp.lines, "line")}${pitch}` }),
            el("small", { textContent: `“${sp.sample.text}”` }),
          ),
          actions,
        );
      });
      return el(
        "div",
        { className: "speakers" },
        el("h3", { textContent: "Who's speaking" }),
        el("p", { className: "fine", textContent: `“${s.name}” has ${s.speakers.length} speakers. This voice is speaker ${s.speaker + 1}${s.speaker === 0 ? ", who speaks the most" : ""}; the others' lines are left out of its suggestions. Play each to check.` }),
        el("ol", { className: "speaker-list" }, ...rows),
      );
    }),
  );
}

async function chooseSpeaker(recording, speaker, button) {
  button.disabled = true;
  button.textContent = "Switching…";
  setStatus(`Switching this voice to speaker ${speaker + 1} and picking their best lines…`);
  try {
    const result = await send(`/api/sources/${recording.id}`, "PATCH", { speaker });
    await refreshLists();
    await refreshVoice();
    setStatus(`This voice is now speaker ${speaker + 1}: ${plural(result.added.length, "line")} of theirs added${result.removed ? `, ${result.removed} unpractised picks of the other speaker removed` : ""}.`);
  } catch (err) {
    setStatus(err.message, true);
    renderSpeakers();
  }
}

async function splitSpeaker(recording, speaker, button) {
  const name = prompt("Name the new voice:", `${voice.name}, speaker ${speaker + 1}`)?.trim();
  if (!name) return;
  button.disabled = true;
  button.textContent = "Making it…";
  setStatus(`Making “${name}” and picking its best lines…`);
  try {
    const result = await send(`/api/sources/${recording.id}/split`, "POST", { speaker, name });
    await refreshLists();
    await openVoice(result.voice.id);
    setStatus(`Made “${name}” with ${plural(result.added.length, "line")}.`);
  } catch (err) {
    setStatus(err.message, true);
    renderSpeakers();
  }
}

async function renderProgressView() {
  const id = voice.id;
  let data;
  try {
    data = await api(`/api/voices/${id}/progress`);
  } catch {
    return;
  }
  if (voice?.id !== id) return;
  const all = data.takes;
  $("#progress-view").hidden = all.length < 2;
  $("#progress-empty").hidden = all.length >= 2;
  if (all.length < 2) return;

  // Every take, oldest to newest, with the average of the last five.
  const ns = "http://www.w3.org/2000/svg";
  const make = (tag, attrs) => {
    const node = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  };
  const W = 640;
  const H = 160;
  const pad = 26;
  const x = (i) => pad + (i / Math.max(1, all.length - 1)) * (W - pad - 8);
  const y = (s) => 8 + (1 - s / 100) * (H - 16 - 8);
  const svg = make("svg", { viewBox: `0 0 ${W} ${H}`, class: "progress-chart", role: "img" });
  svg.setAttribute("aria-label", "Every take's score, oldest to newest, with the average of the last five");
  for (const level of [20, 50, 90]) {
    svg.append(make("line", { x1: pad, x2: W - 8, y1: y(level), y2: y(level), class: "grid" }));
    const label = make("text", { x: 2, y: y(level) + 4, class: "axis" });
    label.textContent = level;
    svg.append(label);
  }
  const lines = [...new Set(all.map((t) => t.target))];
  all.forEach((take, i) => {
    const dot = make("circle", { cx: x(i), cy: y(take.overall), r: 3.5, class: `dot c${lines.indexOf(take.target) % 6}` });
    const title = make("title", {});
    title.textContent = `${Math.round(take.overall)} · “${take.line}” · ${when(take.created)}`;
    dot.append(title);
    svg.append(dot);
  });
  const rolling = all.map((_, i) => {
    const window = all.slice(Math.max(0, i - 4), i + 1);
    return window.reduce((s, t) => s + t.overall, 0) / window.length;
  });
  svg.append(make("polyline", { points: rolling.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" "), class: "trend" }));

  // Each part: your first five takes against your last five.
  const n = Math.min(5, Math.floor(all.length / 2));
  const avg = (takes, k) => {
    const v = takes.map((t) => t.parts?.[k]).filter((s) => s != null);
    return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
  };
  const rows = ALL_PARTS.map(([k, label]) => {
    const a = avg(all.slice(0, n), k);
    const b = avg(all.slice(-n), k);
    const change = a != null && b != null ? Math.round(b - a) : null;
    return el(
      "li",
      {},
      el("span", { className: "label", textContent: label }),
      bar(b),
      el("span", { className: "num", textContent: b == null ? "–" : Math.round(b) }),
      el("span", { className: `delta ${change > 0 ? "up" : change < 0 ? "down" : ""}`, textContent: change == null ? "" : delta(b, a) }),
    );
  });
  const best = all.reduce((m, t) => (t.overall > m.overall ? t : m), all[0]);
  const first = rolling[Math.min(4, rolling.length - 1)];
  const now = rolling.at(-1);
  const summary = `${plural(all.length, "take")} over ${plural(data.days.length, "day")}${data.drills ? `, ${plural(data.drills, "trainer attempt")}` : ""}. Your recent average is ${Math.round(now)}${all.length > 5 ? `, ${now >= first ? "up" : "down"} from ${Math.round(first)} at the start` : ""}. Best take: ${Math.round(best.overall)} on “${best.line}”.`;
  $("#progress-view").replaceChildren(
    el("h3", { textContent: "Your progress" }),
    el("p", { className: "fine", textContent: summary }),
    svg,
    el("p", { className: "fine", textContent: `Each part, your last ${plural(n, "take")} (and the change since your first ${n}):` }),
    el("ul", { className: "components progress-parts" }, ...rows),
  );
}

function renderVoice() {
  $("#voice-name").textContent = voice.name;
  renderProgressView();
  // Where its recordings came from.
  const linked = voice.recordings.filter((s) => s.link);
  $("#voice-links").hidden = !linked.length;
  $("#voice-links").replaceChildren(...linked.map((s) => el("li", {}, videoLink(s))));
  // Another name to offer, once: the channel (for a YouTuber, the person), or
  // the video's title when it was named after the channel (for a fan's
  // compilation of a character, say). Not once it's been named by hand.
  const video = voice.recordings.find((s) => s.channel || s.link);
  let offer = null;
  if (voice.named_from !== "hand" && video) {
    if (voice.named_from === "channel") offer = video.name && [video.name, `Named after the channel. Rename it “${video.name}”, after the video?`];
    else if (video.channel) offer = [video.channel, `Rename it “${video.channel}”, after the channel?`];
  }
  $("#channel-rename").hidden = !offer || offer[0] === voice.name;
  if (offer) {
    $("#channel-rename").textContent = offer[1];
    $("#channel-rename").onclick = () => renameVoice(offer[0]);
  }
  renderProgress();
  renderSpeakers();
  renderScoreCard();
  $("#voice-glance").hidden = !voice.summary && !voice.you;
  $("#glance-voice").textContent = voice.summary ? describeVoice(voice.summary) : "Add a recording to see how this voice sounds overall.";
  $("#glance-you").textContent = describeYou(voice.you);
  $("#glance-gender-row").hidden = voice.gender == null;
  if (voice.gender != null) {
    $("#glance-gender").textContent = `${genderWord(voice.gender)} (${Math.round(voice.gender)} on a scale from 0, male, to 100, female), from the pitch and resonance of its lines.`;
  }
  renderVoiceLines();
  renderSuggestions();
  renderRecordings();
}

function checkLines() {
  return voice.check_set.map((id) => voice.lines.find((l) => l.id === id)).filter(Boolean);
}

function renderScoreCard() {
  const lines = checkLines();
  const enough = lines.length >= voice.min_check_lines;
  const run = el("button", { type: "button", className: "record", textContent: voice.score == null ? "Run a voice check" : "Run another check" });
  run.id = "run-check";
  run.disabled = !enough;
  run.onclick = startCheck;
  const what = `A voice check plays through ${enough ? plural(lines.length, "ticked line") : "the ticked lines"}, one take each with no retries, and averages them. One line can be luck; several show whether the impression holds.`;
  const need = enough
    ? null
    : el("p", { className: "need", textContent: voice.line_count < voice.min_check_lines ? `Add ${voice.min_check_lines - voice.line_count} more line${voice.min_check_lines - voice.line_count === 1 ? "" : "s"} for a voice check (it needs ${voice.min_check_lines}).` : `Tick at least ${voice.min_check_lines} lines below for the check.` });
  if (voice.score == null) {
    $("#score-card").replaceChildren(
      el("div", { className: "score-card-text" }, el("h3", { textContent: "Voice score" }), el("p", { className: "big-empty", textContent: "No voice score yet" }), el("p", { className: "fine", textContent: what }), need),
      el("div", { className: "score-card-action" }, run),
    );
    return;
  }
  const last = voice.checks.at(-1);
  const before = voice.checks.at(-2);
  const seeLast = el("button", { type: "button", className: "btn", textContent: "See the last check" });
  seeLast.onclick = () => openCheck(voice.id, last.id);
  $("#score-card").replaceChildren(
    el(
      "div",
      { className: "score-card-main" },
      ring(voice.score),
      el(
        "div",
        { className: "score-text" },
        el("h3", { textContent: "Voice score" }),
        el("div", { className: "score" }, el("span", { textContent: Math.round(voice.score) }), el("small", { textContent: "/ 100 across lines" })),
        el("p", { className: "fine", textContent: `Last check ${when(last.created)}: lines from ${Math.round(last.low)} to ${Math.round(last.high)}.${before ? ` ${delta(last.average, before.average)} since the check before.` : ""}` }),
      ),
      voice.checks.length >= 2 ? el("div", { className: "check-trend", title: "Voice score at each check" }, sparkline(voice.checks.map((c) => c.average))) : null,
    ),
    ...[
      ladder(voice.score, before?.average ?? null),
      el("p", { className: "fine" }, what),
      need,
      el("div", { className: "score-card-buttons" }, run, seeLast),
    ].filter(Boolean),
  );
}

let voicePlayer = null; // { audio, button }

function stopLinePlayback() {
  for (const player of [linePlayer, voicePlayer]) {
    if (!player) continue;
    player.audio.pause();
    player.button.textContent = "▶";
  }
  linePlayer = voicePlayer = null;
}

function playUrl(url, button, which) {
  const again = (which === "voice" ? voicePlayer : linePlayer)?.button === button;
  stopLinePlayback();
  if (again) return; // a second click stops it
  const audio = new Audio(url);
  const player = { audio, button };
  if (which === "voice") voicePlayer = player;
  else linePlayer = player;
  button.textContent = "■";
  const done = () => (voicePlayer === player || linePlayer === player) && stopLinePlayback();
  audio.onended = done;
  audio.play().catch(done);
}

const CHECK_LINES = 5; // the voice check's default number of lines

// Picking by hand: one row per recording, opening its full list.
function pickMoreRows() {
  return voice.recordings
    .filter((s) => s.status === "ready")
    .map((s) => {
      const added = voice.lines.filter((l) => l.source === s.id).length;
      const button = el("button", { type: "button", className: "btn more-btn", textContent: `Pick by hand from “${s.name}”` });
      button.onclick = () => openSource(s);
      return el("li", { className: "more" }, button, el("small", { textContent: `${s.count} lines found in it, ${added} added.` }));
    });
}

// The best lines not added yet, each one click to add, and Auto-pick to
// top the voice up to the voice check's five.
function renderSuggestions() {
  const ready = voice.recordings.some((s) => s.status === "ready");
  $("#suggest-wrap").hidden = !ready;
  if (!ready) return;
  const busy = voice.recordings.some((s) => s.status === "picking");
  const list = voice.suggestions ?? [];
  const missing = Math.max(0, CHECK_LINES - voice.lines.length);
  const auto = $("#autopick");
  const count = Math.min(missing, list.length);
  auto.hidden = (!count || busy) && !picking;
  auto.disabled = picking;
  auto.textContent = picking ? "Picking…" : `Auto-pick the ${count === 1 ? "best line" : `${count} best lines`}`;
  auto.title = `Adds the best suggested lines until the voice has ${CHECK_LINES}, enough for a steady voice check.`;
  const rows = list.map((s) => {
    const play = el("button", { type: "button", className: "play", textContent: "▶" });
    play.setAttribute("aria-label", "Play this line");
    play.onclick = () => playUrl(`/api/sources/${s.source}/audio?start=${s.start}&end=${s.end}`, play, "voice");
    const key = `${s.source}:${s.start}`;
    const add = el("button", { type: "button", className: "btn practise", textContent: adding.has(key) ? "Adding…" : "Add" });
    add.disabled = busy || picking || adding.has(key);
    add.onclick = () => addSuggested(s);
    return el(
      "li",
      { className: "vline suggestion" },
      play,
      el("span", { className: "vline-text" }, el("span", { className: "words-text", textContent: `“${s.text}”` }), el("small", { textContent: `${s.recording} · ${clock(s.start)} · ${(s.end - s.start).toFixed(1)} s` })),
      add,
    );
  });
  const note = busy
    ? el("li", { className: "none", textContent: "Adding the best lines from a new recording…" })
    : list.length
      ? null
      : el("li", { className: "none", textContent: "Every clear line of its recordings is added." });
  $("#suggestions").replaceChildren(...[note, ...rows, ...pickMoreRows()].filter(Boolean));
}

// Requests under way, kept outside the page so a re-render (the page
// refreshes while a recording is processed) doesn't offer the same click again.
const adding = new Set(); // "<recording>:<start>" of suggested lines being added
let picking = false; // auto-pick is running

async function addSuggested(s) {
  const key = `${s.source}:${s.start}`;
  if (adding.has(key) || picking) return;
  adding.add(key);
  renderSuggestions();
  setStatus("Adding the line: separating the voice from the background takes a few seconds…");
  try {
    await send(`/api/voices/${voice.id}/lines`, "POST", { source: s.source, start: s.start });
    await refreshLists();
    await refreshVoice();
    setStatus(`Added “${s.text}”.`);
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    adding.delete(key);
    if (voice) renderSuggestions();
  }
}

async function autoPick() {
  if (picking) return;
  picking = true;
  renderSuggestions();
  setStatus("Picking the best lines: each is separated from the background first, a few seconds a line…");
  try {
    const { added } = await send(`/api/voices/${voice.id}/autopick`, "POST", { want: CHECK_LINES });
    await refreshLists();
    await refreshVoice();
    setStatus(added.length ? `Added ${plural(added.length, "line")}. ${voice.check_set.length >= voice.min_check_lines ? "You can run a voice check now." : ""}` : "There was nothing left to add.");
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    picking = false;
    if (voice) renderSuggestions();
  }
}

function renderVoiceLines() {
  const ticked = new Set(voice.check_set);
  $("#lines-head").textContent = voice.lines.length ? `Your lines (${voice.lines.length})` : "Your lines";
  if (!voice.lines.length) {
    $("#voice-lines").replaceChildren(
      el("li", { className: "none", textContent: voice.recordings.length ? "No lines added yet: pick from the suggested lines below." : "No lines yet. Add a recording or a clip below." }),
    );
    return;
  }
  $("#voice-lines").replaceChildren(
    ...voice.lines.map((line) => {
      const play = el("button", { type: "button", className: "play", textContent: "▶" });
      play.setAttribute("aria-label", "Play this line");
      play.onclick = () => playUrl(`/api/targets/${line.id}/audio`, play, "voice");
      const box = el("input", { type: "checkbox", checked: ticked.has(line.id) });
      box.onchange = () => toggleCheckLine(line.id, box.checked);
      const tick = el("label", { className: "in-check", title: "Use this line in the voice check" }, box, " check");
      const practise = el("button", { type: "button", className: "btn practise", textContent: line.takes ? "Practise" : "Start" });
      practise.onclick = () => openLine(line);
      const stats =
        (line.takes ? `best ${Math.round(line.best)} · last ${Math.round(line.last_score ?? line.best)} · ${plural(line.takes, "take")}` : "not practised yet") +
        (line.pitch_suspect ? " · pitch unreliable: a poor line to train pitch on" : "");
      return el(
        "li",
        { className: "vline" },
        play,
        el("span", { className: "vline-text" }, el("span", { className: "words-text", textContent: `“${lineText(line)}”` }), el("small", { textContent: stats })),
        tick,
        practise,
      );
    }),
  );
}

async function toggleCheckLine(id, on) {
  const ids = voice.lines.map((l) => l.id).filter((i) => (i === id ? on : voice.check_set.includes(i)));
  try {
    const card = await send(`/api/voices/${voice.id}`, "PATCH", { check_lines: ids });
    voice.check_set = card.check_set;
    renderScoreCard();
  } catch (err) {
    setStatus(err.message, true);
  }
}

function renderRecordings() {
  $("#recordings-wrap").hidden = !voice.recordings.length;
  $("#recordings").replaceChildren(
    ...voice.recordings.map((s) => {
      const open = el("button", { type: "button", className: "btn", textContent: "Pick lines" });
      open.disabled = s.status !== "ready";
      open.onclick = () => openSource(s);
      const added = voice.lines.filter((l) => l.source === s.id).length;
      const speakers = s.speakers?.length > 1 ? ` · ${s.speakers.length} speakers` : "";
      const note = s.status === "ready" ? `${s.count} lines found · ${added} added${speakers}` : s.status === "failed" ? s.error || "didn't work" : `${stepText(s)}…`;
      let source;
      if (s.link) source = videoLink(s);
      else if (s.status === "ready") {
        source = el("button", { type: "button", className: "link add-link", textContent: "Add its YouTube link" });
        source.onclick = () => addRecordingLink(s);
      }
      return el(
        "li",
        { className: "recording" },
        el(
          "span",
          { className: "rec-text" },
          el("span", { className: "name", textContent: s.name || "New video" }),
          el("small", { className: s.status === "failed" ? "error" : "", textContent: note }),
          source,
        ),
        open,
      );
    }),
  );
}

async function renameVoice(to = null) {
  const name = (typeof to === "string" ? to : prompt("Rename this voice:", voice.name))?.trim();
  if (!name || name === voice.name) return;
  try {
    const card = await send(`/api/voices/${voice.id}`, "PATCH", { name });
    voice.name = card.name;
    voices = voices.map((v) => (v.id === voice.id ? { ...v, name: card.name } : v));
    renderSidebar();
    renderVoice();
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function deleteVoice() {
  if (!confirm(`Delete "${voice.name}" with all its lines, takes and recordings?`)) return;
  try {
    await api(`/api/voices/${voice.id}`, { method: "DELETE" });
    await refreshLists();
    await goHome();
  } catch (err) {
    setStatus(err.message, true);
  }
}

function describeVoice(s) {
  const parts = [];
  if (s.pitch) parts.push(`around ${s.pitch} Hz (${noteName(s.pitch)}), usually ${s.low}–${s.high} Hz`);
  if (s.rate != null) parts.push(`about ${s.rate} words a second`);
  // Read speech is voiced on 53-60% of its word frames; a soft, breathy voice on about 45%.
  if (s.voiced != null) parts.push(s.voiced >= 0.52 ? "a full, clear voice" : s.voiced >= 0.38 ? "soft, with some breath in it" : "breathy, often close to a whisper");
  return `${parts.join(" · ")}.`;
}

function habitList(h) {
  const habits = [];
  if (h.pitch != null && Math.abs(h.pitch) >= 1) {
    habits.push(`${Math.abs(h.pitch).toFixed(1)} semitones ${h.pitch > 0 ? "high" : "low"}${h.take_f0 ? ` (you average ${Math.round(h.take_f0)} Hz, ${noteName(h.take_f0)})` : ""}`);
  }
  if (h.resonance != null && Math.abs(h.resonance) >= 0.4) {
    const percent = Math.round(Math.abs(2 ** (h.resonance / 12) - 1) * 100);
    habits.push(`${h.resonance > 0 ? "brighter" : "darker"} (formants ${percent}% ${h.resonance > 0 ? "higher" : "lower"})`);
  }
  if (h.tempo != null && Math.abs(h.tempo - 1) >= 0.1) habits.push(`${Math.round(Math.abs(h.tempo - 1) * 100)}% ${h.tempo > 1 ? "slower" : "faster"}`);
  return habits;
}

function describeYou(you) {
  if (!you) return "Practise a few of its lines and this shows what you do the same way in all of them: the habits worth fixing first.";
  const habits = habitList(you);
  const over = `Over your last ${plural(you.takes, "take")} on its lines`;
  if (!habits.length) return `${over}, no habit stands out: pitch, brightness and pace are all close on average.`;
  return `${over}: ${habits.join(" · ")}. That shows up in every line, so fixing it lifts them all.`;
}

// ---------- adding lines to the open voice ----------

// How long a file plays for, from its header alone; null when the browser can't tell.
function mediaDuration(file) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const media = document.createElement("video"); // reads audio files too
    const done = (value) => {
      URL.revokeObjectURL(url);
      resolve(value);
    };
    media.preload = "metadata";
    media.onloadedmetadata = () => done(Number.isFinite(media.duration) ? media.duration : null);
    media.onerror = () => done(null);
    setTimeout(() => done(null), 5000);
    media.src = url;
  });
}

async function addFile(file) {
  const name = file.name.replace(/\.[^.]+$/, "").slice(0, 80);
  const seconds = await mediaDuration(file);
  const long = seconds == null ? file.size > 20e6 : seconds > LONG_SECONDS;
  if (!long) return openTrim(file, { name, back: "voice", extra: { voice: voice.id } });
  setStatus(`Uploading ${file.name}…`);
  try {
    const params = new URLSearchParams({ name, filename: file.name, voice: voice.id });
    const meta = await api(`/api/sources?${params}`, { method: "POST", body: file });
    sources.push(meta);
    setStatus("Finding the lines in it. That takes a minute or two for a long video; you can practise meanwhile.");
    await refreshVoice();
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function addLink(event) {
  event.preventDefault();
  const url = $("#new-link").value.trim();
  if (!url) return;
  try {
    const meta = await send("/api/sources/link", "POST", { url, voice: voice.id });
    $("#new-link").value = "";
    sources.push(meta);
    setStatus("Downloading it and finding the lines. That takes a minute or two; you can practise meanwhile.");
    await refreshVoice();
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function createTarget(samples, name, extra = {}) {
  setStatus("Adding the line: separating the voice from any music, which can take a little while…");
  const params = new URLSearchParams({ name: name || "Clip" });
  for (const key of ["voice", "source", "line_start"]) if (extra[key] != null) params.set(key, extra[key]);
  try {
    const target = await postWav(`/api/targets?${params}`, encodeWav(samples));
    await refreshLists();
    setStatus("");
    await openLine(target);
  } catch (err) {
    setStatus(err.message, true);
  }
}

// ---------- a recording: its lines to pick from ----------

const FLAG_NOTES = {
  whispered: "Mostly whispered: there's little pitch to compare, so Pitch, Melody and Resonance can't be scored.",
  "other voice": "Sounds like a different person from most of the recording.",
  unclear: "The speech recogniser wasn't sure of the words; check the line after adding it.",
};

const unfinished = (s) => s.status !== "ready" && s.status !== "failed";

function clock(seconds) {
  const s = Math.floor(seconds);
  const parts = [Math.floor(s / 3600), Math.floor((s % 3600) / 60), s % 60];
  const mmss = `${parts[1]}:${String(parts[2]).padStart(2, "0")}`;
  return parts[0] ? `${parts[0]}:${mmss.padStart(5, "0")}` : mmss;
}

function stepText(s) {
  const pct = s.progress == null ? "" : ` ${Math.round(s.progress * 100)}%`;
  const steps = {
    queued: "waiting",
    uploading: "uploading",
    downloading: `downloading${pct}`,
    reading: `reading${pct}`,
    transcribing: `finding lines${pct}`,
    speakers: "finding who's speaking",
    picking: `adding the best lines${pct}`,
    ready: `${s.count} lines`,
    failed: "didn't work",
  };
  return steps[s.status] ?? s.status;
}

async function openSource(meta) {
  leave();
  source = { ...meta, lines: null };
  remember(`source:${meta.id}`);
  const owner = voiceOf(meta.voice_id);
  $("#source-crumb").textContent = owner ? `‹ ${owner.name}` : "‹ Home";
  $("#source-crumb").onclick = () => (owner ? openVoice(owner.id, "add") : goHome());
  renderSidebar();
  renderSource();
  show("source");
  await refreshSource();
}

async function refreshSource() {
  const id = source?.id;
  if (!id) return;
  try {
    const [fresh, lines] = await Promise.all([api(`/api/sources/${id}`), api("/api/targets")]);
    targets = lines; // which of its lines are added already
    if (source?.id !== id) return; // another page was opened meanwhile
    const linesArrived = Boolean(fresh.lines) && !source.lines;
    source = fresh;
    sources = sources.map((s) => (s.id === id ? { ...fresh, lines: undefined } : s));
    renderSource(linesArrived);
  } catch (err) {
    setStatus(err.message, true);
  }
}

function renderSource(withLines = true) {
  $("#source-name").textContent = source.name || "New video";
  $("#source-link").hidden = !source.link;
  if (source.link) $("#source-link").replaceWith(Object.assign(videoLink(source), { id: "source-link" }));
  let text;
  if (source.status === "failed") text = source.error || "That didn't work.";
  else if (unfinished(source)) {
    const step = stepText(source);
    text = `${step[0].toUpperCase()}${step.slice(1)}… A long video takes a minute or two. You can practise something else meanwhile.`;
  } else {
    text =
      `${source.count} lines in ${clock(source.duration)}, ${source.good} of them good to practise on. ` +
      "Pick one, check where it starts and ends, and it's cleaned of music and background and added to the voice.";
  }
  $("#source-status").textContent = text;
  $("#source-status").classList.toggle("error", source.status === "failed");
  const many = source.speakers?.length > 1;
  $("#source-speakers").hidden = !many;
  if (many) {
    $("#source-speakers").textContent = `${source.speakers.length} speakers. The voice is speaker ${source.speaker + 1}; the others' lines are tagged “other voice”. Change it under “Who's speaking” on the voice's page.`;
  }
  const suggested = (source.suggested ?? []).map((i) => source.lines?.[i]).filter(Boolean);
  $("#suggested-wrap").hidden = !suggested.length;
  $("#suggested").replaceChildren(...suggested.map(lineItem));
  $("#all-lines-head").hidden = !source.lines;
  $("#source-tools").hidden = !source.lines;
  if (!source.lines) $("#lines").replaceChildren();
  else if (withLines) renderLines();
}

const searchable = (text) => text.toLowerCase().replace(/[^a-z0-9' ]+/g, " ").replace(/\s+/g, " ").trim();

function renderLines() {
  if (!source?.lines) return;
  const query = searchable($("#line-search").value);
  const onlyGood = $("#only-good").checked;
  const lines = source.lines.filter(
    (l) => (!onlyGood || !l.flags.length) && (!query || searchable(l.text).includes(query)),
  );
  $("#lines").replaceChildren(...lines.map(lineItem));
  if (!lines.length) {
    $("#lines").append(
      el("li", { className: "none", textContent: query ? "No line has those words." : "Every line has a flag; untick the box to see them." }),
    );
  }
}

// A line already added from this recording, if any.
const practised = (line, rec = source) =>
  targets.find((t) => t.source === rec.id && t.line_start != null && Math.abs(t.line_start - line.start) < 0.05);

let linePlayer = null; // { audio, button }

function lineItem(line) {
  const play = el("button", { type: "button", className: "play", textContent: "▶" });
  play.setAttribute("aria-label", "Play this line");
  play.onclick = () => playUrl(`/api/sources/${source.id}/audio?start=${line.start}&end=${line.end}`, play, "line");
  const text = el("span", { className: "text", textContent: line.text });
  if (source.speakers?.length > 1 && line.speaker != null) {
    text.append(" ", el("i", { className: `tag speaker-tag s${line.speaker % 4}`, textContent: `speaker ${line.speaker + 1}` }));
  }
  for (const flag of line.flags) text.append(" ", el("i", { className: "tag", textContent: flag, title: FLAG_NOTES[flag] ?? "" }));
  const done = practised(line);
  if (done) {
    text.append(" ", el("i", { className: "tag done", textContent: done.best == null ? "added" : `best ${Math.round(done.best)}` }));
  }
  const use = el("button", { type: "button", className: "btn practise", textContent: done ? "Open" : "Add" });
  use.title = done ? "Practise this line" : "Add this line to the voice and practise it";
  use.onclick = () => (done ? openLine(done) : practiseLine(line));
  return el(
    "li",
    { className: "line-item" },
    play,
    el("span", { className: "when", textContent: clock(line.start) }),
    text,
    el("span", { className: "len", textContent: `${(line.end - line.start).toFixed(1)} s` }),
    use,
  );
}

// The line plus a little either side, in the trim view, so its ends can be moved.
async function practiseLine(line, rec = source) {
  const start = Math.max(0, line.start - CONTEXT_SECONDS);
  const end = Math.min(rec.duration, line.end + CONTEXT_SECONDS);
  setStatus("Opening the line…");
  try {
    const res = await fetch(`/api/sources/${rec.id}/audio?start=${start}&end=${end}`);
    if (!res.ok) throw new Error(await problem(res));
    await openTrim(await res.blob(), {
      start: line.start - start,
      end: line.end - start,
      name: line.text.slice(0, 80),
      back: current ? "practice" : "source",
      extra: { source: rec.id, line_start: line.start, voice: rec.voice_id },
    });
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function deleteSource() {
  if (!confirm(`Delete the recording "${source.name}"? Lines you've already added stay.`)) return;
  const owner = source.voice_id;
  try {
    await api(`/api/sources/${source.id}`, { method: "DELETE" });
    await refreshLists();
    if (owner && voiceOf(owner)) await openVoice(owner);
    else await goHome();
  } catch (err) {
    setStatus(err.message, true);
  }
}

// ---------- trimming a new clip to the line ----------

let trimming = null; // { samples, name, back, extra }
let trimPlayer = null;

// `start` and `end` preselect a part; `back` is the page Cancel returns to.
async function openTrim(blob, { start = 0, end = null, name = "", back = null, extra = {} } = {}) {
  let samples;
  try {
    samples = await decodeMono(blob, MAX_TRIM_SECONDS);
  } catch (err) {
    setStatus(err.message, true);
    return;
  }
  const seconds = samples.length / SR;
  stopLinePlayback();
  stopPlayback();
  trimming = { samples, name, back, extra };
  for (const id of ["#trim-start", "#trim-end"]) $(id).max = seconds.toFixed(2);
  $("#trim-start").value = start.toFixed(2);
  $("#trim-end").value = Math.min(end ?? seconds, start + MAX_SECONDS, seconds).toFixed(2);
  show("trim");
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
  const seconds = samples.length / SR;
  const per = Math.max(1, Math.floor(samples.length / w));
  // Drawn to the clip's own peak, so a soft-spoken or whispered voice is visible.
  let peak = 1e-3;
  for (let i = 0; i < samples.length; i++) peak = Math.max(peak, Math.abs(samples[i]));
  const gain = 0.95 / peak;
  for (let x = 0; x < w; x++) {
    let lo = 0;
    let hi = 0;
    for (let i = x * per; i < Math.min(samples.length, (x + 1) * per); i++) {
      lo = Math.min(lo, samples[i]);
      hi = Math.max(hi, samples[i]);
    }
    const t = (x / w) * seconds;
    g.fillStyle = css(t >= a && t <= b ? "--accent" : "--line");
    g.fillRect(x, h / 2 - (hi * gain * h) / 2, 1, Math.max(1, ((hi - lo) * gain * h) / 2));
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
  const node = ctx.createBufferSource();
  node.buffer = buffer;
  node.connect(ctx.destination);
  node.onended = stopTrimPlayback;
  node.start();
  trimPlayer = ctx;
}

function closeTrim() {
  stopTrimPlayback();
  const back = trimming?.back;
  trimming = null;
  if (back === "source" && source) show("source");
  else if (back === "practice" && current) show("practice");
  else if (voice) show("voice");
  else show(voices.length ? "home" : "empty");
}

async function useTrim() {
  const [a, b] = trimRange();
  const part = trimming.samples.slice(Math.round(a * SR), Math.round(b * SR));
  const { name, extra } = trimming;
  closeTrim();
  await createTarget(part, name, extra);
}

// ---------- a line: listen, record, improve ----------

const chosenFocus = () => (current && stored.get(`echo.focus.${current.id}`)) || "";
const currentFocus = () => chosenFocus() || shown?.coach?.focus || null;
const inCheck = () => Boolean(check && current && check.lines[check.index]?.id === current.id);

async function openLine(target, { keepCheck = false } = {}) {
  const running = keepCheck ? check : null;
  leave();
  check = running;
  current = target;
  shown = null;
  takes = [];
  profile = null;
  selectedWord = null;
  live = null;
  remember(`line:${target.id}`);
  renderSidebar();
  show("practice");
  const owner = voiceOf(target.voice_id);
  $("#crumb").hidden = Boolean(check);
  $("#crumb").textContent = owner ? `‹ ${owner.name}` : "‹ Home";
  $("#crumb").onclick = () => (owner ? openVoice(owner.id) : goHome());
  $("#target-audio").src = `/api/targets/${target.id}/audio`;
  $("#take-audio").removeAttribute("src");
  $("#pitch-warning").hidden = true;
  renderNote();
  renderLine();
  setShown(null);
  try {
    const [guide, list] = await Promise.all([
      api(`/api/targets/${target.id}/profile`),
      api(`/api/targets/${target.id}/takes`),
    ]);
    if (current?.id !== target.id) return;
    profile = guide;
    takes = list;
    renderNote();
    renderPitchWarning();
    // In a voice check the line starts fresh: earlier takes aren't shown.
    const last = takes.length && !inCheck() ? await api(`/api/targets/${target.id}/takes/${takes.at(-1).id}`) : null;
    if (current?.id !== target.id) return;
    setShown(last);
  } catch (err) {
    setStatus(err.message, true);
  }
}

function setShown(take) {
  shown = take;
  selectedWord = null;
  if (take) $("#take-audio").src = `/api/targets/${current.id}/takes/${take.id}/audio`;
  $("#play-take").disabled = $("#play-both").disabled = !take;
  renderCheckBanner();
  renderResult();
  renderHistory();
  renderCoach();
  renderWords();
  drawGuide();
}

function renderPitchWarning() {
  const usual = voiceOf(current?.voice_id);
  $("#pitch-warning").hidden = !profile?.pitch_suspect;
  if (profile?.pitch_suspect) {
    $("#pitch-warning").textContent =
      `This line's pitch reads ${Math.round(profile.median_f0 ?? 0)} Hz, far from the voice's usual pitch: probably creaky voice, or the pitch tracker off by an octave. ` +
      `Its Pitch and Melody aren't scored, and it's left out of the voice check. To train pitch, use another line${usual ? ` of ${usual.name}` : ""}.`;
  }
}

function renderLine() {
  $("#target-line").textContent = current?.words?.length ? `“${current.words.join(" ")}”` : "No words recognised in this clip.";
  $("#target-line").classList.toggle("empty", !current?.words?.length);
  $("#edit-line").textContent = current?.words?.length ? "Edit" : "Add the words";
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
    const meta = await send(`/api/targets/${current.id}`, "PATCH", { line: $("#line-input").value });
    current.words = meta.words;
    targets = targets.map((t) => (t.id === meta.id ? { ...t, words: meta.words } : t));
    renderLine();
    setStatus("Words saved; they're used from the next take.");
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function deleteTarget() {
  if (!confirm(`Delete this line and all its takes?`)) return;
  const owner = current.voice_id;
  try {
    await api(`/api/targets/${current.id}`, { method: "DELETE" });
    await refreshLists();
    if (owner && voiceOf(owner)) await openVoice(owner);
    else await goHome();
  } catch (err) {
    setStatus(err.message, true);
  }
}

// What has been done on a line before its first take: listened, found the note.
function steps() {
  try {
    return JSON.parse(stored.get(`echo.steps.${current?.id}`) || "{}");
  } catch {
    return {};
  }
}

function markStep(key) {
  if (!current) return;
  const done = steps();
  done[key] = (done[key] || 0) + 1;
  stored.set(`echo.steps.${current.id}`, JSON.stringify(done));
  if (!takes.length) renderCoach();
}

// ---------- listening: the target, your take, one word of each ----------

let playing = null; // { kind, cancelled }

function updatePlayButtons() {
  for (const [id, kind] of [["#play-target", "target"], ["#play-take", "take"], ["#play-both", "both"]]) {
    const on = playing?.kind === kind;
    $(id).classList.toggle("on", on);
    $(id).querySelector(".icon").textContent = on ? "■" : "▶";
  }
}

function stopPlayback() {
  if (playing) playing.cancelled = true;
  playing = null;
  for (const id of ["#target-audio", "#take-audio"]) $(id).pause();
  playhead = null;
  updatePlayButtons();
  drawGuide();
}

function loaded(audio) {
  if (audio.readyState >= 1) return Promise.resolve();
  return new Promise((resolve, reject) => {
    audio.addEventListener("loadedmetadata", resolve, { once: true });
    audio.addEventListener("error", () => reject(new Error("Couldn't load that audio.")), { once: true });
  });
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Play `audio` from `start` to `end` (seconds; null for the end), moving the
// guide's playhead through `toGuide`.
async function playSpan(token, audio, start, end, toGuide) {
  await loaded(audio);
  if (token.cancelled) return;
  audio.playbackRate = speed;
  audio.preservesPitch = true; // slowed down, not lowered
  audio.currentTime = start;
  await audio.play();
  await new Promise((resolve) => {
    const tick = () => {
      // Whatever cancelled this has paused the audio already, and may be
      // playing it again: leave it alone.
      if (token.cancelled) return resolve();
      if (audio.paused || audio.ended || (end != null && audio.currentTime >= end)) {
        audio.pause();
        resolve();
        return;
      }
      playhead = toGuide(audio.currentTime);
      drawGuide();
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
}

// A take's time on the target's timeline, through the word alignment.
function takeToTarget(t) {
  const words = shown?.words ?? [];
  const takeLength = $("#take-audio").duration;
  const points = [[0, 0]];
  for (const w of words) points.push([w.take_start, w.start], [w.take_end, w.end]);
  if (Number.isFinite(takeLength) && profile) points.push([takeLength, profile.duration]);
  points.sort((a, b) => a[0] - b[0]);
  for (let i = 1; i < points.length; i++) {
    const [x0, y0] = points[i - 1];
    const [x1, y1] = points[i];
    if (t <= x1) return x1 > x0 ? y0 + ((t - x0) / (x1 - x0)) * (y1 - y0) : y1;
  }
  return points.at(-1)[1];
}

async function play(kind) {
  const again = playing?.kind === kind;
  stopPlayback();
  if (again) return; // a second click stops it
  const token = { kind, cancelled: false };
  playing = token;
  updatePlayButtons();
  if (kind !== "take") markStep("listened");
  try {
    if (kind !== "take") await playSpan(token, $("#target-audio"), 0, null, (t) => t);
    if (kind === "both" && !token.cancelled) await wait(450);
    if (kind !== "target" && !token.cancelled) await playSpan(token, $("#take-audio"), 0, null, takeToTarget);
  } catch (err) {
    if (!token.cancelled) setStatus(err.message, true); // a play() cut short by a newer one isn't an error
  }
  if (playing === token) stopPlayback();
}

async function playWord(index) {
  const word = shownWords()[index];
  stopPlayback();
  const token = { kind: "word", cancelled: false };
  playing = token;
  try {
    await playSpan(token, $("#target-audio"), word.start, word.end, (t) => t);
    if (shown && word.take_start != null && !token.cancelled) {
      await wait(300);
      await playSpan(token, $("#take-audio"), word.take_start, word.take_end, takeToTarget);
    }
  } catch (err) {
    if (!token.cancelled) setStatus(err.message, true);
  }
  if (playing === token) stopPlayback();
}

// A soft tone on the target's typical pitch, to hum along to.
async function playNote() {
  if (!profile && current) {
    try {
      profile = await api(`/api/targets/${current.id}/profile`); // clicked before the guide arrived
      renderNote();
    } catch {}
  }
  const hz = profile?.median_f0;
  if (!hz) return;
  const ctx = new AudioContext();
  const tone = ctx.createOscillator();
  const level = ctx.createGain();
  tone.type = "triangle";
  tone.frequency.value = hz;
  const now = ctx.currentTime;
  level.gain.setValueAtTime(0, now);
  level.gain.linearRampToValueAtTime(0.25, now + 0.08);
  level.gain.setValueAtTime(0.25, now + 1.4);
  level.gain.linearRampToValueAtTime(0, now + 1.8);
  tone.connect(level).connect(ctx.destination);
  tone.start(now);
  tone.stop(now + 1.85);
  tone.onended = () => ctx.close();
  markStep("note");
}

function renderNote() {
  const hz = profile?.median_f0;
  $("#play-note").disabled = !hz;
  $("#note-name").textContent = hz ? noteName(hz) : "Note";
  $("#play-note").title = hz ? `${noteName(hz)}, ${Math.round(hz)} Hz: the target's typical pitch. Play it and hum it.` : "No clear pitch in this clip";
}

// Speak along with the target, with the live guide on: nothing is saved.
async function speakAlong() {
  if (session) return;
  await startRecording("warmup");
  if (!session) return;
  session.along = true;
  await play("target");
  await wait(300);
  if (session?.along) await stopRecording();
}

function playWorstWord() {
  const words = shownWords();
  const name = shown?.coach?.words?.[0];
  let index = name ? words.findIndex((w) => w.text === name) : -1;
  if (index < 0) {
    const key = currentFocus();
    let lowest = Infinity;
    words.forEach((w, i) => {
      const s = wordScore(w, WORD_PARTS.includes(key) ? key : null);
      if (s != null && s < lowest) [lowest, index] = [s, i];
    });
  }
  if (index < 0) return;
  selectedWord = index;
  renderWords();
  drawGuide();
  playWord(index);
}

function setSpeed(rate) {
  speed = rate;
  for (const b of document.querySelectorAll(".speed button")) b.setAttribute("aria-pressed", String(Number(b.dataset.rate) === rate));
  for (const id of ["#target-audio", "#take-audio"]) $(id).playbackRate = rate;
}

// ---------- the guide: the target's pitch, yours over it, live and after ----------

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function liveEnd() {
  if (!live?.points.length || live.onset == null || !profile) return 0;
  return profile.onset + (live.points.at(-1).t - live.onset);
}

function drawGuide() {
  const canvas = $("#guide");
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (!w || !current) return;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const g = canvas.getContext("2d");
  g.scale(dpr, dpr);
  g.font = "11px system-ui, sans-serif";
  if (!profile) {
    g.fillStyle = css("--muted");
    g.fillText("Loading the line…", 12, 22);
    return;
  }

  const pad = { left: 46, right: 10, top: 10, bottom: 26 };
  const duration = Math.max(profile.duration, liveEnd() + 0.2);
  const target = profile.contour.f0;
  const take = shown?.contour?.take ?? [];
  const livePoints = live?.onset != null ? live.points.filter((p) => p.f0 && p.t >= live.onset) : [];
  const values = [...target, ...take, ...livePoints.map((p) => p.f0)].filter((v) => v);
  let lo = values.length ? Math.min(...values) / 1.1 : 80;
  let hi = values.length ? Math.max(...values) * 1.1 : 300;
  if (hi / lo < 2) {
    const mid = Math.sqrt(hi * lo);
    lo = mid / Math.SQRT2;
    hi = mid * Math.SQRT2;
  }
  const x = (t) => pad.left + (t / duration) * (w - pad.left - pad.right);
  const y = (hz) => pad.top + (1 - Math.log(hz / lo) / Math.log(hi / lo)) * (h - pad.top - pad.bottom);

  // Pitch is heard in ratios, so the axis is logarithmic.
  g.lineWidth = 1;
  for (const hz of [60, 80, 100, 125, 150, 200, 250, 300, 400, 500, 600, 800]) {
    if (hz < lo || hz > hi) continue;
    g.strokeStyle = css("--line");
    g.beginPath();
    g.moveTo(pad.left, y(hz));
    g.lineTo(w - pad.right, y(hz));
    g.stroke();
    g.fillStyle = css("--muted");
    g.fillText(`${hz} Hz`, 2, y(hz) + 4);
  }
  if (profile.low && profile.high) {
    g.fillStyle = css("--band");
    g.fillRect(pad.left, y(profile.high), w - pad.left - pad.right, y(profile.low) - y(profile.high));
  }
  if (profile.median_f0) {
    // The target's typical pitch, the note to find before a take.
    g.strokeStyle = css("--target");
    g.globalAlpha = 0.6;
    g.setLineDash([4, 4]);
    g.beginPath();
    g.moveTo(pad.left, y(profile.median_f0));
    g.lineTo(w - pad.right, y(profile.median_f0));
    g.stroke();
    g.setLineDash([]);
    g.globalAlpha = 1;
    g.fillStyle = css("--target");
    const label = noteName(profile.median_f0);
    g.fillText(label, w - pad.right - g.measureText(label).width - 2, y(profile.median_f0) - 4);
  }

  // The words along the bottom; the selected one shaded.
  const words = shownWords();
  let labelled = -Infinity; // where the last label ended, so the next doesn't sit on it
  words.forEach((word, i) => {
    const a = x(word.start);
    const b = x(word.end);
    if (i === selectedWord) {
      g.fillStyle = css("--select");
      g.fillRect(a, pad.top, b - a, h - pad.top - pad.bottom);
    }
    g.strokeStyle = css("--line");
    g.beginPath();
    g.moveTo(a, h - pad.bottom);
    g.lineTo(a, h - pad.bottom + 5);
    g.stroke();
    let label = word.text.replace(/[.,!?;:"“”]+/g, "");
    const room = (words[i + 1] ? x(words[i + 1].start) : w - pad.right) - a - 6;
    while (label.length > 3 && g.measureText(label).width > room) label = `${label.slice(0, -2)}…`;
    if (a + 2 >= labelled && g.measureText(label).width <= room) {
      g.fillStyle = i === selectedWord ? css("--text") : css("--muted");
      g.fillText(label, a + 2, h - 8);
      labelled = a + 2 + g.measureText(label).width + 4;
    }
  });

  const line = (times, values, color, width) => {
    g.strokeStyle = color;
    g.lineWidth = width;
    g.lineJoin = "round";
    g.lineCap = "round";
    g.beginPath();
    let pen = false;
    values.forEach((hz, i) => {
      if (hz == null) return (pen = false);
      if (pen) g.lineTo(x(times[i]), y(hz));
      else g.moveTo(x(times[i]), y(hz));
      pen = true;
    });
    g.stroke();
  };
  line(profile.contour.t, target, css("--target"), 3);
  if (take.length && !livePoints.length) line(shown.contour.t, take, css("--accent"), 2);

  // While recording, your pitch from when you start speaking, lined up with
  // where the target starts: sing along to the line.
  g.fillStyle = css("--accent");
  for (const p of livePoints) g.fillRect(x(profile.onset + p.t - live.onset) - 1.5, y(p.f0) - 1.5, 3, 3);

  if (playhead != null) {
    g.strokeStyle = css("--text");
    g.globalAlpha = 0.5;
    g.lineWidth = 1.5;
    g.beginPath();
    g.moveTo(x(playhead), pad.top);
    g.lineTo(x(playhead), h - pad.bottom);
    g.stroke();
    g.globalAlpha = 1;
  }
}

// ---------- the words: where in the line you're furthest off ----------

// A take's rows on the line's units. A take from before words said together
// were grouped has a row per word; those are combined, unit by unit.
let unitCache = { key: "", rows: [] };

function takeUnits(take) {
  const units = profile?.words ?? [];
  const rows = take?.words ?? [];
  if (!rows.length || !units.length) return rows;
  if (rows.length === units.length && rows.every((r, i) => Math.abs(r.start - units[i].start) < 0.02)) return rows;
  const key = `${take.id}:${units.length}:${units[0].start}`;
  if (unitCache.key === key) return unitCache.rows;
  const mean = (values) => {
    const v = values.filter((x) => x != null);
    return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
  };
  const combined = units.map((u) => {
    const inside = rows.filter((r) => (r.start + r.end) / 2 >= u.start - 0.02 && (r.start + r.end) / 2 < u.end + 0.02);
    if (!inside.length) return { text: u.text, start: u.start, end: u.end };
    const scores = Object.fromEntries(WORD_PARTS.map((k) => [k, mean(inside.map((r) => r.scores?.[k]))]));
    const scored = Object.entries(scores).filter(([, s]) => s != null);
    return {
      text: u.text,
      start: u.start,
      end: u.end,
      take_start: Math.min(...inside.map((r) => r.take_start)),
      take_end: Math.max(...inside.map((r) => r.take_end)),
      pitch: mean(inside.map((r) => r.pitch)),
      melody: mean(inside.map((r) => r.melody)),
      resonance: mean(inside.map((r) => r.resonance)),
      articulation: mean(inside.map((r) => r.articulation)),
      scores,
      worst: scored.length ? scored.reduce((a, b) => (b[1] < a[1] ? b : a))[0] : null,
    };
  });
  unitCache = { key, rows: combined };
  return combined;
}

const shownWords = () => (shown?.words?.length ? takeUnits(shown) : profile?.words ?? []);

function wordScore(word, key) {
  if (!word.scores) return null;
  if (key && word.scores[key] != null) return word.scores[key];
  return word.worst ? word.scores[word.worst] : null;
}

function renderWords() {
  const words = inCheck() ? [] : shownWords();
  $("#words").hidden = !words.length;
  const focus = currentFocus();
  const key = WORD_PARTS.includes(focus) ? focus : null;
  const scored = words.some((w) => w.scores);
  $("#words-caption").textContent = !words.length
    ? ""
    : scored
      ? `Words coloured by ${key ? partLabel(key).toLowerCase() : "their weakest part"}. Click one to hear the target, then you.`
      : "Click a word to hear it.";
  $("#words").replaceChildren(
    ...words.map((word, i) => {
      const score = wordScore(word, key);
      const grade = score == null ? "" : score >= 75 ? "good" : score >= 50 ? "mid" : "poor";
      const button = el("button", { type: "button", className: `word ${grade}`, textContent: word.text });
      if (score != null) button.title = `${partLabel(key ?? word.worst)} ${Math.round(score)}`;
      button.setAttribute("aria-pressed", String(i === selectedWord));
      button.onclick = () => {
        selectedWord = i;
        renderWords();
        drawGuide();
        playWord(i);
      };
      return button;
    }),
  );
  const word = words[selectedWord];
  $("#word-note").hidden = !word?.scores;
  if (word?.scores) $("#word-note").textContent = wordNote(word);
}

function wordNote(word) {
  const notes = [];
  if (word.pitch != null && Math.abs(word.pitch) >= 0.5) {
    notes.push(`${Math.abs(word.pitch).toFixed(1)} semitones ${word.pitch > 0 ? "higher" : "lower"} than the target`);
  }
  if (word.melody != null && Math.abs(word.melody) >= 0.75) {
    notes.push(`you lift this word ${word.melody > 0 ? "more" : "less"} than the target does, compared with the rest of the line`);
  }
  if (word.resonance != null && Math.abs(word.resonance) >= 0.4) {
    const percent = Math.round(Math.abs(2 ** (word.resonance / 12) - 1) * 100);
    notes.push(`${word.resonance > 0 ? "brighter" : "darker"} (formants ${percent}% ${word.resonance > 0 ? "higher" : "lower"})`);
  }
  if (word.scores.articulation != null && word.scores.articulation < 60) notes.push("the sounds are shaped differently");
  const text = word.text.replace(/[.,!?;:"“”]+/g, "");
  return notes.length ? `“${text}”: ${notes.join("; ")}.` : `“${text}”: close.`;
}

// ---------- the coach: one thing at a time ----------

function renderCoach() {
  $("#coach").hidden = inCheck();
  if (inCheck()) return;
  const chosen = chosenFocus();
  $("#focus-chips").replaceChildren(
    ...[["", "Auto"], ...PARTS].map(([key, label]) => {
      const chip = el("button", { type: "button", className: "chip", textContent: label });
      chip.setAttribute("aria-pressed", String(key === chosen));
      chip.title = key ? `Work on ${label.toLowerCase()} until you switch back to Auto` : "Let the coach pick after each take";
      chip.onclick = () => {
        stored.set(`echo.focus.${current.id}`, key);
        renderCoach();
        renderWords();
        renderResult();
      };
      return chip;
    }),
  );

  const coach = shown?.coach;
  const focus = currentFocus();
  const progress = coach?.progress;
  $("#coach-progress").hidden = !progress;
  if (progress) {
    const change = progress.after - progress.before;
    const verdict = change >= 3 ? "closer" : change <= -3 ? "further off" : "about the same";
    $("#coach-progress").className = `coach-progress ${change >= 3 ? "up" : change <= -3 ? "down" : ""}`;
    $("#coach-progress").textContent =
      `Last take you worked on ${progress.label.toLowerCase()}: ${Math.round(progress.before)} → ${Math.round(progress.after)}, ${verdict}.`;
  }

  let heading;
  let message;
  if (focus) {
    const part = shown?.components.find((c) => c.key === focus);
    const name = partLabel(focus).toLowerCase();
    heading = chosen ? `Work on ${name} (your pick)` : coach?.why === "keep" ? `Keep working on ${name}` : `Work on ${name}`;
    message =
      (focus === coach?.focus && coach.message) ||
      part?.hint ||
      (part?.score != null ? `${part.label} is at ${Math.round(part.score)}: ${part.detail}.` : "Listen for it in the target, then record.");
  } else if (coach?.why === "all good") {
    heading = "Every part is close";
    message = coach.message;
  } else {
    heading = "Your first take on this line";
    message = "Three steps. Each one ticks off when you've done it.";
  }
  $("#coach-focus").textContent = heading;
  $("#coach-message").textContent = message;
  const words = focus && focus === coach?.focus ? coach.words : [];
  $("#coach-words").hidden = !words?.length;
  if (words?.length) $("#coach-words").textContent = `Furthest off: ${words.map((w) => `“${w.replace(/[.,!?;:"“”]+/g, "")}”`).join(", ")}. Click them above to compare.`;
  const stuck = (coach?.stuck ?? []).filter((k) => k !== focus);
  $("#coach-stuck").hidden = !stuck.length;
  if (stuck.length) {
    const names = stuck.map((k) => partLabel(k).toLowerCase()).join(" and ");
    $("#coach-stuck").replaceChildren(
      `${names[0].toUpperCase()}${names.slice(1)} has stayed near the bottom for your last few takes without moving, so the coach is working on the parts that do. `,
      ...stuck.filter((k) => TRAIN[k]).map((k) => {
        const b = el("button", { type: "button", className: "link", textContent: `Train ${partLabel(k).toLowerCase()} word by word` });
        b.onclick = () => openTrainer(k);
        return b;
      }),
    );
  }
  renderChecklist();
  renderDrill(focus);
  renderPlateau();
}

const ACTIONS = {
  listen: ["▶ Play the line", () => play("target")],
  note: ["♪ Play the note", () => playNote()],
  warmup: ["Warm up", () => toggleRecording("warmup")],
  record: ["● Record a take", () => toggleRecording("take")],
  slow: ["▶ Play at ½ speed", () => (setSpeed(0.5), play("target"))],
  along: ["Speak along", () => speakAlong()],
  worst: ["▶ Furthest-off word", () => playWorstWord()],
  train: ["Train it word by word", () => openTrainer(currentFocus())],
  both: ["Pitch and resonance together", () => openTrainer("both")],
};

function actionButtons(keys) {
  return keys.map((key) => {
    const [label, run] = ACTIONS[key];
    const button = el("button", { type: "button", className: "btn small", textContent: label });
    button.dataset.action = key;
    button.onclick = run;
    return button;
  });
}

// Before the first take: listen, find the note, record.
function renderChecklist() {
  const first = Boolean(current) && !takes.length;
  $("#checklist").hidden = !first;
  if (!first) return;
  const done = steps();
  const items = [
    [(done.listened ?? 0) >= 2, "Listen to the line two or three times. ¾ and ½ speed slow it down without making it lower.", ["listen"]],
    [Boolean(done.note) || Boolean(profile && !profile.median_f0), `Find the note${profile?.median_f0 ? ` (${noteName(profile.median_f0)})` : ""}: play it, then Warm up and hum until your dots sit on the dashed line.`, ["note", "warmup"]],
    [false, "Record your first take, following the blue pitch line as you speak.", ["record"]],
  ];
  const now = items.findIndex(([ok]) => !ok);
  $("#checklist").replaceChildren(
    ...items.map(([ok, text, keys], i) =>
      el("li", { className: ok ? "done" : i === now ? "now" : "" }, el("span", { className: "check-text", textContent: text }), el("span", { className: "drill-actions" }, ...actionButtons(keys))),
    ),
  );
}

const DRILLS = {
  pitch: {
    steps: [
      "Play the note and hum it, then Warm up until your dots sit on the dashed line.",
      "Say the line starting on that note, following the blue line.",
      "Record a take.",
    ],
    actions: ["train", "both", "note", "warmup"],
  },
  melody: {
    steps: [
      "Play the line at ½ speed and hum its tune without the words.",
      "Speak along with it (headphones on) and keep your dots on the blue line.",
      "Record a take on your own.",
    ],
    actions: ["slow", "along"],
  },
  resonance: {
    steps: [
      "Play the word furthest off and copy its vowel a few times on its own.",
      "Say that word, then the line, keeping the same mouth shape.",
      "Record a take.",
    ],
    actions: ["train", "both", "worst", "warmup"],
  },
  articulation: {
    steps: [
      "Click each red word above and copy it, one at a time.",
      "Say the whole line slowly, then at its own speed.",
      "Record a take.",
    ],
    actions: ["train", "worst", "slow"],
  },
  rhythm: {
    steps: [
      "Speak along with the line a few times (headphones on): pause where it pauses, stretch the words it stretches.",
      "Then record a take without it.",
    ],
    actions: ["along"],
  },
};

function renderDrill(focus) {
  const drill = shown && DRILLS[focus];
  $("#drill").hidden = !drill;
  if (!drill) return;
  $("#drill-steps").replaceChildren(...drill.steps.map((s) => el("li", { textContent: s })));
  $("#drill-actions").replaceChildren(...actionButtons(drill.actions));
}

// When a line has stopped improving, or is done: suggest another.
function renderPlateau() {
  const coach = shown?.coach;
  const latest = shown && takes.at(-1)?.id === shown.id;
  const visible = Boolean(latest && (coach?.plateau || coach?.mastered));
  $("#plateau").hidden = !visible;
  if (!visible) return;
  $("#plateau").classList.toggle("mastered", Boolean(coach.mastered));
  $("#plateau-text").textContent = coach.mastered
    ? "This line now scores near the real person. Try it from memory without listening first, or move on: a voice check shows whether it holds on other lines."
    : `You've been around ${Math.round(shown.overall)} for your last few takes. A fresh line usually helps more than another take of this one; come back to this one later.`;
  const buttons = [];
  if (current.source && sources.some((s) => s.id === current.source)) {
    const next = el("button", { type: "button", className: "btn small", textContent: "Try another line" });
    next.onclick = () => nextLine(current.source);
    buttons.push(next);
  }
  const owner = voiceOf(current.voice_id);
  if (owner) {
    const back = el("button", { type: "button", className: "btn small", textContent: `Back to ${owner.name}` });
    back.onclick = () => openVoice(owner.id);
    buttons.push(back);
  }
  $("#plateau-actions").replaceChildren(...buttons);
}

// The best suggested line of a recording that hasn't been added yet.
async function nextLine(sourceId) {
  try {
    const rec = await api(`/api/sources/${sourceId}`);
    const fresh = (line) => !practised(line, rec);
    const pick = rec.suggested.map((i) => rec.lines[i]).find(fresh) ?? rec.lines.find((l) => !l.flags.length && fresh(l));
    if (!pick) return setStatus("You've added every clear line of this recording.");
    await practiseLine(pick, rec);
  } catch (err) {
    setStatus(err.message, true);
  }
}

// ---------- training one part, word by word ----------

const TRAIN = {
  resonance: {
    title: "Resonance",
    what: "Resonance is where your voice rings: the shape of your mouth and throat, heard as brighter or darker. It's measured on the vowels, so each word is compared with the same word.",
    tips: {
      up: [
        "Spread the lips a little, as in a slight smile.",
        "Bring the tongue forward and up, as for “ee”.",
        "Make the space in your mouth smaller: don't drop the jaw as far.",
        "Feel the sound at the front of the face, just behind the teeth.",
      ],
      down: [
        "Round the lips a little, as for “oo”.",
        "Let the tongue sit lower and further back.",
        "Drop the jaw and open up the space at the back of the mouth.",
        "Let the larynx sit low and relaxed, as at the start of a yawn.",
      ],
    },
  },
  pitch: {
    title: "Pitch",
    what: "Pitch is how high or low your voice sits. Each word is compared with the target saying the same word.",
    tips: {
      up: ["Play the note and hum it before you speak.", "Start the word on the note, not below it.", "Lift from the chest into the head: lighter, not louder."],
      down: ["Play the note and hum it before you speak.", "Let the word settle down onto the note.", "Relax the throat and slow down a little; tension pushes pitch up."],
    },
  },
  articulation: {
    title: "Articulation",
    what: "Articulation is how the sounds themselves are shaped: each vowel and consonant. It's the part most tied to the words, so it's trained word by word.",
    tips: {
      any: [
        "Play the word at ½ speed and copy its vowel exactly.",
        "Exaggerate the consonants at first, then ease off.",
        "Copy where the word is stressed and how it ends.",
      ],
    },
  },
};
TRAIN.both = {
  title: "Pitch and resonance",
  what: "Pitch (how high or low) and resonance (how bright or dark) tend to move together: going lower usually drops the larynx and opens the throat, which darkens the sound, and going higher brightens it. Here each attempt is placed on both at once, so you can move one and hold the other. A word counts when both are close.",
};
const CLOSE_PITCH = 1.0; // semitones: near enough not to need moving
const CLOSE_RESONANCE = 3; // percent

// What to do about pitch and resonance together, from where an attempt (or
// the last take) landed: lower or higher, darker or brighter, or hold.
function bothTips(pitch, percent) {
  const p = pitch == null || Math.abs(pitch) < CLOSE_PITCH ? "hold" : pitch > 0 ? "lower" : "higher";
  const r = percent == null || Math.abs(percent) < CLOSE_RESONANCE ? "hold" : percent > 0 ? "darker" : "brighter";
  const lowerBright = [
    "Lower the pitch but keep the sound forward: tongue high and forward, a slight smile, as if about to say “ee”.",
    "Don't drop the jaw or let the larynx sink to get lower; that's what darkens it.",
    "Hum the note on a bright “ng” first, then open it into the word.",
  ];
  const higherDark = [
    "Raise the pitch without pulling the sound forward: lips slightly rounded, throat open.",
    "Lift lightly, as when asking a question, without smiling into it.",
    "Hum the note on “oo” first, then open it into the word.",
  ];
  const table = {
    "lower hold": lowerBright,
    "lower brighter": lowerBright,
    "lower darker": ["Here the two move together: let the voice settle lower and the throat relax.", "Drop the jaw a little and round the lips for the darker sound."],
    "higher hold": higherDark,
    "higher darker": higherDark,
    "higher brighter": ["Here the two move together: lift and brighten at once, with a slight smile, the tongue forward and a lighter voice."],
    "hold darker": TRAIN.resonance.tips.down,
    "hold brighter": TRAIN.resonance.tips.up,
    "hold hold": ["Both are close: say it the same way three times in a row."],
  };
  return { key: `${p} ${r}`, tips: table[`${p} ${r}`], pitch: p, resonance: r };
}

const PASS = 80; // a word counts as close at this score
const IN_A_ROW = 3;

let training = null; // { part, order: [word indices], at, attempts: {index: [result]} }

// The parts a mode trains: "both" is pitch and resonance at once.
const trainedParts = (part) => (part === "both" ? ["pitch", "resonance"] : [part]);
// An attempt's score for the mode: the lower of its parts' scores.
function modeScore(r, part) {
  const scores = trainedParts(part).map((k) => r.scores?.[k]);
  return scores.some((s) => s == null) ? null : Math.min(...scores);
}
const passedWord = (tries, part) => tries.length >= IN_A_ROW && tries.slice(-IN_A_ROW).every((r) => (modeScore(r, part) ?? 0) >= PASS);
let drillRec = null; // { recorder, timer, free, index }: the trainer has the microphone open

async function openTrainer(part) {
  if (!current) return;
  if (!TRAIN[part]) {
    // Melody and rhythm live across the whole line: work on them there.
    stored.set(`echo.focus.${current.id}`, part);
    show("practice");
    setShown(shown);
    $("#coach").scrollIntoView({ behavior: "smooth", block: "center" });
    setStatus(`Working on ${partLabel(part).toLowerCase()}: it runs across the whole line, so use the drill below: slow it down and speak along.`);
    return;
  }
  const words = profile?.words ?? [];
  const scored = shown ? takeUnits(shown) : [];
  const score = (i) => {
    const w = scored.find((x) => x.start === words[i].start);
    return w ? modeScore(w, part) : null;
  };
  // Furthest off first; words with no measure last.
  const order = words.map((_, i) => i).sort((a, b) => (score(a) ?? 101) - (score(b) ?? 101));
  training = { part, order, at: 0, attempts: training?.part && training.attempts ? training.attempts : {} };
  stopPlayback();
  show("train");
  renderTrainer();
  if (!Object.keys(training.attempts).length) {
    // Attempts from earlier sessions: carry on where you left off.
    try {
      const saved = await api(`/api/targets/${current.id}/drills`);
      if (!training) return;
      for (const r of saved) {
        // By position, not number: the line's units may have been regrouped since.
        const unit = (profile?.words ?? []).findIndex((w) => r.start >= w.start - 0.02 && r.start < w.end);
        if (unit >= 0) (training.attempts[unit] ??= []).push(r);
      }
      // Start at the first word not done yet, unless a word was picked meanwhile.
      const next = training.order.findIndex((i) => !passedWord(training.attempts[i] ?? [], training.part));
      if (!training.picked) training.at = Math.max(0, next);
      renderTrainer();
    } catch {}
  }
}

function switchTrainMode(part) {
  if (!training || training.part === part) return;
  const word = training.order[training.at];
  const attempts = training.attempts;
  openTrainer(part);
  training.attempts = attempts; // the same attempts, read for another part
  training.at = Math.max(0, training.order.indexOf(word));
  training.picked = true; // stay on this word when the saved attempts arrive
  renderTrainer();
}

function trainDirection(part) {
  // From the take on screen: which way to move, overall.
  const m = shown?.measures;
  const value = part === "resonance" ? m?.resonance : part === "pitch" ? m?.pitch : null;
  if (value == null) return null;
  return value > 0 ? "down" : "up";
}

function renderTrainer() {
  const { part, order, at } = training;
  const info = TRAIN[part];
  if (drillRec && drillRec.index !== order[at]) stopDrill(); // the word changed under an open microphone
  const scoring = Boolean(drillRec && !drillRec.free);
  const trying = Boolean(drillRec?.free);
  $("#train-say").classList.toggle("live", scoring);
  $("#train-say-label").textContent = scoring ? "Stop" : "Say it";
  $("#train-say").disabled = trying;
  $("#train-try").classList.toggle("on", trying);
  $("#train-try-label").textContent = trying ? "Stop" : "Try it live";
  $("#train-try").disabled = scoring;
  for (const b of document.querySelectorAll("#train-modes button")) b.setAttribute("aria-pressed", String(b.dataset.part === part));
  $("#train-crumb").textContent = `‹ “${lineText(current)}”`;
  $("#train-crumb").onclick = leaveTrainer;
  $("#train-title").textContent = `${info.title} on “${lineText(current)}”`;
  $("#train-what").textContent = info.what;
  const components = trainedParts(part).map((k) => shown?.components.find((c) => c.key === k)).filter((c) => c?.score != null);
  $("#train-now").textContent = components.length
    ? `On your last take: ${components.map((c) => `${c.label.toLowerCase()} ${Math.round(c.score)}, ${c.detail}`).join("; ")}.`
    : "";
  let tips;
  if (part === "both") {
    // From the latest attempt at this word if there is one, else the last take.
    const latest = (training.attempts[order[at]] ?? []).at(-1);
    const advice = latest ? bothTips(latest.pitch, latest.percent) : bothTips(shown?.measures?.pitch, shown?.measures?.resonance == null ? null : (2 ** (shown.measures.resonance / 12) - 1) * 100);
    tips = advice.tips;
  } else {
    const way = trainDirection(part);
    tips = info.tips.any ?? (way ? info.tips[way] : [...info.tips.up.slice(0, 2), ...info.tips.down.slice(0, 2)]);
  }
  $("#train-tips").replaceChildren(...tips.map((tip) => el("li", { textContent: tip })));

  const words = profile?.words ?? [];
  $("#train-card").hidden = !words.length;
  if (!words.length) {
    $("#train-line-text").textContent = "No words were recognised in this line, so it can't be trained word by word.";
    return;
  }
  const index = order[at];
  $("#train-words").replaceChildren(
    ...order.map((i, k) => {
      const tries = training.attempts[i] ?? [];
      const passed = passedWord(tries, part);
      const button = el("button", { type: "button", className: `word ${passed ? "good" : tries.length ? "mid" : ""}`, textContent: words[i].text });
      button.setAttribute("aria-pressed", String(k === at));
      button.onclick = () => {
        training.at = k;
        training.picked = true;
        renderTrainer();
      };
      return button;
    }),
  );
  $("#train-word").textContent = `“${words[index].text.replace(/[.,!?;:"“”]+/g, "")}”`;
  const tries = training.attempts[index] ?? [];
  const last = tries.at(-1);
  const wordHz = wordPitch(index);
  $("#train-note").disabled = !wordHz;
  $("#train-note-name").textContent = wordHz ? `Its note, ${noteName(wordHz)}` : "Its note";
  renderDrillMeter(part, last);
  renderDrillMap(part, tries);
  $("#train-mine").disabled = !last?.id;
  $("#train-result").className = "train-result";
  if (!last) $("#train-result").textContent = "Play the target, then press Say it and say just that.";
  else $("#train-result").replaceChildren(...drillVerdict(part, last));
  $("#train-attempts").replaceChildren(
    ...tries.map((r, k) => {
      const s = modeScore(r, part);
      return el("li", { className: s == null ? "" : s >= PASS ? "good" : s >= 50 ? "mid" : "poor", textContent: s == null ? "–" : Math.round(s), title: `Attempt ${k + 1}` });
    }),
  );
  const passed = passedWord(tries, part);
  $("#train-next").hidden = !(passed || tries.length >= 2) || at + 1 >= order.length;
  $("#train-next").classList.toggle("primary", passed);
  const done = order.filter((i) => passedWord(training.attempts[i] ?? [], part)).length;
  $("#train-line-text").textContent = done
    ? `${done} of ${order.length} words close. When most are, put them back together in the whole line.`
    : "When the words are close, put them back together in the whole line.";
}

// The pitch the target uses on one word: the median of its contour over the word.
function wordPitch(index) {
  const word = profile?.words?.[index];
  if (!word) return null;
  const f0 = profile.contour.t.map((t, i) => (t >= word.start && t < word.end ? profile.contour.f0[i] : null)).filter((v) => v);
  if (f0.length < 3) return null;
  return f0.sort((a, b) => a - b)[Math.floor(f0.length / 2)];
}

function playWordNote() {
  const hz = wordPitch(training.order[training.at]);
  if (!hz) return;
  const ctx = new AudioContext();
  const tone = ctx.createOscillator();
  const level = ctx.createGain();
  tone.type = "triangle";
  tone.frequency.value = hz;
  const now = ctx.currentTime;
  level.gain.setValueAtTime(0, now);
  level.gain.linearRampToValueAtTime(0.25, now + 0.08);
  level.gain.setValueAtTime(0.25, now + 1.2);
  level.gain.linearRampToValueAtTime(0, now + 1.6);
  tone.connect(level).connect(ctx.destination);
  tone.start(now);
  tone.stop(now + 1.65);
  tone.onended = () => ctx.close();
}

// Every attempt at the word on pitch (across) and resonance (up), the target in
// the middle: to go lower but stay as bright, move left without dropping.
function renderDrillMap(part, tries) {
  const show_ = part === "pitch" || part === "resonance" || part === "both";
  $("#train-map").hidden = !show_;
  mapLive = null;
  if (!show_) return;
  const ns = "http://www.w3.org/2000/svg";
  const make = (tag, attrs) => {
    const node = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  };
  const W = 360;
  const H = 220;
  const P = 6; // semitones each side
  const R = 25; // percent each side
  const x = (st) => W / 2 + (Math.max(-P, Math.min(P, st)) / P) * (W / 2 - 14);
  const y = (pct) => H / 2 - (Math.max(-R, Math.min(R, pct)) / R) * (H / 2 - 14);
  const svg = make("svg", { viewBox: `0 0 ${W} ${H}`, class: "map", role: "img" });
  svg.setAttribute("aria-label", "Your attempts: pitch across, resonance up and down, the target in the middle");
  svg.append(make("rect", { x: 0, y: 0, width: W, height: H, rx: 10, class: "map-bg" }));
  // Where a word scores 90 or more on both: ±1.3 semitones, ±3.3%.
  svg.append(make("rect", { x: x(-1.3), y: y(3.3), width: x(1.3) - x(-1.3), height: y(-3.3) - y(3.3), rx: 6, class: "map-zone" }));
  svg.append(make("line", { x1: W / 2, y1: 8, x2: W / 2, y2: H - 8, class: "map-axis" }));
  svg.append(make("line", { x1: 8, y1: H / 2, x2: W - 8, y2: H / 2, class: "map-axis" }));
  const label = (text, ax, ay, anchor) => {
    const node = make("text", { x: ax, y: ay, "text-anchor": anchor, class: "map-label" });
    node.textContent = text;
    svg.append(node);
  };
  label("◀ lower", 10, H / 2 - 6, "start");
  label("higher ▶", W - 10, H / 2 - 6, "end");
  label("▲ brighter", W / 2 + 6, 18, "start");
  label("▼ darker", W / 2 + 6, H - 10, "start");
  svg.append(make("circle", { cx: W / 2, cy: H / 2, r: 4, class: "map-target" }));
  tries.forEach((r, k) => {
    if (r.pitch == null && r.percent == null) return;
    const latest = k === tries.length - 1;
    const dot = make("circle", {
      cx: x(r.pitch ?? 0),
      cy: y(r.percent ?? 0),
      r: latest ? 9 : 6,
      class: `map-dot${latest ? " latest" : ""}${r.pitch == null || r.percent == null ? " partial" : ""}`,
    });
    svg.append(dot);
    const n = make("text", { x: x(r.pitch ?? 0), y: y(r.percent ?? 0) + 4, "text-anchor": "middle", class: `map-n${latest ? " latest" : ""}` });
    n.textContent = String(k + 1);
    svg.append(n);
  });
  // Where your voice is right now, while the microphone is open (renderDrillLive).
  const trail = make("polyline", { points: "", class: "map-trail" });
  const ring_ = make("circle", { cx: W / 2, cy: H / 2, r: 8, class: "map-live" });
  ring_.style.display = "none";
  svg.append(trail, ring_);
  mapLive = { ring: ring_, trail, x, y };
  $("#train-map").replaceChildren(svg);
}

function renderDrillMeter(part, last) {
  const value = part === "resonance" ? last?.percent : part === "pitch" ? last?.pitch : null;
  const scaled = part === "resonance" || part === "pitch";
  const showing = scaled && Boolean(last || drillLive);
  $("#train-meter").hidden = !showing;
  meterLive = null;
  if (!showing) return;
  // ±25% of formant shift, or ±6 semitones; the band is where a word scores 90 or more.
  const [range, band, unit] = part === "resonance" ? [25, 3.3, "%"] : [6, 1.3, " st"];
  const [low, high] = part === "resonance" ? ["darker", "brighter"] : ["lower", "higher"];
  const at = value == null ? null : Math.max(-range, Math.min(range, value));
  const zone = el("i", { className: "zone" });
  zone.style.left = `${50 - (band / range) * 50}%`;
  zone.style.width = `${(band / range) * 100}%`;
  const track = el("div", { className: "drill-track" }, zone, el("i", { className: "centre" }));
  if (at != null) {
    const mark = el("i", { className: "mark" });
    mark.style.left = `${50 + (at / range) * 50}%`;
    track.append(mark);
  }
  meterLive = el("i", { className: "mark now" }); // where your voice is right now
  meterLive.style.display = "none";
  track.append(meterLive);
  $("#train-meter").replaceChildren(
    el("span", { className: "end", textContent: `◀ ${low}` }),
    track,
    el("span", { className: "end", textContent: `${high} ▶` }),
    el("span", { className: "scale-note", textContent: `target in the middle; ±${range}${unit} across` }),
  );
}

function drillVerdict(part, r) {
  if (part === "both") {
    const s = modeScore(r, part);
    if (r.pitch == null || r.percent == null) {
      return [el("span", { textContent: r.voiced ? "This word is too short or unvoiced in the target to measure both." : "Not enough voice to measure: say it aloud, not whispered." })];
    }
    const pitch = Math.abs(r.pitch) < 0.3 ? "on pitch" : `${Math.abs(r.pitch).toFixed(1)} semitones ${r.pitch > 0 ? "high" : "low"}`;
    const res = Math.abs(r.percent) < 1 ? "level resonance" : `${Math.abs(r.percent).toFixed(0)}% ${r.percent > 0 ? "brighter" : "darker"}`;
    const advice = bothTips(r.pitch, r.percent);
    const move = {
      "hold hold": "Both close: do it again the same way.",
      "lower hold": "Go lower, and keep the brightness where it is.",
      "higher hold": "Go higher, and keep the brightness where it is.",
      "hold darker": "Keep the pitch, go darker.",
      "hold brighter": "Keep the pitch, go brighter.",
      "lower darker": "Go lower and darker together.",
      "lower brighter": "Go lower but brighter: keep the sound forward as you drop.",
      "higher darker": "Go higher but darker: keep the throat open as you lift.",
      "higher brighter": "Go higher and brighter together.",
    }[advice.key];
    return [el("b", { textContent: `${Math.round(s)}` }), ` · ${pitch}, ${res}. ${move}`];
  }
  const s = r.scores[part];
  if (s == null) {
    const why = r.voiced ? "This word is too short or unvoiced in the target to measure that." : "Not enough voice to measure: say it aloud, not whispered.";
    return [el("span", { textContent: why })];
  }
  let detail;
  if (part === "resonance") detail = Math.abs(r.percent) < 1 ? "level with the target" : `${Math.abs(r.percent).toFixed(0)}% ${r.percent > 0 ? "brighter" : "darker"} than the target`;
  else if (part === "pitch") detail = Math.abs(r.pitch) < 0.3 ? "on the target's pitch" : `${Math.abs(r.pitch).toFixed(1)} semitones ${r.pitch > 0 ? "higher" : "lower"} than the target`;
  else detail = s >= PASS ? "shaped like the target's" : "shaped differently from the target's";
  const verdict = s >= PASS ? "Close. " : s >= 50 ? "Nearer. " : "";
  const next =
    s >= PASS ? "Do it again the same way." : part === "resonance" ? `Go ${r.percent > 0 ? "darker" : "brighter"}.` : part === "pitch" ? `Go ${r.pitch > 0 ? "lower" : "higher"}.` : "Listen again and copy it closely.";
  return [el("b", { textContent: `${Math.round(s)}` }), ` · ${verdict}${detail}. ${next}`];
}

function playTrainWord() {
  const index = training.order[training.at];
  const word = profile.words[index];
  stopPlayback();
  const token = { kind: "word", cancelled: false };
  playing = token;
  playSpan(token, $("#target-audio"), Math.max(0, word.start - 0.04), word.end + 0.04, (x) => x)
    .catch((err) => !token.cancelled && setStatus(err.message, true))
    .finally(() => playing === token && stopPlayback());
}

// While the microphone is open in the trainer, a ring on the map (and a mark on
// the meter) shows where the last second or so of your voice sits against the
// target's phrase: pitch in semitones, resonance in percent. Both are read in
// the browser; the attempt that counts is still measured on the server.
const LIVE_SECONDS = 1.2;
let drillLive = null; // { listener, points: [{t, f0, f}], started, unit, pitch, trail, ticks }
let mapLive = null; // the map's ring and trail, and its scales
let meterLive = null; // the meter's live mark

function drillNow() {
  const now = (performance.now() - drillLive.started) / 1000;
  const recent = drillLive.points.filter((p) => p.t > now - LIVE_SECONDS);
  const f0s = recent.map((p) => p.f0).filter(Boolean).sort((a, b) => a - b);
  const hz = f0s.length >= 5 ? f0s[f0s.length >> 1] : null;
  return {
    hz,
    pitch: hz && drillLive.pitch ? 12 * Math.log2(hz / drillLive.pitch) : null,
    percent: shift(typical(recent.map((p) => p.f)), drillLive.unit),
  };
}

function renderDrillLive() {
  const { hz, pitch, percent } = drillNow();
  const said = [];
  if (pitch != null) said.push(Math.abs(pitch) < 0.3 ? "on pitch" : `${Math.abs(pitch).toFixed(1)} semitones ${pitch > 0 ? "high" : "low"}`);
  else if (hz) said.push(`${Math.round(hz)} Hz (${noteName(hz)})`);
  if (percent != null) said.push(Math.abs(percent) < 2 ? "resonance about level" : `about ${Math.abs(percent).toFixed(0)}% ${percent > 0 ? "brighter" : "darker"}`);
  if (said.length) $("#train-live").textContent = `Now: ${said.join(", ")}`;
  const seen = pitch != null || percent != null;
  if (mapLive) {
    mapLive.ring.style.display = seen ? "" : "none";
    if (!seen) drillLive.trail.length = 0;
    if (seen) {
      const point = [mapLive.x(pitch ?? 0), mapLive.y(percent ?? 0)];
      mapLive.ring.setAttribute("cx", point[0]);
      mapLive.ring.setAttribute("cy", point[1]);
      // A short tail, so the direction you're moving in shows. It starts over
      // whenever one of the two readings is missing: the ring then sits on an
      // axis, which isn't somewhere your voice has been.
      if (pitch == null || percent == null) drillLive.trail.length = 0;
      else if (drillLive.ticks++ % 3 === 0) {
        drillLive.trail.push(point);
        if (drillLive.trail.length > 40) drillLive.trail.shift();
      }
      mapLive.trail.setAttribute("points", drillLive.trail.map((p) => p.map((v) => v.toFixed(1)).join(",")).join(" "));
    }
  }
  if (meterLive) {
    const [value, range] = training.part === "resonance" ? [percent, 25] : [pitch, 6];
    meterLive.style.display = value == null ? "none" : "";
    if (value != null) meterLive.style.left = `${50 + (Math.max(-range, Math.min(range, value)) / range) * 50}%`;
  }
}

// `free`: just look, for as long as 45 s; nothing is scored or kept.
async function startDrill(free) {
  if (drillRec || session || opening || measuring || !training) return;
  const index = training.order[training.at];
  const word = profile?.words?.[index];
  if (!word) {
    setStatus("This line has no words to train on: correct its words first.", true);
    return;
  }
  opening = free ? "try" : "attempt";
  const at = page;
  try {
    await openDrill(free, index, word, at);
  } finally {
    opening = null;
  }
}

async function openDrill(free, index, word, at) {
  stopPlayback();
  await stopMicTest();
  const recorder = new Recorder();
  const mine = { listener: null, points: [], started: performance.now(), unit: null, pitch: wordPitch(index) ?? profile.median_f0 ?? null, trail: [], ticks: 0 };
  // A scored attempt ends when the phrase has been said; looking live goes on until it is stopped.
  const ending = !free && autoStop() ? new Ending({ wait: 0.7, enough: 0.5 * (word.end - word.start), patient: 2 }) : null;
  $("#train-live").hidden = false;
  $("#train-live").textContent = free ? "Say it, hold it, slide it: the ring follows your voice." : "Say it…";
  try {
    await recorder.start(
      (peak, rms) => {
        if (ending?.hear(rms, (performance.now() - mine.started) / 1000) && drillRec?.recorder === recorder) stopDrill();
      },
      (f0, frame, rate) => {
        if (drillLive !== mine) return;
        const f = mine.listener ? mine.listener.hear(frame, rate) : null;
        // Pitch counts on the frames loud enough to count for resonance, as in the target's phrase.
        mine.points.push({ t: (performance.now() - mine.started) / 1000, f0: mine.listener?.quiet ? null : f0, f });
        if (mine.points.length > 900) mine.points.splice(0, 300);
        renderDrillLive();
      },
      () => drillRec?.recorder === recorder && stopDrill(),
    );
  } catch (err) {
    $("#train-live").hidden = true;
    setStatus(micProblem(err), true);
    return;
  }
  if (page !== at || !training) {
    recorder.stop(); // the trainer was left while the microphone opened
    return;
  }
  drillLive = mine;
  drillRec = { recorder, free, index, timer: setTimeout(stopDrill, (free ? 45 : 6) * 1000) }; // a word, not a line
  renderTrainer();
  renderMic();
  setTimeout(() => {
    if (drillRec?.recorder === recorder && recorder.silent) $("#train-live").textContent = noSound();
  }, 1500);
  // The target's phrase, read the way the microphone is.
  loadTargetTrack().then((frames) => {
    if (!frames || drillLive !== mine) return;
    const said = frames.filter((f) => f.t >= word.start - 0.02 && f.t < word.end + 0.02);
    mine.unit = typical(said.map((f) => f.f), 5);
    const f0s = said.map((f) => f.f0).filter(Boolean).sort((a, b) => a - b);
    if (f0s.length >= 5) mine.pitch = f0s[f0s.length >> 1]; // the live tracker's own reading of the phrase
    mine.listener = new Listener(profile.my_ceiling ?? profile.ceiling);
  });
}

async function stopDrill() {
  if (!drillRec) return;
  const { recorder, timer, free, index } = drillRec;
  drillRec = null;
  drillLive = null;
  clearTimeout(timer);
  $("#train-live").hidden = true;
  if (training) renderTrainer();
  const line = current?.id;
  const trained = training;
  if (free || !training) {
    await recorder.stop(); // just looking: nothing to score or keep
    return;
  }
  measuring = true;
  setStatus("Measuring…");
  try {
    const blob = await recorder.stop();
    const result = await postWav(`/api/targets/${line}/drill?word=${index}`, await toWav(blob));
    if (training === trained) {
      (training.attempts[index] ??= []).push(result);
      setStatus("");
      renderTrainer();
    }
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    measuring = false;
  }
}

const toggleDrill = () => (drillRec ? stopDrill() : startDrill(false));
const toggleTryLive = () => (drillRec ? stopDrill() : startDrill(true));

let minePlayer = null;

// Your last attempt at the word, to hear against the target's.
function playMine() {
  const last = (training.attempts[training.order[training.at]] ?? []).at(-1);
  if (!last?.id) return;
  minePlayer?.pause();
  minePlayer = new Audio(`/api/targets/${current.id}/drills/${last.id}/audio`);
  minePlayer.play().catch(() => {});
}

function nextTrainWord() {
  if (training.at + 1 < training.order.length) training.at += 1;
  training.picked = true;
  renderTrainer();
}

function leaveTrainer() {
  if (drillRec) stopDrill();
  let part = training?.part;
  training = null;
  if (part === "both") {
    // The coach takes one part at a time: the weaker of the two on the last take.
    const score = (k) => shown?.components.find((c) => c.key === k)?.score ?? 100;
    part = score("pitch") <= score("resonance") ? "pitch" : "resonance";
  }
  if (part) stored.set(`echo.focus.${current.id}`, part); // the next take is about it
  show("practice");
  setShown(shown);
}

// ---------- recording ----------

async function startRecording(kind) {
  if (session || drillRec || opening || measuring) return;
  opening = kind;
  renderControls();
  try {
    await openRecording(kind, page);
  } finally {
    opening = null;
    renderControls();
  }
}

async function openRecording(kind, at) {
  stopPlayback();
  await stopMicTest();
  const recorder = new Recorder();
  const withLive = kind !== "target";
  if (withLive && !profile && current) {
    try {
      profile = await api(`/api/targets/${current.id}/profile`); // the live readout needs its pitch
    } catch {}
  }
  if (withLive) startLive();
  // A take ends when the line has been said: the quiet that ends it is a little
  // longer than the longest pause between the line's own words. Warming up and
  // recording a clip go on until they are stopped.
  const ending = kind === "take" && autoStop() ? new Ending({ wait: waitFor(profile?.words), enough: 0.5 * spoken(profile?.words) }) : null;
  const opened = performance.now();
  try {
    await recorder.start(
      (peak, rms) => {
        $("#level").style.width = `${Math.min(100, peak * 140)}%`;
        if (ending?.hear(rms, (performance.now() - opened) / 1000) && session?.recorder === recorder) stopRecording();
      },
      withLive ? onLivePitch : null,
      () => {
        if (session?.recorder !== recorder) return;
        mic.note = "The microphone stopped while recording: unplugged, or switched off.";
        stopRecording();
      },
    );
  } catch (err) {
    stopLive();
    setStatus(micProblem(err), true);
    return;
  }
  if (page !== at) {
    recorder.stop(); // the page was left while the microphone opened
    stopLive();
    return;
  }
  const started = performance.now();
  const limit = kind === "warmup" ? WARMUP_SECONDS : MAX_SECONDS;
  const label = kind === "warmup" ? "Warming up, not scored" : "Recording";
  const until = ending ? ". It stops when you finish" : "";
  const timer = setInterval(() => {
    const seconds = (performance.now() - started) / 1000;
    const dead = seconds > 1.5 && recorder.silent;
    setStatus(dead ? `${label}… ${seconds.toFixed(0)} s. ${noSound()}` : `${label}… ${seconds.toFixed(0)} s${until}`, dead);
    if (seconds >= limit) stopRecording();
  }, 250);
  // What it is for, as it was when it started: the take is scored against this
  // line and a clip goes to this voice, whatever is open when it stops.
  session = { kind, recorder, timer, target: current?.id, owner: voice?.id };
  setStatus(`${label}…${until}`);
  renderControls();
  renderMic();
}

// `discard`: stop and keep nothing (the page it belonged to was left).
async function stopRecording({ discard = false } = {}) {
  if (!session) return;
  const { kind, recorder, timer, target, owner } = session;
  session = null;
  clearInterval(timer);
  stopLive();
  $("#level").style.width = "0";
  if (discard) {
    await recorder.stop();
    renderControls();
    if (kind !== "warmup") setStatus("");
    return;
  }
  measuring = kind !== "warmup";
  renderControls();
  try {
    const blob = await recorder.stop();
    if (kind === "take") await submitTake(blob, target);
    else if (kind === "target") await openTrim(blob, { name: "Recorded clip", back: "voice", extra: { voice: owner } });
    else {
      markStep("note");
      setStatus("Warm-up done. Your pitch stays on the guide until the next take.");
    }
  } finally {
    measuring = false;
    renderControls();
  }
}

const toggleRecording = (kind) => (session ? stopRecording() : startRecording(kind));

// The target's F1-F3 through the line, read in the browser by the tracker the
// microphone goes through (formants.js), so a gap between the two is a gap
// between the voices and not between two ways of measuring.
function loadTargetTrack() {
  if (!current || !profile?.ceiling) return Promise.resolve(null);
  const { id } = current;
  const { ceiling } = profile;
  if (targetTrack?.id !== id || targetTrack.ceiling !== ceiling) {
    const entry = { id, ceiling, frames: null };
    entry.frames = fetch(`/api/targets/${id}/audio`)
      .then((response) => {
        if (!response.ok) throw new Error(response.statusText);
        return response.blob();
      })
      .then((blob) => decodeMono(blob, MAX_SECONDS + 30))
      .then((samples) => track(samples, SR, ceiling))
      .catch(() => {
        if (targetTrack === entry) targetTrack = null; // try again next time, not never
        return null;
      });
    targetTrack = entry;
  }
  return targetTrack.frames;
}

const brightness = (percent) =>
  Math.abs(percent) < 2 ? "about level with" : `about ${Math.abs(percent).toFixed(0)}% ${percent > 0 ? "brighter" : "darker"} than`;

// Your resonance so far against the target's, with your sounds lined up with
// the target's first, as a take's Resonance is measured: each vowel is held
// against the same vowel. Without that, a whole line's typical formants depend
// on how long each speaker dwells on each sound, and read several points off.
// What's lined up is what you've said since your last pause of a second or
// more: one pass through the line, however often you warm up on it.
function renderLiveResonance() {
  let percent = null;
  if (live?.target && live.onset != null) {
    const said = [];
    let later = null;
    for (let i = live.points.length - 1; i >= 0; i--) {
      const point = live.points[i];
      if (!point.f) continue;
      if (later != null && later - point.t > 1) break;
      said.push(point.f);
      later = point.t;
    }
    percent = alignedShift(said.reverse(), live.target.map((f) => f.f), { open: true });
  }
  $("#live-res").hidden = percent == null;
  if (percent == null) return;
  $("#live-res-mark").style.left = `${50 + (Math.max(-25, Math.min(25, percent)) / 25) * 50}%`;
  $("#live-res-text").textContent = `Resonance so far: ${brightness(percent)} the target`;
}

function startLive() {
  const started = { points: [], started: performance.now(), onset: null, done: false, listener: null, target: null };
  live = started;
  selectedWord = null;
  $("#live").hidden = false;
  $("#live-text").textContent = "Speak…";
  $("#live-res").hidden = true;
  drawGuide();
  loadTargetTrack().then((frames) => {
    if (!frames || live !== started) return;
    started.target = frames;
    started.listener = new Listener(profile.my_ceiling ?? profile.ceiling);
  });
}

function stopLive() {
  if (live) live.done = true;
  $("#live").hidden = true;
}

function onLivePitch(f0, frame, rate) {
  if (!live || live.done) return;
  const t = (performance.now() - live.started) / 1000;
  live.points.push({ t, f0, f: live.listener && frame ? live.listener.hear(frame, rate) : null });
  if (f0 && live.onset == null) live.onset = t;
  const recent = live.points.filter((p) => p.f0 && p.t > t - 0.5).map((p) => p.f0).sort((a, b) => a - b);
  const target = profile?.median_f0;
  if (recent.length >= 5) {
    const now = recent[Math.floor(recent.length / 2)];
    let text = `You: ${Math.round(now)} Hz (${noteName(now)})`;
    if (target) {
      const st = 12 * Math.log2(now / target);
      const advice = Math.abs(st) < 1 ? "on target" : st > 0 ? `go lower (${st.toFixed(1)} st)` : `go higher (${(-st).toFixed(1)} st)`;
      text += ` · target about ${Math.round(target)} Hz (${noteName(target)}) — ${advice}`;
    }
    $("#live-text").textContent = text;
  }
  if (live.points.length % 4 === 0) renderLiveResonance(); // a few times a second is plenty
  drawGuide();
}

// `line`: the line the take was recorded on.
async function submitTake(blob, line) {
  setStatus("Scoring…");
  try {
    const focus = chosenFocus();
    const path = `/api/targets/${line}/takes${focus ? `?focus=${focus}` : ""}`;
    const take = await postWav(path, await toWav(blob));
    if (current?.id !== line) return setStatus("The take was scored and kept with its line.");
    if (profile && take.measures?.ceiling_used) profile.my_ceiling = take.measures.ceiling_used; // for the live meter
    takes.push({
      id: take.id,
      created: take.created,
      overall: take.overall,
      parts: Object.fromEntries(take.components.map((c) => [c.key, c.score])),
      focus: take.coach?.focus ?? null,
    });
    if (live?.done) live = null; // the take's own live trace, not a newer one
    const best = Math.max(...takes.map((t) => t.overall));
    current = { ...current, takes: takes.length, best, last: take.created, last_score: take.overall, focus: take.coach?.focus ?? null };
    targets = targets.map((t) => (t.id === current.id ? current : t));
    if (inCheck()) check.results[check.index] = { target: current.id, take: take.id, overall: take.overall };
    setStatus("");
    setShown(take);
  } catch (err) {
    setStatus(err.message, true);
  }
}

function renderControls() {
  const kind = session?.kind;
  const checkDone = inCheck() && check.results[check.index];
  $("#record").classList.toggle("live", kind === "take");
  const busy = Boolean(opening) || measuring; // starting, or a take being scored
  $("#record").disabled = busy || kind === "target" || kind === "warmup" || Boolean(checkDone);
  $("#record-label").textContent = kind === "take" ? "Stop" : "Record a take";
  $("#warmup").classList.toggle("live", kind === "warmup");
  $("#warmup").disabled = busy || kind === "take" || kind === "target";
  $("#warmup").textContent = kind === "warmup" ? "Stop warm-up" : "Warm up";
  $("#new-record").classList.toggle("live", kind === "target");
  $("#new-record").disabled = busy || kind === "take" || kind === "warmup";
  $("#new-record").textContent = kind === "target" ? "Stop" : "Record a clip";
}

// ---------- the result of a take ----------

function renderResult() {
  $("#result").hidden = !shown || inCheck();
  if (!shown || inCheck()) return;
  const overall = Math.round(shown.overall);
  $("#overall").textContent = overall;
  const circumference = 2 * Math.PI * 34;
  $("#ring-fill").setAttribute("stroke-dasharray", `${(circumference * overall) / 100} ${circumference}`);
  $("#check").hidden = !shown.accepted;

  const index = takes.findIndex((t) => t.id === shown.id);
  const earlier = takes.slice(0, Math.max(0, index));
  const previous = earlier.at(-1);
  $("#ladder-now").style.left = `${overall}%`;
  $("#ladder-now").title = `This take: ${overall}`;
  const bestBefore = earlier.length ? Math.round(Math.max(...earlier.map((t) => t.overall))) : null;
  $("#ladder-best").hidden = bestBefore == null;
  if (bestBefore != null) {
    $("#ladder-best").style.left = `${bestBefore}%`;
    $("#ladder-best").title = `Best before: ${bestBefore}`;
  }
  if (!earlier.length) $("#score-note").textContent = "First take — this is your baseline.";
  else {
    const best = Math.max(...earlier.map((t) => t.overall));
    const change = Math.round(shown.overall - previous.overall);
    const said = change === 0 ? "Same as your last take" : `${change > 0 ? "Up" : "Down"} ${Math.abs(change)} from your last take`;
    $("#score-note").textContent = shown.overall > best ? `${said}: a new best.` : `${said}. Best so far: ${Math.round(best)}.`;
  }

  const focus = currentFocus();
  const rows = [...shown.components];
  if (!rows.some((c) => c.key === "gender") && shown.gender?.take != null && shown.gender?.target != null) {
    // A take from before gender read was a measurement: the same row, worked out here.
    const { take, target } = shown.gender;
    rows.push({
      key: "gender",
      label: "Gender read",
      score: Math.max(0, 100 - Math.abs(take - target)),
      detail: `you read ${Math.round(take)} (${genderWord(take)}), the target ${Math.round(target)} (${genderWord(target)}); 0 is male, 100 female`,
    });
  }
  $("#components").replaceChildren(
    ...rows.map((c) => {
      const before = previous?.parts?.[c.key];
      const change = c.score != null && before != null ? Math.round(c.score - before) : null;
      return el(
        "li",
        { className: `${c.key === focus ? "focus" : ""}${READS.has(c.key) ? " extra" : ""}` },
        el("span", { className: "label", textContent: c.label }),
        bar(c.score),
        el("span", { className: "num", textContent: c.score == null ? "–" : Math.round(c.score) }),
        el("span", {
          className: `delta ${change > 0 ? "up" : change < 0 ? "down" : ""}`,
          textContent: change == null ? "" : delta(c.score, before),
          title: change == null ? "" : "Change since your last take",
        }),
        trainButton(c.key),
        el("span", { className: "detail", textContent: READS.has(c.key) ? `${c.detail}. Not counted in the score.` : c.detail }),
      );
    }),
  );

  // Advice on the parts is the coach's job; here only what's left: the words.
  const partHints = new Set(shown.components.map((c) => c.hint));
  const extra = shown.coach ? shown.hints.filter((h) => !partHints.has(h)) : shown.hints;
  $("#hints-wrap").hidden = !extra.length;
  $("#hints").replaceChildren(...extra.map((hint) => el("li", { textContent: hint })));
}

// "Train ›" on each part a person can work on directly (not Voice: it follows from the rest).
function trainButton(key) {
  if (key === "identity" || key === "fry") return el("span");
  const button = el("button", { type: "button", className: "train-btn", textContent: "Train ›" });
  if (key === "gender") {
    // Gender read is pitch and resonance, so that's what moves it.
    button.title = "Work on pitch and resonance together, word by word";
    button.onclick = () => openTrainer("both");
    return button;
  }
  button.title = TRAIN[key] ? `Work on ${partLabel(key).toLowerCase()} word by word` : `Work on ${partLabel(key).toLowerCase()} across the line`;
  button.onclick = () => openTrainer(key);
  return button;
}

// Shown beside the parts and not counted in the score.
const READS = new Set(["gender", "fry"]);

const genderWord = (v) => (v >= 80 ? "female" : v >= 60 ? "leans female" : v > 40 ? "in between" : v > 20 ? "leans male" : "male");

function renderHistory() {
  $("#history").hidden = takes.length < 2 || inCheck();
  if (takes.length < 2 || inCheck()) return;
  const best = Math.max(...takes.map((t) => t.overall));
  $("#history-note").textContent = `${takes.length} so far, best ${Math.round(best)}`;
  $("#takes").replaceChildren(
    ...takes.map((take) => {
      const button = el("button", { type: "button", title: `${Math.round(take.overall)} — ${new Date(take.created).toLocaleString()}` });
      button.style.height = `${Math.max(4, take.overall)}%`;
      button.setAttribute("aria-current", String(take.id === shown?.id));
      button.onclick = async () => {
        stopPlayback();
        setShown(await api(`/api/targets/${current.id}/takes/${take.id}`));
      };
      return button;
    }),
  );
}

// ---------- the voice check: several lines, one take each ----------

async function startCheck() {
  const lines = checkLines();
  if (lines.length < voice.min_check_lines) return;
  const owner = voice;
  check = { voiceId: owner.id, voiceName: owner.name, lines, index: 0, results: [] };
  await openLine(lines[0], { keepCheck: true });
}

function renderCheckBanner() {
  const on = inCheck();
  $("#check-banner").hidden = !on;
  $("#practice").classList.toggle("checking", on);
  renderControls();
  if (!on) return;
  const i = check.index;
  const n = check.lines.length;
  const done = check.results[i];
  $("#check-step").textContent = done
    ? `Line ${i + 1} of ${n}: ${Math.round(done.overall)}. ${i + 1 < n ? "On to the next one." : "That's every line."}`
    : `Line ${i + 1} of ${n}. Listen as often as you like, then one take: no retries, like performing it.`;
  $("#check-dots").replaceChildren(
    ...check.lines.map((line, k) => {
      const result = check.results[k];
      return el("li", {
        className: result ? "done" : k === i ? "now" : "",
        textContent: result ? Math.round(result.overall) : k + 1,
        title: `“${lineText(line)}”`,
      });
    }),
  );
  $("#check-next").hidden = !done;
  $("#check-next").textContent = i + 1 < n ? "Next line ›" : "See your voice score";
}

async function checkNext() {
  if (!check) return;
  if (check.index + 1 < check.lines.length) {
    check.index += 1;
    await openLine(check.lines[check.index], { keepCheck: true });
    return;
  }
  const { voiceId, results } = check;
  setStatus("Working out your voice score…");
  try {
    const result = await send(`/api/voices/${voiceId}/checks`, "POST", { takes: results.map(({ target, take }) => ({ target, take })) });
    check = null;
    await refreshLists();
    setStatus("");
    await openCheck(voiceId, result.id);
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function stopCheck() {
  if (!confirm("Stop the voice check? The takes you've made stay on their lines, but no voice score is saved.")) return;
  const voiceId = check?.voiceId;
  check = null;
  if (voiceId) await openVoice(voiceId);
}

async function openCheck(voiceId, checkId) {
  leave();
  let result;
  try {
    [voice, result] = await Promise.all([api(`/api/voices/${voiceId}`), api(`/api/voices/${voiceId}/checks/${checkId}`)]);
  } catch (err) {
    setStatus(err.message, true);
    return;
  }
  renderSidebar();
  renderCheckResult(result);
  show("check-result");
}

function renderCheckResult(result) {
  const index = voice.checks.findIndex((c) => c.id === result.id);
  const before = index > 0 ? voice.checks[index - 1] : null;
  const back = el("button", { type: "button", className: "crumb", textContent: `‹ ${voice.name}` });
  back.onclick = () => openVoice(voice.id);
  const spread = result.high - result.low;
  const steady =
    spread <= 10
      ? `Steady: your lines ranged ${Math.round(result.low)}–${Math.round(result.high)}, so this is how the voice comes across, not one lucky or unlucky line.`
      : `Uneven: your lines ranged ${Math.round(result.low)}–${Math.round(result.high)}. The voice holds on some lines and slips on others.`;
  const change = before
    ? `${delta(result.average, before.average)} since the check before (${Math.round(before.average)}).`
    : "Your first voice check: this is your baseline.";

  const lines = el(
    "ol",
    { className: "check-lines" },
    ...result.lines.map((row) => {
      const go = el("button", { type: "button", className: "btn small", textContent: "Practise" });
      go.onclick = async () => {
        const line = targets.find((t) => t.id === row.target);
        if (line) await openLine(line);
      };
      return el(
        "li",
        { className: row.target === result.weakest ? "weakest" : "" },
        el("span", { className: "words-text", textContent: `“${row.line}”` }),
        bar(row.overall),
        el("span", { className: "num", textContent: Math.round(row.overall) }),
        go,
      );
    }),
  );
  const parts = el(
    "ul",
    { className: "components" },
    ...ALL_PARTS.map(([key, label]) =>
      el(
        "li",
        { className: key === result.focus ? "focus" : "" },
        el("span", { className: "label", textContent: label }),
        bar(result.parts[key]),
        el("span", { className: "num", textContent: result.parts[key] == null ? "–" : Math.round(result.parts[key]) }),
      ),
    ),
  );
  const habits = habitList(result.habits ?? {});
  const weakest = result.lines.find((r) => r.target === result.weakest);
  const next = [];
  if (result.focus) next.push(`Work on ${partLabel(result.focus).toLowerCase()} across your lines: it would lift the voice score most.`);
  if (weakest && spread > 10) next.push(`Start with your weakest line, “${weakest.line}”.`);
  const practiseWeakest = el("button", { type: "button", className: "record", textContent: "Practise the weakest line" });
  practiseWeakest.onclick = async () => {
    const line = targets.find((t) => t.id === result.weakest);
    if (line) await openLine(line);
  };
  const toVoice = el("button", { type: "button", className: "btn", textContent: `Back to ${voice.name}` });
  toVoice.onclick = () => openVoice(voice.id);

  $("#check-result").replaceChildren(
    back,
    el("header", { className: "page-head" }, el("div", {}, el("p", { className: "eyebrow", textContent: `Voice check · ${when(result.created)}` }), el("h2", { textContent: "Your voice score" }))),
    el(
      "section",
      { className: "card score-card" },
      el(
        "div",
        { className: "score-card-main" },
        ring(result.average),
        el(
          "div",
          { className: "score-text" },
          el("div", { className: "score" }, el("span", { textContent: Math.round(result.average) }), el("small", { textContent: `/ 100, the average of ${plural(result.lines.length, "line")}` })),
          el("p", { className: "score-note", textContent: change }),
          el("p", { className: "fine", textContent: steady }),
        ),
      ),
      ladder(result.average, before?.average ?? null),
    ),
    el("section", { className: "card" }, el("h3", { textContent: "Each line" }), lines),
    el("section", { className: "card" }, el("h3", { textContent: "Each part, averaged over the lines" }), parts),
    el(
      "section",
      { className: "card coach" },
      el("h3", { textContent: "What to work on" }),
      habits.length ? el("p", { textContent: `In every line you were ${habits.join(" and ")}. Fix that once and every line improves.` }) : null,
      ...next.map((text) => el("p", { textContent: text })),
      el("div", { className: "score-card-buttons" }, practiseWeakest, toVoice),
    ),
  );
}

// ---------- polling: recordings being processed ----------

async function poll() {
  if (sources.some(unfinished)) {
    try {
      const before = sources.filter(unfinished).length;
      sources = await api("/api/sources");
      if (source && unfinished(source)) await refreshSource();
      if (voice && !$("#voice").hidden) await refreshVoice();
      if (sources.filter(unfinished).length < before) {
        await refreshLists(); // its job may have added lines
        if (voice && !$("#voice").hidden) {
          await refreshVoice();
          setStatus(voice.lines.length ? "Done: the best lines are under “Your lines”, ready to practise." : "");
        }
      }
    } catch {}
  }
  setTimeout(poll, 1500);
}

// ---------- wiring ----------

$("#home-link").onclick = goHome;
$("#new-voice").onclick = () => toggleNewVoice(true);
$("#new-voice-cancel").onclick = () => toggleNewVoice(false);
// One box for a name or a link: in the sidebar, on the first screen, on home.
for (const [form, input] of [["#new-voice-form", "#new-voice-name"], ["#first-voice-form", "#first-voice-name"], ["#home-new-form", "#home-new-input"]]) {
  $(form).onsubmit = async (e) => {
    e.preventDefault();
    const text = $(input).value.trim();
    if (!text) return $(input).focus();
    $(input).value = "";
    toggleNewVoice(false);
    await startVoice(text);
  };
}
$("#rename-voice").onclick = renameVoice;
for (const b of document.querySelectorAll("#voice-tabs button")) b.onclick = () => setVoiceTab(b.dataset.tab);
$("#autopick").onclick = autoPick;
$("#delete-voice").onclick = deleteVoice;
$("#record").onclick = () => toggleRecording("take");
$("#warmup").onclick = () => toggleRecording("warmup");
$("#new-record").onclick = () => toggleRecording("target");
$("#new-file").onchange = async (e) => {
  const [file] = e.target.files;
  e.target.value = "";
  if (file) await addFile(file);
};
$("#link-form").onsubmit = addLink;
$("#line-search").oninput = renderLines;
$("#only-good").onchange = renderLines;
$("#delete-source").onclick = deleteSource;
$("#trim-start").oninput = renderTrim;
$("#trim-end").oninput = renderTrim;
$("#trim-play").onclick = playTrim;
$("#trim-use").onclick = useTrim;
$("#trim-cancel").onclick = closeTrim;
$("#edit-line").onclick = editLine;
$("#cancel-line").onclick = renderLine;
$("#line-form").onsubmit = saveLine;
$("#delete-target").onclick = deleteTarget;
$("#play-target").onclick = () => play("target");
$("#play-take").onclick = () => play("take");
$("#play-both").onclick = () => play("both");
$("#play-note").onclick = playNote;
$("#check-next").onclick = checkNext;
$("#train-listen").onclick = playTrainWord;
$("#train-note").onclick = playWordNote;
$("#train-mine").onclick = playMine;
for (const b of document.querySelectorAll("#train-modes button")) b.onclick = () => switchTrainMode(b.dataset.part);
$("#train-say").onclick = toggleDrill;
$("#mic-select").onchange = (e) => chooseMic(e.target.value);
$("#mic-test").onclick = testMic;
$("#mic-off").onclick = closeMic;
$("#auto-stop").onchange = (e) => {
  stored.set("echo.autostop", e.target.checked ? "on" : "off");
  renderAutoStop();
};
renderAutoStop();
navigator.mediaDevices?.addEventListener?.("devicechange", listMics);
api("/api/config")
  .then((config) => ($("#version").textContent = `Echo ${config.version}`))
  .catch(() => {});
window.addEventListener("pagehide", () => mic.stream?.getTracks().forEach((t) => t.stop()));
listMics();
$("#train-try").onclick = toggleTryLive;
$("#train-next").onclick = nextTrainWord;
$("#train-back").onclick = leaveTrainer;
$("#check-stop").onclick = stopCheck;
for (const b of document.querySelectorAll(".speed button")) b.onclick = () => setSpeed(Number(b.dataset.rate));

// Space records a take, unless the keyboard is busy with a field or button.
document.addEventListener("keydown", (e) => {
  if (e.code !== "Space" || e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
  const active = document.activeElement;
  if (active && active !== document.body && active.closest("button, input, textarea, select, a, label, [contenteditable]")) return;
  if (opening || measuring) {
    e.preventDefault(); // starting, or scoring: a second press would start another
    return;
  }
  if (!$("#train").hidden) {
    e.preventDefault();
    toggleDrill();
    return;
  }
  if ($("#practice").hidden || (session && session.kind !== "take") || $("#record").disabled) return;
  e.preventDefault();
  toggleRecording("take");
});

window.addEventListener("resize", () => {
  drawGuide();
  renderTrim();
});
// Colours come from CSS, so redraw when the theme flips.
window.matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => drawGuide());

try {
  await refreshLists();
  // Back to the page that was open last; home the first time.
  const [kind, id] = (stored.get("echo.view") || "").split(":");
  const line = kind === "line" && targets.find((t) => t.id === id);
  const recording = kind === "source" && sources.find((s) => s.id === id);
  if (line) await openLine(line);
  else if (kind === "voice" && voiceOf(id)) await openVoice(id);
  else if (recording) await openSource(recording);
  else await goHome();
} catch (err) {
  setStatus(err.message, true);
}
poll();
