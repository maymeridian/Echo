# Echo

Echo is a local web app for practising voice impressions. Give it a video of
the voice you want to imitate, and it finds the lines worth practising. Record
yourself saying one, and it scores how close you got from 0 to 100, part by
part, then tells you the one thing to work on next.

The score is built to agree with what people hear: about **90** is the real
person saying the line again on another microphone, and about **20** is a
stranger saying the same line. Everything runs on your own computer, and
nothing you record leaves it.

Version 1.1.1 · [Changelog](CHANGELOG.md) · [Project page with screenshots](https://maymeridian.dev/projects/echo/) · MIT licence

<img src="https://maymeridian.dev/projects/echo/img/line-dark.webp" alt="A take's result in Echo: the target's pitch line with the take's drawn over it, a score of 44 out of 100, and the six parts that make it up" width="560">

## What it does

- **Finds lines in a video.** Paste a YouTube link, or upload a recording.
  Echo transcribes it, splits it into lines, tells the speakers apart and
  picks the five best lines to practise.
- **Scores a take** on six parts: voice, pitch, melody, resonance,
  articulation and rhythm. Each is calibrated on recordings where the right
  answer is known.
- **Coaches one thing at a time**: the part that would lift the score most,
  with a drill for it and the words that were furthest off.
- **Shows your voice live** while you speak: your pitch drawn over the
  target's, and a meter for resonance, darker or brighter than the target.
- **Trains one part on one phrase**, such as moving pitch while holding
  resonance, with an answer in about a second.
- **Scores the voice, not one line.** A voice check takes one take on each of
  several lines, with no retries, and averages them.
- **Keeps every take**, so you can see whether you're getting closer.

## Install and run

You need Python 3.10 or later and a browser that can use the microphone
(Chrome, Edge or Firefox). YouTube links also need a JavaScript runtime for
yt-dlp: Node, Deno, Bun or QuickJS. There is nothing else to install: audio is
decoded by PyAV, which brings its own FFmpeg, and every model runs on the CPU.

On Windows (PowerShell):

```powershell
git clone https://github.com/maymeridian/Echo
cd Echo
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\echo-voice serve
```

On macOS or Linux:

```bash
git clone https://github.com/maymeridian/Echo
cd Echo
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/echo-voice serve
```

Then open http://127.0.0.1:8000. Echo is developed and tested on Windows 11;
the macOS and Linux commands are the usual equivalents and haven't been tried.

The models download on first use, checked against pinned hashes: a speaker
model (26 MB) and a vocal separation model (67 MB) to `~/.cache/echo` (set
`ECHO_CACHE` to put them elsewhere), and Whisper base.en (about 140 MB)
through faster-whisper. Your voices, lines and takes are kept in `data/`.

The server listens only on this computer, answers only to its own address,
and only its own page can change anything, so another website open in your
browser can't use it. To practise from another device on your network, run
`echo-voice serve --host 0.0.0.0`; it then answers to any address, so do that
only on a network you trust.

## Quick start

1. **Make a voice.** Paste a YouTube link to someone speaking on the first
   screen. Echo downloads it, splits it into lines, and adds the five best
   to a new voice named after the channel. A 25-minute video took 75
   seconds.
2. **Pick your microphone** in the sidebar. Echo remembers it.
3. **Open a line** and listen to it a few times. **♪** plays the target's
   typical note; **Warm up** shows your pitch against it without saving
   anything.
4. **Record a take** (or press Space). It stops by itself about a second
   after your last word. The result shows the score, each part, and what the
   coach suggests next.
5. **Train a part** with **Train ›** next to it, phrase by phrase.
6. **Run a voice check** from the voice's page once a few lines feel good.

The [guide](docs/guide.md) covers every screen in detail.

## What the score means

| Part | Weight | What it compares |
| --- | --- | --- |
| Voice | 22.8% | The rating listeners would likely give the pair for "same speaker?", from a model trained on human ratings |
| Pitch | 14.2% | Median pitch, in semitones |
| Melody | 11.3% | The shape of the pitch line, once its level is taken out |
| Resonance | 18.4% | The second and third formants on matching vowels: brighter or darker |
| Articulation | 12.7% | How each sound is shaped, as a speech recogniser hears it |
| Rhythm | 20.6% | The line's pace, and how evenly the take keeps it |

Each part has its own curve. About 90 is where an actor lands against their
own retake through a different microphone, and about 20 is where a stranger
of the same sex lands saying the same line. Articulation is the exception: a
stranger reading the same line makes nearly the same sounds, so its 20 is a
different sentence altogether, and a stranger reading the right line scores
around 77. Each part counts in proportion to how well it tells a retake from
a stranger, and the headline is their weighted mean put back on the same
scale.

Two reads are shown beside the parts and not counted: a **gender read**
(how female or male the take sounds beside the target) and **vocal fry**
(how much of each recording drops into a low creak).

The take is lined up with the target sound by sound before it is measured,
so the parts that compare the line itself need you to **say the same words**.

## How accurate it is

Every measurement was checked against data with known answers. The
[method](docs/method.md) has the full tables and the commands to reproduce them.

| Check | Data | Result |
| --- | --- | --- |
| Real person against a stranger | RAVDESS: 24 actors, the take through a simulated microphone | retake scored above stranger in 96.4% of pairs |
| Agreement with listeners on "same speaker?" | VCC2018 listening test, 21,343 rated pairs | Voice part 0.55 per pair, 0.94 per system |
| Ranking strangers the way listeners do | VoxSim, 2,775 rated pairs | 0.62, against 0.15 for a speaker-recognition model alone |
| Pitch | PTDB-TUG, laryngograph reference | 2.1% of frames off by more than 20% |
| Formants | Hillenbrand et al. (1995), 1,668 hand-checked vowels | 1.5% of F2 and 2.8% of F3 readings more than 15% off |
| Telling speakers apart in a video | 330 recordings with one to four speakers | every speaker found, every line given to the right one |
| Music under a clip | 48 lines mixed with music as loud as the voice | score against the clean line rises from 65 to 100 after vocal separation |
| The end of a take | 62 real takes, replayed with room noise | none cut off mid-line; ends a median 1.3 s after the last word |

## Limits

- **The calibration's microphone is simulated** (band limit, room echo,
  noise). Whether the real person lands near 90 across real phone and
  computer microphones hasn't been checked yet.
