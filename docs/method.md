# How Echo measures, and how it was checked

This is the method behind Echo's scores and the evidence for them. For using
the app, see the [guide](guide.md).

The checks use public datasets, which aren't included: they go in the
`cache/` folder, and each command's docstring says where to download them.
Commands assume Echo's virtual environment is active.

## How a take is measured

### Lining the take up with the target

Most parts compare the same moment of the line in both recordings, so the take
is first lined up with the target by dynamic time warping. It is lined up by
what a speech recogniser hears in each recording: the frames of Whisper
base.en's encoder, one every 20 ms (`line.sounds`). The encoder is trained to
turn any voice into the same words, so two people saying one line come out
close and one person saying two lines comes out far. That part needs **both
recordings to say the same words**.

Spectral features such as MFCCs carry the voice as much as the words, and line
recordings up worse: carried along an MFCC alignment, a line's words landed a
median 80 ms from where the take's own transcript put them, and more than
200 ms off for 16% of words. Along the recogniser's frames that is 50 ms and
6% (323 words of 30 real takes).

### The six parts

| Part | Weight | What is compared |
| --- | --- | --- |
| Voice | 22.8% | The rating listeners would likely give the pair; doesn't depend on the words |
| Pitch | 14.2% | Median pitch, in semitones |
| Melody | 11.3% | Shape of the pitch contour once the level difference is removed |
| Resonance | 18.4% | Formants F2 and F3 on aligned voiced frames (brighter or darker), both recordings read at one formant ceiling |
| Articulation | 12.7% | Distance between what the recogniser hears in each, where both have sound (how the sounds are shaped) |
| Rhythm | 20.6% | Pace: length from first to last voiced sound. Timing: how unevenly the take is stretched against the target between those, read at ten stretches of the line |

**Voice** is a small model trained on human ratings (VoxSim, below). It
predicts the listener rating from three things:

- **Speaker similarity:** speaker-embedding similarity from WeSpeaker's
  ResNet34-LM (trained on VoxCeleb), run on the CPU with onnxruntime. The mean
  embedding of 137 unrelated speakers is subtracted first.
- **Pitch gap:** the gap in median pitch.
- **Formant gaps:** the gaps in median F1, F2 and F3.

It's constrained so that more similarity can only raise the rating and bigger
gaps can only lower it. Two details matter for the embedding. The model needs
each recording's mean subtracted from its input features, which sherpa-onnx
2.0.1 skips: without it, same-sex strangers averaged 0.72 cosine similarity
against 0.84 for the same person; with it, 0.16 against 0.69. And noise gates,
edits and synthetic speech leave stretches of exact digital silence, which
knock the embedding off; one int16 step of dither, as WeSpeaker uses in
training, keeps a gated clip at 0.97 similarity to its original instead of
0.88.

