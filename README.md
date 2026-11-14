# voice-match

Practice a voice impression and measure how close you get.

Add a short clip of the voice you're imitating, record yourself saying the same
line, and get a 0–100 match with a breakdown of what to change: voice identity,
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

The page also shows **"Passes a speaker check"** when a speaker-verification
model would accept the take as the same person. That threshold lets 1% of
same-sex strangers through and accepts 88% of the real person's own takes.

## How it works

The take is time-aligned to the target (dynamic time warping over MFCCs), so
most measurements compare the same moment of the line in both recordings. That
part needs **both recordings to say the same words**.

| Part | Weight | What is compared |
| --- | --- | --- |
| Voice | 24% | Speaker-embedding similarity; doesn't depend on the words |
| Pitch | 15% | Median pitch, in semitones |
| Melody | 12% | Shape of the pitch contour once the level difference is removed |
| Resonance | 16% | Formants F1–F3 on aligned voiced frames (brighter or darker) |
| Articulation | 19% | MFCC distance along the alignment (how the sounds are shaped) |
| Rhythm | 15% | Length from first to last voiced sound, and timing within the line |

Voice uses WeSpeaker's ResNet34-LM (trained on VoxCeleb), run on the CPU with
onnxruntime, with the mean embedding of 137 unrelated speakers subtracted.
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
| **Headline** | **0.52** | **0.93** |
| Voice | 0.49 | 0.92 |
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
- **0.57:** SVSNet, a model trained on VCC2018 itself.

Their split differs from this one, so the comparison is approximate.

Fitting the weights directly to these ratings, with whole systems held out,
would raise the rank correlation on conversions against their target from
0.38 to 0.40. Most of that gain comes from dropping melody and rhythm, for the
reason given under "Tried and not used". The weights stay as calibrated.

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
  calibration.json   curves and weights from tools/calibrate.py
  speaker_mean.json  mean speaker embedding of 137 unrelated speakers
  store.py           targets and takes on disk
  server.py          FastAPI app
  cli.py             `voicematch serve`, `voicematch score`
  static/            the page: records, converts to 16 kHz WAV, shows results
tools/calibrate.py   fits calibration.json (RAVDESS) and speaker_mean.json
tools/validate.py    checks pitch, formants and the whole score against reference data
tests/               synthetic voices, real speech (CMU ARCTIC), API, the page
```

`tests/test_browser.py` drives the page in headless Edge or Chrome with the
microphone fed from a WAV file. It runs when Playwright is installed
(`pip install -e ".[e2e]"`) and is skipped otherwise. `tests/data/arctic` holds
four CMU ARCTIC clips under their own licence (`COPYING` there).

## Next

1. **Check the line with speech recognition.** Transcribe the target once and
   each take, then compare the words. This would show the line to say, point
   out skipped or added words, and stop the line-match parts from scoring a
   different sentence. Sound-based alignment can't do this: it was tried, and
   flagged words as missing for 65% of strangers who said the whole line.
2. **Clip cleaning.** Strip music and background noise from target clips
   before analysis, using vocal separation and a denoiser, and let a clip be
   trimmed in the page. Check any off-the-shelf pipeline against its reference
   before trusting it: sherpa-onnx's speaker pipeline wasn't right.
3. **Check the scale with real microphones.** Record a few people saying lines
   twice on different mics, and confirm the real person still lands near 90.
4. **Check against VoxSim.** VoxSim has 69k human similarity ratings on
   real-world VoxCeleb clips (Ahn et al., Interspeech 2024,
   [arXiv:2407.18505](https://arxiv.org/abs/2407.18505)). Its ratings are
   open (CC BY 4.0) and are in `cache/voxsim`. The VoxCeleb1 audio needs a
   download key from https://mm.kaist.ac.kr/datasets/voxceleb/, which asks for
   your name and email.
5. **Live feedback.** Show pitch against the target while recording.
