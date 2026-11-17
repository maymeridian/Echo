"""Compare a take against a target and say what to change.

`measure` takes the raw distances. The take is time-aligned to the target first
(DTW over MFCCs), so pitch, formants and timing compare the same moment of the
line in both recordings; that part only works when both say the same words.
Voice doesn't depend on the words: it's the rating listeners would likely give
the pair (similarity.py, trained on VoxSim).

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

from . import gender, identity, line, similarity
from .analysis import MIN_VOICED_FRAMES, Voice

CALIBRATION = json.loads((Path(__file__).parent / "calibration.json").read_text("utf-8"))


@dataclass
class Measures:
    ti: np.ndarray  # alignment path: target frame indices
    ki: np.ndarray  # ...and the matching take frame indices
    identity: float  # cosine similarity of speaker embeddings, -1..1
    listener: float  # rating listeners would likely give, 1 (different) .. 6 (same)
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
            "identity": 6.0 - self.listener,
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
        listener=similarity.rating(target, take),
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


def compare(
    target: Voice,
    take: Voice,
    target_words: list[str] | None = None,
    take_words: list[str] | None = None,
    word_times: list[dict] | None = None,
    pitch_unreliable: bool = False,
) -> dict:
    """Score a take. With both transcripts, also check the take said the line.
    With the target's word timings, also say how close each word came.

    `pitch_unreliable`: the target's pitch sits far from its voice's usual
    pitch (creaky voice, or a pitch tracker off by an octave), so Pitch and
    Melody aren't scored and the headline comes from the other parts."""
    m = measure(target, take)
    check = None
    if target_words and len(target_words) >= 2 and take_words is not None:
        check = line.compare_words(target_words, take_words)
    scores = component_scores(m.distances())
    if pitch_unreliable:
        scores["pitch"] = scores["melody"] = None
    components = [
        _identity(m, scores["identity"]),
        _unreliable("pitch", "Pitch") if pitch_unreliable else _pitch(target, take, m, scores["pitch"]),
        _unreliable("melody", "Melody") if pitch_unreliable else _melody(m, scores["melody"]),
        _resonance(m, scores["resonance"]),
        _articulation(m, scores["articulation"]),
        _rhythm(m, scores["rhythm"], None if check is None else not check.flagged),
        _gender(target, take),
    ]
    scored = [c for c in components if c.score is not None]
    weakest = sorted((c for c in scored if c.hint and c.score < 80), key=lambda c: c.score)
    return {
        "overall": round(overall(scores), 1),
        "identity": round(m.identity, 3),
        "listener": round(m.listener, 2),
        "accepted": m.identity >= CALIBRATION["identity_accept"],
        "components": [
            {
                "key": c.key,
                "label": c.label,
                "score": None if c.score is None else round(c.score, 1),
                "detail": c.detail,
                "hint": c.hint,
            }
            for c in components
        ],
        "words": word_breakdown(target, take, m, word_times) if word_times else [],
        # How female or male each reads, 0-100 (gender.py). Shown beside the
        # parts; not part of the headline, which already counts pitch and resonance.
        "gender": {"target": gender.score(target), "take": gender.score(take)},
        # The raw gaps, so progress can be followed across lines of one voice.
        "measures": {
            "pitch": None if m.pitch is None else round(m.pitch, 2),
            "target_f0": None if target.median_f0 is None else round(target.median_f0, 1),
            "take_f0": None if take.median_f0 is None else round(take.median_f0, 1),
            "resonance": None if m.resonance_shift is None else round(m.resonance_shift, 2),
            "tempo": round(2**m.tempo, 3),
        },
        "hints": (_line_hints(check) + [c.hint for c in weakest])[:3],
        "line": None if check is None else {
            "target": " ".join(check.target),
            "take": " ".join(check.take),
            "match": round(check.match_rate, 2),
            "flagged": check.flagged,
        },
        "contour": _contour(target, take, m.ti, m.ki),
    }


# ---------- word by word ----------

