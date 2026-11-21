# Changelog

## 1.1.1 (2026-11-20)

The source is now public, under the MIT licence. This release fixes what a
review of the whole code found before it went out.

### Safety

- Other websites can no longer use Echo's server. A page on another site
  could make your browser queue downloads or delete voices, and with a DNS
  trick read the API, your takes' audio included. Echo now answers only to
  its own address, and only its own page can change anything.
- An upload over the size limit is refused before it is read in.
- Live streams are refused, since they have no end to wait for, and a
  download stops at 2 GB.

### Recording

- Pressing Record or Space again while the microphone was still opening
  started a second recording that nothing could stop, and every later take
  then ended within a quarter of a second. Nothing new can start while a take
  is starting or being scored.
- Leaving a line in the middle of a take drops the take. Before, it was scored
  against whichever line was open when it stopped.
- If the microphone is unplugged during a take, the take ends and is scored
  with what was recorded. Before, the page hung.
- A take that begins with a moment of digital silence, as a device waking up
  or a Bluetooth headset switching mode gives, no longer runs on in a noisy
  room.
- The live meters no longer take a DC offset on the microphone for a voice.
- Errors from the server read as words, not "[object Object]".

### Your data

- Saved audio is no longer trimmed a second time when it is read back. That
  moved a line's ends by up to 60 ms against its word timings, and by 160 ms
  on a clip that starts in digital silence. Saved takes are scored again the
  first time 1.1.1 starts, as with 1.1.0.
- Re-scoring carries on past a take whose audio is missing. A voice check is
  worked out again only once all its takes have been.
- Choosing another speaker in a recording no longer deletes lines you have
  trained on in the trainer but not yet taken.
- A name you give a voice while its video is downloading is kept. Before, the
  channel's name replaced it.
- Two changes at once to a line or a voice no longer undo each other.
- A recording's list of lines is written whole, so an interrupted job can't
  leave half a file behind.
- Splitting off a speaker marks the copy ready only once it is complete.
- Auto-pick and Add can't be clicked twice while they run.

### Known limits

- With the microphone open and no one speaking, a steady hum can show as a
  pitch in the live readout. Echo can't yet tell a hum from a very soft voice
  without hiding the soft voice too.

## 1.1.0 (2026-11-17)

Every measurement was audited against recordings whose right answer is known
and against 70 real takes, and the parts that failed were rebuilt. Scores from
1.0.0 and 1.1.0 are on different scales, so saved takes and voice checks are
scored again the first time 1.1.0 starts.

### What the audit found

- **Articulation heard the voice, not the sounds.** On the calibration
  recordings a stranger saying the right line scored the same as the right
  person saying another sentence. A take against a breathy voice read 0
  whatever was said.
- **Resonance could not be trusted on a breathy or high voice.** The tracker
  took the first harmonic for the first formant as often as not. The line was
  read at one formant ceiling and the take at another, and read that way a
  line was not level with a copy of itself. Real takes against a breathy
  voice sat near zero whatever was done (a median 13).
- **Rhythm and Resonance moved with the click on Stop.** A second of silence
  and a click after the last word changed Rhythm by 49 points and Resonance
  by 38 for one take in ten. Half of Rhythm was a measure that barely told a
  retake from a stranger.
- **Pitch and Voice held up.** The same changes moved Pitch by nothing and
  Voice by under 1 point for a typical take.

### Scoring

- Takes are lined up with the target by what a speech recogniser hears in
  each, not by their spectra. A line's words land a median 50 ms from where
  the take's own transcript puts them, against 80 ms before, and over 200 ms
  off for 6% of words against 16%.
- **Articulation** compares those sounds, whoever is speaking. About 90 is
  the real person saying the line again, a stranger reading the same line
  scores around 80, and a different sentence about 20.
- **Resonance** is read from the second and third formants, with both
  recordings at one formant ceiling and at four more around it.
- **Rhythm:** pace is still the length from first to last voiced sound, now
  without a stray blip at either end. Timing inside the line is read at ten
  stretches of it, and tells a retake from a stranger far better than it did
  (0.71 from 0.59, where 0.5 is a coin toss).
- A short sound on its own at either end of a recording, such as the click on
  Stop, is cut before anything is measured.
- From the third take on a line, the Resonance row also says where your last
  three takes sit together, which is steadier than one take's reading.
- **Vocal fry** is a new read beside the parts, not counted in the score: how
  much of the target's voiced sound drops into a low creak, and how much of
  yours. It explains lines that used to defeat Pitch.

### Recording

- **A take stops by itself** about a second after your last word. The quiet
  it waits for comes from the line's own pauses, and the button and Space
  still stop it at any moment. A scored attempt in the trainer stops the same
  way. Switch it off under the microphone list if you want to stop by hand.

### Lines

- A suggested line that starts in the middle of a sentence is widened back to
  the sentence's start when it is added. Auto-pick leaves out one it can't
  repair.

### Fixed

- The live ring's tail in the trainer no longer starts with a line from the
  middle of the map. It begins once both pitch and resonance have a reading.

### Known limits

- Resonance on one short take is still a rough reading. Now that takes are
  no longer pinned near zero, small things move it: cutting 30 ms off a take
  moves it 3 points for a typical take and 19 for one in ten, and faint hiss
  8 and 42. Judge it over several takes.
- On the VCC 2018 listening test the headline now agrees less with
  listeners: 0.48 per pair and 0.90 per system, from 0.53 and 0.94. Those
  listeners judged only whether two clips were the same speaker, and
  Articulation no longer hears the speaker. The Voice part, which answers
  that question, is unchanged at 0.55 and 0.94. The README has the detail.
- The calibration's microphone is still simulated, and the vocal-fry read and
  the new Articulation have not been checked against listeners.

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
