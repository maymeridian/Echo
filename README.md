# Echo

Practise a voice impression and get closer with every take. Named after Echo,
the nymph of Greek myth who could only speak in other people's words.

Version 1.0.0. [CHANGELOG.md](CHANGELOG.md) says what's in it.

Paste a link to a video of the voice you're imitating, and Echo finds its
lines and picks a handful worth practising. Record yourself saying a line and
get a 0–100 match with a breakdown of what to change: voice identity, pitch,
melody, resonance, articulation and rhythm. Every take is kept, so you can see
whether you're getting closer. A coach picks one thing to work on after each
take. While you speak, a guide draws your pitch over the target's and a meter
shows your resonance against it. Each phrase of the line shows how close you
came, with the target's and yours to play back to back, and a trainer works on
one part of one phrase at a time.

Everything belongs to a **voice**: the person or character you're imitating.
A voice holds the lines you practise and the recordings they came from, such
as a long video of one person talking or a compilation of a game character's voice lines. Paste
a YouTube link or upload the file, and Echo splits it into lines to pick from.

One line can be luck: a take that happens to suit that sentence. So a voice's
**voice score** comes from a **voice check**: one take each on several of its
lines (three or more; five by default), with no retries, averaged.

## Run it

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\echo-voice serve
```

Then open http://127.0.0.1:8000. `echo-voice --version` says which version
this is, and so does the foot of the sidebar. Clips and takes are stored in
`data/`, which is not tracked by git. Its files are written whole or not at
all, so a crash can't leave half a file; a file that can't be read is left out
of its list, and the rest still open. The first run downloads the speaker model (26 MB) to
`~/.cache/echo`; set `ECHO_CACHE` to put it elsewhere.

**The microphone.** The sidebar's Microphone box lists the microphones by name
once the browser has allowed one. Choose yours there: Echo remembers it and
asks the browser for exactly that one from then on. **Test** shows its level
for a few seconds without recording anything.

This matters on a computer with virtual audio devices, which streaming, headset
and VR software add. A browser told to remember the permission stops asking
which microphone and hands over the system's default, and that is often one of
those, hearing nothing. The microphone also stays open between takes, so a
browser that asks every time it's opened (Firefox, unless you tick "Remember
this decision") asks once. It turns off after 15 minutes unused, or with
**Turn off**. If nothing at all is coming from it, the page says so while you
record.

**A take stops by itself.** Once you've said the line, the take ends about a
second after your last word. The page listens to the microphone's level and
waits for a quiet a little longer than the longest pause between the line's
own words (0.9 to 2 seconds). What counts as speaking is judged against the
room, 10 dB above its noise floor, so a soft voice still counts and a fan
doesn't. If you've said less than half the line it waits longer (2.5 s), so a
false start or a breath doesn't end the take. A scored attempt in the trainer
stops the same way. Warming up and "Try it live" run until you stop them, and
the button and Space still stop a take at any moment. Untick **Stop a take
when I finish speaking** under the microphone list to stop by hand.

Played back to the detector with room noise put in, none of 62 saved takes
was ended while the line was still being said, and a take ended a median
1.3 s after its last word (9 in 10 within 2.1 s):

```powershell
node tools\check_ending.mjs data
```

To score two files without the app:

```powershell
.\.venv\Scripts\echo-voice score target.wav take.wav
```

## Long recordings and YouTube videos

**Paste a YouTube link** in the box on the first screen, at the top of home,
or in "+ New voice" (it takes a name or a link). Echo makes a voice for it
straight away and opens its page, where a progress card shows each step.

**Naming.** Once the video is known, the voice is named after its
**channel**, which for a YouTuber is the person. A second video from a channel
that already has a voice joins that voice, with a few of its own lines picked,
rather than making a second voice with the same name. When the site gives no
channel, the voice is named after the title, without [tags], (asides) or
anything after a " | " or " - ". The page offers the other name once: the
video's title for a channel-named voice (for a fan's compilation of a
character, "Ellie - All Voice Lines | The Last of Us Part II" gives "Ellie"),
or the channel for a title-named one. It stops offering once you rename the
voice yourself.

**Links.** The voice's page shows the link to each video under its name
("▶ Harbour Tales on YouTube: …"), and each recording shows its own. A recording
uploaded as a file can be given the link it came from: "Add its YouTube
link" looks the video up for its title and channel without downloading it.

A link pasted on a voice's own page adds to that voice. An uploaded file over
a minute long goes through the same steps.

A background job does the work:

1. **Download:** links go through yt-dlp, which covers YouTube and most video
   sites.
2. **Read:** the audio is decoded to 16 kHz mono.
3. **Transcribe:** Whisper base.en transcribes it with word timestamps, after
   voice-activity detection drops stretches with no speech (music, mouth
   sounds, silence).
4. **Split:** lines end at full stops and at pauses of 0.7 s or more. Lines
   over 10 s are split at a comma or pause near the middle.
5. **Speakers:** lines are grouped by who's speaking (see below).
6. **Pick:** the voice gets the best lines of its speaker by itself, up to
   five, enough for a voice check (see "Suggested lines" below).

A 25-minute YouTube video took 75 s from pasting the link to five picked lines.

### More than one speaker

Videos often have more than one voice: an interviewer, a second character, a
narrator. Each line of 1.5 s or more gets a speaker embedding, and the lines
are grouped by average-linkage clustering while the average cosine between two
groups is at least 0.4. A group of fewer than 3 lines, or under 5% of them,
isn't called a speaker; its lines join the nearest speaker if they're that
close to it, and are left unsure if not. Speaker 1 is the one with the most
speech, and a new voice is speaker 1.

When a recording has more than one speaker, the voice's page shows **Who's
speaking**: each speaker's lines, typical pitch and a sample to play. **Use
this one** makes the voice that speaker instead. Echo's picks from the old
speaker that you haven't practised are swapped for the new speaker's, and
lines you've practised stay. **Make a separate voice** gives that speaker a
voice of their own, with their best lines picked. In the recording's list each
line is tagged with its speaker, and "other voice" means not the voice's
speaker.

Tested with `tools/validate.py speakers` at the 0.4 threshold:

| Recordings | Speaker count right | Lines to the right speaker |
| --- | --- | --- |
| VCC2018 studio speakers, 1–4 per recording (240) | 100% | 100% |
| VoxCeleb1 interviews from YouTube, 1–3 per recording (90) | 100% | 100% |
| A 25-minute video of one soft-spoken speaker | 1 found | 174 of 175, 1 unsure |

The 25-minute video is one speaker all the way through. It stayed one speaker at every threshold from 0.3
to 0.5. Lower thresholds start merging speakers (at 0.3, 88% of 4-speaker
studio mixes were right); higher ones leave more lines unsure.

**Limits:**
- VoxCeleb clips are about 8 s long, and real lines are often 2–5 s.
- Speakers in different studios or videos are easier to tell apart than two
  people on one microphone. The VCC2018 test, all one studio, covers the
  second case.
- Lines under 1.5 s get no speaker.
The job runs at below-normal priority, keeps going if the page is closed, and
starts again if the computer stops mid-way. A recording processed before
speakers were found gets them when the app next starts, keeping its lines.

Each line has a play button and **Practise**. Practise opens the line in the
trim view, with 1.5 s either side so its ends can be adjusted. Then it goes
through vocal separation like any other clip, so music and background under
the voice don't matter. Lines are tagged when they make poor targets, and
these are hidden until you untick the box:

- **whispered:** under 20% of the words' frames have a pitch. The scorer can't
  compare pitch, melody or resonance without one, so those parts show "–".
- **other voice:** the line doesn't sound like the recording's main voice
  (speaker-embedding cosine under 0.4). This catches the other speakers in a
  video, or a song in the background (see Validation).
- **unclear:** Whisper's mean word confidence is under 0.5. Check the line's
  words after adding it.

The same job can run from a terminal:

```powershell
.\.venv\Scripts\echo-voice import "https://www.youtube.com/watch?v=..." --name "Lighthouse"
.\.venv\Scripts\echo-voice import "C:\Users\me\Downloads\video.mp4"
```

YouTube changes often, and an old yt-dlp stops working. When links start
failing, update it:
`.\.venv\Scripts\python -m pip install -U "yt-dlp[default]"`. YouTube also
needs a JavaScript runtime for yt-dlp; Echo uses Node, Deno, Bun or QuickJS,
whichever is installed.

## Voices and the voice check

The sidebar lists voices, and nothing else. A voice's page has:

- **The voice score.** It comes from the last voice check, with the change
  since the check before and a trend over all checks.
- **The voice at a glance**, and **your habits** across its lines (see below).
- **Lines.** Each line shows its best and last score and its number of takes,
  with a tick for whether the voice check uses it.
- **Suggested lines.** These are the best lines from its recordings that the
  voice doesn't have yet, each with ▶ and a one-click **Add**. **Auto-pick**
  tops the voice up to five. A suggested line:
  - is untagged and has at least three words;
  - is near 3.5 s long;
  - is clearly voiced and sure of its words;
  - is most like the main voice;
  - is a whole sentence;
  - sits within 4 semitones of the voice's median pitch. A line far outside it
    is usually creaky voice or a pitch tracker off by an octave. On the 25-minute
    video this ruled out 19 of 185 untagged lines, including two of the five
    suggested before, at 98–99 Hz against her typical 191.

  "Pick by hand" opens the recording's whole list.

  A line is read again by itself when it is added. If its first word isn't
  the one the recording's transcript starts it with, the transcript missed
  the start of the sentence (on a 25-minute video, the fifth-best line began three words into its sentence). The cut is then widened
  back by up to 0.8 s, never into the line before. Auto-pick leaves out a
  line that still starts mid-sentence, and it isn't suggested again.
- **Add lines.** Paste a link, upload a file (over a minute long, it's split
  into lines; shorter, it becomes one line), or record a clip.
- **Recordings.** Each one, with how many lines were found and how many you've
  added. "Pick lines" opens its list.

**The voice check** walks through the ticked lines on the practice page. You
can listen to each line as often as you like, but you get one take and no
retries, like performing it. The coach, earlier takes and the score stay
hidden until the end. The result shows:

- the **voice score**, the plain mean of the lines' scores;
- each line's score, with the weakest marked;
- each part averaged over the lines;
- whether the lines were **steady** (within 10 points of each other) or
  **uneven**;
- the habits that showed up in every line: the median pitch, brightness and
  pace gaps;
- the part that would lift the voice most.

Every line is on the same calibrated scale (about 90 for the real person on
another mic, about 20 for a stranger), so the mean keeps that meaning. Keep
the same lines ticked between checks, and the checks compare like with like.

Data from before voices existed moves over on its own when the app starts. A
recording's name becomes a voice, and lines go to their recording's voice, or
to the voice their old name started with ("Lighthouse: …").

## Practising

The practice page is built to work on the voice, not just test it, and to
say what to do next at every point.

- **Home.** "Continue" opens the line you practised last, with its next
  focus. Each voice shows its voice score and how many lines it has.
- **The voice at a glance.** Each recording summarises the voice from its
  untagged lines: typical pitch and note, usual range, pace in words a second,
  and how breathy it is. Read speech is voiced on 53–60% of its word frames;
  a soft, breathy voice on about 45%.
- **You against it.** Once you've practised a few of its lines, the same
  card shows your habits across all of them, from your last 10 takes: for
  example "3.0 semitones low (you average 170 Hz, F3), darker (formants 11%
  lower)". Habits that show up in every line are the first to fix.
- **Start with these.** Five suggested lines per recording: untagged, three
  words or more, near 3.5 seconds long, clearly voiced, and most like the main
  voice.
- **First take on a line.** A checklist: listen two or three times, find the
  note (play it, then hum it with Warm up), then record.
- **The note.** "♪ G3" plays a soft tone on the target's typical pitch to hum.
  The guide shows it as a dashed line, and the live readout names notes:
  "You: 170 Hz (F3) · target about 202 Hz (G♯3) — go higher (3.0 st)".
- **Drills.** Each focus comes with a short drill and buttons for it. Pitch:
  play the note, warm up on it. Melody: hum the tune at ½ speed, then speak
  along. Resonance and articulation: play the word furthest off and copy it.
  Rhythm: speak along. **Speak along** plays the target with the live guide
  on, so you can match it as it goes; use headphones.
- **When to move on.** If four takes in a row land within 6 points and none
  beats your best, the coach suggests a fresh line, with a button to open the
  next suggested one. At 85 it calls the line done.
- **The scale.** Each score sits on a bar from a stranger (20) to the real
  person (90), with your best before it.

- **Listen.** Play the target at full, ¾ or ½ speed (slowed, not lowered).
  Play your last take, or both back to back.
- **The guide.** The chart shows the target's pitch through the line, with the
  words underneath and its usual range shaded. While you record, your pitch
  is drawn over it as you speak, lined up with where the target starts, so you
  can follow the melody and timing. After the take your whole contour sits on
  the target's timeline.
- **Words.** After a take each word is coloured by how close it came: green,
  amber or red, on the part the coach is focused on. Click a word to hear the
  target say it, then you, and to read what's off. For example: "“hear”: 3.1
  semitones lower than the target; darker (formants 9% lower)".
- **The coach.** After each take it names one part to work on next, with
  advice and the words furthest off. It picks the part that would lift the
  overall score most: the part's weight times its distance from 100. It stays
  on that part until the part reaches 80, unless another becomes clearly
  bigger (the current one's share drops under 60% of the biggest). Then it
  says how that part moved, for example "resonance: 48 → 66, closer". Voice
  is never the focus, because it follows from the others. You can also pick
  the focus yourself; it stays until you switch back to Auto.
- **Progress.** Each part shows its change since the last take and a small
  trend across all your takes on the line.
- **Warm up.** This records with the live guide but saves nothing: use it to
  find the target's pitch before a scored take.
- **Space** starts a take. It stops by itself when you've finished; Space or
  the button stops it sooner.

The word-by-word numbers are the line's own measures restricted to one word,
scored on the line's curves:

| Measure | What it is |
| --- | --- |
| Pitch | Gap between the two medians |
| Melody | That gap minus the typical gap over the line |
| Resonance | Mean shift of F2 and F3 |
| Articulation | Distance between what a speech recogniser hears in each, along the alignment |

Rhythm isn't scored per word, because the alignment moves word edges by a
frame or two. On a synthetic voice shifted 3.9 semitones, every word reads
3.84–3.90 semitones high, with no melody error; with formants raised 12%,
every word reads 1.8–2.1 semitones brighter. The per-word numbers haven't been
checked against listeners, and a single word is noisier than a whole line. Use
them to find where to listen, not as scores.

## Training one part

Each part in a take's result has **Train ›**, which opens a word-by-word
drill for that part on that line. You hear the target say one word, then say
just that word, and get its score in about a second. The words come
furthest-off first, and a word counts once three attempts in a row score 80
or more. Every attempt is saved with its audio, so training picks up where
it left off; **▶ You** replays your last attempt.

- **Resonance** and **pitch** show where the attempt landed on a meter
  (darker to brighter, ±25% of formant shift; lower to higher, ±6
  semitones), with the band where a word scores 90 or more.
- **Both together** places every attempt on a map of pitch across and
  resonance up and down. Going lower tends to darken the sound and going
  higher to brighten it, so this is how to practise moving one while holding
  the other. The tips follow the combination: to go lower but stay as bright,
  keep the tongue forward and don't drop the jaw or larynx. A word counts
  when both are close.
- **"♪ Its note"** plays the pitch the target uses on that word.
- **Live.** While the microphone is open a ring on the map, and a hollow mark
  on the meter, follow your voice: where the last 1.2 seconds of it sit against
  the target's phrase, in pitch and in resonance, with a short tail showing
  which way you're moving. **Try it live** opens the microphone for up to 45
  seconds without scoring or keeping anything, to hold a sound, slide the
  pitch and watch whether the brightness comes with it. During **Say it** the
  ring shows too, and the scored dot replaces it when the attempt comes back.
- **Melody and rhythm** run across the whole line, so their Train sets the
  coach's focus and points at the line drills (½ speed, speak along).

`scoring.word_drill` uses the same measures as the per-word breakdown,
restricted to one word against the same word. Formants on single words are
what Hillenbrand et al. (1995) measured, and where the per-recording ceiling
was validated. On synthetic vowels with formants moved ±10% it reads +9.2%
and −9.9%. Another man saying the same word reads 11.5% darker and 6
semitones lower, matching his whole-line result.

An attempt is measured at your formant ceiling, the middle of what your
recent takes on the voice picked. It doesn't pick one for itself, because a
word or a phrase is too short to choose well. On 182 phrases from two VCC2018
speakers, a phrase cut from its line and scored against that line picked the
line's ceiling 20% of the time, and its resonance read more than 3% off in
36% of cases (median 1.7%). Measured at the line's ceiling, that's 13% of
cases (median 0.7%). `cache/experiments/ceiling/self_units.py` runs it.

**Word edges.** Whisper's word times are approximate and often end a word
early, so a word played on its own was cut off. Word edges are moved onto the
audio (`line.refine_word_times`): between two words to the quietest point
between them, and the last word to where the sound stops. Across 118 words
from 11 lines, words that ended while the sound was still loud fell from 16.9%
to 0.8%.

**Words said in one breath.** Speech isn't a string of separate words. In
"The proper course to pursue is to offer your name and address", "to pursue is to" comes out as one
stretch of sound, and the recogniser's times inside it are guesses. A word that can't be heard on its own can't be practised or measured
on its own, so adjacent words are joined into one unit when the sound doesn't
dip between them (`line.speech_units`). The rule is a dip of less than 6 dB
below the quieter of the two. On two real practice lines, every boundary
heard as a break dipped 6.5 dB or more, and every run-together one under 5.2.
A run over 1.3 s is split at its deepest dip, and a unit under 0.15 s joins
the neighbour it dips least from. The word strip, the chart's labels, the
per-word feedback and the trainer all use these units. That threshold comes
from 15 boundaries on two lines, so it's a starting point, not a validated
number.

## Gender read

Every take has a **Gender read** row among its measurements: 100 when the
take reads as female or male as the target does, with both reads in the
detail, each from 0 (male) to 100 (female). It isn't weighted into the
headline, and its Train button opens "pitch and resonance together", since
those are what move it. The voice's page shows the voice's own read. It comes
from the two things that carry most of perceived voice gender, pitch and
resonance: a logistic regression on median pitch and the mean F2–F3 shift
(`gender.py`, `tools/train_gender.py`). It isn't part of the headline score,
which already counts pitch and resonance.

F1 is left out. It moves most with the vowel and with a soft, breathy
delivery: one such voice's F1 read about 330 Hz against 610 for a woman
reading aloud, which made her read as male. Trained on the speakers' sex in
two corpora and tested on a third, with no speaker in common:

| Trained on | Tested on | Clips right | Speakers right |
| --- | --- | --- | --- |
| RAVDESS + VCC2018 | PTDB-TUG | 96.9% (AUC 0.996) | 20 of 20 |
| RAVDESS | VCC2018 | 90.0% | 11 of 12 |
| VCC2018 + PTDB-TUG | RAVDESS (emotional acting) | 80.2% | 21 of 24 |

The labels are the speakers' sex, not listeners' judgments of each clip, so
the score says how a voice's pitch and resonance compare with women's and
men's. Labels: 80 and up "female", 60–80 "leans female", 40–60 "in between",
20–40 "leans male", under 20 "male". A soft-spoken, breathy voice reads
36–96 across her lines, lower than a woman reading aloud.

## Lines that can't be scored fairly

**Pitch.** A line whose pitch sits more than 7 semitones from its voice's
usual pitch gets a warning, and Pitch and Melody aren't scored on it. Such a
line is usually creaky voice, or a pitch tracker off by an octave, and its
pitch scores say nothing about you. Its headline comes from the other parts,
and the voice check leaves it out unless you tick it.

The author's own data showed why. One line read 98 Hz against the voice's usual
191; 24 takes all scored Pitch 2, measured an octave high, and the coach kept
saying "go lower".

**The coach moves on from a part that isn't moving.** If a part has stayed
under 15 for three takes in a row, the coach works on the parts that do
respond, and says so (unless you picked that part yourself). On a breathy
voice, Articulation sat at 0–11 on almost every take, while the same lines
through other microphones scored 94–100. So the gap is real, most likely her
breathy delivery, but advice that isn't working is worse than none.

## Progress

The voice's page has three tabs: **Practise** (the voice score and your
lines), **Progress** and **Add lines** (suggestions, links, uploads,
recordings and speakers). A voice with no lines yet opens on Add lines.
**Progress** shows every take as a dot (one colour per line), the average of
the last five, each part over your last five takes against your first five,
and the voice at a glance.

On a wide window the line's page has two columns: the guide, the record
button and the result on the left, and the coach and your takes on the right,
so the advice stays beside the chart. The guide leaves out the pitch
tracker's slips: points more than 7 semitones from the line's median, nearly
always an octave error on a creaky or breathy stretch.

## What the number means

**About 90:** what the real person scores saying the line again on a different
microphone. **About 20:** a stranger of the same sex saying the same line their
own way. Each part of the breakdown uses the same scale, with one exception.

**Articulation** compares the sounds themselves, as a speech recogniser hears
them, whoever is speaking. A stranger reading the same line makes nearly the
same sounds, so its 20 is not the stranger: it is a different sentence
altogether. About 90 is the real person saying the line again, a stranger
reading it their own way scores around 77, and saying other words scores 20.
It tells you how closely you shape the line's sounds, not whose voice it is.

**Vocal fry** is shown beside the parts and not counted: how much of the
target's voiced sound drops into a low creak (more than 7 semitones under the
voice's usual pitch), and how much of yours. A line that is mostly fry reads
an octave low, which is why some lines can't be scored on Pitch; this row
says so in plain terms. Ordinary speech reads 2-13%, so only a gap against
the target means anything.

**Scores from an earlier version** are worked out again. Each result is
stamped with the scoring it came from (`scoring.SCORING`). When that changes,
the app scores every saved take again from its own audio the next time it
starts, in the background, measures the trainer's saved attempts again, and
works the voice checks out again from the takes, so a best, a trend or the
trainer's map never mixes two scales.

The Voice part also says, in words, what listeners would likely answer if asked
whether the two recordings are the same speaker. For example: "probably the
same speaker (5.2 of 6)".

The page also shows **"Passes a speaker check"** when a speaker-verification
model would accept the take as the same person. That threshold lets 1% of
same-sex strangers through and accepts 88% of the real person's own takes.

**Live pitch.** While you record a take, a chart scrolls your pitch over the
last 5 seconds against the target's pitch band (10th to 90th percentile, line at
the median), with a readout such as "You: 144 Hz · target about 95 Hz — go
lower (7.2 st)".

**Live resonance.** Under that readout a meter runs from darker to brighter
with the target in the middle: "Resonance so far: about 6% brighter than the
target". It takes what you've said since your last pause and lines it up with
the target sound by sound, the way a take's Resonance is measured, so each
vowel is held against the same vowel. It shows while you warm up, speak along
or record a take. The
browser reads it (see "Live resonance" under Validation); the Resonance score
still comes from the server once the take is in.

**Music under the target.** Every target clip goes through vocal separation
(UVR-MDX-NET-Voc_FT, on the CPU) before anything is measured. Clips from films
and videos usually have music or background under the voice, which throws off
pitch, formants and Voice. The upload is kept as `original.wav` next to the
cleaned `target.wav`. Your takes aren't cleaned: they come from your own mic,
and changing your voice before scoring it would defeat the point.

**The words.** Each target is transcribed once, with Whisper base.en on the
CPU, and the page shows its line. Each take is transcribed and compared word
by word. When the take skipped, swapped or added words, the first hint says
which, for example "You left out “Steels, etc”". If the recogniser misheard
the target, correct the line with Edit next to it. The word check doesn't change
the score. It's advice only, because the recogniser sometimes mishears a
correct take (see Validation).

## How it works

The take is time-aligned to the target, so most measurements compare the same
moment of the line in both recordings. That part needs **both recordings to
say the same words**. The alignment is dynamic time warping over what a speech
recogniser hears in each recording: the frames of Whisper's encoder, one every
20 ms (`line.sounds`). It was over MFCCs until 1.1.0. MFCCs carry the voice as
much as the words, and the recogniser's frames put a line's words nearer to
where the take's own transcript has them (a median 50 ms off against 80 ms,
and over 200 ms off for 6% of words against 16%, on 30 real takes).

| Part | Weight | What is compared |
| --- | --- | --- |
| Voice | 22.8% | The rating listeners would likely give the pair; doesn't depend on the words |
| Pitch | 14.2% | Median pitch, in semitones |
| Melody | 11.3% | Shape of the pitch contour once the level difference is removed |
| Resonance | 18.4% | Formants F2 and F3 on aligned voiced frames (brighter or darker), both recordings read at one formant ceiling |
| Articulation | 12.7% | Distance between what the recogniser hears in each, where both have sound (how the sounds are shaped) |
| Rhythm | 20.6% | Pace: length from first to last voiced sound. Timing: how unevenly the take is stretched against the target between those |

Voice is a small model trained on human ratings (VoxSim, below). It predicts
the listener rating from three things:

- **Speaker similarity:** speaker-embedding similarity from WeSpeaker's
  ResNet34-LM (trained on VoxCeleb), run on the CPU with onnxruntime. The mean
  embedding of 137 unrelated speakers is subtracted first.
- **Pitch gap:** the gap in median pitch.
- **Formant gaps:** the gaps in median F1, F2 and F3.

It's constrained so that more similarity can only raise the rating and bigger
gaps can only lower it.
Pitch and formants come from Praat (via parselmouth). Each recording gets its
own formant ceiling: the one, from 4500 to 7000 Hz, whose tracks jump least from
frame to frame. Resonance reads both recordings at one ceiling instead, the
middle of the two speakers' (see Formants, below). The alignment itself comes
from librosa.

Before anything is measured a recording is trimmed to its sound, and a short
sound on its own at either end is cut with the quiet around it: the click on
Stop lands a second after the last word.

### Calibration

`tools/calibrate.py` sets every curve and weight from
[RAVDESS](https://zenodo.org/records/1188976). In RAVDESS, 24 actors say the
same two sentences in eight emotions, twice each. Each take goes through a
simulated microphone and room (band limit, reverb, noise), because in real use
the target is a clip and the take comes from your own mic.

- **Curves:** each measure's curve puts 90 at the median distance between an
  actor and their own retake, and 20 at the median distance to a same-sex
  stranger.
- **Articulation's 20** is another sentence altogether: the same speaker
  saying a different line (320 pairs from VCC2018, which has 35 sentences
  from each of 8 speakers; RAVDESS's two sentences share four of their six
  words). On that curve a median stranger reading the right line scores 77,
  an actor's own other sentence 64, and an actor of the other sex 79: it
  doesn't hear whose voice it is.
- **Weights:** each part counts in proportion to how well it tells a retake
  from a stranger (AUC − 0.5): Voice 0.99, Resonance 0.89, Pitch 0.80,
  Articulation 0.77, Melody 0.74, pace 0.73, timing 0.71. Rhythm is pace and
  timing mixed in the same proportion (52% and 48%).
- **Headline:** the weighted mean goes through one more curve with the same
  anchors.

Headline scores on the calibration pairs (10th / 50th / 90th percentile):

| Pair | Score |
| --- | --- |
| Same actor, same line, clean | 57 / 96 / 100 |
| Same actor, same line, through a mic | 43 / 90 / 100 |
| Same actor, different emotion | 24 / 58 / 95 |
| Same actor, other sentence | 28 / 65 / 93 |
| Same-sex stranger, same line | 11 / 20 / 42 |
| Other-sex stranger, same line, same emotion | 10 / 15 / 26 |

The headline tells an actor's retake from a stranger at AUC 0.964 (0.962
before 1.1.0), so rebuilding three of its parts did not cost it anything
there. "Same actor, other sentence" rose from 58 because Articulation now
scores two sentences that share four words of six as mostly the same sounds.

Resonance is measured as the app measures it: both recordings of a pair at
the middle of their two speakers' formant ceilings. The app has about five
lines of a voice and a few of your takes to choose each speaker's from, so
`collect` gives a recording the middle of its own pick and the picks of four
other recordings of the same actor through the same channel.

To re-run it, download `Audio_Speech_Actors_01-24.zip` from Zenodo and unzip
it into `cache/ravdess`, then:

```powershell
.\.venv\Scripts\python tools\calibrate.py cohort-mean
.\.venv\Scripts\python tools\calibrate.py collect --ravdess cache\ravdess --vcc cache\vcc2018\vcc2018_evaluation --out cache\pairs.jsonl
.\.venv\Scripts\python tools\calibrate.py fit cache\pairs.jsonl
```

`cohort-mean` also needs the PTDB-TUG and Hillenbrand data used in Validation
below. These steps write `src/echo_voice/speaker_mean.json` and
`src/echo_voice/calibration.json`. Only the fitted numbers are in
git; RAVDESS itself (CC BY-NC-SA 4.0) stays in the ignored `cache/`.

**Limits.** RAVDESS is acted speech: short lines from one studio. The
microphone is simulated, not real. A held-out check on four CMU ARCTIC clips
agrees with the scale:

| ARCTIC pair | Score |
| --- | --- |
| Another man, same line | 28 (Articulation 92) |
| A woman, same line | 30 (Articulation 96) |
| Same man, other sentence (passes the speaker check) | 72 (Articulation 24) |
| Same man, through a simulated mic | above 85 |

### Things that were wrong and are now tested

- **Speaker embedding:** the model needs the per-utterance mean subtracted from
  its input features. sherpa-onnx 2.0.1 skips that step. Without it,
  same-sex strangers average 0.72 cosine similarity against 0.84 for the same
  person. With it, strangers fall to 0.16 and the same person sits at 0.69.
- **Exact digital silence:** noise gates, edits and TTS produce stretches of
  exact zeros, which knock the embedding off. One int16 step of dither, as
  WeSpeaker uses in training, keeps a gated clip at 0.97 similarity to the
  original instead of 0.88.
- **Trimming:** trimming used to cut only what was 40 dB below the peak, which
  never happens in a room with audible noise. Now it cuts relative to the
  measured noise floor.
- **Formant ceiling:** a fixed 5500 Hz ceiling mis-tracked women's and
  children's F2 and F3 (see Validation). The ceiling is now chosen per
  recording.
- **Resonance moved when the voice hadn't:** the same person got a different
  ceiling from one recording to the next, and each switch shifted the
  formants. Resonance now uses one ceiling per speaker (see Formants).
  A trainer attempt on one word chose its own ceiling too, and a word is too
  short for that; it now uses yours.
- **Articulation heard the voice:** it was the MFCC distance along the
  alignment, and MFCCs carry the speaker as much as the sounds. On RAVDESS a
  stranger saying the right line (0.364) was as far as the right actor saying
  the other sentence (0.368), and a real take against a breathy voice sat past
  both, so it read 0 whatever was said. It now compares what a speech
  recogniser hears (see How it works).
- **The first formant on breathy and high voices:** near 190 Hz Praat's F1
  flips between the first harmonic and the formant. Across takes of one line
  its shift varied by 0.7 to 2.4 semitones, and the whole Resonance curve
  from 90 to 20 was 0.75 wide. Resonance now reads F2 and F3.
- **Two ceilings for one comparison:** the line was read at its voice's
  formant ceiling and the take at its speaker's. Read at 4750 Hz against 5250, a
  copy of a line scored 3 to 24 against itself on four of five lines. Both
  are now read at one ceiling, and at four more around it, and the middle
  reading counts.
- **The click on Stop:** it lands a second after the last word, the trim kept
  it, and the quiet before it was lined up against the end of the target.
  The trim now drops a short sound on its own at either end.
- **Timing was half of Rhythm:** it told a retake from a stranger at AUC 0.59
  and its curve from 90 to 20 spanned 0.054 to 0.070, so any wobble in the
  alignment moved Rhythm. It is now read at ten stretches of the line, between
  each recording's first and last voiced sound, and tells the two apart at
  0.71. Pace is still the length from first to last voiced sound, now without
  a voiced blip on its own at either end. Reading pace from the alignment
  (the span of everything with sound) was tried and dropped: a breath after
  the last word has sound, and takes read up to 50% slow.
- **A suggested line that started mid-sentence:** see Suggested lines.

## Validation

`tools/validate.py` checks each measurement against data with known answers.
The data goes in the ignored `cache/`. Each command's docstring says where to
download it.

### The audit behind 1.1.0

Every part was put through two tests in November 2026. The scripts are in the
ignored `cache/experiments/audit/`, `articulation/` and `resonance_check/`.

**Known answers.** The calibration pairs come in kinds whose right answer is
known. A part should score a stranger low, and should not care which of two
people says the wrong words. Median score of each kind of pair, before and
after:

| Part | Same actor, other sentence | Same-sex stranger, same line | Other sex, same line |
| --- | --- | --- | --- |
| Voice | 88, 88 | 20, 20 | 11, 11 |
| Pitch | 87, 87 | 20, 20 | 5, 5 |
| Melody | 69, 75 | 20, 20 | 36, 33 |
| Resonance | 84, 66 | 20, 20 | 1, 4 |
| Articulation | 18, 64 | 20, 77 | 11, 79 |

Articulation was the one that failed: it scored a stranger saying the right
line (20) the same as the right actor saying the other sentence (18), and it
told those two apart at AUC 0.51, a coin toss. It now does at 0.81, and an
actor of the other sex reading the right line scores what a same-sex stranger
does. Resonance now reads a different sentence as a difference (66 from 84):
it compares the vowels the two recordings line up on, and those are other
vowels.

**Changes that should not matter.** 70 real practice takes against a breathy
voice were each scored, changed in a way nobody would hear as a different
performance, and scored again. Points the score moved, as median / 9 takes in
10 within, before and after:

| Change | Voice | Melody | Resonance | Articulation | Rhythm | Headline |
| --- | --- | --- | --- | --- | --- | --- |
| A second of silence and a click after the take | 0.7 / 5.8, 0.0 / 1.0 | 0.5 / 12.8, 0.0 / 1.5 | 1.5 / 38.0, 0.0 / 6.1 | 4.1 / 19.1, 0.0 / 0.2 | 9.9 / 48.9, 0.0 / 1.4 | 2.6 / 11.1, 0.0 / 1.5 |
| A quarter second of faint room tone either side | 0.5 / 5.8, 0.0 / 2.7 | 0.1 / 6.1, 0.0 / 1.7 | 0.2 / 9.2, 0.0 / 9.2 | 0.2 / 1.2, 0.0 / 1.5 | 1.3 / 7.9, 0.0 / 1.3 | 0.3 / 4.3, 0.0 / 2.5 |
| 30 ms cut off each end | 0.8 / 5.9, 0.3 / 4.1 | 0.0 / 6.9, 0.2 / 10.0 | 0.3 / 6.2, 3.3 / 18.7 | 0.7 / 4.6, 0.9 / 3.1 | 1.5 / 37.6, 0.3 / 4.1 | 0.6 / 7.2, 1.3 / 5.6 |
| Hiss 40 dB under the speech | 2.5 / 9.4, 2.2 / 7.9 | 0.6 / 9.5, 0.1 / 9.6 | 2.5 / 24.8, 8.1 / 42.3 | 1.6 / 11.3, 0.9 / 5.6 | 0.6 / 31.3, 0.1 / 1.6 | 1.6 / 9.4, 2.1 / 9.7 |

Pitch moved by 0.0 / 0.2 or less in every row, before and after. The click
on Stop is dealt with, and Rhythm now holds still: one take in ten used to
move 31 to 49 points, and now moves 1 to 4. Resonance looks worse in the last
two rows, and
that needs reading with care: before, these takes scored a median 13 on
Resonance, on the flat floor of its curve, where a score cannot move. They
now score a median 33, on its slope. In semitones the new reading moves by a
median 0.053 for the cut and 0.058 for the hiss (43 of the takes); read from
all three formants the same way it moves 0.072 and 0.065, and tells a retake
from a stranger worse (AUC 0.84 against 0.89 on 260 pairs). But the slope is
steep: the curve runs from 90 to 20 over half a semitone. **Resonance on one
short take is a rough reading: small things move it 3 to 8 points for a
typical take and 19 to 42 for one in ten. Judge it over several takes.** From
the third take on a line, the Resonance row says where your last three takes
sit together (the middle of their shifts), which is steadier than any one.

**The same takes, scored again.** Articulation went from a median 8 (1 to 28
across the takes) to 65 (44 to 75). Scored against the voice's other lines,
the wrong words, the same takes get a median 21, and the right line scores
higher for 96% of them. Melody went from 14 to 31 (the takes are lined up better), Rhythm from 50 to 57, and the headline from a median 28 to 29.

### Against human judgments

The whole scorer was run on the Voice Conversion Challenge 2018 similarity
test (Lorenzo-Trueba et al., Odyssey 2018; [data](https://doi.org/10.7488/ds/2337),
CC BY 4.0). It has 21,343 pairs: listeners heard a converted clip and a natural
recording of the same sentence and judged whether they were the same speaker,
from 1 (different, sure) to 4 (same, sure). Each converted clip is, in effect,
a machine's impression of the target voice. That makes this the closest public
test of what the app measures.

| Measure | Per pair, 1.0.0 | Per system, 1.0.0 | Per pair, 1.1.0 | Per system, 1.1.0 |
| --- | --- | --- | --- | --- |
| **Headline** | **0.53** | **0.94** | **0.48** | **0.90** |
| Voice | 0.56 | 0.95 | 0.55 | 0.94 |
| Pitch | 0.47 | 0.78 | 0.48 | 0.78 |
| Resonance | 0.42 | 0.91 | 0.39 | 0.91 |
| Articulation | 0.21 | 0.33 | −0.11 | −0.49 |

The table shows Pearson correlation with the mean listener rating. The
per-system column averages over 72 groups (system × compared-with × task),
each with many ratings.

**The headline agrees less with these listeners since 1.1.0, and that is the
price of Articulation meaning what it says.** The listeners were asked one
thing: is this the same speaker? A converted clip is its source speaker's
recording with the voice swapped, so it keeps the source's sounds and timing.
The old Articulation rose with the listeners' ratings (0.21) because it heard
the voice. The new one hears the sounds, which in this test belong to the
source speaker, so it falls with them (−0.11), as Melody (−0.05) and Rhythm
(−0.13) always have here. Voice, the part that answers the listeners'
question, is where it was. This test cannot say whether a headline that
counts delivery is a better measure of an impression: nobody in it was
imitating anyone's delivery. A listening test of real impressions would, and
there isn't a public one. If the headline should track "same speaker?" above
all, the remedy is to weigh Voice more, not to make Articulation hear voices
again. Run on 2026-11-17: `tools/validate.py vcc2018`, 21,343 pairs.

Rechecked on 2026-11-16 after Resonance was recalibrated for one ceiling per
speaker: the headline is unchanged at 0.528 per pair and 0.939 per system
(`cache/vcc_recheck.py`, from cached features). Resonance in this test still
uses a ceiling per recording; it wasn't rerun with one per speaker.

Each pair has only 1.4 ratings on average, so listener noise caps the per-pair
numbers. For comparison, Ahn et al. (2024,
[arXiv:2407.18505](https://arxiv.org/abs/2407.18505)) report two results on
their VCC2018 test split:

- **0.51:** a pretrained ECAPA speaker model, with no training on VCC2018.
- **0.56:** the same ECAPA model fine-tuned on VoxSim, still without training
  on VCC2018.
- **0.57:** SVSNet, a model trained on VCC2018 itself.

Their split differs from this one, so the comparison is approximate. Voice's
listener model was trained only on VoxSim and lands next to Ahn et al.'s
VoxSim-trained model here (0.56). Before, when Voice was speaker-embedding
similarity alone, it scored 0.49.

The new Voice costs a little on one slice: conversions compared with their
target. There the headline's rank correlation dropped from 0.378 to 0.368
(bootstrap 95% CI for the change, −0.013 to −0.007), even though Voice on its
own improved from 0.371 to 0.402. The likely reason is that Voice now leans on
the pitch gap, which is a weak signal in voice-converted speech, and the
headline also counts pitch separately. The change was kept for two reasons:

- **Across the rest of VCC2018 the headline improved:** 0.524 to 0.528 per
  pair and 0.932 to 0.939 per system.
- **On real speech the gain is large:** ranking strangers on VoxSim went from
  0.15 to 0.62.

These numbers were recomputed from cached features. That's exact because
only Voice's curve, the weights and the headline curve changed in
`calibration.json`.

Fitting the weights directly to these ratings, with whole systems held out,
would raise the rank correlation on conversions against their target from
0.38 to 0.40. Most of that gain comes from dropping melody and rhythm, for the
reason given under "Tried and not used". The weights stay as calibrated.

**Real-world clips: VoxSim.** VoxSim (Ahn et al., Interspeech 2024) has
human ratings, from 1 (definitely different) to 6 (definitely the same), of
pairs of VoxCeleb1 clips: celebrities in interviews and talk shows, saying
different sentences. These pairs can't test the parts that need the same
words, but they test Voice, Pitch and Resonance on recordings like the clips
you'd use. Resonance here uses median formants instead of aligned ones.

The table shows Pearson / Spearman correlation with the ratings on VoxSim's
test split: 2,775 pairs, none of them used for training.

| Measure | All pairs | Different speakers only |
| --- | --- | --- |
| **Voice (listener model, used)** | **0.789 / 0.772** | **0.589 / 0.623** |
| Speaker-embedding similarity alone (the previous Voice) | 0.761 / 0.744 | 0.237 / 0.150 |
| Pitch alone | 0.493 / 0.484 | 0.414 / 0.457 |
| Resonance alone | 0.400 / 0.467 | 0.326 / 0.492 |

- **Embedding similarity tells people apart but barely ranks strangers.** It
  matches the VoxSim paper's pretrained ECAPA baseline across all pairs
  (Pearson 0.761 here against 0.768 there). Among strangers it does little to
  order them by how close they sound, which is exactly what an impression is.
  Chen et al. (2026) found the same for speaker models trained this way.
- **Pitch and formant gaps fill the gap.** On their own they rank strangers
  three times better. So Voice is now a model that combines all three, trained
  with `tools/train_similarity.py` on all 24,919 VoxSim training pairs. Its
  settings were chosen on 10% of the training speakers, held out; the test
  split was scored once.
- **More data didn't move it.** Trained on 3,037 pairs it scored within 0.002
  of these numbers on the test split. So the limit is the five inputs, not the
  amount of training data. Adding where two speaker embeddings differ along
  their 5 to 20 main directions didn't help either: test strangers 0.612 to
  0.615, against 0.623 without.
- **The model does both jobs.** It ranks strangers at 0.62 and beats the
  pretrained ECAPA baseline across all pairs. Ahn et al.'s GPU-fine-tuned
  model reaches 0.829; this one gets about half the way there from five numbers
  and 200 small trees. It ships as plain JSON (`similarity_model.json`), and
  the training script checks that the JSON gives exactly scikit-learn's
  predictions.

`tools/validate.py voxsim-fetch` downloads only the clips these pairs need from
the VoxCeleb1 zips on the Hugging Face mirror: 46,348 clips, about 11 GB, for
all training and test pairs. It fetches neighbouring clips in one request. Each
clip is checked against the zip's CRC. You need a Hugging Face login that has
accepted the mirror's terms.

### Pitch

The pitch tracker was checked against [PTDB-TUG](http://www2.spsc.tugraz.at/databases/PTDB-TUG/)
(Pirker et al., Interspeech 2011): 160 sentences from 10 women and 10 men, with
reference pitch taken from a laryngograph. The current tracker (Praat
autocorrelation, 60–600 Hz) was the best of five:

| Tracker | Gross errors (>20% off) | Voicing wrong | Fine error |
| --- | --- | --- | --- |
| **Autocorrelation, 60–600 Hz (used)** | **2.1%** (women 3.6%, men 0.5%) | 4.5% | 11 cents |
| Cross-correlation, 60–600 Hz | 2.8% | 4.5% | 14 cents |
| Two-pass range, De Looze & Hirst 2008 | 2.4% | 4.3% | 12 cents |
| Two-pass range, Hirst 2011 | 2.6% | 4.2% | 12 cents |

The adaptive ranges fix some octave-down errors for women, but they cause more
octave-up errors than they fix.

The page's live tracker is YIN (de Cheveigné & Kawahara 2002) in
`static/pitch.js`, run on 2048-sample frames as the browser sees them. On the
same recordings it gets 1.9% gross errors (3.0% for women, 0.2% for men) with
a 15-cent fine error. It skips 8.4% of voiced frames, which just leaves gaps in
the live trace, and calls 1.0% of unvoiced frames voiced. A looser threshold
fills the gaps but nearly triples the gross errors.
`node tools/check_live_pitch.mjs cache/ptdb` reproduces this.

### Live resonance

The live meter reads F1–F3 in the browser (`static/formants.js`). It follows
Praat's Burg tracker step by step: 50 ms frames, resampled to twice the
speaker's formant ceiling, pre-emphasis from 50 Hz, Praat's Gaussian window,
ten coefficients, and the three lowest roots between 50 Hz and 50 Hz under the
ceiling. The page reads the target's audio and the microphone with the same
code, at the one ceiling the scorer reads both at, and says brighter or
darker from F2 and F3 as the scorer does, so a gap between them is a gap
between the voices.

On the same audio and the same frames it lands a median 1.1% (F1), 0.8% (F2)
and 0.7% (F3) from Praat, with no lean either way. Which frames count matters
more than the fit, because typical formants are medians over the voiced
frames. The pitch tracker's own test of "voiced" kept 30% fewer frames than
Praat and put typical F1 and F2 4–6% off. So a frame counts when its
normalised autocorrelation is at least 0.55 and it is at least a tenth as loud
as the loudest frame so far, which finds 93% of Praat's voiced frames with 6%
extra. That test was chosen on two sentences; the check below uses ten others.

Checked on 120 recordings (12 VCC2018 speakers, the same 10 sentences each),
against what the app measures. These are the numbers since 1.1.0, when
Resonance went to F2 and F3 at a shared ceiling and to the new alignment.
Before, with all three formants, the two meters agreed with the app at
correlations 0.983 and 0.987: the meter lines sounds up by their formants and
the scorer now by what a recogniser hears, so the two differ a little more.

| | Result |
| --- | --- |
| Typical F1 / F2 / F3 against Praat at the same ceiling | 2.2% / 1.2% / 0.6% apart (median) |
| The gap between two speakers on a sentence, against the trainer's measure (660 pairs) | correlation 0.933; a median 1.4 points apart; within 3 points in 77% |
| Says the right way, brighter or darker, when the app puts them 3% or more apart | 98% of 467 pairs |
| The line page's meter, lined up first, against a take's Resonance (557 pairs it could be read on) | correlation 0.903; a median 1.7 points apart; within 3 points in 70%; right way in 95% |
| The same without lining up, as the meter first was | correlation 0.736; a median 3.2 points apart; within 3 points in 47% |
| The pitch gap between two speakers, the live tracker on both sides against Praat on both | correlation 0.982; a median 0.31 semitones apart; over 1 semitone in 11% |

**Lining up.** Typical formants over a whole line depend on how long each
speaker dwells on each vowel, and no two people dwell the same. On one demo
pair the unaligned reading was 5% darker, by Praat as well as by the browser,
where the take's Resonance, measured along the alignment, was level. So the
line page's meter aligns first (`formants.alignedShift`): dynamic time warping
on the formants themselves, each side centred on its own typical values, with
an open end so a take in progress lines up with the part of the line it has
reached. Half-way through a take it is a median 2.1 points from the finished
take's Resonance. The trainer's ring doesn't align, and doesn't need to: a
scored attempt on a phrase is measured by typical formants too.

One reading takes 0.4 ms and one alignment 0.6 ms. To reproduce:

```powershell
.\.venv\Scripts\python tools\live_resonance_ref.py --vcc cache\vcc2018 --out cache\live_resonance_ref.json
node tools\check_live_resonance.mjs cache\live_resonance_ref.json
```

End to end in a real browser, with the microphone fed from a file
(`cache/experiments/live_resonance/live_check.py`): a target's own phrase
reads "on pitch, resonance about level". Played 8% faster, which lifts pitch
by 1.3 semitones and every formant by 8%, it reads 0.8–1.3 semitones high and
4–15% brighter, mostly 7–10; the server scored that attempt 1.3 semitones high
and 6% brighter. Played 8% slower it reads 1.4–2.3 semitones low and 0–10%
darker, less steadily.

**Limits:**
- The ring shows the last 1.2 seconds, so it moves by a few points as
  different sounds pass through. Its direction and rough size are what to go
  by; the scored attempt is the number.
- The trainer's live pitch is the live tracker's reading of the target's
  phrase, not Praat's. On a word with a creaky or fading end the two trackers
  count different frames as voiced: on one such word they were 3 semitones
  apart, which is why the ring isn't held against the score's pitch.
- All of this is studio speech read aloud. Whispered and breathy voices,
  where formants are hardest, haven't been checked.

### Formants

The formant tracker was checked against the hand-checked measurements of
[Hillenbrand et al. (1995)](https://github.com/santiagobarreda/hillenbrand_et_al_1995):
1,668 /hVd/ words from 45 men, 48 women and 46 children. Each speaker's 12
words were joined into one recording, and one ceiling was chosen per recording
without vowel labels, as the app does. The table shows the share of
measurements more than 15% off:

| Ceiling | F1 | F2 | F3 | Children's F2 / F3 |
| --- | --- | --- | --- | --- |
| Fixed 5500 Hz (before) | 1.8% | 5.6% | 6.9% | 10.4% / 11.3% |
| **Least frame-to-frame jumping, per recording (used)** | **0.9%** | **1.5%** | **2.8%** | **2.4% / 4.3%** |

Median errors are 1–2% throughout. The chosen ceilings come out where
phonetics puts them without being told: 5000 Hz for men, 5750 for women and
6000 for children.

**One ceiling per speaker for Resonance.** The per-recording pick is right on
average but not steady. Over an actor's 60 RAVDESS recordings it lands on 5.4
different ceilings, and each change moves the formants a little, so Resonance
moved between takes when the voice hadn't. Resonance compares formants frame
by frame, so it now reads both sides at one ceiling per speaker
(`analysis.speaker_ceiling`, `analysis.steadied`):

- **The voice:** the middle of what its lines pick.
- **You:** the middle of what your last 15 takes on that voice picked, the
  new one included.

On the RAVDESS calibration pairs, telling an actor's retake from a same-sex
stranger by Resonance alone (AUC; the bar set before the run was +0.02 with
the median retake no further off):

| Ceiling | AUC | Median retake | Worst 10% of retakes |
| --- | --- | --- | --- |
| Per recording (before) | 0.825 | 0.56 st | 1.19 st |
| Per speaker, from 5 recordings (as the app has) | 0.848 | 0.53 st | 1.07 st |
| Per speaker, from all 60 | 0.849 | 0.53 st | 1.04 st |

The gain from five recordings is +0.023 (bootstrap 95% interval +0.013 to
+0.033). On the author's own 55 practice takes, the change in Resonance from one
take to the next fell from 1.62 to 1.40 semitones at the 90th percentile, and
the spread across the takes of a line from 0.75 to 0.52. The calibration was
collected again this way: Resonance's AUC in `calibration.json` is 0.846, and
its weight went from 15.7% to 16.5%.

Typical formants, the listener model and the gender read keep each
recording's own pick, because that is how they were fitted (VoxSim pairs are
single clips with no other recordings of the speaker). The experiment is in
`cache/experiments/ceiling/` (`ceiling_exp.py`, `boot.py`, `user_takes.py`).

**One ceiling for both sides, and F2 and F3 only (1.1.0).** Two ceilings in
one comparison turned out to be a fault of their own: formants read at 4750 Hz
sit a little lower than the same audio read at 5250, so a line was not level
with a copy of itself. And on a breathy voice near 190 Hz the first formant
could not be read at all steadily: Praat's F1 flipped between the first
harmonic (about 250 Hz) and the formant (about 430). So Resonance now:

- reads both recordings at one ceiling, the middle of the voice's and yours
  (`analysis.shared_ceiling`), and at 250 and 500 Hz either side of it, and
  takes the middle of the five readings;
- reads F2 and F3 only;
- isn't read with fewer than 60 aligned voiced frames (two takes with 31 and
  40 gave junk, one of them +8.4 semitones).

On the calibration pairs its AUC went from 0.846 to 0.89, and its curve is
wider: 90 at 0.29 semitones and 20 at 0.83, where it was 0.54 and 1.29. It is
unread on 1.4% of retakes and 2.8% of strangers, RAVDESS lines being short.
The live meter and the trainer read F2 and F3 at the same shared ceiling. The
experiments are in `cache/experiments/resonance_check/`.

### Clip cleaning

48 clean sentences (CMU ARCTIC, RAVDESS, VCC2018 targets) were mixed with
music. The music was six public-domain or CC0 recordings from Wikimedia Commons
(orchestra, piano, strings, oboe, mandolin) plus a synthetic drum-and-bass
loop. Each mix was scored against the clean original, where 100 is a perfect
match. The table shows medians, with the worst 10% in brackets:

| Music level | Uncleaned | Vocal separation (used) | DPDFNet denoiser | GTCRN denoiser |
| --- | --- | --- | --- | --- |
| None | 100 | 100 | 100 | 100 |
| 10 dB below the voice | 100 (90) | 100 (100) | 100 | 100 |
| 5 dB below | 92 (69) | 100 (100) | 100 | 100 |
| As loud as the voice | 65 (27) | 100 (99) | 100 | 100 |

The headline hides how far the parts drift. With music as loud as the voice,
uncleaned pitch is 2.0 semitones off and formants 2.4. After separation they
are 0.07 and 0.36, and SI-SDR rises from 0 dB to 15.6 dB, against 12.2 dB for
DPDFNet and 6.6 dB for GTCRN. On clean speech separation changes almost
nothing (SI-SDR 37 dB), which is why every target is cleaned.
`tools/validate.py cleaning` reproduces the numbers.

These are for the 1.1.0 scoring. Uncleaned clips scored lower before it (99,
86 and 51, with 79, 50 and 23 for the worst tenth): music under a clip used to
throw the alignment, Articulation and Rhythm as well as the pitch. The parts
that now go by what a speech recogniser hears hold up under music, and Pitch
and Melody still don't, so a clip still has to be cleaned.

### The word check

The word check was tested on RAVDESS, where every actor says the same two
sentences. The target was clean and the take went through the simulated mic.
The target's words also came from the recogniser, as they do in the app.

| Case | Flagged |
| --- | --- |
| Same sentence, other actor or emotion (false alarms) | 7.1% |
| The other sentence ("Dogs are sitting" for "Kids are talking") | 100% |
| Take cut to its first 60% | 90% |
| Take cut to its last 60% | 96% |

Whisper runs at temperature 0 only. Its default retries an unsure pass with
random sampling, so the same audio came back differently from run to run: 4
transcripts in 6 runs of one test recording. At temperature 0 all 6 matched,
and a 25-minute recording split into the same 200 lines as before.

Through the mic, the recogniser gets 96.8% of words right. One misheard word
in a correct take is common ("Docs" for "Dogs"), so a take is flagged only
when at least 2 words, or 15% of the line, are off. Flagging on any single
word gave 20% false alarms. Even 7% is too many to let the word check lower
the score, so it only produces hints.

### Splitting long recordings

**Other voice.** Test recordings were built from VCC2018: for each speaker,
30 of their sentences mixed with 8 by others (3 each by two speakers of the
same sex, 2 by one of the other sex). All twelve speakers were recorded in the
same studio, so the check can't lean on the microphone. That makes 36
recordings, three rounds of random picks per speaker, run through the whole job
(`tools/validate.py other-voice`). At the cosine threshold of 0.4:

| Lines | Tagged "other voice" |
| --- | --- |
| The main speaker's (873) | 0.2% |
| Another speaker's (259) | 96.1% |
| Another speaker of the same sex (190) | 94.7% |

Raising the threshold to 0.5 catches 98.5% of others but tags 0.5% of the main
speaker's lines. Lines that Whisper ran together across a change of speaker
can't be checked this way.

**A real video.** This was 25 minutes of one soft-spoken speaker, with music
and background ambience. It gave 200 lines,
most of them 1.4–7.2 s long (10th to 90th percentile), in 55 s. Of those:

- 9 were tagged whispered;
- 5 were tagged unclear;
- 1 was tagged other voice: a 1.6 s "hmm".

Targets made from a
normal line, a whispered one and one over background ambience each scored 100
against themselves after cleaning. On the whispered one, Pitch, Melody and
Resonance were "–".

### Tried and not used

- **A background-noise tag on lines.** It compared the level between a line's
  words with the level of the words. On the 25-minute video above it tagged 136
  of 200 lines. Separating a sample of them showed the background sat 8–12 dB
  under the voice. Separation handles that easily: it cleans music as loud as
  the voice (see Clip cleaning). The level between words also counts breaths, which aren't background. So the tag was dropped, and every
  line is cleaned when it's picked.

- **Larger or different speaker models.** On the VCC2018 judgments, the
  following models all agree with listeners within 0.02 of each other:
  WeSpeaker ResNet293 and CAM++, 3D-Speaker ERes2Net and CAM++. ResNet293 is
  the best verifier, four times slower, and slightly *worse*. That matches
  Chen et al. (2026, [arXiv:2609.33999](https://arxiv.org/abs/2609.33999)):
  lower verification error doesn't buy agreement with listeners.
- **Averaging three models.** This adds 0.015 rank correlation for three times
  the work and two more downloads. It's worth revisiting if Voice ever needs
  that extra.
- **Projecting embeddings onto a few principal directions.** Chen et al. found
  that low-dimensional embeddings match perception better, but that came from
  training with a bottleneck. Projecting afterwards lowered agreement, from
  0.48 to 0.31 at three dimensions.
- **Voice quality (CPPS, harmonics-to-noise).** These are standard breathiness
  measures. On RAVDESS they barely tell a retake from a stranger (AUC 0.61 and
  0.59). The simulated mic alone moves CPPS by 1 dB, about twice the typical
  difference between a retake and a stranger. As a part of the score they would
  mostly measure your microphone.
- **Speaking-style and voice-quality gaps as Voice inputs.** Six gaps were
  tried as extra inputs to the listener model: pitch range, speaking rate,
  voiced share, spectral envelope (mean MFCCs over voiced frames), CPPS and HNR.
  Each was tuned on the held-out training speakers. Only the spectral envelope
  helped there (+0.022). The four that helped at all, taken together, did better
  on VoxSim's test split overall, but not for strangers:

  | Test split, Spearman | All pairs | Strangers | Same speaker |
  | --- | --- | --- | --- |
  | Five inputs (used) | 0.772 | 0.624 | 0.489 |
  | With pitch range, spectral envelope, CPPS, HNR | 0.802 | 0.619 | 0.573 |

  An impression is always a stranger, so they weren't adopted. The envelope
  also carries the microphone and room, so the gain on same-speaker pairs may
  come from matching recordings, not voices.
- **Melody and rhythm can't be checked against VCC2018.** Voice conversion
  keeps the source speaker's timing and intonation, so in that data these parts
  track the source, not the voice people hear. The evidence for them comes from
  mimicry research: speaking rate is what amateur impersonators change most
  (Vestman et al. 2020, [arXiv:1906.01454](https://arxiv.org/abs/1906.01454)).

## Layout

```
src/echo_voice/
  analysis.py        recording -> pitch, formants, MFCCs on one 10 ms frame grid
  identity.py        speaker embeddings (model download, features, ONNX)
  scoring.py         target + take -> distances -> calibrated scores, hints, chart, words, coach; voice checks; word drills
  gender.py          how female or male a voice reads, 0-100 (gender_model.json from tools/train_gender.py)
  line.py            speech recognition, what the recogniser hears (for alignment and Articulation), and the word-by-word comparison
  clean.py           vocal separation for target clips
  models.py          model downloads, checked against pinned hashes
  similarity.py      the listener-similarity model behind Voice
  similarity_model.json  its trees, from tools/train_similarity.py
  calibration.json   curves and weights from tools/calibrate.py
  speaker_mean.json  mean speaker embedding of 137 unrelated speakers
  sources.py         long recordings and links: download, decode, transcribe, split into lines, tag, rank
  autopick.py        a voice's best lines from its recordings: suggest, add, auto-pick
  store.py           voices and their checks, targets (lines), takes and recordings on disk
  server.py          FastAPI app; runs the recordings' jobs
  cli.py             `echo-voice serve`, `echo-voice score`, `echo-voice import`
  static/            the page: records, trims, recordings' lines, live pitch (pitch.js) and resonance (formants.js), the end of a take (ending.js), shows results
