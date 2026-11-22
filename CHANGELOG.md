# Changelog

This file lists the changes in each release of Echo, newest first.

## Unreleased

The code and documentation are reorganised; behaviour is unchanged. Every
saved take, trainer attempt and voice check was scored again from its audio
with the previous and the new code, and every result and every API response
was byte-identical. All 86 tests pass before and after.

### Code

- Every source file opens with a header giving its path, author and purpose.
  Every function and class has a docstring, and long files are divided into
  named sections.
- Lines are at most 100 characters; a statement that does not fit puts one
  item per line. `ruff format` and `ruff check`, configured in
  `pyproject.toml`, enforce both.
- Duplicated code is merged into shared helpers, and unused code is removed.

### Documentation

- The README, the user guide (`docs/guide.md`) and the method
  (`docs/method.md`) are rewritten in a formal register, with terms defined on
  first use, numbered tables and lists of references.
- The README's "Install and run" heading restores the project page's link to
  it.

## 1.1.1 (2026-11-20)

The source code is now public, under the MIT licence. This release fixes the
problems that a review of the whole code found before publication.

### Security

- Other websites can no longer use Echo's server. Previously, a page on
  another site could make the browser queue downloads or delete voices, and,
  through DNS rebinding, read the application programming interface (API),
  including the audio of takes. Echo now answers only requests made to its own
  address, and only its own page can change anything.
- An upload over the size limit is refused before it is read in.
- Live streams are refused, because they have no end to wait for, and a
  download stops at 2 GB.

### Recording

- Pressing Record or Space a second time while the microphone was still
  opening started a second recording that nothing could stop, after which
  every take ended within a quarter of a second. Nothing new can now start
  while a take is starting or being scored.
- Leaving a line in the middle of a take now discards the take. Previously,
  the take was scored against whichever line was open when it stopped.
- If the microphone is unplugged during a take, the take now ends and is
  scored with what was recorded. Previously, the page stopped responding.
- A take that begins with a moment of digital silence, as produced when a
  device wakes up or a Bluetooth headset switches mode, no longer fails to
  stop in a noisy room.
