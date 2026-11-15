// Live pitch with YIN (de Cheveigné & Kawahara, JASA 2002): the cumulative
// mean normalised difference function, the first dip under a threshold, and
// parabolic interpolation around it. Checked against PTDB-TUG's laryngograph
// references by tools/check_live_pitch.mjs.

export function yin(frame, sampleRate, { fmin = 60, fmax = 600, threshold = 0.15, minRms = 0.01 } = {}) {
  const n = frame.length;
  let energy = 0;
  for (let i = 0; i < n; i++) energy += frame[i] * frame[i];
  if (Math.sqrt(energy / n) < minRms) return null; // too quiet to call

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
  // Cumulative mean normalised difference.
  const cmnd = new Float32Array(maxLag + 1);
  cmnd[0] = 1;
  let running = 0;
  for (let lag = 1; lag <= maxLag; lag++) {
    running += d[lag];
    cmnd[lag] = running > 0 ? (d[lag] * lag) / running : 1;
  }
  let lag = -1;
  for (let t = minLag; t <= maxLag; t++) {
    if (cmnd[t] < threshold) {
      while (t + 1 <= maxLag && cmnd[t + 1] < cmnd[t]) t++; // walk to the bottom of the dip
      lag = t;
      break;
    }
  }
  if (lag < 0) return null; // unvoiced
  // Parabolic interpolation for a sub-sample lag.
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