tools/calibrate.py   fits calibration.json (RAVDESS) and speaker_mean.json
tools/validate.py    checks pitch, formants, cleaning, the word check and the whole score against reference data
tools/train_similarity.py  trains similarity_model.json on VoxSim ratings
tools/check_live_pitch.mjs  checks the page's live pitch tracker against PTDB-TUG
tools/live_resonance_ref.py, check_live_resonance.mjs  check the page's live formant tracker against the app's own measurements
tools/check_ending.mjs  checks the page's end-of-take detector on saved takes
tests/               synthetic voices, real speech (CMU ARCTIC), API, the page
```

`tests/test_browser.py` drives the page in headless Edge or Chrome with the
microphone fed from a WAV file. It runs when Playwright is installed
(`pip install -e ".[e2e]"`) and is skipped otherwise. `tests/data/arctic` holds
four CMU ARCTIC clips under their own licence (`COPYING` there).

## Next

1. **Check the scale with real microphones.** The calibration's mic is
   simulated. The app itself can check it with real ones:
   1. Record yourself saying a line on your phone, and add that recording as
      a target.
   2. Record the same line as a take on the computer's mic.
   3. Repeat with a few lines, and with someone else as the target.

   The real person on another mic should land near 90. Takes of other people
   imitating that target should mostly land well below. If your own takes come
   out much lower than 90, the simulated mic was too kind, and the curves need
   anchoring on real recordings.
2. **Give Voice more to go on.** The listener model has hit the ceiling of
   its five inputs: eight times more training data changed nothing, and
   speaking-style and voice-quality gaps didn't help with strangers (see "Tried
   and not used"). The remaining route is to fine-tune the speaker network on
   the ratings, as Ahn et al. (2024) did, or train it with a prototypical loss
   (Chen et al. 2026). Either needs a GPU.