- The live meters no longer mistake a DC offset (a constant shift in the
  microphone's signal) for a voice.
- Errors from the server are shown as text, not as "[object Object]".

### Saved data

- Saved audio is no longer trimmed a second time when it is read back. The
  second trim moved a line's ends by up to 60 ms relative to its word timings,
  and by 160 ms on a clip that starts in digital silence. Saved takes are
  scored again the first time 1.1.1 starts, as with 1.1.0.
- Rescoring continues past a take whose audio is missing. A voice check is
  recalculated only once all its takes have been rescored.
- Choosing another speaker in a recording no longer deletes lines that have
  been practised in the trainer but not yet in a take.
- A name given to a voice while its video is downloading is now kept.
  Previously, the channel's name replaced it.
- Two simultaneous changes to a line or a voice no longer undo each other.
- A recording's list of lines is written whole, so an interrupted job cannot
  leave half a file behind.
- Splitting off a speaker marks the copy as ready only once it is complete.
- Auto-pick and Add cannot be clicked a second time while they run.

### Known limitations

- With the microphone open and no one speaking, a steady hum can appear as a
  pitch in the live readout. Echo cannot yet tell a hum from a very soft voice
  without also hiding the soft voice.

## 1.1.0 (2026-11-17)

Every measurement was audited against recordings whose correct answer is
known and against 70 real takes, and the parts that failed were rebuilt.
Scores from 1.0.0 and 1.1.0 are on different scales, so saved takes and voice
checks are scored again the first time 1.1.0 starts.

### Audit findings

- **Articulation responded to the voice, not to the speech sounds.** On the
  calibration recordings, a stranger saying the correct line scored the same
  as the correct person saying a different sentence. A take against a breathy
  voice scored 0 whatever was said.
- **Resonance was unreliable on a breathy or high voice.** The formant tracker
  took the first harmonic for the first formant as often as it found the
  formant. The line was read at one formant ceiling (the upper frequency limit
  of the formant search) and the take at another, and read that way a line did
  not score level with a copy of itself. Real takes against a breathy voice
  scored near zero whatever was done (a median of 13).
- **Rhythm and Resonance changed with the click on Stop.** A second of silence
  and a click after the last word changed Rhythm by 49 points and Resonance by
  38 points for one take in ten. Half of Rhythm was a measure that barely
  distinguished a retake from a stranger.
- **Pitch and Voice were robust.** The same changes moved Pitch by nothing and
  Voice by under 1 point for a typical take.

### Scoring

- Takes are aligned with the target by what a speech recogniser hears in
  each, not by their spectra. A line's words now land a median 50 ms from
  where the take's own transcript places them, against 80 ms before, and more
  than 200 ms off for 6% of words, against 16% before.
- Articulation compares those speech sounds, whoever is speaking. The real
  person saying the line again scores about 90, a stranger reading the same
  line around 80, and a different sentence about 20.
- Resonance is read from the second and third formants, with both recordings
  read at one formant ceiling and at four more ceilings around it.
- In Rhythm, pace is still the length from the first to the last voiced
  sound, now without a stray blip at either end. Timing within the line is read at
  ten stretches of it, and distinguishes a retake from a stranger far better
  than before: an area under the curve (AUC) of 0.71, up from 0.59, where 0.5
  is chance.
- A short isolated sound at either end of a recording, such as the click on
  Stop, is cut before anything is measured.
- From the third take on a line, the Resonance row also shows where the last
  three takes lie together, which is steadier than the reading from one take.
- Vocal fry is a new read beside the parts, not counted in the score: the
  share of the target's voiced sound that drops into a low creak, and the same
  share for the take. It explains lines on which Pitch could not previously be
  matched.

### Recording

- A take stops by itself about a second after the last word. The silence it
  waits for is derived from the line's own pauses, and the button and Space
  still stop it at any moment. A scored attempt in the trainer stops in the
  same way. The behaviour can be switched off under the microphone list, to
  stop each take by hand.

### Lines

- A suggested line that starts in the middle of a sentence is widened back to
  the sentence's start when it is added. Auto-pick leaves out a line it cannot
  repair.

### Fixes

- The tail of the live ring in the trainer no longer starts with a line from
  the middle of the map. It begins once both pitch and resonance have a
  reading.

### Known limitations

- Resonance on one short take is still a rough reading. Now that takes are no
  longer pinned near zero, small changes move it: cutting 30 ms off a take
  moves it by 3 points for a typical take and by 19 points for one take in
  ten, and faint hiss by 8 and 42 points. Judge it over several takes.
- On the Voice Conversion Challenge 2018 (VCC 2018) listening test, the
  headline score now agrees less with listeners: a Pearson correlation of 0.48
  per pair and 0.90 per system, down from 0.53 and 0.94. Those listeners
  judged only whether two clips were of the same speaker, and Articulation no
  longer responds to the speaker. The Voice part, which answers that question,
  is unchanged at 0.55 and 0.94. The details are in
  [docs/method.md](docs/method.md).
- The calibration's microphone is still simulated, and neither the vocal-fry
  read nor the new Articulation has been checked against listeners.

## 1.0.0 (2026-11-16)

The first release. Echo is a local web app for practising a voice impression:
it finds a voice's lines in a video, scores takes from 0 to 100 in the way
listeners judge voices, and states what to work on next. Everything runs on
the user's own computer.

### Creating a voice

- A voice can be created from a pasted YouTube link, an uploaded recording or
  a recorded clip. A long recording is transcribed and split into lines at its
  pauses.
- Speakers are told apart, and the user chooses whose voice to practise.
- The five best lines are picked automatically: whole sentences of around
  3.5 seconds, clearly voiced, at the voice's usual pitch. More lines are
  suggested, and lines can also be picked by hand.
- Music and background sound are removed from every line before it is scored.

### Scoring

- The score is made of six parts: voice, pitch, melody, resonance,
  articulation and rhythm. The real person on another microphone scores about
  90; a stranger saying the same line scores about 20.
- The voice part predicts the rating listeners would give the pair.
- Resonance is measured at one formant ceiling per speaker, so a reading
  changes when the voice changes and not when the tracker changes its choice
  of ceiling.
- A gender read is shown beside the parts and is not counted in the score.
- A line whose pitch cannot be trusted (creaky voice, an octave error) is not
  scored on pitch or melody.

### Practising

- A coach picks one part to work on after every take and stays on it until
  the part is close.
- The line is shown phrase by phrase, with words said in one breath kept
  together. Clicking a phrase plays the target saying it, then the take.
- The trainer works on pitch, resonance or articulation one phrase at a time,
  with a map of pitch against resonance for practising moving one while
  holding the other.
- During speech, the user's pitch is drawn over the target's, and a meter
  shows the user's resonance against the target's. In the trainer a ring
  follows the voice on the map, and "Try it live" does the same without
  scoring or keeping anything.
- A voice check takes one take on each of several lines, with no retries, and
  averages them into the voice score.
- Every take and trainer attempt is kept, with a progress view for each voice.

### Microphone

- The microphone is chosen in the sidebar. Echo remembers the choice and asks
  the browser for exactly that microphone, which matters when the system's
  default is a virtual device that hears nothing.
- The microphone stays open between takes, so a browser that asks for
  permission every time the microphone is opened asks only once.
- A microphone that gives no sound at all is reported during recording.

### Reliability

- Data files are written whole or not at all, so a crash cannot leave half a
  file behind. A damaged file is left out of its list and does not prevent the
  rest from opening.
- The same audio always gives the same transcript.
- A number such as "12,000" is treated as one word, as written.

### Validation

- Human judgments: a Pearson correlation of 0.53 per pair and 0.94 per system
  on the Voice Conversion Challenge 2018 listening test.
- Listeners' ratings of real-world clips (VoxSim): on pairs of different
  speakers, a rank correlation of 0.62 with the ratings, up from 0.15 with a
  speaker model alone.
- Pitch was checked against a laryngograph (an instrument that measures
  vocal-fold contact directly), formants against hand-checked vowels, the live
  pitch and resonance trackers against the app's own measurements, and speaker
  separation on 330 test recordings.

Every number, and how to reproduce it, is in [docs/method.md](docs/method.md).

### Known limitations

- The calibration's microphone is simulated. The check on real devices has
  not yet been done.
- Whispered and breathy voices are the difficult case: pitch and formants are
  least dependable there, and the live resonance meter has not been checked on
  them.
- Lines are split at pauses, not where the speaker changes, so two people
  speaking with no pause between them can share a line.
- Only English is supported.
