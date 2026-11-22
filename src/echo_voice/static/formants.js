/*
src/echo_voice/static/formants.js
Author: maymeridian
Description: Live resonance: F1-F3 in the browser, found the way Praat's Burg tracker finds them.
             - Lets the page show where your resonance sits while you speak.
             - A frame of 50 ms is resampled to twice the formant ceiling, pre-emphasised from
               50 Hz, given Praat's Gaussian window, and fitted with ten linear prediction
               coefficients (Burg). The roots of that polynomial are the resonances; the three
               lowest between 50 Hz and 50 Hz under the ceiling are F1-F3. On the same frames
               this lands a median 1% from Praat with no lean either way.
             - Which frames count matters as much as the fit, because typical formants are
               medians over the voiced ones. A frame counts when it is periodic and not much
               quieter than the loudest so far (VOICED, LOUD): the test that best picks the
               frames Praat calls voiced.
             - The page measures the target's audio and the microphone with this same code.
               tools/check_live_resonance.mjs checks it against the app's own measurements.
*/

import { yin } from "./pitch.js";

export const FRAME_SECONDS = 0.05; // what one reading looks at
const PITCH_SECONDS = 2048 / 48000; // what the pitch tracker looks at, as on the microphone
export const VOICED = 0.55; // periodicity from here up is voice
export const LOUD = 0.1; // and at least this share of the loudest frame so far
const ORDER = 10; // two coefficients per formant, five formants under the ceiling
const MARGIN = 50; // Hz kept clear at both ends, as Praat does

// --- RESAMPLING ---

// Band-limited resampling: a windowed sinc, 16 zero crossings each side. Every frame of a
// session has the same length and rates, so the weights are worked out once and kept: the
// sines are most of the cost. At most 8 plans are kept before the cache starts over.
const plans = new Map();

// The resampling weights for frames of `length` samples from `rate` to `newRate`: for each
// output sample, the first input sample it draws on and `width` weights from there.
function plan(length, rate, newRate) {
  const key = `${length}:${rate}:${newRate}`;
  if (!plans.has(key)) {
    const step = rate / newRate;
    const cutoff = newRate / rate;
    const half = Math.ceil(16 * step);
    const n = Math.floor(length / step);
    const width = 2 * half + 1;
    const first = new Int32Array(n);
    const weights = new Float64Array(n * width);
    for (let i = 0; i < n; i++) {
      const centre = i * step;
      first[i] = Math.ceil(centre - half);
      for (let k = 0; k < width; k++) {
        const d = first[i] + k - centre;
        if (Math.abs(d) > half) continue;
        const arg = Math.PI * cutoff * d;
        const sinc = d === 0 ? 1 : Math.sin(arg) / arg;
        // A raised-cosine (Hann) window over the sinc, scaled by the cutoff.
        weights[i * width + k] = sinc * (0.5 + 0.5 * Math.cos((Math.PI * d) / half)) * cutoff;
      }
    }
    if (plans.size >= 8) plans.clear();
    plans.set(key, { n, width, first, weights });
  }
  return plans.get(key);
}

// `x` taken down from `rate` to `newRate`, as a new Float64Array (a copy when it isn't lower).
function resample(x, rate, newRate) {
  if (newRate >= rate) return Float64Array.from(x);
  const { n, width, first, weights } = plan(x.length, rate, newRate);
  const y = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const from = first[i];
    const base = i * width;
    const end = Math.min(width, x.length - from);
    let sum = 0;
    for (let k = Math.max(0, -from); k < end; k++) sum += x[from + k] * weights[base + k];
    y[i] = sum;
  }
  return y;
}

// --- LINEAR PREDICTION ---

// Burg's method: the coefficients a[1..order] with x[n] ≈ Σ a[k] x[n-k], or null for a silent
// or too short frame.
function burg(x, order) {
  const n = x.length;
  let power = 0;
  for (let j = 0; j < n; j++) power += x[j] * x[j];
  if (!(power > 0) || n <= order + 1) return null;
  const a = new Float64Array(order + 1);
  const before = new Float64Array(order + 1);
  const b1 = new Float64Array(n + 1);
  const b2 = new Float64Array(n + 1);
  b1[1] = x[0];
  b2[n - 1] = x[n - 1];
  for (let j = 2; j <= n - 1; j++) b1[j] = b2[j - 1] = x[j - 1];
  for (let i = 1; i <= order; i++) {
    let num = 0;
    let den = 0;
    for (let j = 1; j <= n - i; j++) {
      num += b1[j] * b2[j];
      den += b1[j] * b1[j] + b2[j] * b2[j];
    }
    if (!(den > 0)) return null;
    a[i] = (2 * num) / den;
    for (let j = 1; j < i; j++) a[j] = before[j] - a[i] * before[i - j];
    if (i < order) {
      for (let j = 1; j <= i; j++) before[j] = a[j];
      for (let j = 1; j <= n - i - 1; j++) {
        b1[j] -= before[i] * b2[j];
        b2[j] = b2[j + 1] - before[i] * b1[j + 1];
      }
    }
  }
  return a;
}