- **Breathy and whispered voices are the hard case.** Pitch and formants are
  least dependable there, and Resonance on a single short take moves by
  several points with small changes. Judge it over a few takes; from the
  third take on a line, Echo also shows your last three together.
- **The listening tests only asked "same speaker?".** Nobody in them was
  imitating anyone's delivery, so melody, rhythm and articulation are
  calibrated, but not yet checked against listeners.
- **Lines are split at pauses**, not where the speaker changes, so two
  people with no pause between them can share a line.
- **English only.**

## From the command line

```bash
echo-voice serve [--host 127.0.0.1] [--port 8000] [--data data]
echo-voice score target.wav take.wav [--json] [--no-clean]
echo-voice import "https://www.youtube.com/watch?v=..." [--name "Narrator"]
echo-voice import path/to/recording.mp4
```

`score` prints the score of one recording against another. `import` splits a
long recording or a video link into lines, as the page does.

## Development

```bash
python -m pip install -e ".[dev,e2e]"
python -m pytest -q
ruff check .
```

The tests cover synthetic voices, real speech (four CMU ARCTIC clips), the
API, and the page itself: `tests/test_browser.py` drives it in headless Edge or
Chrome with the microphone fed from a WAV file, and is skipped when Playwright
isn't installed. The full suite takes about eight minutes.

```
src/echo_voice/
  analysis.py        a recording -> pitch, formants and what a recogniser hears, on one 10 ms frame grid
  scoring.py         target + take -> distances -> calibrated scores; hints, coach, word breakdown, voice checks, drills
  identity.py        speaker embeddings (WeSpeaker ResNet34-LM through ONNX)
  similarity.py      the listener model behind Voice (similarity_model.json)
  gender.py          the gender read (gender_model.json)
  line.py            speech recognition, word timings, the word check
  clean.py           vocal separation for target clips
  sources.py         long recordings and links: download, decode, transcribe, split, tell speakers apart
  autopick.py        a voice's best lines: suggest, add, auto-pick
  store.py           voices, lines, takes, checks and recordings on disk
  server.py          the FastAPI app, and the recordings' background jobs
  cli.py             echo-voice serve, score, import
  static/            the page, with live pitch (pitch.js), live formants (formants.js) and the end of a take (ending.js)
tools/
  calibrate.py       fits calibration.json and speaker_mean.json
  validate.py        checks each measurement against reference data
  train_similarity.py, train_gender.py
  check_live_pitch.mjs, check_live_resonance.mjs, live_resonance_ref.py, check_ending.mjs
```

## Licence and credits

The code is under the MIT licence ([LICENSE](LICENSE)).

Echo stands on other people's work, which keeps its own terms:

- **Models**, downloaded on first use and not included here: WeSpeaker
  ResNet34-LM (speaker embeddings, from the sherpa-onnx releases),
  UVR-MDX-NET Voc_FT (vocal separation, also from the sherpa-onnx releases)
  and Whisper base.en (speech recognition, through faster-whisper).
- **Fitted numbers** in `src/echo_voice/`: `calibration.json` was fitted on
  RAVDESS (Livingstone & Russo, 2018; CC BY-NC-SA 4.0) and VCC2018,
  `similarity_model.json` on VoxSim ratings (Ahn et al., 2024; CC BY 4.0) of
  VoxCeleb1 clips, `gender_model.json` on RAVDESS and VCC2018 (Lorenzo-Trueba
  et al., 2018; CC BY 4.0), and `speaker_mean.json` on recordings from
  RAVDESS, PTDB-TUG (Pirker et al., 2011) and Hillenbrand et al. (1995). None
  of those recordings are included.
- **Test clips:** four CMU ARCTIC recordings in `tests/data/arctic`, under
  their own licence (`COPYING` there).
- **Libraries:** Praat through parselmouth, librosa, onnxruntime, sherpa-onnx,
  faster-whisper, FastAPI and yt-dlp, among others in `pyproject.toml`.
