# Echo

Echo is a local web application for practising voice impressions. It scores a
recording of the user saying a line (a *take*) against a recording of the
person being imitated (the *target*) on a scale from 0 to 100, in six parts,
and names the part that would raise the score most. On the calibration set, a
speaker's own retake scores higher than a same-sex stranger in 96.4% of pairs.
All processing runs on the local computer; no recording leaves it.

[Project page](https://maymeridian.dev/projects/echo/) · [User guide](docs/guide.md) · [Method and validation](docs/method.md) · [Changelog](CHANGELOG.md) · MIT licence

<img src="https://maymeridian.dev/projects/echo/img/line-dark.webp" alt="A take's result in Echo: the target's pitch line with the take's drawn over it, a score of 44 out of 100, and the six parts that make it up" width="560">

*Figure 1. A scored take: the target's pitch contour with the take's drawn
over it, the headline score (44) and the six part scores.*

## What it does

- **Practice lines from a recording.** Echo transcribes a YouTube link or an
  uploaded file with Whisper, a speech recogniser; splits the speech into
  lines; separates the speakers; and selects five lines suited to practice.
- **A six-part score:** voice, pitch, melody, resonance, articulation and
  rhythm (defined under [Scoring](#scoring)), each calibrated on recordings
  with known answers.
- **Coaching.** After each take, Echo names the part worth the most points and
  gives an exercise for it.
- **Live feedback.** While the user speaks, the page draws their pitch over
  the target's and shows a resonance meter.
- **Phrase-by-phrase training** of pitch, resonance or articulation, including
  moving pitch while holding resonance.
- **Voice checks:** one take on each of several lines, without retries,
  averaged into one score.

## Install and run

Echo requires Python 3.10 or later and a browser with microphone access
(Chrome, Edge or Firefox). YouTube links also require Node, Deno, Bun or
QuickJS, which yt-dlp uses.

```bash
git clone https://github.com/maymeridian/Echo
cd Echo
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"     # Windows: .\.venv\Scripts\python
.venv/bin/echo-voice serve                       # Windows: .\.venv\Scripts\echo-voice serve
```

Open http://127.0.0.1:8000. On first use Echo downloads three models (about
230 MB in total), each checked against a pinned hash.

The server accepts connections from the local computer only and refuses
state-changing requests from other websites. `echo-voice serve --host 0.0.0.0`
makes it reachable from the local network; use this only on a trusted network.
Echo is developed and tested on Windows 11.

## Inputs and outputs

| | |
| --- | --- |
| Target voice | A YouTube link, or an audio or video file |
| Takes | Recordings made in the page from the microphone, up to 30 s each |
| Result of a take | A headline score and six part scores (0–100), a score for each word, and up to three hints |
| Storage | `data/`: audio as WAV files, results as JSON files |

`echo-voice score target.wav take.wav --json` prints the full result of one
take as JSON.

## Usage

1. Paste a YouTube link on the first screen. Echo builds a voice with five
   practice lines (about 75 s for a 25-minute video).
2. Select the microphone in the sidebar.
3. Open a line, listen to it, and use **Warm up** to find its pitch.
4. Press **Record a take** or the space bar. Recording stops about one second
   after the last word, and the take is scored.
5. Use **Train ›** on any part to practise it phrase by phrase. Run a
   **voice check** once several lines are consistent.

The [user guide](docs/guide.md) describes every screen.

## Scoring

The take is first aligned with the target by dynamic time warping (DTW) over
the frames of Whisper's encoder, so that each moment of the take is compared
with the same moment of the target. Both recordings must therefore contain the
same words.

| Part | Weight | Measures |
| --- | --- | --- |
| Voice | 23% | The rating listeners would give the pair when asked whether it is the same speaker, predicted by a model trained on VoxSim ratings [3] |
| Pitch | 14% | The difference in median fundamental frequency (F0), in semitones |
| Melody | 11% | The difference in the shape of the pitch contour |
| Resonance | 18% | The shift of the second and third formants (F2, F3: resonances of the vocal tract), heard as a brighter or darker voice |
| Articulation | 13% | How each sound is formed, as Whisper's encoder represents it |
| Rhythm | 21% | The overall pace, and how evenly it is kept |

Each part's distance is mapped to 0–100 so that a speaker's retake on another
microphone scores about 90 and a same-sex stranger about 20. Each part is
weighted in proportion to how well it separates a retake from a stranger: its
area under the receiver operating characteristic (ROC) curve (AUC) minus 0.5.
A gender estimate and a vocal-fry estimate are shown but not counted. The
[method](docs/method.md) gives every definition and curve.

## Accuracy

| Check | Data | Result |
| --- | --- | --- |
| Retake against stranger | RAVDESS [1], takes through a simulated microphone | The retake scores higher in 96.4% of pairs |
| Agreement with listeners' same-speaker ratings | VCC2018 listening test [2], 21,343 rated pairs | Voice: Pearson correlation 0.55 per pair, 0.94 per system |
| Ranking strangers as listeners do | VoxSim test split [3], 2,775 pairs | Spearman correlation 0.62 on different-speaker pairs (speaker model alone: 0.15) |
| Pitch against a laryngograph | PTDB-TUG [4] | 2.1% of frames more than 20% off |
| Formants against hand-measured vowels | Hillenbrand et al. (1995) [5] | 1.5% of F2 and 2.8% of F3 values more than 15% off |
| Speaker separation | 330 recordings of one to four speakers (VCC2018, VoxCeleb1) | Speaker count and every line's speaker correct |

The [method](docs/method.md) gives the full results and the procedure behind
each.

## Limitations

- The microphone and room in the calibration are simulated. Real phone and
  computer microphones have not been tested.
- Breathy and whispered voices are the least reliable case. On a single take,
  changes a listener would not hear move Resonance by 3 to 8 points for a
  typical take, so the page also shows the last three takes together.
- Melody, rhythm and articulation are calibrated on recordings with known
  answers but have not been compared with listeners' judgements of
  impressions; no public listening test of impressions exists.
- English only.

## Command-line interface

```bash
echo-voice serve [--host HOST] [--port PORT] [--data DIR]   # run the app
echo-voice score target.wav take.wav [--json]                # score one take
echo-voice import <video link or file> [--name NAME]         # make practice lines from a recording
```

## Reproducing the results

The fitted values shipped in `src/echo_voice/` (`calibration.json`,
`speaker_mean.json`, `similarity_model.json` and `gender_model.json`) are
produced by `tools/calibrate.py`, `tools/train_similarity.py` and
`tools/train_gender.py`; every check above is a command of
`tools/validate.py`. The datasets are not included. The
[method](docs/method.md) gives where to obtain each dataset and the command
for every table.

## Development

```bash
python -m pip install -e ".[dev,e2e]"
python -m pytest -q          # about 8 minutes; the browser tests need Edge or Chrome
ruff check . && ruff format --check .
```

The source is in `src/echo_voice/` (the scorer, the server and the page), the
calibration and validation scripts in `tools/`, and the tests in `tests/`.

## Licence and credits

MIT ([LICENSE](LICENSE)). The models are downloaded on first use under their
own licences: WeSpeaker ResNet34-LM (speaker embeddings), UVR-MDX-NET Voc_FT
(vocal separation) and Whisper base.en (speech recognition). The fitted values
in `src/echo_voice/` come from RAVDESS (CC BY-NC-SA 4.0), VoxSim and VCC2018
(CC BY 4.0), PTDB-TUG and Hillenbrand et al. (1995); none of their recordings
are included. The test clips in `tests/data/arctic` are from CMU ARCTIC, a
corpus of read speech from Carnegie Mellon University (Kominek and Black,
2004), under the licence in that folder.

## References

1. Livingstone, S. R. and Russo, F. A. (2018). The Ryerson Audio-Visual
   Database of Emotional Speech and Song (RAVDESS). *PLoS ONE* 13(5).
   https://doi.org/10.1371/journal.pone.0196391
2. Lorenzo-Trueba, J. et al. (2018). The Voice Conversion Challenge 2018:
   promoting development of parallel and nonparallel methods. *Odyssey 2018*.
   https://arxiv.org/abs/1804.04262
3. Ahn, J. et al. (2024). VoxSim: a perceptual voice similarity dataset.
   *Interspeech 2024*. https://arxiv.org/abs/2407.18505
4. Pirker, G., Wohlmayr, M., Petrik, S. and Pernkopf, F. (2011). A pitch
   tracking corpus with evaluation on multipitch tracking scenario.
   *Interspeech 2011*.
5. Hillenbrand, J., Getty, L. A., Clark, M. J. and Wheeler, K. (1995).
   Acoustic characteristics of American English vowels. *Journal of the
   Acoustical Society of America* 97(5). Data:
   https://github.com/santiagobarreda/hillenbrand_et_al_1995
