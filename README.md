# voice-match

Practice a voice impression and measure how close you get.

Add a clip of the voice you're imitating, trim it to one line in the page,
record yourself saying the same line, and get a 0–100 match with a breakdown of what to change: voice identity,
pitch, melody, resonance, articulation and rhythm. Every take is kept, so you
can see whether you're getting closer.

## Run it

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\voicematch serve
```

Then open http://127.0.0.1:8000. Clips and takes are stored in `data/`, which
is not tracked by git. The first run downloads the speaker model (26 MB) to
`~/.cache/voicematch`; set `VOICEMATCH_CACHE` to put it elsewhere.

To score two files without the app:

```powershell
.\.venv\Scripts\voicematch score target.wav take.wav
```

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
below. These steps write `src/voicematch/speaker_mean.json` and
`src/voicematch/calibration.json`. Only the fitted numbers are in
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
target. There the headline's rank correlation dropped from 0.378 to 0.365
(bootstrap 95% CI for the change, −0.016 to −0.010), even though Voice on its
own improved from 0.371 to 0.393. The likely reason is that Voice now leans on
the pitch gap, which is a weak signal in voice-converted speech, and the
headline also counts pitch separately. The change was kept for two reasons:

- **Across the rest of VCC2018 the headline improved:** 0.524 to 0.527 per
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
| **Voice (listener model, used)** | **0.785 / 0.770** | **0.590 / 0.621** |
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
  with `tools/train_similarity.py` on 3,037 VoxSim training pairs.
- **The model does both jobs.** It ranks strangers at 0.62 and beats the
  pretrained ECAPA baseline across all pairs. Ahn et al.'s GPU-fine-tuned
  model reaches 0.829; this one gets about half the way there from six numbers
  and 200 small trees. It ships as plain JSON (`similarity_model.json`), and
  the training script checks that the JSON gives exactly scikit-learn's
  predictions.

`tools/validate.py voxsim-fetch` downloads only the 10,953 clips these pairs
need, about 2.8 GB, from the VoxCeleb1 zips on the Hugging Face mirror. Each
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

Through the mic, the recogniser gets 96.8% of words right. One misheard word
in a correct take is common ("Docs" for "Dogs"), so a take is flagged only
when at least 2 words, or 15% of the line, are off. Flagging on any single
word gave 20% false alarms. Even 7% is too many to let the word check lower
the score, so it only produces hints.

### Tried and not used

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
- **Melody and rhythm can't be checked against VCC2018.** Voice conversion
  keeps the source speaker's timing and intonation, so in that data these parts
  track the source, not the voice people hear. The evidence for them comes from
  mimicry research: speaking rate is what amateur impersonators change most
  (Vestman et al. 2020, [arXiv:1906.01454](https://arxiv.org/abs/1906.01454)).

## Layout

```
src/voicematch/
  analysis.py        recording -> pitch, formants, MFCCs on one 10 ms frame grid
  identity.py        speaker embeddings (model download, features, ONNX)
  scoring.py         target + take -> distances -> calibrated scores, hints, chart
  line.py            speech recognition and the word-by-word comparison
  clean.py           vocal separation for target clips
  models.py          model downloads, checked against pinned hashes
  similarity.py      the listener-similarity model behind Voice
  similarity_model.json  its trees, from tools/train_similarity.py
  calibration.json   curves and weights from tools/calibrate.py
  speaker_mean.json  mean speaker embedding of 137 unrelated speakers
  store.py           targets and takes on disk
  server.py          FastAPI app
  cli.py             `voicematch serve`, `voicematch score`
  static/            the page: records, trims, live pitch (pitch.js), shows results
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

1. **Check the scale with real microphones.** Record a few people saying lines
   twice on different mics, and confirm the real person still lands near 90.
2. **Train Voice on all of VoxSim.** The listener model was trained on
   3,037 of VoxSim's 24,921 training pairs. Fetching the rest of the clips
   (`voxsim-fetch --train-pairs 24921`, about 8 GB) and re-running
   `tools/train_similarity.py` should help, since tree models gain from more
   data. Beyond that, fine-tuning the speaker network itself on the ratings
   (Ahn et al. 2024) or training it with a prototypical loss (Chen et al. 2026)
   needs a GPU.