**Pitch and formants** come from Praat, through parselmouth. Each recording
picks its own formant ceiling: the one, from 4500 to 7000 Hz, whose tracks jump
least from frame to frame (the idea behind Barreda's FastTrack). Resonance
reads both recordings at one ceiling instead, the middle of the two speakers'
ceilings, and at 250 and 500 Hz either side of it, and takes the middle of the
five readings (see Formants, below).

**Before anything is measured** a recording is trimmed to its sound, relative
to its measured noise floor rather than to a fixed level below the peak, which
never fires in a room with audible noise. A short sound on its own at either
end is cut too: the click on Stop lands a second after the last word. Audio
Echo saved is read back as saved, not trimmed a second time, since trimming
isn't idempotent.

**Every target clip goes through vocal separation** (UVR-MDX-NET Voc_FT, on
the CPU) first, because clips from films and videos usually have music or
background under the voice. Takes aren't cleaned.

## Calibration

`tools/calibrate.py` sets every curve and weight from
[RAVDESS](https://zenodo.org/records/1188976). In RAVDESS, 24 actors say the
same two sentences in eight emotions, twice each. Each take goes through a
simulated microphone and room (band limit, reverb, noise), because in real use
the target is a clip and the take comes from someone's own microphone.

- **Curves:** each measure's curve puts 90 at the median distance between an
  actor and their own retake, and 20 at the median distance to a same-sex
  stranger.
- **Articulation's 20** is another sentence altogether: the same speaker
  saying a different line (320 pairs from VCC2018, which has 35 sentences from
  each of 8 speakers; RAVDESS's two sentences share four of their six words).
  On that curve a median stranger reading the right line scores 77, an actor's
  own other sentence 64, and an actor of the other sex 79: it doesn't hear
  whose voice it is.
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

The headline tells an actor's retake from a stranger at AUC 0.964. A speaker
check on the raw embedding similarity, at a threshold that lets 1% of
emotion-matched same-sex strangers through, accepts 88% of same-person pairs
through a microphone.

Resonance is calibrated as the app measures it: both recordings of a pair at
the middle of their two speakers' ceilings. The app has about five lines of a
voice and a few takes to choose each speaker's ceiling from, so a recording
here gets the middle of its own pick and the picks of four other recordings of
the same actor through the same channel.

To reproduce it, unzip RAVDESS's `Audio_Speech_Actors_01-24.zip` into
`cache/ravdess` and the VCC2018 evaluation set into `cache/vcc2018`, then:

```bash
python tools/calibrate.py cohort-mean
python tools/calibrate.py collect --ravdess cache/ravdess --vcc cache/vcc2018/vcc2018_evaluation --out cache/pairs.jsonl
python tools/calibrate.py fit cache/pairs.jsonl
```

`cohort-mean` also needs the PTDB-TUG and Hillenbrand data used below. These
steps write `src/echo_voice/speaker_mean.json` and
`src/echo_voice/calibration.json`; only the fitted numbers are in the
repository.

**Limits.** RAVDESS is acted speech: short lines from one studio, and the
microphone is simulated. A held-out check on four CMU ARCTIC clips agrees with
the scale:

| ARCTIC pair | Score |
| --- | --- |
| Another man, same line | 28 (Articulation 92) |
| A woman, same line | 30 (Articulation 96) |
| Same man, other sentence (passes the speaker check) | 72 (Articulation 24) |
| Same man, through a simulated mic | above 85 |

## Validation

`tools/validate.py` has a command for each check below.

### Known answers

The calibration pairs come in kinds whose right answer is known. A part should
score a stranger low, and should not care which of two people says the wrong
words. Median score of each kind of pair, in version 1.0.0 and now:

| Part | Same actor, other sentence | Same-sex stranger, same line | Other sex, same line |
| --- | --- | --- | --- |
| Voice | 88, 88 | 20, 20 | 11, 11 |
| Pitch | 87, 87 | 20, 20 | 5, 5 |
| Melody | 69, 75 | 20, 20 | 36, 33 |
| Resonance | 84, 66 | 20, 20 | 1, 4 |
| Articulation | 18, 64 | 20, 77 | 11, 79 |

Articulation used to compare MFCCs along the alignment, and failed this: it
scored a stranger saying the right line the same as the right actor saying the
other sentence, telling those two apart at AUC 0.51, a coin toss. Comparing
what the recogniser hears, it does at 0.81. Resonance now reads a different
sentence as a difference, because it compares the vowels the two recordings
line up on, and those are other vowels.

### Robustness on real takes

70 real practice takes against a breathy target voice were each scored,
changed in a way nobody would hear as a different performance, and scored
again. Points the score moved, as median / 9 takes in 10 within, in version
1.0.0 and now:

| Change | Voice | Melody | Resonance | Articulation | Rhythm | Headline |
| --- | --- | --- | --- | --- | --- | --- |
| A second of silence and a click after the take | 0.7 / 5.8, 0.0 / 1.0 | 0.5 / 12.8, 0.0 / 1.5 | 1.5 / 38.0, 0.0 / 6.1 | 4.1 / 19.1, 0.0 / 0.2 | 9.9 / 48.9, 0.0 / 1.4 | 2.6 / 11.1, 0.0 / 1.5 |
| A quarter second of faint room tone either side | 0.5 / 5.8, 0.0 / 2.7 | 0.1 / 6.1, 0.0 / 1.7 | 0.2 / 9.2, 0.0 / 9.2 | 0.2 / 1.2, 0.0 / 1.5 | 1.3 / 7.9, 0.0 / 1.3 | 0.3 / 4.3, 0.0 / 2.5 |
| 30 ms cut off each end | 0.8 / 5.9, 0.3 / 4.1 | 0.0 / 6.9, 0.2 / 10.0 | 0.3 / 6.2, 3.3 / 18.7 | 0.7 / 4.6, 0.9 / 3.1 | 1.5 / 37.6, 0.3 / 4.1 | 0.6 / 7.2, 1.3 / 5.6 |
| Hiss 40 dB under the speech | 2.5 / 9.4, 2.2 / 7.9 | 0.6 / 9.5, 0.1 / 9.6 | 2.5 / 24.8, 8.1 / 42.3 | 1.6 / 11.3, 0.9 / 5.6 | 0.6 / 31.3, 0.1 / 1.6 | 1.6 / 9.4, 2.1 / 9.7 |

Pitch moved by 0.2 points or less in every row. Resonance looks worse in the
last two rows, and needs reading with care: in 1.0.0 these takes scored a
median 13 on Resonance, on the flat floor of its curve, where a score can't
move; they now score a median 33, on its slope. In semitones the reading moves
by a median 0.053 for the cut and 0.058 for the hiss; read from all three
formants the same way it would move 0.072 and 0.065, and tell a retake from a
stranger worse (AUC 0.84 against 0.89). But the curve runs from 90 to 20 over
half a semitone, so **Resonance on one short take is a rough reading**: small
things move it 3 to 8 points for a typical take and 19 to 42 for one in ten.
The page also shows the last three takes together.

Scored against the voice's other lines, the wrong words, the same takes get a
median Articulation of 21 against 65 for the right line, and the right line
scores higher for 96% of them.

### Against human judgments

The whole scorer was run on the Voice Conversion Challenge 2018 similarity
test (Lorenzo-Trueba et al., Odyssey 2018; [data](https://doi.org/10.7488/ds/2337),
CC BY 4.0). It has 21,343 pairs: listeners heard a converted clip and a
natural recording of the same sentence and judged whether they were the same
speaker, from 1 (different, sure) to 4 (same, sure). Each converted clip is,
in effect, a machine's impression of the target voice.

| Measure | Per pair | Per system |
| --- | --- | --- |
| **Headline** | **0.48** | **0.90** |
| Voice | 0.55 | 0.94 |
| Pitch | 0.48 | 0.78 |
| Resonance | 0.39 | 0.91 |
| Articulation | −0.11 | −0.49 |

Pearson correlation with the mean listener rating. The per-system column
averages over 72 groups (system × compared-with × task), each with many
ratings; each pair has only 1.4 ratings on average, so listener noise caps the
per-pair numbers.

These listeners were asked one thing: is this the same speaker? A converted
clip is its source speaker's recording with the voice swapped, so it keeps the
source's sounds and timing. Articulation hears the sounds, which in this test
belong to the source speaker, so it falls as the ratings rise, as Melody and
Rhythm do. When Articulation compared MFCCs and so heard the voice, the
headline agreed more (0.53 and 0.94). Voice, the part that answers the
listeners' question, is where it was. This test can't say whether a headline
that counts delivery measures an impression better, because nobody in it was
imitating anyone's delivery, and there is no public listening test of real
impressions.

For comparison, Ahn et al. (2024, [arXiv:2407.18505](https://arxiv.org/abs/2407.18505))
report 0.51 for a pretrained ECAPA speaker model, 0.56 for it fine-tuned on
VoxSim, and 0.57 for SVSNet trained on VCC2018 itself, on their own split of
the same test. Voice's listener model was trained only on VoxSim and lands
next to their VoxSim-trained model.

```bash
python tools/validate.py vcc2018 --vcc cache/vcc2018 --out cache/vcc2018/scored.jsonl
```

### Real-world clips: VoxSim

VoxSim (Ahn et al., Interspeech 2024) has human ratings, from 1 (definitely
different) to 6 (definitely the same), of pairs of VoxCeleb1 clips:
celebrities in interviews and talk shows, saying different sentences. These
pairs can't test the parts that need the same words, but they test Voice,
Pitch and Resonance on recordings like the clips people practise with.
Pearson / Spearman correlation with the ratings on VoxSim's test split, 2,775
pairs, none used for training:

| Measure | All pairs | Different speakers only |
| --- | --- | --- |
| **Voice (listener model)** | **0.789 / 0.772** | **0.589 / 0.623** |
| Speaker-embedding similarity alone | 0.761 / 0.744 | 0.237 / 0.150 |
| Pitch alone | 0.493 / 0.484 | 0.414 / 0.457 |
| Resonance alone (median formants) | 0.400 / 0.467 | 0.326 / 0.492 |

- **Embedding similarity tells people apart but barely ranks strangers.** It
  matches the VoxSim paper's pretrained ECAPA baseline across all pairs
  (Pearson 0.761 here against 0.768 there), but among strangers it does
  little to order them by how close they sound, which is exactly what an
  impression is. Chen et al. (2026) found the same for speaker models trained
  this way.
- **Pitch and formant gaps fill the gap.** On their own they rank strangers
  three times better, so Voice combines all three. It was trained with
  `tools/train_similarity.py` on all 24,919 VoxSim training pairs, its
  settings chosen on 10% of the training speakers held out, and the test split
  scored once.
- **More data didn't move it.** Trained on 3,037 pairs it scored within 0.002
  of these numbers, so the limit is the five inputs, not the amount of data.
- **It's small.** Ahn et al.'s GPU-fine-tuned model reaches 0.829 across all
  pairs; this one gets about half the way from the pretrained baseline with
  five numbers and 200 small trees, shipped as plain JSON
  (`similarity_model.json`). The training script checks that the JSON gives
  exactly scikit-learn's predictions.

`tools/validate.py voxsim-fetch` downloads only the clips these pairs need
from the VoxCeleb1 zips on the Hugging Face mirror: 46,348 clips, about 11 GB,
each checked against the zip's CRC. It needs a Hugging Face login that has
accepted the mirror's terms.

### Pitch

The pitch tracker was checked against [PTDB-TUG](http://www2.spsc.tugraz.at/databases/PTDB-TUG/)
(Pirker et al., Interspeech 2011): 160 sentences from 10 women and 10 men,
with reference pitch from a laryngograph. Praat's autocorrelation tracker,
60–600 Hz, was the best of five:

| Tracker | Gross errors (>20% off) | Voicing wrong | Fine error |
| --- | --- | --- | --- |
| **Autocorrelation, 60–600 Hz (used)** | **2.1%** (women 3.6%, men 0.5%) | 4.5% | 11 cents |
| Cross-correlation, 60–600 Hz | 2.8% | 4.5% | 14 cents |
| Two-pass range, De Looze & Hirst 2008 | 2.4% | 4.3% | 12 cents |
| Two-pass range, Hirst 2011 | 2.6% | 4.2% | 12 cents |

The adaptive ranges fix some octave-down errors for women, but cause more
octave-up errors than they fix.

The page's live tracker is YIN (de Cheveigné & Kawahara 2002) in
`static/pitch.js`, run on 2048-sample frames as the browser sees them. On the
same recordings it gets 1.9% gross errors (3.0% for women, 0.2% for men) with a
15-cent fine error. It skips 8.4% of voiced frames, which only leaves gaps in
the live trace, and calls 1.0% of unvoiced frames voiced.

```bash
python tools/validate.py pitch --ptdb cache/ptdb
node tools/check_live_pitch.mjs cache/ptdb
```

### Formants

The formant tracker was checked against the hand-checked measurements of
[Hillenbrand et al. (1995)](https://github.com/santiagobarreda/hillenbrand_et_al_1995):
1,668 /hVd/ words from 45 men, 48 women and 46 children. Each speaker's 12
words were joined into one recording, and one ceiling was chosen per recording
without vowel labels, as the app does. Share of measurements more than 15% off:

| Ceiling | F1 | F2 | F3 | Children's F2 / F3 |
| --- | --- | --- | --- | --- |
| Fixed 5500 Hz | 1.8% | 5.6% | 6.9% | 10.4% / 11.3% |
| **Least frame-to-frame jumping, per recording (used)** | **0.9%** | **1.5%** | **2.8%** | **2.4% / 4.3%** |

Median errors are 1–2% throughout. The chosen ceilings come out where
phonetics puts them without being told: 5000 Hz for men, 5750 for women and
6000 for children.

**One ceiling for both sides of a comparison.** The per-recording pick is right
on average but not steady: over an actor's 60 RAVDESS recordings it lands on
5.4 different ceilings, and each change moves the formants a little. Formants
read at 4750 Hz also sit a little lower than the same audio read at 5250, so
read at two different ceilings a line isn't level with a copy of itself (it
scored 3 to 24 against itself on four of five lines). So Resonance reads both
recordings at one ceiling, the middle of the voice's (what its lines pick) and
the speaker's (what their last 15 takes on that voice picked), plus four more
around it.

**F2 and F3 only.** On a breathy voice near 190 Hz, Praat's F1 flips between
the first harmonic (about 250 Hz) and the formant (about 430): across takes of
one line its shift varied by 0.7 to 2.4 semitones, against 0.4–0.85 for F2 and
0.2–0.45 for F3, while the whole Resonance curve from 90 to 20 is under one
semitone. Read from F2 and F3 at the shared ceiling, Resonance tells a retake
from a stranger at AUC 0.89, against 0.85 with F1 included. With fewer than 60
aligned voiced frames it isn't read at all: shorter readings gave junk (one,
+8.4 semitones). It is unread on 1.4% of RAVDESS retakes and 2.8% of
strangers, those lines being short.

Typical formants, the listener model and the gender read keep each
recording's own pick, because that is how they were fitted.

### Live resonance

The live meter reads F1–F3 in the browser (`static/formants.js`). It follows
Praat's Burg tracker step by step: 50 ms frames, resampled to twice the formant
ceiling, pre-emphasis from 50 Hz, Praat's Gaussian window, ten coefficients,
and the three lowest roots between 50 Hz and 50 Hz under the ceiling. The page
reads the target's audio and the microphone with the same code, at the ceiling
the scorer uses, and says brighter or darker from F2 and F3 as the scorer
does, so a gap between them is a gap between the voices.

On the same audio and frames it lands a median 1.1% (F1), 0.8% (F2) and 0.7%
(F3) from Praat, with no lean either way. Which frames count matters more than
the fit, because typical formants are medians over the voiced frames: a frame
counts when its normalised autocorrelation, with the frame's mean removed, is
at least 0.55 and it is at least a tenth as loud as the loudest frame so far.
That finds 93% of Praat's voiced frames with 6% extra; it was chosen on two
sentences, and the check below uses ten others.

Checked on 120 recordings (12 VCC2018 speakers, the same 10 sentences each),
against what the app measures:

| | Result |
| --- | --- |
| Typical F1 / F2 / F3 against Praat at the same ceiling | 2.2% / 1.3% / 0.7% apart (median) |
| The gap between two speakers on a sentence, against the trainer's measure (660 pairs) | correlation 0.932; a median 1.4 points apart; within 3 points in 77% |
| Says the right way, brighter or darker, when the app puts them 3% or more apart | 98% of 467 pairs |
| The line page's meter, lined up first, against a take's Resonance (557 pairs it could be read on) | correlation 0.906; a median 1.7 points apart; within 3 points in 70%; right way in 95% |
| The same without lining up | correlation 0.737; a median 3.2 points apart; within 3 points in 48% |
| The pitch gap between two speakers, the live tracker against Praat | correlation 0.982; a median 0.31 semitones apart; over 1 semitone in 11% |

**Lining up.** Typical formants over a whole line depend on how long each
speaker dwells on each vowel, and no two people dwell the same. So the line
page's meter lines the two up first (`formants.alignedShift`): dynamic time
warping on the formants themselves, each side centred on its own typical
values, with an open end so a take in progress lines up with the part of the
line it has reached. The trainer's ring doesn't need to: a scored attempt on a
phrase is measured by typical formants too. One reading takes about 0.3 ms and
one alignment 0.6 ms.

```bash
python tools/live_resonance_ref.py --vcc cache/vcc2018 --out cache/live_resonance_ref.json
node tools/check_live_resonance.mjs cache/live_resonance_ref.json
```

**Limits.** The ring shows the last 1.2 seconds, so it moves by a few points as
different sounds pass through; its direction and rough size are what to go
by. All of this is studio speech read aloud; whispered and breathy voices,
where formants are hardest, haven't been checked this way.

### Clip cleaning

48 clean sentences (CMU ARCTIC, RAVDESS, VCC2018) were mixed with music: six
public-domain or CC0 recordings from Wikimedia Commons (orchestra, piano,
strings, oboe, mandolin) and a synthetic drum-and-bass loop. Each mix was
scored against the clean original, where 100 is a perfect match. Medians,
with the worst 10% in brackets:

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

```bash
python tools/validate.py cleaning --music cache/music
```

### The word check

Tested on RAVDESS, where every actor says the same two sentences. The target
was clean and the take went through the simulated microphone; the target's
words also came from the recogniser, as they do in the app.

| Case | Flagged |
| --- | --- |
| Same sentence, other actor or emotion (false alarms) | 7.1% |
| The other sentence ("Dogs are sitting" for "Kids are talking") | 100% |
| Take cut to its first 60% | 90% |
| Take cut to its last 60% | 96% |

Through the microphone the recogniser gets 96.8% of words right. One misheard
word in a correct take is common ("Docs" for "Dogs"), so a take is flagged only
when at least 2 words, or 15% of the line, are off; flagging on any single word
gave 20% false alarms. Even 7% is too many to let the word check lower the
score, so it only gives hints.

Whisper runs at temperature 0 only. Its default retries an unsure pass with
random sampling, so the same audio came back differently from run to run: 4
transcripts in 6 runs of one test recording. At temperature 0 all 6 matched.

### Splitting long recordings

**Speakers.** Each line of 1.5 s or more gets a speaker embedding, and the
lines are grouped by average-linkage clustering while the average cosine
between two groups is at least 0.4. A group of fewer than 3 lines, or under 5%
of them, isn't called a speaker; its lines join the nearest speaker if they
are that close to it, and are left unsure if not.

| Recordings (`tools/validate.py speakers`) | Speaker count right | Lines to the right speaker |
| --- | --- | --- |
| VCC2018 studio speakers, 1–4 per recording (240) | 100% | 100% |
| VoxCeleb1 interviews from YouTube, 1–3 per recording (90) | 100% | 100% |
| A 25-minute video of one soft-spoken speaker | 1 found | 174 of 175, 1 unsure |

The 25-minute video stayed one speaker at every threshold from 0.3 to 0.5.
Lower thresholds start merging speakers (at 0.3, 88% of 4-speaker studio
mixes were right); higher ones leave more lines unsure. VoxCeleb clips are
about 8 s long where real lines are often 2–5 s, and speakers in different
studios are easier to tell apart than two people on one microphone; the
VCC2018 test, all one studio, covers the second case.

**Other voice.** Test recordings built from VCC2018: for each speaker, 30 of
their sentences mixed with 8 by others (3 each by two speakers of the same sex,
2 by one of the other sex), all from one studio, 36 recordings in all
(`tools/validate.py other-voice`). At the cosine threshold of 0.4:

| Lines | Tagged "other voice" |
| --- | --- |
| The main speaker's (873) | 0.2% |
| Another speaker's (259) | 96.1% |
| Another speaker of the same sex (190) | 94.7% |

**A real video.** 25 minutes of one soft-spoken speaker, with music and
background ambience, gave 200 lines, most of them 1.4–7.2 s long (10th to 90th
percentile), in 55 s. 9 were tagged whispered, 5 unclear, and 1 other voice (a
1.6 s "hmm"). Targets made from a normal line, a whispered one and one over
background ambience each scored 100 against themselves after cleaning; on the
whispered one, Pitch, Melody and Resonance were "–". The suggestions' pitch
rule ruled out 19 of its 185 untagged lines, including two that had been
suggested before, at 98–99 Hz against a typical 191.

### Word timings

**Word edges.** Whisper's word times are approximate and often end a word
early, so a word played on its own was cut off. Edges are moved onto the audio
(`line.refine_word_times`): between two words to the quietest point between
them, and the last word to where the sound stops. Across 118 words from 11
lines, words that ended while the sound was still loud fell from 16.9% to 0.8%.

**Words said in one breath.** Adjacent words are joined into one unit when the
sound dips less than 6 dB below the quieter of the two between them
(`line.speech_units`). On two real lines, every boundary heard as a break
dipped 6.5 dB or more, and every run-together one under 5.2. A run over 1.3 s
is split at its deepest dip, and a unit under 0.15 s joins the neighbour it
dips least from. That threshold comes from 15 boundaries, so it's a starting
point, not a validated number.

### The trainer

`scoring.word_drill` uses the same measures as the per-phrase breakdown,
restricted to one phrase against the same phrase. On synthetic vowels with
formants moved ±10% it reads +9.2% and −9.9%. Another man saying the same word
reads 11.5% darker and 6 semitones lower, matching his whole-line result.

An attempt is measured at the formant ceiling the line and the speaker's takes
settle on, never at one it picks for itself: a phrase is too short to choose
well. On 182 phrases from two VCC2018 speakers, a phrase cut from its line and
scored against that line picked the line's ceiling only 20% of the time, and
its resonance read more than 3% off in 36% of cases. At the line's ceiling,
that falls to 13%.

### Gender read

A logistic regression on median pitch and the mean F2–F3 shift
(`gender.py`, `tools/train_gender.py`), trained on the speakers' sex in two
corpora and tested on a third, with no speaker in common:

| Trained on | Tested on | Clips right | Speakers right |
| --- | --- | --- | --- |
| RAVDESS + VCC2018 | PTDB-TUG | 96.9% (AUC 0.996) | 20 of 20 |
| RAVDESS | VCC2018 | 90.0% | 11 of 12 |
| VCC2018 + PTDB-TUG | RAVDESS (emotional acting) | 80.2% | 21 of 24 |

F1 is left out. It moves most with the vowel and with a soft, breathy
delivery: one such voice's F1 read about 330 Hz against 610 for a woman
reading aloud, which made her read as male. The labels are the speakers' sex,
not listeners' judgments of each clip, so the read says how a voice's pitch
and resonance compare with women's and men's: 80 and up "female", 60–80 "leans
female", 40–60 "in between", 20–40 "leans male", under 20 "male".

### Vocal fry

The share of a recording's voiced sound more than 7 semitones under its
speaker's usual pitch. Against the laryngograph in PTDB-TUG, that finds 60% of
creaky frames and fires on 1.3% of ordinary ones, and a speaker's share follows
the laryngograph's at r = 0.81. Ordinary speech reads 2–13%, so only a gap
against the target says anything. A rule on period irregularity found 17% of
creaky frames and fired on 13% of ordinary speech, so it wasn't used. Nothing
calibrates fry as a part of the score yet, so it is shown and not counted.

### The end of a take

The page ends a take once enough of the line has been said and it has been
quiet for a little longer than the line's longest pause (`static/ending.js`).
Speech is judged against the room: 10 dB over the 10th-percentile level, and
within 35 dB of the 95th. Digital silence before the first sound (a device
starting up) is left out of the room's level; digital silence later on (a
noise gate between words) isn't.

Saved takes, replayed with room noise added at 45 and 60 dB under the peak:
none was ended while the line was still being said, and a take ended a median
1.0–1.3 s after its last word (9 in 10 within about 2 s).

```bash
node tools/check_ending.mjs data
```

## Lessons

Things that were wrong at some point, and are now tested:

- **A fixed formant ceiling** (5500 Hz) mis-tracked women's and children's F2
  and F3. The ceiling is chosen per recording.
- **Trimming at a fixed level below the peak** never fired in a room with
  audible noise. It now cuts relative to the measured noise floor.
- **Saved audio trimmed twice.** Trimming isn't idempotent: on a separated clip
  a second trim moved the ends by up to 60 ms, and on a clip that starts in
  digital silence by 160 ms, away from the word timings measured on it.
- **The click on Stop** lands a second after the last word; kept, the quiet
  before it was lined up against the end of the target, and moved Rhythm by
  49 points for one take in ten.
- **Timing as half of Rhythm.** Read step by step along the alignment it told a
  retake from a stranger at AUC 0.59, and its curve from 90 to 20 spanned only
  0.054 to 0.070, so any wobble moved Rhythm. Read at ten stretches of the line
  it reaches 0.71, and it counts in proportion to that.
- **Pace from everything with sound** read takes up to 50% slow, because a
  breath after the last word has sound. Pace is the length from the first to
  the last voiced sound, without a voiced blip on its own at either end.
- **Lines that start mid-sentence.** The recording's transcript can miss a
  sentence's first words; read by itself, the cut shows it, because its first
  word isn't the line's.

## Tried and not used

- **A background-noise tag on lines.** It compared the level between a line's
  words with the level of the words, and tagged 136 of the 200 lines of the
  25-minute video, where separating a sample showed the background sat 8–12 dB
  under the voice. Separation handles that easily, and the level between words
  also counts breaths, which aren't background.
- **Larger or different speaker models.** On the VCC2018 judgments, WeSpeaker
  ResNet293 and CAM++ and 3D-Speaker ERes2Net and CAM++ all agree with
  listeners within 0.02 of each other. ResNet293 is the best verifier, four
  times slower, and slightly *worse*, matching Chen et al. (2026,
  [arXiv:2609.33999](https://arxiv.org/abs/2609.33999)): lower verification
  error doesn't buy agreement with listeners.
- **Averaging three speaker models** added 0.015 rank correlation for three
  times the work.
- **Projecting embeddings onto a few principal directions** lowered agreement,
  from 0.48 to 0.31 at three dimensions. Chen et al.'s low-dimensional gains
  came from training with a bottleneck, not projecting afterwards.
- **Voice quality (CPPS, harmonics-to-noise)** as a part of the score. On
  RAVDESS they barely tell a retake from a stranger (AUC 0.61 and 0.59), and
  the simulated microphone alone moves CPPS by 1 dB, about twice the typical
  gap between a retake and a stranger.
- **Speaking-style and voice-quality gaps as Voice inputs:** pitch range,
  speaking rate, voiced share, spectral envelope, CPPS and HNR. Together they
  raised VoxSim's test split overall (0.772 to 0.802) but not for strangers
  (0.624 to 0.619), and an impression is always a stranger. The envelope also
  carries the microphone and room.
- **Checking melody and rhythm against VCC2018.** Voice conversion keeps the
  source speaker's timing and intonation, so in that data these parts track
  the source, not the voice people hear. The evidence for them comes from
  mimicry research: speaking rate is what amateur impersonators change most
  (Vestman et al. 2020, [arXiv:1906.01454](https://arxiv.org/abs/1906.01454)).

## Open questions

- **Real microphones.** The calibration's microphone is simulated. The check
  that matters is the real person recorded on a phone and on a computer: they
  should land near 90 against each other, and impressions of them well below.
- **A better Voice.** The listener model has reached the limit of its five
  inputs. Fine-tuning the speaker network on the ratings, as Ahn et al. did,
  or training it with a prototypical loss (Chen et al. 2026) would go further;
  either needs a GPU.
- **Listeners for delivery.** Melody, rhythm and articulation are calibrated
  on known answers but not yet checked against people's judgments of
  impressions.
