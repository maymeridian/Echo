"""Compare a take against a target and say what to change.

The take is time-aligned to the target first (DTW over MFCCs), so every other
measurement compares the same moment of the line in both recordings. That only
works when both say the same words.

The 0-100 mappings below are hand-set starting points, not calibrated against
real impressions yet — see the README.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import librosa
import numpy as np

from .analysis import MIN_VOICED_FRAMES, Voice

WEIGHTS = {
    "pitch": 0.20,
    "melody": 0.15,
    "resonance": 0.25,
    "articulation": 0.25,
    "rhythm": 0.15,
}

# Distance at which each score has fallen to about 61 (one sigma).
PITCH_SIGMA_ST = 2.5
MELODY_SIGMA_ST = 2.0
RESONANCE_SIGMA_ST = 1.5
TEMPO_SIGMA_OCT = 0.4
TIMING_SIGMA = 0.06
# Mean cosine distance along the alignment path that scores zero.
ARTICULATION_ZERO = 0.6


@dataclass
class Component:
    key: str
    label: str
    score: float | None  # None when it couldn't be measured
    detail: str
    hint: str | None = None


def compare(target: Voice, take: Voice) -> dict:
    cost, path = librosa.sequence.dtw(X=target.mfcc, Y=take.mfcc, metric="cosine")
    path = path[::-1]
    ti, ki = path[:, 0], path[:, 1]

    components = [
        _pitch(target, take),
        _melody(target, take, ti, ki),
        _resonance(target, take, ti, ki),
        _articulation(float(cost[-1, -1]) / len(path)),
        _rhythm(target, take, ti, ki),
    ]
    scored = [c for c in components if c.score is not None]
    total = sum(WEIGHTS[c.key] for c in scored)
    overall = sum(WEIGHTS[c.key] * c.score for c in scored) / total

    weakest = sorted((c for c in scored if c.hint and c.score < 80), key=lambda c: c.score)
    return {
        "overall": round(overall, 1),
        "components": [
            {
                "key": c.key,
                "label": c.label,
                "score": None if c.score is None else round(c.score, 1),
                "detail": c.detail,
            }
            for c in components
        ],
        "hints": [c.hint for c in weakest[:3]],
        "contour": _contour(target, take, ti, ki),
    }


def _gauss(distance: float, sigma: float) -> float:
    return 100.0 * math.exp(-0.5 * (distance / sigma) ** 2)


def _semitones(ratio):
    return 12.0 * np.log2(ratio)


def _unmeasured(key: str, label: str) -> Component:
    return Component(key, label, None, "not enough voiced sound to measure")


def _pitch(target: Voice, take: Voice) -> Component:
    t, k = target.median_f0, take.median_f0
    if t is None or k is None:
        return _unmeasured("pitch", "Pitch")
    shift = float(_semitones(k / t))
    if abs(shift) < 0.25:
        return Component("pitch", "Pitch", _gauss(shift, PITCH_SIGMA_ST), f"on target, around {t:.0f} Hz")
    way = "higher" if shift > 0 else "lower"
    fix = "lower" if shift > 0 else "higher"
    return Component(
        "pitch",
        "Pitch",
        _gauss(shift, PITCH_SIGMA_ST),
        f"{abs(shift):.1f} semitones {way} ({k:.0f} Hz, target {t:.0f} Hz)",
        f"Pitch your voice about {abs(shift):.1f} semitones {fix}: you averaged "
        f"{k:.0f} Hz and the target sits around {t:.0f} Hz.",
    )


def _melody(target: Voice, take: Voice, ti: np.ndarray, ki: np.ndarray) -> Component:
    both = target.voiced[ti] & take.voiced[ki]
    if both.sum() < MIN_VOICED_FRAMES:
        return _unmeasured("melody", "Melody")
    a = _semitones(target.f0[ti][both])
    b = _semitones(take.f0[ki][both])
    # Centre each contour so this measures the shape, not the level (that's Pitch).
    drift = float(np.mean(np.abs((a - np.median(a)) - (b - np.median(b)))))
    span_t = float(np.percentile(a, 95) - np.percentile(a, 5))
    span_k = float(np.percentile(b, 95) - np.percentile(b, 5))

    spans = f"the target's pitch moves over {span_t:.0f} semitones and yours over {span_k:.0f}"
    if span_k < 0.7 * span_t:
        hint = f"Exaggerate the rises and falls — {spans}."
    elif span_k > 1.4 * span_t:
        hint = f"Flatten your delivery — {spans}."
    else:
        hint = (
            "Your pitch range is right but the ups and downs land in different "
            "places — listen for which words the target lifts and drops."
        )
    return Component(
        "melody",
        "Melody",
        _gauss(drift, MELODY_SIGMA_ST),
        f"contour off by {drift:.1f} semitones on average",
        hint,
    )


def _resonance(target: Voice, take: Voice, ti: np.ndarray, ki: np.ndarray) -> Component:
    a, b = target.formants[:, ti], take.formants[:, ki]
    ok = np.isfinite(a).all(axis=0) & np.isfinite(b).all(axis=0)
    if ok.sum() < MIN_VOICED_FRAMES:
        return _unmeasured("resonance", "Resonance")
    # Median per formant: single-frame tracking errors are common and large.
    per_formant = np.median(_semitones(b[:, ok] / a[:, ok]), axis=1)
    shift = float(per_formant.mean())
    distance = float(np.abs(per_formant).mean())
    percent = abs(2 ** (shift / 12) - 1) * 100

    if abs(shift) < 0.4:
        detail = "formants level with the target" if distance < 0.6 else "vowel shapes differ"
        hint = (
            "Your overall resonance is close, but individual vowels are shaped "
            "differently — copy the target's mouth shape on the long vowels."
        )
    elif shift > 0:
        detail = f"brighter than the target (formants {percent:.0f}% higher)"
        hint = (
            f"You sound brighter than the target (formants about {percent:.0f}% higher). "
            "Go darker: more space at the back of the mouth, larynx relaxed low."
        )
    else:
        detail = f"darker than the target (formants {percent:.0f}% lower)"
        hint = (
            f"You sound darker than the target (formants about {percent:.0f}% lower). "
            "Go brighter: tongue forward, smaller mouth space, a slight smile."
        )
    return Component("resonance", "Resonance", _gauss(distance, RESONANCE_SIGMA_ST), detail, hint)


def _articulation(distance: float) -> Component:
    score = 100.0 * min(1.0, max(0.0, 1.0 - distance / ARTICULATION_ZERO))
    return Component(
        "articulation",
        "Articulation",
        score,
        f"sound-by-sound distance {distance:.2f}",
        "The sounds themselves differ most — check you're saying the same words, "
        "then copy how the target shapes its vowels and hits its consonants.",
    )


def _rhythm(target: Voice, take: Voice, ti: np.ndarray, ki: np.ndarray) -> Component:
    ratio = take.duration / target.duration
    # How far the alignment strays from "same pace throughout": 0 means the take
    # is a uniformly faster or slower copy of the target's timing.
    wander = float(
        np.mean(np.abs(ti / max(1, len(target.times) - 1) - ki / max(1, len(take.times) - 1)))
    )
    score = 0.5 * _gauss(math.log2(ratio), TEMPO_SIGMA_OCT) + 0.5 * _gauss(wander, TIMING_SIGMA)
    percent = abs(ratio - 1) * 100
    if ratio > 1.1:
        detail = f"{percent:.0f}% slower than the target"
        hint = f"Speed up — your take ran {percent:.0f}% longer than the target."
    elif ratio < 0.9:
        detail = f"{percent:.0f}% faster than the target"
        hint = f"Slow down — your take was {percent:.0f}% shorter than the target."
    else:
        detail = "same length as the target"
        hint = (
            "The overall length matches but the timing inside the line drifts — "
            "match where the target pauses and which words it stretches."
        )
    return Component("rhythm", "Rhythm", score, detail, hint)


def _contour(target: Voice, take: Voice, ti: np.ndarray, ki: np.ndarray) -> dict:
    """Both pitch tracks on the target's timeline, for plotting."""
    n = len(target.times)
    # Every target frame is on the path at least once; average its partners.
    partner = np.round(np.bincount(ti, weights=ki, minlength=n) / np.bincount(ti, minlength=n))
    step = 2  # 20 ms is plenty for a chart

    def series(values: np.ndarray) -> list[float | None]:
        return [round(float(v), 1) if np.isfinite(v) else None for v in values[::step]]

    return {
        "t": [round(float(t), 2) for t in target.times[::step]],
        "target": series(target.f0),
        "take": series(take.f0[partner.astype(int)]),
    }