// Every root of z^m + c[1] z^(m-1) + … + c[m] at once (Durand-Kerner), as { re, im }, or null
// when they don't settle within 300 rounds.
function roots(c) {
  const m = c.length - 1;
  const re = new Float64Array(m);
  const im = new Float64Array(m);
  // 1. The first guesses: spread round a circle of radius 0.9, turned 0.4 rad so none
  //    is real.
  for (let k = 0; k < m; k++) {
    const angle = (2 * Math.PI * k) / m + 0.4;
    re[k] = 0.9 * Math.cos(angle);
    im[k] = 0.9 * Math.sin(angle);
  }
  // 2. Move each guess by the polynomial there over its distances to the others, until
  //    none moves.
  for (let round = 0; round < 300; round++) {
    let moved = 0;
    for (let i = 0; i < m; i++) {
      // The polynomial at this guess (Horner)...
      let pr = 1;
      let pi = 0;
      for (let k = 1; k <= m; k++) {
        const r = pr * re[i] - pi * im[i] + c[k];
        pi = pr * im[i] + pi * re[i];
        pr = r;
      }
      // ...over the product of its distances to the other guesses.
      let dr = 1;
      let di = 0;
      for (let j = 0; j < m; j++) {
        if (j === i) continue;
        const xr = re[i] - re[j];
        const xi = im[i] - im[j];
        const r = dr * xr - di * xi;
        di = dr * xi + di * xr;
        dr = r;
      }
      const size = dr * dr + di * di;
      if (!(size > 0)) continue;
      const qr = (pr * dr + pi * di) / size;
      const qi = (pi * dr - pr * di) / size;
      re[i] -= qr;
      im[i] -= qi;
      moved = Math.max(moved, Math.abs(qr) + Math.abs(qi));
    }
    if (moved < 1e-11) return { re, im };
  }
  return null; // didn't settle: no reading for this frame
}

// --- ONE FRAME ---

// The last FRAME_SECONDS of a frame, or all of it when it is no longer than that.
const latest = (frame, sampleRate) => {
  const want = Math.round(FRAME_SECONDS * sampleRate);
  return frame.length > want ? frame.subarray(frame.length - want) : frame;
};

// F1-F3 in Hz for one frame of sound (its last 50 ms are used), or null.
export function formants(frame, sampleRate, ceiling = 5500) {
  // 1. Resampled to twice the ceiling; too few samples for the fit give no reading.
  const rate = Math.min(sampleRate, 2 * ceiling);
  const x = resample(latest(frame, sampleRate), sampleRate, rate);
  const n = x.length;
  if (n < 4 * ORDER) return null;

  // 2. Pre-emphasis from 50 Hz.
  const emphasis = Math.exp((-2 * Math.PI * 50) / rate);
  for (let i = n - 1; i > 0; i--) x[i] -= emphasis * x[i - 1];

  // 3. Praat's Gaussian window, which falls to exp(-12) at the edges and is lowered to 0 there.
  const mid = 0.5 * (n + 1);
  const edge = Math.exp(-12);
  for (let i = 0; i < n; i++) {
    const d = i + 1 - mid;
    x[i] *= (Math.exp((-48 * d * d) / ((n + 1) * (n + 1))) - edge) / (1 - edge);
  }

  // 4. The prediction polynomial and its roots.
  const a = burg(x, ORDER);
  if (!a) return null;
  const c = new Float64Array(ORDER + 1);
  c[0] = 1;
  for (let k = 1; k <= ORDER; k++) c[k] = -a[k];
  const found = roots(c);
  if (!found) return null;

  // 5. The three lowest resonances clear of both ends. Only roots above the real axis are
  // taken: one of each conjugate pair; real roots aren't resonances.
  const out = [];
  for (let k = 0; k < ORDER; k++) {
    if (!(found.im[k] > 1e-9)) continue;
    const hz = (Math.atan2(found.im[k], found.re[k]) * rate) / (2 * Math.PI);
    if (hz > MARGIN && hz < rate / 2 - MARGIN) out.push(hz);
  }
  if (out.length < 3) return null;
  out.sort((p, q) => p - q);
  return out.slice(0, 3);
}

