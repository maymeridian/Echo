"""How female or male a voice reads, 0 (male) to 100 (female).

Perceived voice gender rests mostly on pitch and resonance: the median pitch,
and the formants, which follow the length of the vocal tract. A logistic
regression on those two (gender_model.json, tools/train_gender.py) learned
speakers' sex from RAVDESS and VCC2018. On PTDB-TUG, a different corpus with
no speaker in common, it tells the speakers apart: see the README. The labels are speakers'
sex, so the score says how a voice's pitch and resonance compare with men's
and women's, not what any one listener would say.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .analysis import Voice

# A uniform tube's second and third resonances. F1 is left out: it moves
# most with the vowel and with a soft, breathy delivery (one such voice's F1
# read about 330 Hz against 610 for a woman reading aloud), while F2 and F3
# follow the length of the vocal tract more closely.
NEUTRAL = np.array([1500.0, 2500.0])
_PATH = Path(__file__).parent / "gender_model.json"
_MODEL = json.loads(_PATH.read_text("utf-8")) if _PATH.is_file() else None


def features(voice: Voice) -> np.ndarray | None:
    """Median pitch in semitones above 100 Hz, and the mean F2-F3 shift from a
    neutral tube in semitones; None without enough voiced sound."""
    f0 = voice.median_f0
    formants = voice.median_formants[1:]
    if f0 is None or not np.isfinite(formants).all():
        return None
    return np.array([12 * np.log2(f0 / 100.0), float(np.mean(12 * np.log2(formants / NEUTRAL)))])


def score(voice: Voice) -> float | None:
    """0 reads male, 100 female; None if it can't be measured."""
    x = features(voice)
    if x is None or _MODEL is None:
        return None
    z = (x - np.array(_MODEL["mean"])) / np.array(_MODEL["scale"])
    logit = float(z @ np.array(_MODEL["weights"]) + _MODEL["bias"])
    return round(100.0 / (1.0 + np.exp(-logit)), 1)


def describe(value: float | None) -> str | None:
    if value is None:
        return None
    if value >= 80:
        return "female"
    if value >= 60:
        return "leans female"
    if value > 40:
        return "in between"
    return "leans male" if value > 20 else "male"
