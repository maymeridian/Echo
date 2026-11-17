// Check the page's live formant tracker (static/formants.js) against what the
// app itself measures with Praat.
//
//   python tools/live_resonance_ref.py --vcc cache/vcc2018 --out cache/live_resonance_ref.json
//   node tools/check_live_resonance.mjs cache/live_resonance_ref.json
//
// The reference has 12 VCC2018 speakers reading the same 12 sentences, each
// with its speaker's formant ceiling, and for every pair the one ceiling the
// app reads both at. The tracker sees each recording as the page would, at
// the file's own rate, and is compared on:
//
//   typical F1-F3   against Praat's at the same ceiling
//   the gap between two speakers on one sentence, in percent brighter or
//   darker (from F2 and F3, both read at the pair's ceiling), two ways:
//     the trainer's live ring (typical formants) against the trainer's measure
//     the line page's live meter (the two lined up first, formants.alignedShift)
//     against a take's Resonance, which the app measures along its own alignment
//
// Sentences 30001 and 30007 chose the tracker's test for "voiced" (VOICED and
// LOUD in formants.js), so they are left out here.

import { readFileSync } from "node:fs";
import { alignedShift, shift, track, typical } from "../src/echo_voice/static/formants.js";

const TUNED_ON = new Set([30001, 30007]);

function readWav(path) {
  const buf = readFileSync(path);
  let offset = 12;
  let rate = 0;
  let bits = 16;
  let channels = 1;
  while (offset < buf.length) {
    const id = buf.toString("ascii", offset, offset + 4);
    const size = buf.readUInt32LE(offset + 4);
    if (id === "fmt ") {
      channels = buf.readUInt16LE(offset + 10);
      rate = buf.readUInt32LE(offset + 12);
      bits = buf.readUInt16LE(offset + 22);
    } else if (id === "data") {
      if (bits !== 16 || channels !== 1) throw new Error(`${path}: expected 16-bit mono PCM`);
      const samples = new Float32Array(size / 2);
      for (let i = 0; i < samples.length; i++) samples[i] = buf.readInt16LE(offset + 8 + i * 2) / 32768;
      return { rate, samples };
    }
    offset += 8 + size + (size % 2);
  }
  throw new Error(`${path}: no data chunk`);
}

const median = (values) => {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[sorted.length >> 1];
};
const share = (values, test) => `${((100 * values.filter(test).length) / values.length).toFixed(0)}%`;
function pearson(x, y) {
  const mx = x.reduce((s, v) => s + v, 0) / x.length;
  const my = y.reduce((s, v) => s + v, 0) / y.length;
  let sxy = 0;
  let sxx = 0;
  let syy = 0;
  for (let i = 0; i < x.length; i++) {
    sxy += (x[i] - mx) * (y[i] - my);
    sxx += (x[i] - mx) ** 2;
    syy += (y[i] - my) ** 2;
  }
  return sxy / Math.sqrt(sxx * syy);
}
const percent = (semitones) => (2 ** (semitones / 12) - 1) * 100;

const ref = JSON.parse(readFileSync(process.argv[2], "utf8"));
const used = (file) => !TUNED_ON.has(file.sentence);
let frames = 0;
let seconds = 0;
const started = performance.now();
const pitch = [];
const readings = [];
const live = ref.files.map((file, i) => {
  if (!used(file)) return null;
  const { rate, samples } = readWav(file.path);
  const tracked = track(samples, rate, file.ceiling);
  readings[i] = tracked.map((f) => f.f);
  frames += tracked.length;
  seconds += samples.length / rate;
  const f0s = tracked.map((f) => f.f0).filter(Boolean);
  pitch[i] = f0s.length >= 5 ? median(f0s) : null;
  return typical(tracked.map((f) => f.f));
});
const perFrame = (performance.now() - started) / frames;
const files = ref.files.filter(used);

console.log(`${files.length} recordings of ${new Set(files.map((f) => f.speaker)).size} speakers, ${seconds.toFixed(0)} s of speech.`);
console.log("Typical formants, live tracker against Praat at the same ceiling:");
for (const k of [0, 1, 2]) {
  const errors = ref.files.map((f, i) => (live[i] ? Math.abs(live[i][k] / f.formants[k] - 1) * 100 : null)).filter((e) => e != null);
  console.log(`  F${k + 1}  median ${median(errors).toFixed(1)}% apart, over 5% in ${share(errors, (e) => e > 5)}, over 10% in ${share(errors, (e) => e > 10)}`);
}

// A recording as the tracker reads it at a pair's ceiling, worked out once.
const at = new Map();
function read(i, ceiling) {
  const key = `${i}@${ceiling}`;
  if (!at.has(key)) {
    const { rate, samples } = readWav(ref.files[i].path);
    const frames = track(samples, rate, ceiling).map((f) => f.f);
    at.set(key, { frames, typical: typical(frames) });
  }
  return at.get(key);
}
const WAYS = [
  ["typical", "the trainer's live ring against the trainer's measure (typical formants)", (p) => shift(read(p.take, p.ceiling).typical, read(p.target, p.ceiling).typical)],
  ["aligned", "the line page's live meter against a take's Resonance (both lined up)", (p) => alignedShift(read(p.take, p.ceiling).frames, read(p.target, p.ceiling).frames, { open: true })],
  ["aligned", "the same without lining up, as the meter first was", (p) => shift(read(p.take, p.ceiling).typical, read(p.target, p.ceiling).typical)],
];
for (const [key, label, read] of WAYS) {
  const rows = ref.pairs
    .filter((p) => live[p.take] && live[p.target])
    .map((p) => ({ app: p[key] == null ? null : percent(p[key]), live: read(p) }))
    .filter((r) => r.app != null && r.live != null);
  const apart = rows.map((r) => Math.abs(r.live - r.app));
  const way = rows.filter((r) => Math.abs(r.app) >= 3);
  console.log(`The gap between two speakers on one sentence (${rows.length} pairs), ${label}:`);
  console.log(`  correlation ${pearson(rows.map((r) => r.live), rows.map((r) => r.app)).toFixed(3)}; ` +
    `median ${median(apart).toFixed(1)} points apart, within 3 points in ${share(apart, (e) => e <= 3)}, over 6 in ${share(apart, (e) => e > 6)}`);
  console.log(`  says the right way (brighter or darker) in ${share(way, (r) => Math.sign(r.live) === Math.sign(r.app))} of the ${way.length} pairs the app puts 3% or more apart`);
}
// The trainer's live ring also needs pitch: the live tracker's typical pitch
// (pitch.js) on both sides, against Praat's on both sides.
const gaps = ref.pairs
  .filter((p) => pitch[p.take] && pitch[p.target] && ref.files[p.take].f0 && ref.files[p.target].f0)
  .map((p) => ({ live: 12 * Math.log2(pitch[p.take] / pitch[p.target]), app: 12 * Math.log2(ref.files[p.take].f0 / ref.files[p.target].f0) }));
const off = gaps.map((g) => Math.abs(g.live - g.app));
console.log(`The pitch gap between two speakers on one sentence (${gaps.length} pairs), live tracker against Praat:`);
console.log(`  correlation ${pearson(gaps.map((g) => g.live), gaps.map((g) => g.app)).toFixed(3)}; median ${median(off).toFixed(2)} semitones apart, over 1 semitone in ${share(off, (e) => e > 1)}`);
console.log(`${perFrame.toFixed(2)} ms of work per reading on this machine.`);