WORD_PARTS = ("pitch", "melody", "resonance", "articulation")
MIN_WORD_FRAMES = 5  # aligned voiced frames before a word's pitch or formants count


def word_breakdown(target: Voice, take: Voice, m: Measures, word_times: list[dict]) -> list[dict]:
    """How close each of the target's words came, along the alignment.

    The measures are the line's own, restricted to one word, and scored on the
    line's curves: pitch is the gap between the two medians in semitones;
    melody is that gap minus the typical gap over the line's words, so it shows
    where the take's contour bends differently; resonance is the mean F1-F3 shift; articulation the MFCC
    distance. Rhythm isn't scored per word: the alignment moves word edges by
    a frame or two, which is a large share of a short word.

    `take_start` and `take_end` say where the word falls in the take, so the
    page can play the target's word and then the take's.
    """
    ti, ki = m.ti, m.ki
    ta, tb = target.mfcc[:, ti], take.mfcc[:, ki]
    step_cost = 1 - (ta * tb).sum(axis=0) / np.maximum(
        np.linalg.norm(ta, axis=0) * np.linalg.norm(tb, axis=0), 1e-9
    )
    curves = CALIBRATION["curves"]
    hop = float(target.times[1] - target.times[0]) if len(target.times) > 1 else 0.01

    out = []
    for w in word_times:
        on = (target.times[ti] >= w["start"]) & (target.times[ti] < w["end"])
        if on.sum() < 3:
            continue
        row = {
            "text": w["text"],
            "start": w["start"],
            "end": w["end"],
            "take_start": round(float(take.times[ki[on]].min()), 2),
            "take_end": round(float(take.times[ki[on]].max() + hop), 2),
            "pitch": None,
            "melody": None,
            "resonance": None,
            "articulation": round(float(step_cost[on].mean()), 3),
        }
        # Pitch and formants compare each side's median over the word, not
        # frame pairs: inside a held or gliding vowel the alignment can pair
        # the start of one with the end of the other.
        tf, kf = np.unique(ti[on]), np.unique(ki[on])
        a = target.f0[tf][target.voiced[tf]]
        b = take.f0[kf][take.voiced[kf]]
        if min(a.size, b.size) >= MIN_WORD_FRAMES:
            row["pitch"] = round(float(np.median(_semitones(b)) - np.median(_semitones(a))), 2)
        fa, fb = target.formants[:, tf], take.formants[:, kf]
        fa, fb = fa[:, np.isfinite(fa).all(axis=0)], fb[:, np.isfinite(fb).all(axis=0)]
        if min(fa.shape[1], fb.shape[1]) >= MIN_WORD_FRAMES:
            row["resonance"] = round(float(_semitones(np.median(fb, axis=1) / np.median(fa, axis=1)).mean()), 2)
        out.append(row)
    # Melody: each word's pitch gap against the typical gap over the line, so a
    # take that is simply higher throughout has no melody error.
    gaps = [w["pitch"] for w in out if w["pitch"] is not None]
    if len(gaps) >= 2:
        typical = float(np.median(gaps))
        for w in out:
            if w["pitch"] is not None:
                w["melody"] = round(w["pitch"] - typical, 2)
    for w in out:
        w["scores"] = {k: None if w[k] is None else round(curve(abs(w[k]), **curves[k]), 1) for k in WORD_PARTS}
        scored = {k: s for k, s in w["scores"].items() if s is not None}
        w["worst"] = min(scored, key=scored.get) if scored else None
    return out


# ---------- training one part on one word ----------

DRILL_PARTS = ("resonance", "pitch", "articulation")