// How loud a frame is (RMS) and how periodic, 0 to 1: the highest normalised autocorrelation at
// a lag a voice could have (60-600 Hz), on the sound taken down to 8 kHz. Returns { loud, clear }.
export function voicing(frame, sampleRate) {
  // 1. Down to 8 kHz, with the frame's mean taken out: without that, a DC offset on room noise
  // reads as perfectly periodic (0.9997 with an offset of 0.01).
  const rate = Math.min(sampleRate, 8000);
  const x = resample(latest(frame, sampleRate), sampleRate, rate);
  const n = x.length;
  let mean = 0;
  for (let i = 0; i < n; i++) mean += x[i];
  mean = n ? mean / n : 0;
  for (let i = 0; i < n; i++) x[i] -= mean;

  // 2. The level.
  let power = 0;
  for (let i = 0; i < n; i++) power += x[i] * x[i];

  // 3. The best normalised autocorrelation from a 600 Hz period to a 60 Hz one (or half the
  // frame, if that is shorter).
  const longest = Math.min(Math.floor(rate / 60), n >> 1);
  let clear = 0;
  for (let lag = Math.floor(rate / 600); lag <= longest; lag++) {
    let xy = 0;
    let xx = 0;
    let yy = 0;
    for (let i = 0; i + lag < n; i++) {
      xy += x[i] * x[i + lag];
      xx += x[i] * x[i];
      yy += x[i + lag] * x[i + lag];
    }
    if (xx > 0 && yy > 0) clear = Math.max(clear, xy / Math.sqrt(xx * yy));
  }
  return { loud: n ? Math.sqrt(power / n) : 0, clear };
}

// --- LIVE AND WHOLE RECORDINGS ---

// The microphone, a frame at a time: F1-F3 when the frame is voice, else null.
export class Listener {
  // `ceiling`: the formant ceiling to read this voice at, in Hz.
  constructor(ceiling) {
    this.ceiling = ceiling;
    this.loudest = 0;
    this.quiet = true; // the last frame was too quiet to count, for pitch as well
  }

  // F1-F3 for the latest frame from the microphone, or null when it isn't voice.
  hear(frame, sampleRate) {
    const { loud, clear } = voicing(frame, sampleRate);
    this.loudest = Math.max(this.loudest, loud);
    this.quiet = loud < LOUD * this.loudest;
    if (clear < VOICED || this.quiet) return null;
    return formants(frame, sampleRate, this.ceiling);
  }
}

// Pitch and F1-F3 every `hop` seconds through a whole recording (null where there is none):
// what the page works out once for the target, to hold the microphone against. The pitch is the
// live tracker's (pitch.js), frame for frame as the microphone gets it, because two trackers
// disagree about which frames of a creaky or fading word are voiced, and so about its typical
// pitch. Returns [{ t, loud, f0, f }], f being F1-F3.
export function track(samples, sampleRate, ceiling, { hop = 0.01 } = {}) {
  const wide = Math.round(FRAME_SECONDS * sampleRate);
  const narrow = Math.round(PITCH_SECONDS * sampleRate);
  const half = wide >> 1;
  const frames = [];
  let loudest = 0;
  // 1. Every frame: its level and formants over FRAME_SECONDS, and its pitch over the
  // PITCH_SECONDS that end where the frame does, peak-normalised as on the microphone.
  for (let t = FRAME_SECONDS / 2; Math.round(t * sampleRate) + half <= samples.length; t += hop) {
    const centre = Math.round(t * sampleRate);
    const piece = samples.subarray(centre - half, centre + half);
    const { loud, clear } = voicing(piece, sampleRate);
    loudest = Math.max(loudest, loud);
    const tail = samples.subarray(Math.max(0, centre + half - narrow), centre + half);
    let peak = 0;
    for (let i = 0; i < tail.length; i++) peak = Math.max(peak, Math.abs(tail[i]));
    const f0 = peak > 0 ? (yin(tail.map((v) => v / peak), sampleRate)?.f0 ?? null) : null;
    frames.push({ t, loud, f0, f: clear >= VOICED ? formants(piece, sampleRate, ceiling) : null });
  }
  // 2. Frames under LOUD of the loudest in the whole recording have no reading.
  for (const frame of frames) if (frame.loud < LOUD * loudest) frame.f = frame.f0 = null;
  return frames;
}

// --- COMPARING TWO VOICES ---

// The median of some numbers (the mean of the middle two for an even count).
const median = (values) => {
  const sorted = [...values].sort((p, q) => p - q);
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
};

