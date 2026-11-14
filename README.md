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
same-sex strangers through and accepts 86% of the real person's own takes.

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
onnxruntime. Pitch and formants come from Praat (via parselmouth); MFCCs and
alignment from librosa.

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
| Same actor, same line, clean | 73 / 98 / 100 |
| Same actor, same line, through a mic | 48 / 90 / 100 |
| Same actor, different emotion | 32 / 71 / 97 |
| Same actor, other sentence | 27 / 57 / 84 |
| Same-sex stranger, same line | 11 / 20 / 41 |
| Other-sex stranger, same line, same emotion | 11 / 16 / 28 |

To re-run it, download `Audio_Speech_Actors_01-24.zip` from Zenodo and unzip
it into `cache/ravdess`, then:

```powershell
.\.venv\Scripts\python tools\calibrate.py collect --ravdess cache\ravdess --out cache\pairs.jsonl
.\.venv\Scripts\python tools\calibrate.py fit cache\pairs.jsonl
```

This writes `src/voicematch/calibration.json`. Only the fitted numbers are in
git; RAVDESS itself (CC BY-NC-SA 4.0) stays in the ignored `cache/`.

**Limits.** RAVDESS is acted speech: short lines from one studio. The
microphone is simulated, not real. Clips from films or videos with music under
them will score lower until clip cleaning exists (see Next). A held-out check on
four CMU ARCTIC clips agrees with the scale:

| ARCTIC pair | Score |
| --- | --- |
| Another man, same line | 30 |
| A woman, same line | 28 |
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

## Layout

```
src/voicematch/
  analysis.py        recording -> pitch, formants, MFCCs on one 10 ms frame grid
  identity.py        speaker embeddings (model download, features, ONNX)
  scoring.py         target + take -> distances -> calibrated scores, hints, chart
  calibration.json   curves and weights from tools/calibrate.py
  store.py           targets and takes on disk
  server.py          FastAPI app
  cli.py             `voicematch serve`, `voicematch score`
  static/            the page: records, converts to 16 kHz WAV, shows results
tools/calibrate.py   fits calibration.json from RAVDESS
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
4. **Live feedback.** Show pitch against the target while recording.