def word_drill(target: Voice, word: dict, attempt: Voice) -> dict:
    """One word of the target against an attempt at just that word.

    The same measures as word_breakdown, on the same scale: resonance is the
    mean F1-F3 shift between the two medians, pitch the gap between the two
    medians, articulation the MFCC distance along a DTW alignment of the word
    to the attempt. Comparing one word with the same word keeps resonance fair:
    each vowel has formants of its own. Formants on single words are what
    Hillenbrand et al. (1995) measured, and where per-recording ceilings cut
    F2/F3 errors over 15% to 1.5%/2.8% (tools/validate.py formant-lines)."""
    on = np.flatnonzero((target.times >= word["start"]) & (target.times < word["end"]))
    spoken = np.arange(len(attempt.times))[attempt.spoken]
    out = {"text": word["text"], "pitch": None, "resonance": None, "percent": None, "articulation": None}
    if on.size < 3 or spoken.size < 3:
        return out | {"scores": dict.fromkeys(DRILL_PARTS), "voiced": False}

    a = target.f0[on][target.voiced[on]]
    b = attempt.f0[spoken][attempt.voiced[spoken]]
    voiced = b.size >= MIN_WORD_FRAMES
    if a.size >= MIN_WORD_FRAMES and voiced:
        out["pitch"] = round(float(np.median(_semitones(b)) - np.median(_semitones(a))), 2)
    fa, fb = target.formants[:, on], attempt.formants[:, spoken]
    fa, fb = fa[:, np.isfinite(fa).all(axis=0)], fb[:, np.isfinite(fb).all(axis=0)]
    if min(fa.shape[1], fb.shape[1]) >= MIN_WORD_FRAMES:
        shift = float(_semitones(np.median(fb, axis=1) / np.median(fa, axis=1)).mean())
        out["resonance"] = round(shift, 2)
        out["percent"] = round((2 ** (shift / 12) - 1) * 100, 1)
    # Each side centred on its own word, since a line and a lone word have
    # different averages.
    ta = target.mfcc[:, on] - target.mfcc[:, on].mean(axis=1, keepdims=True)
    tb = attempt.mfcc[:, spoken] - attempt.mfcc[:, spoken].mean(axis=1, keepdims=True)
    cost, path = librosa.sequence.dtw(X=ta, Y=tb, metric="cosine")
    out["articulation"] = round(float(cost[-1, -1]) / len(path), 3)

    curves = CALIBRATION["curves"]
    out["scores"] = {k: None if out[k] is None else round(curve(abs(out[k]), **curves[k]), 1) for k in DRILL_PARTS}
    out["voiced"] = voiced
    out["duration"] = round(attempt.spoken_duration / max(0.05, word["end"] - word["start"]), 2)
    return out


# ---------- coaching: one thing at a time ----------

FOCUSABLE = ("pitch", "melody", "resonance", "articulation", "rhythm")
GOOD_ENOUGH = 80  # a part at or above this stops being the focus
STAY = 0.6  # keep the last focus while its gain is at least this share of the biggest
STUCK_TAKES = 3  # a part this low on this many takes in a row...
STUCK_SCORE = 15  # ...without rising: it isn't answering to effort right now
PLATEAU_TAKES = 4  # this many takes in a row...
PLATEAU_SPREAD = 6.0  # ...within this many points, and none a new best: time for another line
MASTERED = 85.0


