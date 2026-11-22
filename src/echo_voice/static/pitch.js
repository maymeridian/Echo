/*
src/echo_voice/static/pitch.js
Author: maymeridian
Description: Live pitch with YIN (de Cheveigné & Kawahara, JASA 2002), one frame at a time.
             - The cumulative mean normalised difference function, the first dip under a
               threshold, and parabolic interpolation around it.
             - Checked against PTDB-TUG's laryngograph references by tools/check_live_pitch.mjs.
*/

/**
 * The pitch of one frame of sound, or null when it is too quiet or unvoiced.
 * Returns { f0, confidence }: f0 in Hz, and confidence as 1 minus the normalised difference at
 * the period, so above 1 - threshold.
 * `fmin`, `fmax`: the pitch range searched, Hz. `threshold`: how low the normalised difference
 * must dip for a lag to count as the period. `minRms`: below this level (1 being full scale)
 * the frame is too quiet to call.
 */
export function yin(
  frame,
  sampleRate,
  { fmin = 60, fmax = 600, threshold = 0.15, minRms = 0.01 } = {},
) {
  // 1. Too quiet to call.
  const n = frame.length;
  let energy = 0;
  for (let i = 0; i < n; i++) energy += frame[i] * frame[i];
  if (Math.sqrt(energy / n) < minRms) return null;

  // 2. The difference function: how unlike itself the frame is at each lag. The shortest lag
  // tried is at least 2 samples.
  const maxLag = Math.min(Math.floor(sampleRate / fmin), Math.floor(n / 2));
  const minLag = Math.max(2, Math.floor(sampleRate / fmax));
  const width = n - maxLag;
  const d = new Float32Array(maxLag + 1);
  for (let lag = 1; lag <= maxLag; lag++) {
    let sum = 0;
    for (let i = 0; i < width; i++) {
      const diff = frame[i] - frame[i + lag];
      sum += diff * diff;
    }
    d[lag] = sum;
  }

  // 3. The cumulative mean normalised difference: 1 at lag 0 by definition.
  const cmnd = new Float32Array(maxLag + 1);
  cmnd[0] = 1;
  let running = 0;
  for (let lag = 1; lag <= maxLag; lag++) {
    running += d[lag];
    cmnd[lag] = running > 0 ? (d[lag] * lag) / running : 1;
  }

  // 4. The first dip under the threshold, followed to its bottom; none means unvoiced.
  let lag = -1;
  for (let t = minLag; t <= maxLag; t++) {
    if (cmnd[t] < threshold) {
      while (t + 1 <= maxLag && cmnd[t + 1] < cmnd[t]) t++;
      lag = t;
      break;
    }
  }
  if (lag < 0) return null;

  // 5. Parabolic interpolation for a sub-sample lag.
  let refined = lag;
  if (lag > 1 && lag < maxLag) {
    const a = cmnd[lag - 1];
    const b = cmnd[lag];
    const c = cmnd[lag + 1];
    const denom = a - 2 * b + c;
    if (denom > 0) refined = lag + (a - c) / (2 * denom);
  }
  return { f0: sampleRate / refined, confidence: 1 - cmnd[lag] };
}
