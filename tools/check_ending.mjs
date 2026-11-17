// Check the page's end-of-speech detector (static/ending.js) on saved takes.
//
//   node tools/check_ending.mjs <data dir> [noise dB under the speech peak, default 45]
//
// Every take in the data folder is played to the detector as the page would
// hear it: a lead-in of room noise, the take, then four seconds of room noise,
// read in 43 ms stretches 60 times a second. The quiet it waits for comes from
// the take's own line (`waitFor`), as on the page. A take must not be ended
// before its last word, and should be ended soon after.
//
// Where the last word ends is read from the take's record: the scorer lines
// the take up with the target and keeps each word's place in the take. That
// is the reference, and not the end of the file, because a take recorded
// before this existed ends with whatever came after the line: a breath, and
// the click on Stop.
//
// Saved takes are trimmed of their lead-in and tail, cleaned of background
// and scaled to a 0.9 peak, so the room is put back as white noise at a
// chosen level under that peak.

import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { Ending, spoken, waitFor } from "../src/echo_voice/static/ending.js";

function readWav(path) {
  const buf = readFileSync(path);
  let offset = 12;
  let rate = 0;
  while (offset < buf.length) {
    const id = buf.toString("ascii", offset, offset + 4);
    const size = buf.readUInt32LE(offset + 4);
    if (id === "fmt ") rate = buf.readUInt32LE(offset + 12);
    else if (id === "data") {
      const samples = new Float32Array(size / 2);
      for (let i = 0; i < samples.length; i++) samples[i] = buf.readInt16LE(offset + 8 + i * 2) / 32768;
      return { rate, samples };
    }
    offset += 8 + size + (size % 2);
  }
  throw new Error(`${path}: no data chunk`);
}

// A small generator with a seed, so every run hears the same room.
function noiseMaker(seed) {
  let s = seed >>> 0;
  const uniform = () => ((s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 4294967296);
  return () => Math.sqrt(-2 * Math.log(uniform() + 1e-12)) * Math.cos(2 * Math.PI * uniform());
}

const root = process.argv[2];
const under = Number(process.argv[3] ?? 45);
const LEAD = 0.4;
const TAIL = 4;
const quantile = (xs, q) => [...xs].sort((a, b) => a - b)[Math.min(xs.length - 1, Math.floor(q * xs.length))];

let takes = 0;
const early = [];
const never = [];
const delays = [];
for (const id of readdirSync(join(root, "targets"))) {
  const folder = join(root, "targets", id);
  if (!existsSync(join(folder, "takes"))) continue;
  const meta = JSON.parse(readFileSync(join(folder, "meta.json"), "utf8"));
  const words = meta.word_times ?? [];
  const wait = waitFor(words);
  const enough = 0.5 * spoken(words);
  for (const file of readdirSync(join(folder, "takes")).filter((f) => f.endsWith(".wav"))) {
    const { rate, samples } = readWav(join(folder, "takes", file));
    const noise = noiseMaker(takes + 1);
    const level = 0.9 * 10 ** (-under / 20);
    const lead = Math.round(LEAD * rate);
    const heard = new Float32Array(lead + samples.length + Math.round(TAIL * rate));
    for (let i = 0; i < heard.length; i++) heard[i] = level * noise() + (i >= lead && i < lead + samples.length ? samples[i - lead] : 0);
    const record = JSON.parse(readFileSync(join(folder, "takes", file.replace(/\.wav$/, ".json")), "utf8"));
    const ends = (record.words ?? []).map((w) => w.take_end).filter((x) => x != null);
    const end = LEAD + (ends.length ? Math.max(...ends) : samples.length / rate);
    const span = Math.round((2048 / 48000) * rate);
    const ending = new Ending({ wait, enough });
    let stopped = null;
    for (let t = span / rate; t < heard.length / rate; t += 1 / 60) {
      const to = Math.round(t * rate);
      let sum = 0;
      for (let i = to - span; i < to; i++) sum += heard[i] * heard[i];
      if (ending.hear(Math.sqrt(sum / span), t)) {
        stopped = t;
        break;
      }
    }
    takes++;
    const name = `${id}/${file} (${(samples.length / rate).toFixed(1)} s, waits ${wait.toFixed(2)} s)`;
    if (stopped == null) never.push(name);
    else if (stopped < end) early.push(`${name}: stopped ${(end - stopped).toFixed(2)} s before its last word ended`);
    else delays.push(stopped - end);
  }
}
console.log(`${takes} takes, room noise ${under} dB under the peak`);
console.log(`ended early: ${early.length}`);
for (const line of early) console.log(`  ${line}`);
console.log(`never ended: ${never.length}`);
for (const line of never) console.log(`  ${line}`);
if (delays.length) console.log(`ended after the last word by: median ${quantile(delays, 0.5).toFixed(2)} s, 90% within ${quantile(delays, 0.9).toFixed(2)} s, at most ${Math.max(...delays).toFixed(2)} s`);
