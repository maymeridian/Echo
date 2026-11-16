# Echo

Practise a voice impression and get closer with every take. Named after Echo,
the nymph of Greek myth who could only speak in other people's words.

Add a clip of the voice you're imitating, trim it to one line in the page,
record yourself saying the same line, and get a 0–100 match with a breakdown of what to change: voice identity,
pitch, melody, resonance, articulation and rhythm. Every take is kept, so you
can see whether you're getting closer. A coach picks one thing to work on
after each take, a guide draws your pitch over the target's while you speak,
and each word of the line shows how close you came, with the target's word
and yours to play back to back.

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

Then open http://127.0.0.1:8000. Clips and takes are stored in `data/`, which
is not tracked by git. The first run downloads the speaker model (26 MB) to
`~/.cache/echo`; set `ECHO_CACHE` to put it elsewhere.

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
- **Space** starts and stops a take.

The word-by-word numbers are the line's own measures restricted to one word,
scored on the line's curves:

| Measure | What it is |
| --- | --- |
| Pitch | Gap between the two medians |
| Melody | That gap minus the typical gap over the line |
| Resonance | Mean F1–F3 shift |
| Articulation | MFCC distance along the alignment |

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
- **Melody and rhythm** run across the whole line, so their Train sets the
  coach's focus and points at the line drills (½ speed, speak along).

`scoring.word_drill` uses the same measures as the per-word breakdown,
restricted to one word against the same word. Formants on single words are
what Hillenbrand et al. (1995) measured, and where the per-recording ceiling
was validated. On synthetic vowels with formants moved ±10% it reads +9.2%
and −9.9%. The target's own word scores 100. Another man saying the same word
reads 11.5% darker and 6 semitones lower, matching his whole-line result.

**Word edges.** Whisper's word times are approximate and often end a word
early, so a word played on its own was cut off. Word edges are moved onto the
audio (`line.refine_word_times`): between two words to the quietest point
between them, and the last word to where the sound stops. Across 118 words
from 11 lines, words that ended while the sound was still loud fell from 16.9%
to 0.8%.

## Gender read

Every take shows how female or male it reads, 0 (male) to 100 (female),
beside the target's. The voice's page shows the voice's own read. It comes
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

The voice's page shows **Your progress**: every take as a dot (one colour
per line), the average of the last five, and each part over your last five
takes against your first five.

## What the number means

**About 90:** what the real person scores saying the line again on a different
microphone. **About 20:** a stranger of the same sex saying the same line their
own way. Each part of the breakdown uses the same scale.

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

The take is time-aligned to the target (dynamic time warping over MFCCs), so
most measurements compare the same moment of the line in both recordings. That
part needs **both recordings to say the same words**.

| Part | Weight | What is compared |
| --- | --- | --- |
| Voice | 24% | The rating listeners would likely give the pair; doesn't depend on the words |
| Pitch | 15% | Median pitch, in semitones |
| Melody | 12% | Shape of the pitch contour once the level difference is removed |
| Resonance | 16% | Formants F1–F3 on aligned voiced frames (brighter or darker) |
| Articulation | 19% | MFCC distance along the alignment (how the sounds are shaped) |
| Rhythm | 15% | Length from first to last voiced sound, and timing within the line |

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
frame to frame. MFCCs and alignment come from librosa.

### Calibration

