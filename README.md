# Echo

A local web app for practising voice impressions. Paste a link to a video of
the voice you're imitating; Echo finds the lines worth practising, scores each
take from 0 to 100, part by part, and names the one thing to work on next.

The score is calibrated to what people hear: about **90** is the real person on
another microphone, about **20** a stranger saying the same line. Everything
runs locally; nothing you record leaves your computer.

[Project page](https://maymeridian.dev/projects/echo/) · [Guide](docs/guide.md) · [Method](docs/method.md) · [Changelog](CHANGELOG.md) · MIT licence

<img src="https://maymeridian.dev/projects/echo/img/line-dark.webp" alt="A take's result in Echo: the target's pitch line with the take's drawn over it, a score of 44 out of 100, and the six parts that make it up" width="560">

## Features

- **Lines from a video.** Transcribes a YouTube link or an upload, splits it
  into lines, separates the speakers and picks the five best lines.
- **Six-part score:** voice, pitch, melody, resonance, articulation and rhythm,
  each calibrated on recordings with known answers.
- **A coach** that picks the part worth the most points and gives a drill for it.
- **Live feedback:** your pitch over the target's, and a resonance meter, as
  you speak.
- **Phrase-by-phrase training**, including moving pitch while holding resonance.
- **Voice checks:** one take on each of several lines, no retries, averaged.

## Installation

Requires Python 3.10+ and a browser with microphone access (Chrome, Edge or
Firefox). YouTube links also need Node, Deno, Bun or QuickJS for yt-dlp.

```bash
git clone https://github.com/maymeridian/Echo
cd Echo
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"     # Windows: .\.venv\Scripts\python
.venv/bin/echo-voice serve                       # Windows: .\.venv\Scripts\echo-voice serve
```

Open http://127.0.0.1:8000. On first use Echo downloads its models (about
230 MB in all, checked against pinned hashes). Data is kept in `data/`.

The server listens on this computer only and refuses requests from other
websites. `echo-voice serve --host 0.0.0.0` opens it to your network; use that
only on a network you trust. Developed and tested on Windows 11.

## Usage

1. Paste a YouTube link on the first screen. Echo builds a voice with five
   practice lines (about 75 seconds for a 25-minute video).
2. Choose your microphone in the sidebar.
3. Open a line, listen, and use **Warm up** to find the pitch.
4. Press **Record a take** or Space. The take stops about a second after your
   last word and is scored.
5. Use **Train ›** on any part to practise it phrase by phrase, and run a
   **voice check** when a few lines feel right.

The [guide](docs/guide.md) covers every screen.

## Scoring

| Part | Weight | Compares |
| --- | --- | --- |
| Voice | 23% | the rating listeners would give the pair for "same speaker?" |
| Pitch | 14% | median pitch |
| Melody | 11% | the shape of the pitch line |
| Resonance | 18% | the second and third formants: brighter or darker |
| Articulation | 13% | how each sound is shaped, as a speech recogniser hears it |
| Rhythm | 21% | pace, and how evenly it is kept |

The take is aligned with the target sound by sound, so both must say the same
words. Each part is weighted by how well it separates a speaker's retake from a
stranger. A gender read and a vocal-fry read are shown but not counted.

## Accuracy

| Check | Result |
| --- | --- |
| Retake vs stranger (RAVDESS, simulated microphone) | the retake scores higher in 96.4% of pairs |
| Listeners' same-speaker verdicts (VCC2018, 21,343 pairs) | Voice correlates 0.55 per pair, 0.94 per system |
| Ranking strangers like listeners (VoxSim, 2,775 pairs) | 0.62, against 0.15 for a speaker model alone |
| Pitch against a laryngograph (PTDB-TUG) | 2.1% of frames off by more than 20% |
| Formants against hand-checked vowels (Hillenbrand et al., 1995) | 1.5% of F2, 2.8% of F3 more than 15% off |
| Speakers in 330 recordings of one to four people | all found, every line assigned correctly |

The [method](docs/method.md) has the full results and how to reproduce them.

## Limitations

- The calibration microphone is simulated; real phone and computer microphones
  are not yet checked.
- Breathy and whispered voices are the hardest case. A single take's Resonance
  can move by several points; judge it over a few takes.
- Melody, rhythm and articulation are calibrated, but not yet checked against
  listeners' judgments of impressions.
- English only.

## Command line

```bash
echo-voice serve [--host HOST] [--port PORT] [--data DIR]
echo-voice score target.wav take.wav [--json]
echo-voice import <video link or file> [--name NAME]
```

## Development

```bash
python -m pip install -e ".[dev,e2e]"
python -m pytest -q          # about 8 minutes; browser tests need Edge or Chrome
ruff check .
```

Source is in `src/echo_voice/` (the scorer, server and page), calibration and
validation scripts in `tools/`, and tests in `tests/`.

## License and credits

MIT ([LICENSE](LICENSE)). Models are downloaded on first use under their own
licences: WeSpeaker ResNet34-LM, UVR-MDX-NET Voc_FT and Whisper base.en. The
fitted numbers in `src/echo_voice/` come from RAVDESS (CC BY-NC-SA 4.0), VoxSim
and VCC2018 (CC BY 4.0), PTDB-TUG and Hillenbrand et al. (1995); none of their
recordings are included. The test clips in `tests/data/arctic` are from CMU
ARCTIC, under the licence in that folder.
