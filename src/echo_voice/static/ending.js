// Hearing that someone has finished speaking, so a take can stop by itself.
//
// The page reads the microphone level many times a second (app.js, Recorder).
// Each reading goes to `Ending.hear`, and `over` turns true once enough has
// been said and it has been quiet for long enough since.
//
// What counts as speaking is judged against the room, not a fixed level: a
// reading is speech when it is MARGIN dB above the noise floor (the 10th
// percentile of the readings so far) and no more than BELOW_LOUDEST dB under
// the loudest speech (the 95th). A soft or breathy voice is still far above a
// quiet room, and a fan or a hiss raises the floor with it. Every reading is
// judged again whenever the floor moves, so a take that starts on the first
// word, with no quiet before it to measure, is read right once its tail comes.
//
// How long the quiet must last comes from the line being said: its longest
// pause between words, with room for a slower speaker (`waitFor`). Someone who
// has said less than half of it gets longer (`patient`), so a false start or a
// breath before the line does not end the take.

export const MARGIN = 10; // dB above the noise floor
export const BELOW_LOUDEST = 35; // dB under the loudest speech
export const LEAST = 0.25; // s of speech before any pause can end a take
const LOWEST = -100; // dB; digital silence

// The quiet that ends a line, in seconds, from the gaps between its words.
export function waitFor(words) {
  let longest = 0;
  for (let i = 1; i < (words?.length ?? 0); i++) longest = Math.max(longest, words[i].start - words[i - 1].end);
  return Math.min(2, Math.max(0.9, 0.7 + 1.5 * longest));
}

// The seconds of speech in a line: its words, without the gaps between them.
export const spoken = (words) => (words ?? []).reduce((sum, w) => sum + Math.max(0, w.end - w.start), 0);

export class Ending {
  // `wait`: s of quiet that end the take once `enough` s of speech were heard.
  // `patient`: the quiet it takes before that.
  constructor({ wait = 1, enough = 0, patient = Math.max(2.5, 2 * wait) } = {}) {
    Object.assign(this, { wait, enough, patient });
    this.times = [];
    this.levels = [];
    this.counts = new Uint32Array(1 - LOWEST); // how many readings at each dB, for the percentiles
    this.speech = 0; // s of speech so far
    this.quiet = 0; // s since the last of it
    this.over = false;
    this.started = false; // a reading other than digital silence has come in
  }

  percentile(share) {
    let left = share * this.levels.length;
    for (let db = 0; db < this.counts.length; db++) {
      left -= this.counts[db];
      if (left < 0) return db + LOWEST;
    }
    return 0;
  }

  // `rms`: the level of the latest stretch of sound, 1 being full scale; `t`: when, in seconds.
  hear(rms, t) {
    const db = Math.max(LOWEST, Math.min(0, Math.round(20 * Math.log10(Math.max(rms, 1e-9)))));
    // Digital silence before anything else is a device starting up (or a
    // Bluetooth headset switching mode), not the room: counted in, it set the
    // floor so low that a fan read as speech and the take ran on. Digital
    // silence later on is a microphone's noise gate between words, and does
    // say how quiet the room is, so it counts.
    if (db === LOWEST && !this.started) return this.over;
    this.started = true;
    this.times.push(t);
    this.levels.push(db);
    this.counts[db - LOWEST]++;
    const threshold = Math.max(this.percentile(0.1) + MARGIN, this.percentile(0.95) - BELOW_LOUDEST);
    let speech = 0;
    let last = null;
    for (let i = 1; i < this.levels.length; i++) {
      if (this.levels[i] <= threshold) continue;
      speech += Math.min(0.1, this.times[i] - this.times[i - 1]);
      last = this.times[i];
    }
    this.threshold = threshold;
    this.speech = speech;
    this.quiet = last == null ? 0 : t - last;
    this.over = speech >= LEAST && this.quiet >= (speech >= this.enough ? this.wait : this.patient);
    return this.over;
  }
}