// The typical F1-F3 over some readings, or null with too few to say.
export function typical(readings, least = 8) {
  const voiced = readings.filter(Boolean);
  if (voiced.length < least) return null;
  return [0, 1, 2].map((k) => median(voiced.map((f) => f[k])));
}

// How much brighter (+) or darker (-) `mine` is than `target`, in percent: the mean shift of F2
// and F3 between two sets of typical formants, as the scorer measures it. F1 is left out: on a
// breathy or high voice the tracker takes the first harmonic for it as often as not.
export function shift(mine, target) {
  if (!mine || !target) return null;
  const semitones = [1, 2].reduce((sum, k) => sum + 12 * Math.log2(mine[k] / target[k]), 0) / 2;
  return (2 ** (semitones / 12) - 1) * 100;
}

// The same shift between two recordings of the same words, with their sounds lined up first, as
// a take's Resonance is measured. Typical formants over a whole line depend on how long each
// speaker dwells on each vowel, and two people never dwell the same; lined up, each vowel is
// held against itself.
//
// `mine` and `target` are readings in time order (voiced frames only). They are aligned by
// dynamic time warping on the formants themselves, each side centred on its own typical values
// so a voice that is brighter throughout still lines up. With `open`, `mine` may stop part-way
// through `target`: a take in progress. At most `most` readings a side are used (every so many
// is kept), and with fewer than 12 on either side there is no reading.
export function alignedShift(mine, target, { open = false, most = 240 } = {}) {
  // 1. The voiced readings of each side, thinned, in semitones.

  // Every step-th voiced reading, so there are no more than `most`.
  const thin = (frames) => {
    const voiced = frames.filter(Boolean);
    const step = Math.max(1, Math.ceil(voiced.length / most));
    return voiced.filter((_, i) => i % step === 0);
  };
  const a = thin(mine);
  const b = thin(target);
  const n = a.length;
  const m = b.length;
  if (n < 12 || m < 12) return null;
  // Each reading's F1-F3 in semitones.
  const semis = (frames) => frames.map((f) => f.map((hz) => 12 * Math.log2(hz)));
  const sa = semis(a);
  const sb = semis(b);

  // 2. The cost of every path through the two, each side measured from its own typical values.

  // The median of each formant over some rows.
  const centre = (rows) => [0, 1, 2].map((k) => median(rows.map((r) => r[k])));
  const ca = centre(sa);
  const cb = centre(sb);
  const WEIGHT = [1, 1, 0.5]; // F3 says less about which vowel it is
  // How unlike reading i of `mine` is to reading j of `target`.
  const cost = (i, j) => {
    let sum = 0;
    for (let k = 0; k < 3; k++) sum += WEIGHT[k] * Math.abs(sa[i][k] - ca[k] - (sb[j][k] - cb[k]));
    return sum;
  };
  const total = new Float64Array(n * m);
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < m; j++) {
      let best = 0;
      if (i || j) {
        best = Infinity;
        if (i && j) best = total[(i - 1) * m + j - 1];
        if (i) best = Math.min(best, total[(i - 1) * m + j]);
        if (j) best = Math.min(best, total[i * m + j - 1]);
      }
      total[i * m + j] = best + cost(i, j);
    }
  }

  // 3. Where the path ends: the last frame of both, or wherever `mine` has got to (the end with
  // the least cost per step).
  let j = m - 1;
  if (open) {
    let least = Infinity;
    for (let end = 0; end < m; end++) {
      const mean = total[(n - 1) * m + end] / (n + end + 1);
      if (mean < least) {
        least = mean;
        j = end;
      }
    }
  }

  // 4. Back along the cheapest path, taking the gap in each formant at every step.
  const gaps = [[], [], []];
  let i = n - 1;
  while (i >= 0 && j >= 0) {
    for (let k = 0; k < 3; k++) gaps[k].push(sa[i][k] - sb[j][k]);
    const diagonal = i && j ? total[(i - 1) * m + j - 1] : Infinity;
    const up = i ? total[(i - 1) * m + j] : Infinity;
    const left = j ? total[i * m + j - 1] : Infinity;
    if (!i && !j) break;
    if (diagonal <= up && diagonal <= left) {
      i--;
      j--;
    } else if (up <= left) i--;
    else j--;
  }

  // 5. The median gap in F2 and F3, as the scorer reads Resonance, in percent.
  const semitones = (median(gaps[1]) + median(gaps[2])) / 2;
  return (2 ** (semitones / 12) - 1) * 100;
}
