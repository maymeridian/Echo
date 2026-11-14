# voice-match

Practice a voice impression and measure how close you get.

Add a short clip of the voice you're imitating, record yourself saying the same
line, and get a 0–100 match with a breakdown of what to change: pitch, melody,
resonance, articulation and rhythm. Every take is kept, so you can see whether
you're getting closer.

## Run it

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\voicematch serve
```

Then open http://127.0.0.1:8000. Clips and takes are stored in `data/`, which
is not tracked by git.

To score two files without the app:

```powershell
.\.venv\Scripts\voicematch score target.wav take.wav
```

## How the score works

The take is time-aligned to the target (dynamic time warping over MFCCs), so
each measurement compares the same moment of the line in both recordings. This
means **both recordings must say the same words**.

| Part | Weight | What is compared |
| --- | --- | --- |
| Pitch | 20% | Median pitch, in semitones |
| Melody | 15% | Shape of the pitch contour once the level difference is removed |
| Resonance | 25% | Formants F1–F3 on aligned voiced frames (brighter or darker) |
| Articulation | 25% | MFCC distance along the alignment (how the sounds are shaped) |
| Rhythm | 15% | Overall length, and how much the timing wanders inside the line |

Pitch and formants come from Praat (via parselmouth); MFCCs and alignment from
librosa.

**The percentages are not calibrated yet.** Each distance is mapped to 0–100
with a hand-picked curve in `src/voicematch/scoring.py`. The scorer has only
been checked against synthetic voices (see `tests/`), where it reacts to the
right property in the right direction. It has not been checked against real
recordings, so treat the number as "higher is closer", not as an accuracy.

## Layout

```
src/voicematch/
  analysis.py   recording -> pitch, formants, MFCCs on one 10 ms frame grid
  scoring.py    target + take -> scores, details, hints, pitch chart data
  store.py      targets and takes on disk
  server.py     FastAPI app
  cli.py        `voicematch serve`, `voicematch score`
  static/       the page: records, converts to 16 kHz WAV, shows results
tests/          synthetic-voice tests for the scorer and the API
```

## Next

1. **Calibrate against real voices.** Collect same-line recordings by the same
   person, by different people, and of different lines, and set the curves so
   0 means "an unrelated speaker" and 100 means "the same person again".
2. **Speaker-identity score.** Add a speaker-embedding similarity (ECAPA or
   similar) as the headline "sounds like them" number. Unlike the current
   measures it does not need the same words.
3. **Clip cleaning.** Strip music and background noise from target clips
   (source separation) before analysis, and let a clip be trimmed in the page.
4. **Live feedback.** Show pitch against the target while recording.
