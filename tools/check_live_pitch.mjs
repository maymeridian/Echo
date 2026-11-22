/*
tools/check_live_pitch.mjs
Author: maymeridian
Description: Checks the page's live pitch tracker (static/pitch.js) against PTDB-TUG.
             - Usage: node tools/check_live_pitch.mjs cache/ptdb
             - PTDB-TUG (Pirker et al., Interspeech 2011) gives laryngograph-derived reference
               pitch every 10 ms for microphone recordings (48 kHz).
             - The tracker sees what the page sees: 2048-sample frames at the recording's rate.
*/

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { yin } from "../src/echo_voice/static/pitch.js";

// The samples of a 16-bit PCM WAV file, scaled to [-1, 1), and its rate.
function readWav(path) {
  const buf = readFileSync(path);
  let offset = 12;
  let rate = 0;
  let bits = 16;
  while (offset < buf.length) {
    const id = buf.toString("ascii", offset, offset + 4);
    const size = buf.readUInt32LE(offset + 4);
    if (id === "fmt ") {
      rate = buf.readUInt32LE(offset + 12);
      bits = buf.readUInt16LE(offset + 22);
    } else if (id === "data") {
      if (bits !== 16) throw new Error(`${path}: expected 16-bit PCM`);
      const samples = new Float32Array(size / 2);
      for (let i = 0; i < samples.length; i++) {
        samples[i] = buf.readInt16LE(offset + 8 + i * 2) / 32768;
      }
      return { rate, samples };
    }
    offset += 8 + size + (size % 2);
  }
  throw new Error(`${path}: no data chunk`);
}

const root = process.argv[2];
const files = readdirSync(root).filter((f) => f.startsWith("mic_") && f.endsWith(".wav"));
const FRAME = 2048; // samples per frame, as the page reads the microphone
const totals = {};
for (const file of files) {
  // 1. The recording, peak-normalised, and its reference pitch (0 where unvoiced).
  const { rate, samples } = readWav(join(root, file));
  const refText = readFileSync(join(root, `ref_${file.slice(4, -4)}.f0`), "utf8");
  const ref = refText.trim().split("\n").map((line) => Number(line.trim().split(/\s+/)[0]));
  const peak = samples.reduce((m, x) => Math.max(m, Math.abs(x)), 0) || 1;
  const group = file.split("_")[1].startsWith("F") ? "female" : "male";
  for (const g of [group, "all"]) totals[g] ??= { both: 0, gross: 0, frames: 0, vde: 0, cents: [] };

  // 2. The tracker on the frame centred on each reference frame, tallied by group: voicing
  // decisions that differ, gross errors (more than 20% off), and the error in cents otherwise.
  for (let i = 0; i < ref.length; i++) {
    // Reference frame i is centred at 16 ms + 10 ms * i (32 ms window, 10 ms hop).
    const centre = Math.round((0.016 + 0.01 * i) * rate);
    const start = centre - FRAME / 2;
    if (start < 0 || start + FRAME > samples.length) continue;
    const frame = samples.slice(start, start + FRAME).map((x) => x / peak);
    const est = yin(frame, rate);
    const rv = ref[i] > 0;
    const ev = est !== null;
    for (const g of [group, "all"]) {
      const t = totals[g];
      t.frames++;
      if (rv !== ev) t.vde++;
      if (rv && ev) {
        t.both++;
        const ratio = est.f0 / ref[i];
        if (Math.abs(ratio - 1) > 0.2) t.gross++;
        else t.cents.push(Math.abs(1200 * Math.log2(ratio)));
      }
    }
  }
}

console.log(
  `${files.length} utterances. Live tracker (YIN, 2048-sample frames) against laryngograph pitch:`,
);
for (const [g, t] of Object.entries(totals)) {
  t.cents.sort((a, b) => a - b);
  const median = t.cents[Math.floor(t.cents.length / 2)];
  const gross = ((100 * t.gross) / t.both).toFixed(2);
  const vde = ((100 * t.vde) / t.frames).toFixed(2);
  const fine = median.toFixed(1);
  console.log(
    `  ${g.padEnd(7)} gross errors ${gross}%   voicing wrong ${vde}%   fine error ${fine} cents`,
  );
}