`tools/calibrate.py` sets every curve and weight from
[RAVDESS](https://zenodo.org/records/1188976). In RAVDESS, 24 actors say the
same two sentences in eight emotions, twice each. Each take goes through a
simulated microphone and room (band limit, reverb, noise), because in real use
the target is a clip and the take comes from your own mic.

- **Curves:** each measure's curve puts 90 at the median distance between an
  actor and their own retake, and 20 at the median distance to a same-sex
  stranger.
- **Weights:** each part counts in proportion to how well it tells those two
  apart (AUC − 0.5).
- **Headline:** the weighted mean goes through one more curve with the same
  anchors.

Headline scores on the calibration pairs (10th / 50th / 90th percentile):

| Pair | Score |
| --- | --- |
| Same actor, same line, clean | 71 / 98 / 100 |
| Same actor, same line, through a mic | 46 / 90 / 100 |
| Same actor, different emotion | 33 / 72 / 97 |
| Same actor, other sentence | 28 / 58 / 83 |
| Same-sex stranger, same line | 12 / 20 / 40 |
| Other-sex stranger, same line, same emotion | 11 / 15 / 24 |

To re-run it, download `Audio_Speech_Actors_01-24.zip` from Zenodo and unzip
it into `cache/ravdess`, then:

```powershell
.\.venv\Scripts\python tools\calibrate.py cohort-mean
.\.venv\Scripts\python tools\calibrate.py collect --ravdess cache\ravdess --out cache\pairs.jsonl
.\.venv\Scripts\python tools\calibrate.py fit cache\pairs.jsonl
```

`cohort-mean` also needs the PTDB-TUG and Hillenbrand data used in Validation
below. These steps write `src/echo_voice/speaker_mean.json` and
`src/echo_voice/calibration.json`. Only the fitted numbers are in
git; RAVDESS itself (CC BY-NC-SA 4.0) stays in the ignored `cache/`.

**Limits.** RAVDESS is acted speech: short lines from one studio. The
microphone is simulated, not real. Clips from films or videos with music under
them will score lower until clip cleaning exists (see Next). A held-out check on
four CMU ARCTIC clips agrees with the scale:

| ARCTIC pair | Score |
| --- | --- |
| Another man, same line | 31 |
| A woman, same line | 27 |
| Same man, other sentence (passes the speaker check) | 76 |
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

## Validation

`tools/validate.py` checks each measurement against data with known answers.
The data goes in the ignored `cache/`. Each command's docstring says where to
download it.

### Against human judgments

The whole scorer was run on the Voice Conversion Challenge 2018 similarity
test (Lorenzo-Trueba et al., Odyssey 2018; [data](https://doi.org/10.7488/ds/2337),
CC BY 4.0). It has 21,343 pairs: listeners heard a converted clip and a natural
recording of the same sentence and judged whether they were the same speaker,
from 1 (different, sure) to 4 (same, sure). Each converted clip is, in effect,
a machine's impression of the target voice. That makes this the closest public
test of what the app measures.

| Measure | Per pair | Per system |
| --- | --- | --- |
| **Headline** | **0.53** | **0.94** |
| Voice | 0.56 | 0.95 |
| Pitch | 0.47 | 0.78 |
| Resonance | 0.42 | 0.91 |
| Articulation | 0.21 | 0.33 |

The table shows Pearson correlation with the mean listener rating. The
per-system column averages over 72 groups (system × compared-with × task),
each with many ratings.

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

### Clip cleaning

48 clean sentences (CMU ARCTIC, RAVDESS, VCC2018 targets) were mixed with
music. The music was six public-domain or CC0 recordings from Wikimedia Commons
(orchestra, piano, strings, oboe, mandolin) plus a synthetic drum-and-bass
loop. Each mix was scored against the clean original, where 100 is a perfect
match. The table shows medians, with the worst 10% in brackets:

| Music level | Uncleaned | Vocal separation (used) | DPDFNet denoiser | GTCRN denoiser |
| --- | --- | --- | --- | --- |
| None | 100 | 100 | 100 | 100 |
| 10 dB below the voice | 99 (79) | 100 (100) | 100 | 100 |
| 5 dB below | 86 (50) | 100 (99) | 100 | 100 |
| As loud as the voice | 51 (23) | 100 (99) | 100 | 100 |

The headline hides how far the parts drift. With music as loud as the voice,
uncleaned pitch is 1.9 semitones off and formants 2.4. After separation they
are 0.07 and 0.34, and SI-SDR rises from 0 dB to 15.6 dB, against 12.2 dB for
DPDFNet and 6.7 dB for GTCRN. On clean speech separation changes almost
nothing (SI-SDR 37 dB), which is why every target is cleaned.
`tools/validate.py cleaning` reproduces the numbers.

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
  line.py            speech recognition and the word-by-word comparison
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
  static/            the page: records, trims, recordings' lines, live pitch (pitch.js), shows results
tools/calibrate.py   fits calibration.json (RAVDESS) and speaker_mean.json
tools/validate.py    checks pitch, formants, cleaning, the word check and the whole score against reference data
tools/train_similarity.py  trains similarity_model.json on VoxSim ratings
tools/check_live_pitch.mjs  checks the page's live pitch tracker against PTDB-TUG
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