def coach(
    result: dict,
    previous: dict | None = None,
    chosen: str | None = None,
    history: list[float] | None = None,
    part_history: dict[str, list[float | None]] | None = None,
) -> dict:
    """What to work on in the next take, and how the last focus went.

    The focus is the part that would lift the overall score most: its weight
    times its distance from 100. Voice isn't a focus; it follows from the
    others. The coach stays with a focus until it reaches 80, unless something
    else has become clearly bigger, so the advice doesn't jump around between
    takes. `chosen` is a part picked by hand for this take; it stays the focus.
    `progress` reports the part this take was meant to work on. `history` is
    the line's earlier headline scores, oldest first: with it, the coach says
    when the line has stopped improving, so another line would help more."""
    scores = {c["key"]: c["score"] for c in result["components"]}
    hints = {c["key"]: c.get("hint") for c in result["components"]}
    labels = {c["key"]: c["label"] for c in result["components"]}
    weights = CALIBRATION["weights"]
    gains = {k: weights[k] * (100 - scores[k]) for k in FOCUSABLE if scores.get(k) is not None}
    # A part that has sat near the bottom for several takes without rising
    # isn't answering to effort, often because it measures something the
    # take can't change much right now (a target's breathy delivery, say).
    # Advice that isn't working is worse than none: work on the parts that move.
    stuck = []
    for k in list(gains):
        recent = [s for s in (part_history or {}).get(k, [])[-(STUCK_TAKES - 1):] if s is not None] + [scores[k]]
        if len(recent) >= STUCK_TAKES and max(recent) < STUCK_SCORE and k != chosen:
            stuck.append(k)
    if stuck and len(stuck) < len(gains):
        for k in stuck:
            gains.pop(k)

    last = (previous or {}).get("coach") or {}
    last_focus = chosen if chosen in labels else last.get("focus")
    before = None
    if last_focus and previous:
        before = next((c["score"] for c in previous["components"] if c["key"] == last_focus), None)
    progress = None
    if last_focus and before is not None and scores.get(last_focus) is not None:
        progress = {
            "key": last_focus,
            "label": labels[last_focus],
            "before": before,
            "after": scores[last_focus],
        }

    if chosen in gains:
        focus, why = chosen, "chosen"
    elif not gains:
        focus, why = None, None
    elif all(scores[k] >= GOOD_ENOUGH for k in gains):
        focus, why = None, "all good"
    else:
        best = max(gains, key=gains.get)
        weak = {k: g for k, g in gains.items() if scores[k] < GOOD_ENOUGH}
        if last_focus in weak and weak[last_focus] >= STAY * gains[best]:
            focus, why = last_focus, "keep"
        else:
            focus, why = max(weak, key=weak.get), "new"

    words = []
    if focus in WORD_PARTS:
        ranked = sorted(
            (w for w in result.get("words", []) if w["scores"].get(focus) is not None and w["scores"][focus] < 70),
            key=lambda w: w["scores"][focus],
        )
        words = [w["text"] for w in ranked[:3]]
    if focus is None:
        message = (
            "Every part is close. Go for the Voice score, try the line faster or slower, or pick another line."
            if why == "all good"
            else None
        )
    else:
        message = hints.get(focus) or f"Work on {labels[focus].lower()}."
    plateau = False
    overall = result.get("overall")
    if history and overall is not None and len(history) + 1 >= PLATEAU_TAKES:
        recent = history[-(PLATEAU_TAKES - 1):] + [overall]
        earlier_best = max(history[: -(PLATEAU_TAKES - 1)], default=-1.0)
        plateau = (
            max(recent) - min(recent) <= PLATEAU_SPREAD
            and max(recent) <= max(earlier_best, recent[0]) + 1
            and overall < MASTERED
        )
    return {
        "plateau": plateau,
        "stuck": stuck,
        "mastered": overall is not None and overall >= MASTERED,
        "focus": focus,
        "label": labels.get(focus) if focus else None,
        "score": scores.get(focus) if focus else None,
        "why": why,
        "message": message,
        "words": words,
        "progress": progress,
    }


# ---------- the voice check: several lines, one take each ----------

MIN_CHECK_LINES = 3
CHECK_LINES = 5


def voice_check(rows: list[dict]) -> dict:
    """A voice score from one fresh take on each of several lines.

    One line can be luck: a take that happens to suit that sentence. An
    impression holds when it holds across lines, so the voice score is the
    plain mean of the lines' headline scores. Every line is on the same
    calibrated scale (90: the real person on another mic, 20: a stranger), so
    the mean keeps that meaning. Each part is averaged the same way. `habits`
    are the median raw gaps across the lines, the things done the same way in
    all of them. `focus` is the part that would lift the voice most, by the
    coach's rule.

    `rows`: one per line, with `target`, `take`, `line`, `overall`, `parts`
    (part -> score) and `measures` (from `compare`)."""
    overall = np.array([r["overall"] for r in rows], dtype=float)
    weights = CALIBRATION["weights"]
    parts = {}
    for key in weights:
        values = [r["parts"].get(key) for r in rows if r["parts"].get(key) is not None]
        parts[key] = round(float(np.mean(values)), 1) if values else None
    gains = {
        k: weights[k] * (100 - v) for k, v in parts.items() if k in FOCUSABLE and v is not None and v < GOOD_ENOUGH
    }
    habits = {}
    for key in ("pitch", "resonance", "tempo", "take_f0", "target_f0"):
        values = [r["measures"][key] for r in rows if (r.get("measures") or {}).get(key) is not None]
        habits[key] = round(float(np.median(values)), 2) if values else None
    return {
        "average": round(float(overall.mean()), 1),
        "low": round(float(overall.min()), 1),
        "high": round(float(overall.max()), 1),
        "lines": rows,
        "parts": parts,
        "focus": max(gains, key=gains.get) if gains else None,
        "habits": habits,
        "weakest": rows[int(np.argmin(overall))]["target"],
    }


