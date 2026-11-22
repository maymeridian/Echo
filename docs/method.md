# Method: how Echo measures a take, and how the measures were checked

Echo scores a recorded attempt at a line (a *take*) against a recording of the
voice being imitated (the *target*). It compares six parts of the two
recordings, maps each part to a 0–100 scale, and combines the parts into one
headline score. This document describes each measurement, how the scales and
weights were calibrated, how they were validated, and how to reproduce each
reported table. For using the app, see the [guide](guide.md).

## Summary

**Method.** The take is aligned with the target by dynamic time warping on the
frames of a speech recogniser. Six parts are compared: Voice, Pitch, Melody,
Resonance, Articulation and Rhythm. On the RAVDESS corpus (24 actors saying two
sentences in eight emotions; Livingstone and Russo, 2018), each part's scale is
anchored at 90 for the median distance between an actor and their own retake
and at 20 for the median distance to a same-sex stranger (Articulation's 20 is
a different sentence instead), and each part is weighted by how well it
separates retakes from strangers.

**Main results (measured).**

- On the calibration pairs, the headline score tells an actor's retake from a
  stranger with an area under the receiver operating characteristic curve
  (AUC) of 0.964 ([Calibration](#calibration)).
- In the listening test of the Voice Conversion Challenge 2018 (VCC2018), the
  headline correlates with the mean
  listener rating at Pearson 0.48 per pair and 0.90 per system
  ([Agreement with listeners: VCC2018](#agreement-with-listeners-vcc2018)).
- On the test split of VoxSim, a set of listeners' voice-similarity ratings
  (Ahn et al., 2024), Voice reaches Pearson 0.789 across all pairs and
  0.589 among different speakers, against 0.761 and 0.237 for
  speaker-embedding similarity alone
  ([VoxSim](#agreement-with-listeners-on-real-world-clips-voxsim)).
- The pitch tracker makes 2.1% gross errors on the PTDB-TUG pitch-tracking
  database (Pirker et al., 2011)
  ([Pitch tracking](#pitch-tracking)). The formant tracker puts 0.9%, 1.5% and
  2.8% of measurements of the first three formants (F1, F2 and F3: resonances
  of the vocal tract) more than 15% off on the Hillenbrand et
  al. (1995) vowels ([Formant tracking](#formant-tracking)).
- With music as loud as the voice, vocal separation restores a median headline
  score of 100 against the clean original, against 65 without it
  ([Clip cleaning](#clip-cleaning)).

**Main limitations.** The calibration microphone is simulated. Melody, Rhythm
and Articulation have not been checked against human judgments of
impressions. Resonance on one short take is a rough reading. See
[Open questions](#open-questions).

**Measured and inferred.** Results are measurements made for Echo unless they
are attributed to a cited source; thresholds and other settings are stated as
such. Sentences that explain a result rather than report it are marked
*Interpretation*. Figures describe the current version unless a table gives
version 1.0.0 alongside for comparison.

## Terms and abbreviations

| Term | Meaning |
| --- | --- |
| Target | The recording of the voice being imitated: one line cut from a clip. |
| Take | One recorded attempt at a target line. |
| Retake | In calibration, the same actor saying the same line again, in the same emotion. |
| Same-sex / other-sex stranger | Another speaker, of the same or the other sex, saying the same line. |
| Part | One of the six component scores. The *headline* is the combined score. A *point* is one unit on a 0–100 score. |
| F0, pitch | Fundamental frequency: the rate at which the vocal folds vibrate, heard as pitch, in hertz (Hz). |
| Semitone, cent | A semitone is one twelfth of an octave, a frequency ratio of 2^(1/12). A cent is one hundredth of a semitone. |
| Voiced frame | A frame in which the vocal folds vibrate, so that F0 is defined. |
| Formant (F1, F2, F3) | A resonance of the vocal tract, numbered from the lowest. Echo reports higher F2 and F3 as *brighter* and lower as *darker*. |
| Formant ceiling | The upper frequency limit of the formant search. Formant values depend on it. |
| DTW | Dynamic time warping: aligns two sequences of frames by finding the lowest-cost monotonic pairing of their frames, so that the same moment of a line can be compared in two recordings spoken at different speeds. |
| MFCC | Mel-frequency cepstral coefficients: a compact description of the short-term spectrum. |
| LPC | Linear predictive coding: models each frame's spectrum as an all-pole filter. Burg's method estimates the filter coefficients; the roots of the filter give formant candidates. |
| Speaker embedding | A fixed-length vector from a speaker-recognition network that summarises who is speaking. |
| Cosine similarity | The dot product of two vectors divided by the product of their lengths: 1 when they point the same way, 0 when they are orthogonal. |
| AUC | Area under the ROC (receiver operating characteristic) curve: the probability that a randomly chosen pair of one kind (for example, a retake) is ranked closer than a randomly chosen pair of another kind (for example, a stranger). 0.5 is chance and 1 is perfect separation. |
| Pearson, Spearman | Correlation between two sets of values (Pearson) or between their ranks (Spearman). |
| Gross pitch error | A frame, voiced in both the estimate and the reference, whose estimated F0 is more than 20% from the reference. *Voicing error:* a frame whose voiced/unvoiced decision differs from the reference. *Fine error:* the median error, in cents, on the frames without a gross error. |
| SI-SDR | Scale-invariant signal-to-distortion ratio (Le Roux et al. 2019), in decibels (dB): how close a processed signal is to a clean reference, independent of level. Higher is closer. |
| CPPS, HNR | Smoothed cepstral peak prominence and harmonics-to-noise ratio: two measures of how periodic a voice is. |
| Dither | Very low-level random noise added to a signal. One int16 step is the smallest step of 16-bit audio. |
| Laryngograph | A device that records vocal-fold contact through electrodes on the neck, giving a reference F0 independent of the audio. |
| Vocal fry | Creaky voice: low, irregular vibration of the vocal folds. |
| CPU, GPU | Central processing unit; graphics processing unit. |

## Data and reproduction

The checks use public datasets, which are not included in the repository.
Each goes in the `cache/` folder, and the docstring of each command says where
to download it. Commands assume Echo's virtual environment is active. Each
section below gives the command that reproduces its table where one exists.
No command is given for the known-answers, robustness, word-timing, phrase
trainer and vocal-fry checks, or for the calibration check on CMU ARCTIC, a
corpus of read speech (Kominek and Black, 2004).

| Dataset | Contents | Used for | Reference |
| --- | --- | --- | --- |
| RAVDESS | Acted emotional speech by professional actors | Calibration, known answers, word check, clip cleaning, gender read | Livingstone & Russo (2018), [doi:10.1371/journal.pone.0196391](https://doi.org/10.1371/journal.pone.0196391); audio on [Zenodo](https://zenodo.org/records/1188976); CC BY-NC-SA 4.0 |
| VCC2018 | Voice Conversion Challenge 2018: natural and converted speech, with listener same-speaker judgments | Articulation's lower anchor, agreement with listeners, live resonance, speaker splitting, phrase trainer, gender read | Lorenzo-Trueba et al. (2018), [arXiv:1804.04262](https://arxiv.org/abs/1804.04262); data [doi:10.7488/ds/2337](https://doi.org/10.7488/ds/2337) and [doi:10.7488/ds/2496](https://doi.org/10.7488/ds/2496); CC BY 4.0 |
| VoxSim | Listener ratings of how alike the voices in two VoxCeleb1 clips sound | Training and testing Voice | Ahn et al. (2024), [arXiv:2407.18505](https://arxiv.org/abs/2407.18505); ratings from https://mm.kaist.ac.kr/projects/voxsim/; CC BY 4.0 |
| VoxCeleb1 | Clips of celebrities in interviews and talk shows, from YouTube | Audio for VoxSim; speaker splitting | Nagrani et al. (2017); Hugging Face mirror `ProgramComputer/voxceleb` |
| PTDB-TUG | Read sentences with laryngograph reference pitch | Pitch tracking, vocal fry, gender read, cohort mean | Pirker et al. (2011); [download](http://www2.spsc.tugraz.at/databases/PTDB-TUG/); ODbL |
| Hillenbrand et al. (1995) | Hand-checked F1–F3 of /hVd/ words | Formant tracking, cohort mean | Hillenbrand et al. (1995); [data](https://github.com/santiagobarreda/hillenbrand_et_al_1995) |
| CMU ARCTIC | Read speech recorded in a studio | Held-out calibration check, clip cleaning | Kominek & Black (2004) |

Full references are listed under [References](#references).

## How a take is measured

### Aligning the take with the target

Most parts compare the same moment of the line in both recordings, so the take
is first aligned with the target by DTW. The alignment uses what a speech
recogniser hears in each recording: the frames of the encoder of Whisper
base.en (Radford et al. 2023), one every 20 ms (`line.sounds`). This requires
**both recordings to say the same words**.

*Interpretation:* the encoder is trained to map any voice to the same words,
so two people saying one line produce similar frames, and one person saying
two lines produces dissimilar frames.

Spectral features such as MFCCs describe the voice as much as the words, and
align recordings less well (Table 1).

**Table 1.** Alignment accuracy. A line's word times are carried onto the take
along the alignment and compared with where the take's own transcript places
the same words; 323 words of 30 real takes.

| Alignment features | Median offset | Words more than 200 ms off |
| --- | --- | --- |
| MFCC | 80 ms | 16% |
| Recogniser encoder frames (used) | 50 ms | 6% |

### The six parts

**Table 2.** The six parts and their weights in the headline. The weights come
from calibration (see [Calibration procedure](#calibration-procedure)).

| Part | Weight | What is compared |
| --- | --- | --- |
| Voice | 22.8% | The rating listeners would likely give the pair; does not depend on the words |
| Pitch | 14.2% | Median pitch, in semitones |
| Melody | 11.3% | Shape of the pitch contour once the level difference is removed |
| Resonance | 18.4% | Formants F2 and F3 on aligned voiced frames (brighter or darker), both recordings read at one formant ceiling |
| Articulation | 12.7% | Distance between what the recogniser hears in each recording, where both have sound (how the sounds are shaped) |
| Rhythm | 20.6% | Pace: length from first to last voiced sound. Timing: how unevenly the take is stretched against the target between those, read at ten stretches of the line |

### Voice: a model of listener ratings

Voice is a small model trained on human ratings (VoxSim; see
[Agreement with listeners on real-world clips](#agreement-with-listeners-on-real-world-clips-voxsim)).
It predicts the listener rating from three kinds of input, five numbers in all:

- **Speaker similarity:** cosine similarity between speaker embeddings from
  WeSpeaker's ResNet34-LM (Wang et al. 2023), trained on VoxCeleb and run on
  the CPU with onnxruntime. The mean embedding of 137 unrelated speakers is
  subtracted first.
- **Pitch gap:** the difference in median pitch.
- **Formant gaps:** the differences in median F1, F2 and F3.

The model is constrained so that more similarity can only raise the rating and
larger gaps can only lower it.

Two details of the embedding change its output:

- **Feature mean removal.** The model needs each recording's mean subtracted
  from its input features, a step that sherpa-onnx 2.0.1 skips. Without it,
  same-sex strangers averaged 0.72 cosine similarity against 0.84 for the same
  person; with it, 0.16 against 0.69.
- **Digital silence.** Noise gates, edits and synthetic speech leave stretches
  of exact digital silence, which shift the embedding. One int16 step of
  dither, as WeSpeaker uses in training, keeps a gated clip at 0.97 similarity
  to its original, against 0.88 without it.

### Pitch and formant measurement

Pitch and formants are measured with Praat (Boersma & Weenink), through
Parselmouth (Jadoul et al. 2018). Each recording chooses its own formant
ceiling: the one, from 4500 to 7000 Hz, whose formant tracks jump least from
frame to frame, the idea behind Fast Track (Barreda 2021).

Resonance instead reads both recordings of a comparison at one shared ceiling,
the middle of the two speakers' ceilings (`analysis.shared_ceiling`), and at
250 and 500 Hz either side of it, and takes the median of the five readings.
The reasons are given under [Formant tracking](#formant-tracking).

### Preparing recordings

- **Vocal separation of targets.** Every target clip first goes through vocal
  separation (UVR-MDX-NET Voc_FT, a model from the Ultimate Vocal Remover
  project, run on the CPU), because clips from films and videos usually have
  music or background sound under the voice. Takes are not cleaned. The
  evidence is under [Clip cleaning](#clip-cleaning).
- **Trimming.** Before anything is measured, a recording is trimmed to its
  sound, relative to its measured noise floor. A short isolated sound at either
  end is also cut, such as the click of the Stop button. Audio that Echo saved
  is read back as saved and is not trimmed a second time. The errors these
  rules correct are listed under
  [Errors found and corrected](#errors-found-and-corrected).

## Calibration

### Calibration procedure

`tools/calibrate.py` sets every curve and weight from RAVDESS (Livingstone &
Russo 2018; [audio](https://zenodo.org/records/1188976)). In RAVDESS, 24 actors
say the same two sentences in eight emotions, twice each. Each take goes
through a simulated microphone and room (band limit, reverberation, noise),
because in real use the target is a clip and the take comes from the user's
own microphone.

1. **Curves.** Each measure's curve places 90 at the median distance between an
   actor and their own retake, and 20 at the median distance to a same-sex
   stranger.
2. **Articulation's lower anchor.** Articulation's 20 is placed at another
   sentence altogether: the same speaker saying a different line (320 pairs
   from VCC2018, which has 35 sentences from each of 8 speakers). RAVDESS
   cannot provide this anchor, because its two sentences share four of their
   six words. On this curve a median stranger reading the right line scores
   77, an actor's own other sentence 64, and an actor of the other sex 79.
   *Interpretation:* Articulation does not respond to whose voice it is.
3. **Weights.** Each part counts in proportion to how well it tells a retake
   from a stranger, measured as AUC − 0.5. The AUCs are: Voice 0.99, Resonance
   0.89, Pitch 0.80, Articulation 0.77, Melody 0.74, pace 0.73, timing 0.71.
   Rhythm is pace and timing mixed in the same proportion (52% and 48%).
4. **Headline.** The weighted mean of the parts goes through one more curve
   with the same anchors.

Resonance is calibrated as the app measures it: both recordings of a pair at
the middle of their two speakers' ceilings. The app has about five lines of a
voice and a few takes to choose each speaker's ceiling from, so in calibration
a recording's speaker ceiling is the middle of its own pick and the picks of
four other recordings of the same actor through the same channel.

### Results on the calibration pairs

**Table 3.** Headline scores on the calibration pairs (10th / 50th / 90th
percentile).

| Pair | Score |
| --- | --- |
| Same actor, same line, clean | 57 / 96 / 100 |
| Same actor, same line, through a microphone | 43 / 90 / 100 |
| Same actor, different emotion | 24 / 58 / 95 |
| Same actor, other sentence | 28 / 65 / 93 |
| Same-sex stranger, same line | 11 / 20 / 42 |
| Other-sex stranger, same line, same emotion | 10 / 15 / 26 |

The headline tells an actor's retake from a stranger at AUC 0.964. A speaker
check on the raw embedding similarity, at a threshold that lets 1% of
emotion-matched same-sex strangers through, accepts 88% of same-person pairs
through a microphone.

### Reproducing the calibration

1. Unzip RAVDESS's `Audio_Speech_Actors_01-24.zip` into `cache/ravdess` and the
   VCC2018 evaluation set into `cache/vcc2018`.
2. Run:

   ```bash
   python tools/calibrate.py cohort-mean
   python tools/calibrate.py collect --ravdess cache/ravdess --vcc cache/vcc2018/vcc2018_evaluation --out cache/pairs.jsonl
   python tools/calibrate.py fit cache/pairs.jsonl
   ```

`cohort-mean` also needs the PTDB-TUG and Hillenbrand data
(see [Data and reproduction](#data-and-reproduction)). These steps write
`src/echo_voice/speaker_mean.json` and `src/echo_voice/calibration.json`; only
the fitted numbers are in the repository.

### Limitations and a held-out check

RAVDESS is acted speech: short lines from one studio, and the microphone is
simulated. A held-out check on four CMU ARCTIC clips (Kominek & Black 2004)
agrees with the scale (Table 4).

**Table 4.** Headline scores on held-out CMU ARCTIC pairs.

| ARCTIC pair | Score |
| --- | --- |
| Another man, same line | 28 (Articulation 92) |
| A woman, same line | 30 (Articulation 96) |
| Same man, other sentence (passes the speaker check) | 72 (Articulation 24) |
| Same man, through a simulated microphone | above 85 |

## Validation

### Known answers by part

The calibration pairs come in kinds whose right answer is known. A part should
score a stranger low, and should not depend on which of two people says the
wrong words.

**Table 5.** Median score of each kind of pair, by part. Each cell gives
version 1.0.0, then the current version.

| Part | Same actor, other sentence | Same-sex stranger, same line | Other sex, same line |
| --- | --- | --- | --- |
| Voice | 88, 88 | 20, 20 | 11, 11 |
| Pitch | 87, 87 | 20, 20 | 5, 5 |
| Melody | 69, 75 | 20, 20 | 36, 33 |
| Resonance | 84, 66 | 20, 20 | 1, 4 |
| Articulation | 18, 64 | 20, 77 | 11, 79 |

Articulation in version 1.0.0 compared MFCCs along the alignment, and failed
this check: it scored a stranger saying the right line the same as the right
actor saying the other sentence, separating the two at AUC 0.51, which is
chance. Comparing what the recogniser hears, it separates them at 0.81.
Resonance now reads a different sentence as a difference. *Interpretation:* it
compares the vowels on which the two recordings align, and for a different
sentence those are different vowels.

### Robustness on real takes

70 real practice takes against a breathy target voice were each scored,
changed in a way nobody would hear as a different performance, and scored
again.

**Table 6.** Points the score moved after each change. Each cell gives version
1.0.0, then the current version, each as median / the change that 9 takes in 10
stay within.

| Change | Voice | Melody | Resonance | Articulation | Rhythm | Headline |
| --- | --- | --- | --- | --- | --- | --- |
| A second of silence and a click after the take | 0.7 / 5.8, 0.0 / 1.0 | 0.5 / 12.8, 0.0 / 1.5 | 1.5 / 38.0, 0.0 / 6.1 | 4.1 / 19.1, 0.0 / 0.2 | 9.9 / 48.9, 0.0 / 1.4 | 2.6 / 11.1, 0.0 / 1.5 |
| A quarter second of faint room tone either side | 0.5 / 5.8, 0.0 / 2.7 | 0.1 / 6.1, 0.0 / 1.7 | 0.2 / 9.2, 0.0 / 9.2 | 0.2 / 1.2, 0.0 / 1.5 | 1.3 / 7.9, 0.0 / 1.3 | 0.3 / 4.3, 0.0 / 2.5 |
| 30 ms cut off each end | 0.8 / 5.9, 0.3 / 4.1 | 0.0 / 6.9, 0.2 / 10.0 | 0.3 / 6.2, 3.3 / 18.7 | 0.7 / 4.6, 0.9 / 3.1 | 1.5 / 37.6, 0.3 / 4.1 | 0.6 / 7.2, 1.3 / 5.6 |
| Hiss 40 dB under the speech | 2.5 / 9.4, 2.2 / 7.9 | 0.6 / 9.5, 0.1 / 9.6 | 2.5 / 24.8, 8.1 / 42.3 | 1.6 / 11.3, 0.9 / 5.6 | 0.6 / 31.3, 0.1 / 1.6 | 1.6 / 9.4, 2.1 / 9.7 |

Pitch moved by 0.2 points or less in every row.

Resonance moves more in the current version in the last two rows. In 1.0.0
these takes scored a median 13 on Resonance, on the flat floor of its curve;
they now score a median 33, on its slope. *Interpretation:* a score on the
floor cannot move, so the 1.0.0 figures understate how much the reading
changed. In semitones the reading moves by a median 0.053 for the cut and 0.058
for the hiss. Read from all three formants in the same way, it would move 0.072
and 0.065, and would tell a retake from a stranger less well (AUC 0.84 against
0.89). Because the curve runs from 90 to 20 over half a semitone, however,
**Resonance on one short take is a rough reading**: small changes move it 3 to
8 points for a typical take and 19 to 42 for one take in ten. The page also
shows the last three takes together.

Scored against the voice's other lines (the wrong words), the same takes get a
median Articulation of 21, against 65 for the right line, and the right line
scores higher for 96% of them.

### Agreement with listeners: VCC2018

The whole scorer was run on the Voice Conversion Challenge 2018 similarity test
(Lorenzo-Trueba et al., Odyssey 2018; [data](https://doi.org/10.7488/ds/2337),
CC BY 4.0). It has 21,343 pairs: listeners heard a converted clip and a natural
recording of the same sentence and judged whether they were the same speaker,
from 1 (different, sure) to 4 (same, sure). Each converted clip is, in effect,
a machine's impression of the target voice.

**Table 7.** Pearson correlation with the mean listener rating.

| Measure | Per pair | Per system |
| --- | --- | --- |
| **Headline** | **0.48** | **0.90** |
| Voice | 0.55 | 0.94 |
| Pitch | 0.48 | 0.78 |
| Resonance | 0.39 | 0.91 |
| Articulation | −0.11 | −0.49 |

The per-system column averages over 72 groups (system × compared-with × task,
where compared-with is whether the natural recording is of the target or the
source speaker), each with many ratings. Each pair has only 1.4 ratings on average, so listener
noise limits the per-pair correlations.

These listeners were asked one question: is this the same speaker? A converted
clip is its source speaker's recording with the voice replaced, so it keeps the
source speaker's sounds and timing. *Interpretation:* Articulation measures the
sounds, which in this test belong to the source speaker, so it falls as the
ratings rise; Melody and Rhythm also fall. When Articulation compared MFCCs,
and so responded to the voice, the headline agreed more (0.53 and 0.94). Voice,
the part that answers the listeners' question, is unchanged.

**Limitation.** This test cannot show whether a headline that counts delivery
measures an impression better, because nobody in it was imitating anyone's
delivery, and no public listening test of real impressions was found.

For comparison, Ahn et al. (2024, [arXiv:2407.18505](https://arxiv.org/abs/2407.18505))
report 0.51 for a pretrained ECAPA speaker model (ECAPA-TDNN, a
speaker-verification network), 0.56 for it fine-tuned on VoxSim, and 0.57 for
SVSNet (a network that predicts voice similarity) trained on VCC2018 itself,
on their own split of
the same test. Voice's listener model was trained only on VoxSim and lands
next to their VoxSim-trained model. The splits differ, so the comparison is
approximate.

```bash
python tools/validate.py vcc2018 --vcc cache/vcc2018 --out cache/vcc2018/scored.jsonl
```

### Agreement with listeners on real-world clips: VoxSim

VoxSim (Ahn et al., Interspeech 2024) has human ratings, from 1 (definitely
different) to 6 (definitely the same), of pairs of VoxCeleb1 clips (Nagrani et
al. 2017): celebrities in interviews and talk shows, saying different
sentences. These pairs cannot test the parts that need the same words, but
they test Voice, Pitch and Resonance on recordings like the clips people
practise with.

**Table 8.** Pearson / Spearman correlation with the ratings on VoxSim's test
split, 2,775 pairs, none used for training.

| Measure | All pairs | Different speakers only |
| --- | --- | --- |
| **Voice (listener model)** | **0.789 / 0.772** | **0.589 / 0.623** |
| Speaker-embedding similarity alone | 0.761 / 0.744 | 0.237 / 0.150 |
| Pitch alone | 0.493 / 0.484 | 0.414 / 0.457 |
| Resonance alone (median formants) | 0.400 / 0.467 | 0.326 / 0.492 |

- **Embedding similarity tells people apart but barely ranks strangers.** It
  matches the VoxSim paper's pretrained ECAPA baseline across all pairs
  (Pearson 0.761 here against 0.768 there), but among strangers it does little
  to order them by how close they sound, which is what scoring an impression
  requires. Chen et al. (2026,
  [arXiv:2609.33999](https://arxiv.org/abs/2609.33999)) found the same for
  speaker models trained in the same way.
- **Pitch and formant gaps supply what the embedding lacks.** On their own
  they rank strangers three times better (Spearman 0.457 and 0.492 against
  0.150), so Voice combines all three. It was trained with
  `tools/train_similarity.py` on all 24,919 VoxSim training pairs, with its
  settings chosen on 10% of the training speakers held out, and the test split
  scored once.
- **More data did not change it.** Trained on 3,037 pairs it scored within
  0.002 of these numbers. *Interpretation:* the limit is the five inputs, not
  the amount of data.
- **It is small.** Ahn et al.'s GPU-fine-tuned model reaches 0.829 across all
  pairs; Voice gets about half the way there from the pretrained baseline with
  five numbers and 200 small trees, shipped as plain JSON
  (`similarity_model.json`). The training script checks that the JSON gives
  exactly scikit-learn's predictions.

`tools/validate.py voxsim-fetch` downloads only the clips these pairs need from
the VoxCeleb1 zips on the Hugging Face mirror: 46,348 clips, about 11 GB, each
checked against the zip's CRC (cyclic redundancy check). It needs a Hugging
Face login that has accepted the mirror's terms.

```bash
python tools/validate.py voxsim-fetch --voxsim cache/voxsim --out cache/voxceleb1
python tools/validate.py voxsim --voxsim cache/voxsim --audio cache/voxceleb1
python tools/train_similarity.py --voxsim cache/voxsim --audio cache/voxceleb1
```

### Pitch tracking

The pitch tracker was checked against [PTDB-TUG](http://www2.spsc.tugraz.at/databases/PTDB-TUG/)
(Pirker et al., Interspeech 2011): 160 sentences from 10 women and 10 men,
with reference pitch from a laryngograph. Praat's autocorrelation tracker with
a 60–600 Hz range was the best of the five trackers tested (Table 9). The
two-pass trackers first estimate the speaker's pitch distribution and then
track again within a range set from its quantiles.

**Table 9.** Pitch tracking errors on PTDB-TUG (terms defined under
[Terms and abbreviations](#terms-and-abbreviations)).

| Tracker | Gross errors (>20% off) | Voicing errors | Fine error |
| --- | --- | --- | --- |
| **Autocorrelation, 60–600 Hz (used)** | **2.1%** (women 3.6%, men 0.5%) | 4.5% | 11 cents |
| Cross-correlation, 60–600 Hz | 2.8% | 4.5% | 14 cents |
| Two-pass range, De Looze & Hirst 2008 | 2.4% | 4.3% | 12 cents |
| Two-pass range, Hirst 2011 | 2.6% | 4.2% | 12 cents |

The adaptive ranges fix some octave-down errors for women, but cause more
octave-up errors than they fix.

The page's live tracker is YIN (de Cheveigné & Kawahara 2002) in
`static/pitch.js`, run on 2048-sample frames as the browser delivers them. On
the same recordings it makes 1.9% gross errors (3.0% for women, 0.2% for men)
with a 15-cent fine error. It skips 8.4% of voiced frames, which only leaves
gaps in the live trace, and labels 1.0% of unvoiced frames as voiced.

```bash
python tools/validate.py pitch --ptdb cache/ptdb
node tools/check_live_pitch.mjs cache/ptdb
```

### Formant tracking

The formant tracker was checked against the hand-checked measurements of
[Hillenbrand et al. (1995)](https://github.com/santiagobarreda/hillenbrand_et_al_1995):
1,668 /hVd/ words (h, a vowel, d, as in "heed") from 45 men, 48 women and 46
children. Each speaker's 12 words were joined into one recording, and one
ceiling was chosen per recording without vowel labels, as the app does.

**Table 10.** Share of formant measurements more than 15% off.

| Ceiling | F1 | F2 | F3 | Children's F2 / F3 |
| --- | --- | --- | --- | --- |
| Fixed 5500 Hz | 1.8% | 5.6% | 6.9% | 10.4% / 11.3% |
| **Least frame-to-frame jumping, per recording (used)** | **0.9%** | **1.5%** | **2.8%** | **2.4% / 4.3%** |

Median errors are 1–2% throughout. The median chosen ceilings fall where
phonetic practice puts them, without the speaker group being given: 5000 Hz for
men, 5750 for women and 6000 for children.

```bash
python tools/validate.py formant-lines --h95 cache/hillenbrand
```

**One ceiling for both sides of a comparison.** The per-recording choice is
right on average but not stable: over an actor's 60 RAVDESS recordings it lands
on 5.4 different ceilings, and each change moves the formants a little.
Formants read at 4750 Hz also sit a little lower than the same audio read at
5250, so a line read at two different ceilings does not match a copy of itself
(it scored 3 to 24 against itself on four of five lines). Resonance therefore
reads both recordings at one ceiling, as described under
[Pitch and formant measurement](#pitch-and-formant-measurement). The centre is
the middle of the voice's ceiling (what its lines pick) and the speaker's
ceiling (what the speaker's last 15 takes on that voice picked).

**F2 and F3 only.** On a breathy voice near 190 Hz, Praat's F1 alternates
between the first harmonic (about 250 Hz) and the formant (about 430): across
takes of one line its shift varied by 0.7 to 2.4 semitones, against 0.4–0.85
for F2 and 0.2–0.45 for F3, while the whole Resonance curve from 90 to 20 spans
under one semitone. Read from F2 and F3 at the shared ceiling, Resonance tells
a retake from a stranger at AUC 0.89, against 0.85 with F1 included.

**Minimum length.** With fewer than 60 aligned voiced frames, Resonance is not
read at all: shorter readings gave unusable values (one was +8.4 semitones).
It is unread on 1.4% of RAVDESS retakes and 2.8% of strangers, those lines
being short.

*Typical formants* (the median formants over a recording's voiced frames), the
Voice listener model and the gender read use each recording's own ceiling,
because that is how they were fitted.

### Live resonance meter

The live meter reads F1–F3 in the browser (`static/formants.js`). It follows
Praat's Burg tracker step by step: 50 ms frames, resampled to twice the formant
ceiling, pre-emphasis from 50 Hz, Praat's Gaussian window, ten LPC
coefficients, and the three lowest roots between 50 Hz and 50 Hz under the
ceiling. The page reads the target's audio and the microphone with the same
code, at the ceiling the scorer uses, and reports brighter or darker from F2
and F3 as the scorer does, so a gap between them is a gap between the voices.

On the same audio and frames it lands a median 1.1% (F1), 0.8% (F2) and 0.7%
(F3) from Praat, with no bias in either direction. Which frames count matters
more than the fit, because typical formants are medians over the voiced
frames. A frame counts as voiced when its normalised autocorrelation, with the
frame's mean removed, is at least 0.55 and it is at least a tenth as loud as
the loudest frame so far. That rule finds 93% of Praat's voiced frames with 6%
extra; it was chosen on two sentences, and the check below uses ten others.

**Table 11.** The live meter against what the app measures, on 120 recordings
(12 VCC2018 speakers, the same 10 sentences each).

| Comparison | Result |
| --- | --- |
| Typical F1 / F2 / F3 against Praat at the same ceiling | 2.2% / 1.3% / 0.7% apart (median) |
| The gap between two speakers on a sentence, against the trainer's measure (660 pairs) | correlation 0.932; a median 1.4 points apart; within 3 points in 77% |
| Says the right way, brighter or darker, when the app puts them 3% or more apart | 98% of 467 pairs |
| The line page's meter, aligned first, against a take's Resonance (557 pairs it could be read on) | correlation 0.906; a median 1.7 points apart; within 3 points in 70%; right way in 95% |
| The same without alignment | correlation 0.737; a median 3.2 points apart; within 3 points in 48% |
| The pitch gap between two speakers, the live tracker against Praat | correlation 0.982; a median 0.31 semitones apart; over 1 semitone in 11% |

**Alignment.** Typical formants over a whole line depend on how long each
speaker dwells on each vowel, and no two people dwell the same. The line
page's meter therefore aligns the two recordings first
(`formants.alignedShift`): DTW on the formants themselves, each side centred on
its own typical values, with an open end so that a take in progress aligns
with the part of the line it has reached. The phrase trainer's ring (see
[Phrase trainer](#phrase-trainer)) does not need alignment, because a scored
attempt on a phrase is also measured by typical formants. One reading takes
about 0.3 ms and one alignment 0.6 ms.

```bash
python tools/live_resonance_ref.py --vcc cache/vcc2018 --out cache/live_resonance_ref.json
node tools/check_live_resonance.mjs cache/live_resonance_ref.json
```

**Limitations.** The ring shows the last 1.2 seconds, so it moves by a few
points as different sounds pass through; its direction and rough size are what
to rely on. All of this is studio speech read aloud. Whispered and breathy
voices, where formants are hardest to track, have not been checked this way.

### Clip cleaning

48 clean sentences (CMU ARCTIC, RAVDESS, VCC2018) were mixed with music: six
public-domain or CC0 recordings from Wikimedia Commons (orchestra, piano,
strings, oboe, mandolin) and a synthetic drum-and-bass loop. Each mix was
scored against the clean original, where 100 is a perfect match. The
alternatives to vocal separation were two speech denoisers, DPDFNet and GTCRN.

**Table 12.** Headline score of each mix against the clean original: medians,
with the worst 10% in brackets.

| Music level | Uncleaned | Vocal separation (used) | DPDFNet denoiser | GTCRN denoiser |
| --- | --- | --- | --- | --- |
| None | 100 | 100 | 100 | 100 |
| 10 dB below the voice | 100 (90) | 100 (100) | 100 | 100 |
| 5 dB below | 92 (69) | 100 (100) | 100 | 100 |
| As loud as the voice | 65 (27) | 100 (99) | 100 | 100 |

The headline hides how far the parts drift. With music as loud as the voice,
uncleaned pitch is 2.0 semitones off and formants 2.4. After separation they
are 0.07 and 0.36, and SI-SDR rises from 0 dB to 15.6 dB, against 12.2 dB for
DPDFNet and 6.6 dB for GTCRN. On clean speech, separation changes almost
nothing (SI-SDR 37 dB), which is why every target is cleaned.

```bash
python tools/validate.py cleaning --music cache/music
```

### Word check

The word check compares the recogniser's transcript of a take with the
target's words and flags a take that does not say the line. It was tested on
RAVDESS, where every actor says the same two sentences. The target was clean
and the take went through the simulated microphone; the target's words also
came from the recogniser, as they do in the app.

**Table 13.** Share of takes flagged by the word check.

| Case | Flagged |
| --- | --- |
| Same sentence, other actor or emotion (false alarms) | 7.1% |
| The other sentence ("Dogs are sitting" for "Kids are talking") | 100% |
| Take cut to its first 60% | 90% |
| Take cut to its last 60% | 96% |

Through the microphone the recogniser gets 96.8% of words right. One misheard
word in a correct take is common ("Docs" for "Dogs"), so a take is flagged only
when at least 2 words, or 15% of the line, are wrong; flagging on any single
word gave 20% false alarms. Even 7% is too many to let the word check lower the
score, so it only gives hints.

Whisper runs at temperature 0 only, which always takes the most likely output.
Its default setting retries an uncertain pass with random sampling, so the same
audio gave different transcripts from run to run: 4 transcripts in 6 runs of
one test recording. At temperature 0 all 6 matched.

```bash
python tools/validate.py line --ravdess cache/ravdess
```

### Splitting long recordings

**Speakers.** Each line of 1.5 s or more gets a speaker embedding, and the
lines are grouped by average-linkage clustering (repeatedly merging the two
groups with the highest average similarity) while the average cosine
similarity between two groups is at least 0.4 (`sources.find_speakers`). A
group of fewer than 3 lines, or under 5% of them, is not treated as a speaker;
its lines join the nearest speaker if they are that close to it, and are marked
unsure if not.

**Table 14.** Speaker finding.

| Recordings (`tools/validate.py speakers`) | Speaker count right | Lines to the right speaker |
| --- | --- | --- |
| VCC2018 studio speakers, 1–4 per recording (240) | 100% | 100% |
| VoxCeleb1 interviews from YouTube, 1–3 per recording (90) | 100% | 100% |
| A 25-minute video of one soft-spoken speaker | 1 found | 174 of 175, 1 unsure |

The 25-minute video stayed one speaker at every threshold from 0.3 to 0.5.
Lower thresholds start merging speakers (at 0.3, 88% of 4-speaker studio mixes
were right); higher ones leave more lines unsure.

**Limitation.** VoxCeleb clips are about 8 s long, where real lines are often
2–5 s, and speakers in different studios are easier to tell apart than two
people on one microphone. The VCC2018 test, all from one studio, covers the
second case.

**Other voice.** The "other voice" tag marks a line spoken by someone other
than the recording's main speaker. Test recordings were built from VCC2018:
for each speaker, 30 of their sentences mixed with 8 by others (3 each by two
speakers of the same sex, 2 by one of the other sex), all from one studio, 36
recordings in all (`tools/validate.py other-voice`). At the cosine threshold
of 0.4:

**Table 15.** Lines tagged "other voice".

| Lines | Tagged "other voice" |
| --- | --- |
| The main speaker's (873) | 0.2% |
| Another speaker's (259) | 96.1% |
| Another speaker of the same sex (190) | 94.7% |

```bash
python tools/validate.py speakers --vcc cache/vcc2018 --voxceleb cache/voxceleb1
python tools/validate.py other-voice --vcc cache/vcc2018
```

**A real video.** A 25-minute video of one soft-spoken speaker, with music and
background ambience, gave 200 lines, most of them 1.4–7.2 s long (10th to 90th
percentile), and was processed in 55 s. 9 lines were tagged whispered, 5
unclear, and 1 other voice (a 1.6 s "hmm"). Targets made from a normal line, a
whispered one and one over background ambience each scored 100 against
themselves after cleaning; on the whispered one, Pitch, Melody and Resonance
were not read ("–"). The pitch rule for suggested lines ruled out 19 of the
185 untagged lines, including two that had been suggested before, at 98–99 Hz
against a typical 191.

### Word timings

**Word edges.** Whisper's word times are approximate and often end a word
early, so a word played on its own was cut off. Edges are moved onto the audio
(`line.refine_word_times`): an edge between two words moves to the quietest
point between them, and the end of the last word moves to where the sound
stops. Across 118 words from 11 lines, words that ended while the sound was
still loud fell from 16.9% to 0.8%.

**Words said in one breath.** Adjacent words are joined into one unit when the
sound between them dips less than 6 dB below the quieter of the two
(`line.speech_units`). On two real lines, every boundary heard as a break
dipped 6.5 dB or more, and every run-together one under 5.2. A run over 1.3 s
is split at its deepest dip, and a unit under 0.15 s joins the neighbour it
dips least from.

**Limitation.** The threshold comes from 15 boundaries, so it is a starting
point, not a validated number.

### Phrase trainer

The phrase trainer is the phrase-by-phrase drill described in the
[guide](guide.md). `scoring.word_drill` uses the same measures as the
per-phrase breakdown, restricted to one phrase against the same phrase. On
synthetic vowels with formants moved ±10% it reads +9.2% and −9.9%. Another
man saying the same word reads 11.5% darker and 6 semitones lower, matching
his whole-line result.

An attempt is measured at the formant ceiling that the line and the speaker's
takes settle on, never at one it picks for itself, because a phrase is too
short to choose well. On 182 phrases from two VCC2018 speakers, a phrase cut
from its line and scored against that line picked the line's ceiling only 20%
of the time, and its resonance read more than 3% off in 36% of cases. At the
line's ceiling, that falls to 13%.

### Gender read

The gender read is a logistic regression on median pitch and the mean F2–F3
shift (`gender.py`, `tools/train_gender.py`), trained on the speakers' sex in
two corpora and tested on a third, with no speaker in common. The read is 100
times the model's probability of "female".

**Table 16.** Gender read on a corpus not used in training.

| Trained on | Tested on | Clips correct | Speakers correct |
| --- | --- | --- | --- |
| RAVDESS + VCC2018 | PTDB-TUG | 96.9% (AUC 0.996) | 20 of 20 |
| RAVDESS | VCC2018 | 90.0% | 11 of 12 |
| VCC2018 + PTDB-TUG | RAVDESS (emotional acting) | 80.2% | 21 of 24 |

F1 is left out. It moves most with the vowel and with a soft, breathy
delivery: one such voice's F1 read about 330 Hz against 610 for a woman
reading aloud, which made her read as male.

**Limitation.** The labels are the speakers' sex, not listeners' judgments of
each clip, so the read says how a voice's pitch and resonance compare with
women's and men's: 80 and up "female", 60–80 "leans female", 40–60 "in
between", 20–40 "leans male", under 20 "male".

```bash
python tools/train_gender.py
```

### Vocal fry

The vocal-fry measure is the share of a recording's voiced sound more than 7
semitones under its speaker's usual pitch. Against the laryngograph in
PTDB-TUG, it finds 60% of creaky frames and fires on 1.3% of ordinary ones,
and a speaker's share follows the laryngograph's at Pearson r = 0.81. Ordinary
speech reads 2–13%. *Interpretation:* only a gap against the target is
informative. A rule based on period irregularity found 17% of creaky frames
and fired on 13% of ordinary speech, so it was not used. Nothing calibrates
fry as a part of the score yet, so it is shown and not counted.

### End-of-take detection

The page ends a take once enough of the line has been said and it has been
quiet for a little longer than the line's longest pause (`static/ending.js`).
Speech is judged against the room: a frame is speech when it is 10 dB over the
10th-percentile level and within 35 dB of the 95th. Digital silence before the
first sound (a device starting up) is left out of the room's level; digital
silence later on (a noise gate between words) is not.

Saved takes were replayed with room noise added at 45 and 60 dB under the
peak. None was ended while the line was still being said, and a take ended a
median 1.0–1.3 s after its last word (9 in 10 within about 2 s).

```bash
node tools/check_ending.mjs data
```

## Errors found and corrected

Each of these was wrong at some point and is now tested:

- **A fixed formant ceiling** (5500 Hz) mis-tracked women's and children's F2
  and F3 (Table 10). The ceiling is now chosen per recording.
- **Trimming at a fixed level below the peak** never fired in a room with
  audible noise. It now cuts relative to the measured noise floor.
- **Saved audio trimmed twice.** Trimming is not idempotent: on a separated
  clip a second trim moved the ends by up to 60 ms, and on a clip that starts in
  digital silence by 160 ms, away from the word timings measured on it.
- **The click on Stop** lands a second after the last word. When it was kept,
  the quiet before it was aligned against the end of the target, and moved
  Rhythm by 49 points for one take in ten.
- **Timing as half of Rhythm.** Read step by step along the alignment, timing
  told a retake from a stranger at AUC 0.59, and its curve from 90 to 20
  spanned only 0.054 to 0.070, so any small variation moved Rhythm. Read at ten
  stretches of the line it reaches 0.71, and it counts in proportion to that.
- **Pace from everything with sound** read takes up to 50% slow, because a
  breath after the last word has sound. Pace is now the length from the first
  to the last voiced sound, without an isolated voiced blip at either end.
- **Lines that start mid-sentence.** The recording's transcript can miss a
  sentence's first words. When the cut line is transcribed by itself, the error
  shows, because its first word is not the line's.

## Approaches tried and not used

- **A background-noise tag on lines.** It compared the level between a line's
  words with the level of the words, and tagged 136 of the 200 lines of the
  25-minute video, where separating a sample showed the background sat 8–12 dB
  under the voice. Separation handles that level easily, and the level between
  words also includes breaths, which are not background.
- **Larger or different speaker models.** On the VCC2018 judgments, WeSpeaker
  ResNet293 and CAM++ and 3D-Speaker ERes2Net and CAM++ all agree with
  listeners within 0.02 of each other (`tools/validate.py vcc2018 --models`).
  ResNet293 is the best verifier, four times slower, and slightly *worse*,
  matching Chen et al. (2026,
  [arXiv:2609.33999](https://arxiv.org/abs/2609.33999)): lower verification
  error does not bring better agreement with listeners.
- **Averaging three speaker models** added 0.015 rank correlation for three
  times the computation.
- **Projecting embeddings onto a few principal directions** (principal
  component analysis) lowered agreement, from 0.48 to 0.31 at three
  dimensions. Chen et al.'s low-dimensional gains came from training with a
  bottleneck, not from projecting afterwards.
- **Voice quality (CPPS, HNR) as a part of the score.** On RAVDESS they barely
  tell a retake from a stranger (AUC 0.61 and 0.59), and the simulated
  microphone alone moves CPPS by 1 dB, about twice the typical gap between a
  retake and a stranger.
- **Speaking-style and voice-quality gaps as Voice inputs:** pitch range,
  speaking rate, voiced share, spectral envelope, CPPS and HNR. Together they
  raised Spearman correlation on VoxSim's test split overall (0.772 to 0.802)
  but not for strangers (0.624 to 0.619), and an impression is always by a
  stranger. The envelope also carries the microphone and room.
- **Checking Melody and Rhythm against VCC2018.** Voice conversion keeps the
  source speaker's timing and intonation, so in that data these parts track
  the source, not the voice people hear. The evidence for them comes from
  mimicry research instead: speaking rate is what amateur impersonators change
  most (Vestman et al. 2020, [arXiv:1906.01454](https://arxiv.org/abs/1906.01454)).

## Open questions

- **Real microphones.** The calibration's microphone is simulated. The check
  that matters is the real person recorded on a phone and on a computer: they
  should land near 90 against each other, and impressions of them well below.
- **A better Voice.** The listener model has reached the limit of its five
  inputs. Fine-tuning the speaker network on the ratings, as Ahn et al. did,
  or training it with a prototypical loss (Chen et al. 2026) would go further;
  either needs a GPU.
- **Listeners for delivery.** Melody, Rhythm and Articulation are calibrated
  on known answers but not yet checked against people's judgments of
  impressions.

## References

- Ahn et al. (2024). VoxSim: A perceptual voice similarity dataset.
  Interspeech 2024. [arXiv:2407.18505](https://arxiv.org/abs/2407.18505).
- Barreda, S. (2021). Fast Track: fast (nearly) automatic formant-tracking
  using Praat. *Linguistics Vanguard*.
- Boersma, P., & Weenink, D. Praat: doing phonetics by computer.
  https://www.fon.hum.uva.nl/praat/
- Chen et al. (2026). [arXiv:2609.33999](https://arxiv.org/abs/2609.33999).
- de Cheveigné, A., & Kawahara, H. (2002). YIN, a fundamental frequency
  estimator for speech and music. *Journal of the Acoustical Society of
  America*, 111(4).
- De Looze, C., & Hirst, D. (2008). Detecting changes in key and range for the
  automatic modelling and coding of intonation. Speech Prosody 2008.
- Hillenbrand, J., Getty, L. A., Clark, M. J., & Wheeler, K. (1995). Acoustic
  characteristics of American English vowels. *Journal of the Acoustical
  Society of America*, 97, 3099. Data:
  https://github.com/santiagobarreda/hillenbrand_et_al_1995
- Hirst, D. (2011). The analysis by synthesis of speech melody: from data to
  models. *Journal of Speech Sciences*, 1(1).
- Jadoul, Y., Thompson, B., & de Boer, B. (2018). Introducing Parselmouth: A
  Python interface to Praat. *Journal of Phonetics*, 71.
  https://github.com/YannickJadoul/Parselmouth
- Kominek, J., & Black, A. W. (2004). The CMU Arctic speech databases. 5th
  ISCA Speech Synthesis Workshop.
- Le Roux, J., Wisdom, S., Erdogan, H., & Hershey, J. R. (2019). SDR –
  half-baked or well done? ICASSP 2019.
- Livingstone, S. R., & Russo, F. A. (2018). The Ryerson Audio-Visual Database
  of Emotional Speech and Song (RAVDESS). *PLoS ONE*, 13(5), e0196391.
  https://doi.org/10.1371/journal.pone.0196391. Audio:
  https://zenodo.org/records/1188976
- Lorenzo-Trueba, J., et al. (2018). The Voice Conversion Challenge 2018:
  Promoting development of parallel and nonparallel methods. Odyssey 2018.
  [arXiv:1804.04262](https://arxiv.org/abs/1804.04262). Data:
  https://doi.org/10.7488/ds/2337 and https://doi.org/10.7488/ds/2496
- Nagrani, A., Chung, J. S., & Zisserman, A. (2017). VoxCeleb: a large-scale
  speaker identification dataset. Interspeech 2017.
- Pirker, G., Wohlmayr, M., Petrik, S., & Pernkopf, F. (2011). A pitch tracking
  corpus with evaluation on multipitch tracking scenario. Interspeech 2011.
  http://www2.spsc.tugraz.at/databases/PTDB-TUG/
- Radford, A., et al. (2023). Robust speech recognition via large-scale weak
  supervision. ICML 2023. [arXiv:2212.04356](https://arxiv.org/abs/2212.04356).
- Vestman, V., et al. (2020). Voice mimicry attacks assisted by automatic
  speaker verification. *Computer Speech & Language*.
  [arXiv:1906.01454](https://arxiv.org/abs/1906.01454).
- Wang, H., et al. (2023). WeSpeaker: A research and production oriented
  speaker embedding learning toolkit. ICASSP 2023.
  https://github.com/wenet-e2e/wespeaker
