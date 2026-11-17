# Changelog

## 1.0.0 (2026-11-16)

The first release. Echo is a local web app for practising a voice impression:
it finds a voice's lines in a video, scores your takes from 0 to 100 the way
listeners judge voices, and says what to work on next. Everything runs on your
own computer.

### Getting a voice

- Paste a YouTube link, upload a recording, or record a clip. A long recording
  is transcribed and split into lines at the pauses.
- Speakers are told apart, and you choose whose voice you're practising.
- The five best lines are picked for you: whole sentences around 3.5 seconds,
  clearly voiced, at the voice's usual pitch. More are suggested, and you can
  pick by hand.
- Music and background are taken out of every line before it is scored.

### Scoring

- Six parts make the score: voice, pitch, melody, resonance, articulation and
  rhythm. About 90 is the real person on another microphone; about 20 is a
  stranger saying the same line.
- The voice part predicts the rating listeners would give the pair.
- Resonance is measured at one formant ceiling per speaker, so a reading
  moves when your voice does and not when the tracker changes its mind.
- A gender read shows beside the parts and isn't counted in the score.
- A line whose pitch can't be trusted (creaky voice, an octave slip) isn't
  scored on pitch or melody.

### Practising

- A coach picks one part to work on after every take and stays on it until
  it's close.
- The line is shown phrase by phrase, with words said in one breath kept
  together. Click a phrase to hear the target say it, then you.
- The trainer works on pitch, resonance or articulation one phrase at a time,
  with a map of pitch against resonance for moving one while holding the
  other.
- While you speak, your pitch is drawn over the target's and a meter shows
  your resonance against it. In the trainer a ring follows your voice on the
  map, and "Try it live" does that without scoring or keeping anything.
- A voice check takes one take on each of several lines, with no retries, and
  averages them into the voice score.
- Every take and trainer attempt is kept, with a progress view for each voice.

### The microphone

- Choose your microphone in the sidebar. Echo remembers it and asks the
  browser for exactly that one, which matters when the system's default is a
  virtual device that hears nothing.
- The microphone stays open between takes, so a browser that asks every time
  it is opened asks once.
- A microphone that gives no sound at all is called out while you record.

### Reliability

- Data files are written whole or not at all, so a crash can't leave half a
  file behind. A damaged file is left out of a list and doesn't stop the rest
  from opening.
- The same audio always gives the same transcript.
- A number such as "12,000" is one word, as written.

### Checked against

- Human judgments: 0.53 per pair and 0.94 per system on the Voice Conversion
  Challenge 2018 listening test.
- Listeners' ratings of real-world clips (VoxSim): strangers ranked at 0.62,
  up from 0.15 with a speaker model alone.
- Pitch against a laryngograph, formants against hand-checked vowels, the
  live pitch and resonance trackers against the app's own measurements, and
  speaker separation on 330 test recordings.

The README has every number and how to reproduce it.

### Known limits

- The calibration's microphone is simulated. The check on real devices is
  still to do.
- Whispered and breathy voices are the hard case: pitch and formants are
  least dependable there, and the live resonance meter hasn't been checked on
  them.
- Lines are split at pauses, not where the speaker changes, so two people
  with no pause between them can share a line.
- English only.