def _phrase(words: str) -> str:
    return words.strip(".,!?;:\"'“”‘’…-– ")


def _line_hints(check: line.LineCheck | None) -> list[str]:
    """Word problems come first: the line-matching parts mean little until the
    take says the target's words."""
    if check is None or not check.flagged:
        return []
    if check.match_rate < 0.5:
        return [f"That doesn't sound like the target's line. It says “{_phrase(' '.join(check.target))}”; "
                f"you said “{_phrase(' '.join(check.take)) or '…'}”."]
    hints = [f"You said “{_phrase(said)}” where the line has “{_phrase(has)}”." for has, said in check.swaps()]
    hints += [f"You left out “{_phrase(w)}”." for w in check.runs("miss")]
    hints += [f"You added “{_phrase(w)}”." for w in check.runs("extra")]
    return hints[:2]


def _gender(target: Voice, take: Voice) -> Component:
    """How female or male the take reads against the target (gender.py), as a
    row among the measurements: 100 when the two read the same. It isn't
    weighted into the headline, which already counts pitch and resonance."""
    a, b = gender.score(target), gender.score(take)
    if a is None or b is None:
        return Component("gender", "Gender read", None, "not enough voiced sound to measure")
    gap = b - a
    how = "the same as the target" if abs(gap) < 5 else f"{abs(gap):.0f} more {'female' if gap > 0 else 'male'} than the target"
    detail = f"you read {b:.0f} ({gender.describe(b)}), the target {a:.0f} ({gender.describe(a)}): {how}; 0 is male, 100 female"
    return Component("gender", "Gender read", max(0.0, 100.0 - abs(gap)), detail)


def _unreliable(key: str, label: str) -> Component:
    return Component(key, label, None, "not scored: the target's pitch reading is unreliable on this line")


def _unmeasured(key: str, label: str) -> Component:
    return Component(key, label, None, "not enough voiced sound to measure")


# VoxSim's rating labels.
LISTENER_LABELS = (
    "definitely different speakers",
    "probably different speakers",
    "possibly different speakers",
    "possibly the same speaker",
    "probably the same speaker",
    "definitely the same speaker",
)


def _identity(m: Measures, score: float) -> Component:
    label = LISTENER_LABELS[min(5, max(0, round(m.listener) - 1))]
    detail = f"listeners would likely say: {label} ({m.listener:.1f} of 6)"
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


def _rhythm(m: Measures, score: float, words_complete: bool | None = None) -> Component:
    """`words_complete`: the word check found every word (True), found words
    missing or added (False), or didn't run (None)."""
    ratio = 2**m.tempo
    percent = abs(ratio - 1) * 100
    big = ratio > 1.35 or ratio < 0.75
    if big and words_complete is False:
        # The word hints already explain the length; don't guess at a second cause.
        way = "longer" if ratio > 1 else "shorter"
        return Component("rhythm", "Rhythm", score, f"{percent:.0f}% {way} than the target")
    # Without the word check, timing can't tell a rushed line from a skipped
    # word, so a big difference asks rather than assumes.
    if big and words_complete is None and ratio > 1.35:
        detail = f"{percent:.0f}% longer than the target"
        hint = (
            f"Your take ran {percent:.0f}% longer than the target. Did you add or repeat "
            "anything? If it's the same line, speed up."
        )
    elif big and words_complete is None:
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
