"""Compare a take against a target and say what to change.

`measure` takes the raw distances. The take is time-aligned to the target first
(DTW over MFCCs), so pitch, formants and timing compare the same moment of the
line in both recordings; that part only works when both say the same words.
The speaker-identity similarity doesn't depend on the words.

`compare` maps each distance to 0-100 with curves from calibration.json (see
tools/calibrate.py) and writes the details and hints.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np

from . import identity
from .analysis import MIN_VOICED_FRAMES, Voice

CALIBRATION = json.loads((Path(__file__).parent / "calibration.json").read_text("utf-8"))


@dataclass
class Measures:
    ti: np.ndarray  # alignment path: target frame indices
    ki: np.ndarray  # ...and the matching take frame indices
    identity: float  # cosine similarity of speaker embeddings, -1..1
    articulation: float  # mean cosine distance between aligned MFCC frames
    tempo: float  # log2 of take / target length, first to last voiced sound
    timing: float  # how far the alignment strays from a uniform stretch, 0..1
    pitch: float | None = None  # take minus target median pitch, semitones
    melody: float | None = None  # median contour difference after centring, semitones
    melody_span: tuple[float, float] | None = None  # target, take 5-95% pitch range
    resonance: float | None = None  # mean |F1-F3 shift|, semitones
    resonance_shift: float | None = None  # signed mean F1-F3 shift, semitones

    def distances(self) -> dict[str, float | None]:
        """Each measure as a non-negative distance where 0 is a perfect match."""
        return {
            "identity": 1.0 - self.identity,
            "pitch": None if self.pitch is None else abs(self.pitch),
            "melody": self.melody,
            "resonance": self.resonance,
            "articulation": self.articulation,
            "tempo": abs(self.tempo),
            "timing": self.timing,
        }


def _semitones(ratio):
    return 12.0 * np.log2(ratio)


def measure(target: Voice, take: Voice) -> Measures:
    cost, path = librosa.sequence.dtw(X=target.mfcc, Y=take.mfcc, metric="cosine")
    path = path[::-1]
    ti, ki = path[:, 0], path[:, 1]
    n_t, n_k = len(target.times), len(take.times)
    m = Measures(
        ti=ti,
        ki=ki,
        identity=identity.similarity(target.embedding, take.embedding),
        articulation=float(cost[-1, -1]) / len(path),
        tempo=math.log2(take.spoken_duration / target.spoken_duration),
        timing=float(np.mean(np.abs(ti / max(1, n_t - 1) - ki / max(1, n_k - 1)))),
    )

    if target.median_f0 is not None and take.median_f0 is not None:
        m.pitch = float(_semitones(take.median_f0 / target.median_f0))

    both = target.voiced[ti] & take.voiced[ki]
    if both.sum() >= MIN_VOICED_FRAMES:
        a = _semitones(target.f0[ti][both])
        b = _semitones(take.f0[ki][both])
        # Centre each contour so this measures the shape, not the level (that's
        # pitch). The median ignores the octave jumps pitch trackers make.
        m.melody = float(np.median(np.abs((a - np.median(a)) - (b - np.median(b)))))
        m.melody_span = (
            float(np.percentile(a, 95) - np.percentile(a, 5)),
            float(np.percentile(b, 95) - np.percentile(b, 5)),
        )

    fa, fb = target.formants[:, ti], take.formants[:, ki]
    ok = np.isfinite(fa).all(axis=0) & np.isfinite(fb).all(axis=0)
    if ok.sum() >= MIN_VOICED_FRAMES:
        # Median per formant: single-frame tracking errors are common and large.
        per_formant = np.median(_semitones(fb[:, ok] / fa[:, ok]), axis=1)
        m.resonance = float(np.abs(per_formant).mean())
        m.resonance_shift = float(per_formant.mean())
    return m


# ---------- distances to scores ----------


def curve(distance: float, at90: float, at20: float) -> float:
    """0-100 on a log-logistic curve: 100 at zero distance, 90 at `at90`
    (the real person retaking the line) and 20 at `at20` (a stranger)."""
    k = math.log(36) / math.log(at20 / at90)
    d50 = at20 / 4 ** (1 / k)
    return 100.0 / (1.0 + (max(distance, 0.0) / d50) ** k)


def component_scores(distances: dict[str, float | None], calibration: dict = CALIBRATION) -> dict:
    """Score per displayed component; rhythm averages tempo and timing."""
    s = {
        k: None if v is None else curve(v, **calibration["curves"][k])
        for k, v in distances.items()
    }
    s["rhythm"] = (s.pop("tempo") + s.pop("timing")) / 2
    return s


def weighted(scores: dict[str, float | None], weights: dict[str, float]) -> float:
    """Weighted mean of the components that could be measured."""
    present = [k for k, v in scores.items() if v is not None]
    return sum(weights[k] * scores[k] for k in present) / sum(weights[k] for k in present)


def overall(scores: dict[str, float | None], calibration: dict = CALIBRATION) -> float:
    """The headline: the weighted mean put back on the same scale as the parts,
    so the real person retaking the line scores 90 and a stranger 20."""
    mean = weighted(scores, calibration["weights"])
    return curve(100.0 - mean, **calibration["overall"])


@dataclass
class Component:
    key: str
    label: str
    score: float | None  # None when it couldn't be measured
    detail: str
    hint: str | None = None


def compare(target: Voice, take: Voice) -> dict:
    m = measure(target, take)
    scores = component_scores(m.distances())
    components = [
        _identity(m, scores["identity"]),
        _pitch(target, take, m, scores["pitch"]),
        _melody(m, scores["melody"]),
        _resonance(m, scores["resonance"]),
        _articulation(m, scores["articulation"]),
        _rhythm(m, scores["rhythm"]),
    ]
    scored = [c for c in components if c.score is not None]
    weakest = sorted((c for c in scored if c.hint and c.score < 80), key=lambda c: c.score)
    return {
        "overall": round(overall(scores), 1),
        "identity": round(m.identity, 3),
        "accepted": m.identity >= CALIBRATION["identity_accept"],
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
        "contour": _contour(target, take, m.ti, m.ki),
    }


def _unmeasured(key: str, label: str) -> Component:
    return Component(key, label, None, "not enough voiced sound to measure")


def _identity(m: Measures, score: float) -> Component:
    if m.identity >= CALIBRATION["identity_accept"]:
        detail = "a speaker-verification model would accept this as the same person"
    else:
        detail = f"speaker similarity {m.identity:.2f}"
    return Component(
        "identity",
        "Voice",
        score,
        detail,
        "Overall you don't yet sound like the same person — get pitch and "
        "resonance close first; they carry most of a voice's identity.",
    )


def _pitch(target: Voice, take: Voice, m: Measures, score: float | None) -> Component:
    if m.pitch is None:
        return _unmeasured("pitch", "Pitch")
    t, k = target.median_f0, take.median_f0
    if abs(m.pitch) < 0.25:
        return Component("pitch", "Pitch", score, f"on target, around {t:.0f} Hz")
    way, fix = ("higher", "lower") if m.pitch > 0 else ("lower", "higher")
    return Component(
        "pitch",
        "Pitch",
        score,
        f"{abs(m.pitch):.1f} semitones {way} ({k:.0f} Hz, target {t:.0f} Hz)",
        f"Pitch your voice about {abs(m.pitch):.1f} semitones {fix}: you averaged "
        f"{k:.0f} Hz and the target sits around {t:.0f} Hz.",
    )


def _melody(m: Measures, score: float | None) -> Component:
    if m.melody is None:
        return _unmeasured("melody", "Melody")
    span_t, span_k = m.melody_span
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
        "melody", "Melody", score, f"contour typically off by {m.melody:.1f} semitones", hint
    )


def _resonance(m: Measures, score: float | None) -> Component:
    if m.resonance is None:
        return _unmeasured("resonance", "Resonance")
    shift = m.resonance_shift
    percent = abs(2 ** (shift / 12) - 1) * 100
    if abs(shift) < 0.4:
        detail = "formants level with the target" if m.resonance < 0.6 else "vowel shapes differ"
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
    return Component("resonance", "Resonance", score, detail, hint)


def _articulation(m: Measures, score: float) -> Component:
    return Component(
        "articulation",
        "Articulation",
        score,
        f"sound-by-sound distance {m.articulation:.2f}",
        "The sounds themselves differ most — check you're saying the same words, "
        "then copy how the target shapes its vowels and hits its consonants.",
    )


def _rhythm(m: Measures, score: float) -> Component:
    ratio = 2**m.tempo
    percent = abs(ratio - 1) * 100
    # Timing can't tell a rushed line from a skipped word (that needs speech
    # recognition), so a big difference asks rather than assumes.
    if ratio > 1.35:
        detail = f"{percent:.0f}% longer than the target"
        hint = (
            f"Your take ran {percent:.0f}% longer than the target. Did you add or repeat "
            "anything? If it's the same line, speed up."
        )
    elif ratio < 0.75:
        detail = f"{percent:.0f}% shorter than the target"
        hint = (
            f"Your take was {percent:.0f}% shorter than the target. Did you say the whole "
            "line? If you did, slow down."
        )
    elif ratio > 1.1:
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
