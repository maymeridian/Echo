"""Compare a take against a target and say what to change.

`measure` takes the raw distances. The take is time-aligned to the target first,
so pitch, formants and timing compare the same moment of the line in both
recordings; that part only works when both say the same words. Voice doesn't
depend on the words: it's the rating listeners would likely give the pair
(similarity.py, trained on VoxSim).

The alignment is dynamic time warping over what a speech recogniser hears
(line.sounds), not over MFCCs. MFCCs carry the voice as much as the words: on
RAVDESS a stranger saying the right line was as far from the target (0.364) as
the right actor saying the other sentence (0.368), and a real take against a
breathy target sat past both, so Articulation read 0 whatever was said. The
recogniser's frames put a stranger (0.17) between a retake (0.11) and another
sentence (0.23), and they line takes up better: carried along the alignment,
a line's words landed a median 50 ms from where the take's own transcript has
them, against 80 ms, and over 200 ms off for 6% of words against 16%
(cache/experiments/articulation, 2026-11).

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
from .analysis import HOP, MIN_VOICED_FRAMES, SR, Voice, shared_ceiling

# Resonance is read from F2 and F3. On a breathy voice near 190 Hz, Praat's F1
# flips between the first harmonic (about 250 Hz) and the formant (about 430):
# across takes of one line its shift varied by 0.7-2.4 semitones, against
# 0.4-0.85 for F2 and 0.2-0.45 for F3, and the whole curve from 90 to 20 spans
# less than one. Both sides are read at one ceiling (analysis.shared_ceiling)
# and at four more around it, and the middle reading counts, so the reading no
# longer moves with the ceiling. Fewer than RESONANCE_FRAMES aligned voiced
# frames gave junk (+8.4 semitones on one take), so then it isn't read. On 300
# RAVDESS pairs this tells a retake from a stranger at AUC 0.94, from 0.83
# (cache/experiments/resonance_check, 2026-11).
RESONANCE_FRAMES = 60
TIMING_POINTS = 10  # stretches of the line that timing is read at
CEILING_SPREAD = (-500.0, -250.0, 0.0, 250.0, 500.0)  # Hz around the shared ceiling

# Vocal fry (creak) is read as the share of a recording's voiced sound more
# than FRY_UNDER semitones under its speaker's usual pitch. Against the
# laryngograph in PTDB-TUG that finds 60% of creaky frames and fires on 1.3%
# of ordinary ones, and a speaker's share follows the laryngograph's at r 0.81.
# Ordinary speech reads 2-13%, so only a gap against the target says anything.
# It is shown beside the parts and not counted: nothing calibrates it yet
# (cache/experiments/phonation, 2026-11).
FRY_UNDER = 7.0
FRY_GAP = 0.15  # a difference in share worth a hint

CALIBRATION = json.loads((Path(__file__).parent / "calibration.json").read_text("utf-8"))
# Which scoring a result came from. It goes up whenever the same take would
# score differently, and saved takes from an earlier one are scored again
# (server.rescore), so a best or a trend never mixes two scales.
#   2 (1.1.0): alignment and Articulation from what a speech recogniser hears,
#              Rhythm from that alignment, Resonance from F2 and F3 at one ceiling.
#   3 (1.1.1): saved audio is read as saved, not trimmed a second time.
SCORING = 3


@dataclass
class Measures:
    ti: np.ndarray  # the alignment on the 10 ms grid: every target frame...
    ki: np.ndarray  # ...and the take frame that matches it
    identity: float  # cosine similarity of speaker embeddings, -1..1
    listener: float  # rating listeners would likely give, 1 (different) .. 6 (same)
    articulation: float  # mean cosine distance between aligned recogniser frames that both have sound
    tempo: float  # log2 of take / target length, first to last voiced sound
    timing: float  # how far the alignment strays from an even stretch between those, 0..1
    si: np.ndarray | None = None  # the alignment as found, on the recogniser's 20 ms frames: target...
    sj: np.ndarray | None = None  # ...and take
    step: np.ndarray | None = None  # the distance at each of its steps
    ceiling: float | None = None  # the formant ceiling Resonance was read around
    pitch: float | None = None  # take minus target median pitch, semitones
    melody: float | None = None  # median contour difference after centring, semitones
    melody_span: tuple[float, float] | None = None  # target, take 5-95% pitch range
    resonance: float | None = None  # mean |shift| of F2 and F3, semitones
    resonance_shift: float | None = None  # their signed mean shift, semitones

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


def _cosine_steps(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return 1 - (a * b).sum(axis=1) / np.maximum(np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1), 1e-9)


def resonance_ceilings(target: Voice, take: Voice) -> tuple[float, list[float]]:
    """The ceiling both sides are read at, and the ones around it."""
    middle = take.steady_ceiling if take.steady_ceiling and take.steady_ceiling == target.steady_ceiling else None
    middle = middle or shared_ceiling(target.formant_ceiling, take.formant_ceiling)
    return middle, sorted({min(7000.0, max(4000.0, middle + d)) for d in CEILING_SPREAD})


def align(target: Voice, take: Voice) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The take lined up with the target by what a recogniser hears in each:
    the target's and the take's frame at every step (20 ms frames), and how
    far apart the two sound there."""
    _, path = librosa.sequence.dtw(X=target.sounds.T, Y=take.sounds.T, metric="cosine")
    path = path[::-1]
    si, sj = path[:, 0], path[:, 1]
    return si, sj, _cosine_steps(target.sounds[si], take.sounds[sj])


def on_grid(target: Voice, take: Voice, si: np.ndarray, sj: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """That alignment on the 10 ms grid pitch and formants are on: the take
    frame for every target frame, through the middle of each recogniser
    frame's run."""
    n_t, n_k, n_s = len(target.times), len(take.times), len(target.sounds)
    first = np.searchsorted(si, np.arange(n_s), side="left")
    last = np.searchsorted(si, np.arange(n_s), side="right") - 1
    middle = (sj[first] + sj[last]) / 2
    ti = np.arange(n_t)
    ki = np.clip(np.round(np.interp(ti, 2 * np.arange(n_s) + 0.5, 2 * middle + 0.5)).astype(int), 0, n_k - 1)
    return ti, ki


def read_resonance(target: Voice, take: Voice, ti: np.ndarray, ki: np.ndarray) -> tuple[float, float | None, float | None]:
    """Resonance along an alignment: the ceiling it was read around, the mean
    |shift| of F2 and F3 in semitones, and their signed mean shift (None when
    there is too little voiced sound in common)."""
    middle, ceilings = resonance_ceilings(target, take)
    readings = []
    for ceiling in ceilings:
        fa, fb = target.at(ceiling)[1:, ti], take.at(ceiling)[1:, ki]
        ok = np.isfinite(fa).all(axis=0) & np.isfinite(fb).all(axis=0)
        if ok.sum() >= RESONANCE_FRAMES:
            # Median per formant: single-frame tracking errors are common and large.
            per_formant = np.median(_semitones(fb[:, ok] / fa[:, ok]), axis=1)
            readings.append((float(np.abs(per_formant).mean()), float(per_formant.mean())))
    if not readings:
        return middle, None, None
    return middle, float(np.median([r[0] for r in readings])), float(np.median([r[1] for r in readings]))


def measure(target: Voice, take: Voice) -> Measures:
    si, sj, step = align(target, take)
    # Only where both have sound: a pause, a silent tail, the gaps a gated
    # microphone leaves and the click on Stop are not part of the line.
    on = target.sound[si] & take.sound[sj]
    if on.sum() < 5:
        on = np.ones(len(si), dtype=bool)
    # The line in each recording runs from its first to its last voiced sound
    # (Voice.spoken): a breath after the last word has sound, and lined up
    # against the end of the target it read as part of the line (takes that
    # ran 10% long by their own word timings read 50% long).
    # Timing: how far the take runs ahead of or behind an even stretch of the
    # line between those, read at TIMING_POINTS stretches of it and not step
    # by step. The alignment wanders by a frame or two with any change in the
    # sound, and the whole curve from 90 to 20 is a few hundredths wide.
    per = round(line.SOUND_STEP * SR / HOP)  # 10 ms frames in one recogniser frame
    ts, ks = target.spoken, take.spoken
    ta, ka = si * per, sj * per
    inside = on & (ta >= ts.start) & (ta < ts.stop) & (ka >= ks.start) & (ka < ks.stop)
    drift = []
    if inside.sum() >= 2:
        along = (ta[inside] - ts.start) / max(1, ts.stop - ts.start)
        there = (ka[inside] - ks.start) / max(1, ks.stop - ks.start)
        stretch = np.minimum((along * TIMING_POINTS).astype(int), TIMING_POINTS - 1)
        drift = [abs(float(along[stretch == i].mean() - there[stretch == i].mean())) for i in range(TIMING_POINTS) if (stretch == i).sum() >= 2]
    ti, ki = on_grid(target, take, si, sj)
    m = Measures(
        ti=ti,
        ki=ki,
        identity=identity.similarity(target.embedding, take.embedding),
        listener=similarity.rating(target, take),
        articulation=float(step[on].mean()),
        tempo=math.log2(take.spoken_duration / target.spoken_duration),
        timing=float(np.mean(drift)) if drift else 0.0,
        si=si,
        sj=sj,
        step=step,
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

    m.ceiling, m.resonance, m.resonance_shift = read_resonance(target, take, ti, ki)
    return m


# ---------- distances to scores ----------


def curve(distance: float, at90: float, at20: float) -> float:
    """0-100 on a log-logistic curve: 100 at zero distance, 90 at `at90`
    (the real person retaking the line) and 20 at `at20` (a stranger)."""
    k = math.log(36) / math.log(at20 / at90)
    d50 = at20 / 4 ** (1 / k)
    return 100.0 / (1.0 + (max(distance, 0.0) / d50) ** k)


def component_scores(distances: dict[str, float | None], calibration: dict = CALIBRATION) -> dict:
    """Score per displayed component. Rhythm mixes tempo (the pace of the
    whole line) and timing (the pace inside it) by how well each tells a
    retake from a stranger: timing barely does, and at half of Rhythm its
    steep curve made Rhythm jump."""
    s = {
        k: None if v is None else curve(v, **calibration["curves"][k])
        for k, v in distances.items()
    }
    mix = calibration.get("rhythm", {"tempo": 0.5, "timing": 0.5})
    s["rhythm"] = mix["tempo"] * s.pop("tempo") + mix["timing"] * s.pop("timing")
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


def fry_share(voice: Voice, usual: float | None) -> float | None:
    """How much of a recording's voiced sound is vocal fry: more than
    FRY_UNDER semitones under `usual`, its speaker's usual pitch in Hz."""
    if not usual or voice.voiced.sum() < MIN_VOICED_FRAMES:
        return None
    return float(np.mean(_semitones(voice.f0[voice.voiced] / usual) < -FRY_UNDER))


def compare(
    target: Voice,
    take: Voice,
    target_words: list[str] | None = None,
    take_words: list[str] | None = None,
    word_times: list[dict] | None = None,
    pitch_unreliable: bool = False,
    usual: tuple[float | None, float | None] = (None, None),
) -> dict:
    """Score a take. With both transcripts, also check the take said the line.
    With the target's word timings, also say how close each word came.

    `pitch_unreliable`: the target's pitch sits far from its voice's usual
    pitch (creaky voice, or a pitch tracker off by an octave), so Pitch and
    Melody aren't scored and the headline comes from the other parts.

    `usual`: the usual pitch of the target's voice and of whoever made the
    take, in Hz, when they are known. With both, the result says how much of
    each recording is vocal fry."""
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
    fry = (fry_share(target, usual[0]), fry_share(take, usual[1]))
    if None not in fry:
        components.append(_fry(*fry))
    scored = [c for c in components if c.score is not None]
    weakest = sorted((c for c in scored if c.hint and c.score < 80), key=lambda c: c.score)
    return {
        "scoring": SCORING,
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
        # The share of each recording's voiced sound that is vocal fry, when the usual pitches are known.
        "fry": None if None in fry else {"target": round(fry[0], 3), "take": round(fry[1], 3)},
        # The raw gaps, so progress can be followed across lines of one voice.
        "measures": {
            "pitch": None if m.pitch is None else round(m.pitch, 2),
            "target_f0": None if target.median_f0 is None else round(target.median_f0, 1),
            "take_f0": None if take.median_f0 is None else round(take.median_f0, 1),
            "resonance": None if m.resonance_shift is None else round(m.resonance_shift, 2),
            "tempo": round(2**m.tempo, 3),
            # The formant ceiling the take picks for itself, and the one Resonance
            # was measured with (the middle of your recent takes' picks).
            "ceiling": take.formant_ceiling,
            "ceiling_used": m.ceiling,
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
    where the take's contour bends differently; resonance is the mean shift of
    F2 and F3; articulation the distance between what the recogniser hears.
    Rhythm isn't scored per word: the alignment moves word edges by a frame or
    two, which is a large share of a short word.

    `take_start` and `take_end` say where the word falls in the take, so the
    page can play the target's word and then the take's.
    """
    ti, ki = m.ti, m.ki
    heard = target.sound[m.si] & take.sound[m.sj]
    starts = m.si * line.SOUND_STEP  # where each step of the alignment falls in the target
    fa_all, fb_all = target.at(m.ceiling)[1:], take.at(m.ceiling)[1:]
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
            "articulation": None,
        }
        said = heard & (starts >= w["start"] - line.SOUND_STEP / 2) & (starts < w["end"])
        if said.sum() >= 2:
            row["articulation"] = round(float(m.step[said].mean()), 3)
        # Pitch and formants compare each side's median over the word, not
        # frame pairs: inside a held or gliding vowel the alignment can pair
        # the start of one with the end of the other.
        tf, kf = np.unique(ti[on]), np.unique(ki[on])
        a = target.f0[tf][target.voiced[tf]]
        b = take.f0[kf][take.voiced[kf]]
        if min(a.size, b.size) >= MIN_WORD_FRAMES:
            row["pitch"] = round(float(np.median(_semitones(b)) - np.median(_semitones(a))), 2)
        fa, fb = fa_all[:, tf], fb_all[:, kf]
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
    mean shift of F2 and F3 between the two medians, pitch the gap between the
    two medians, articulation the distance between what the recogniser hears
    in the target's word, cut out and heard by itself, and in the attempt,
    along their alignment. Comparing one word with the same word keeps resonance fair:
    each vowel has formants of its own. Formants on single words are what
    Hillenbrand et al. (1995) measured, and where per-recording ceilings cut
    F2/F3 errors over 15% to 1.5%/2.8% (tools/validate.py formant-lines)."""
    on = np.flatnonzero((target.times >= word["start"]) & (target.times < word["end"]))
    spoken = np.arange(len(attempt.times))[attempt.spoken]
    out = {"scoring": SCORING, "text": word["text"], "pitch": None, "resonance": None, "percent": None, "articulation": None}
    if on.size < 3 or spoken.size < 3:
        return out | {"scores": dict.fromkeys(DRILL_PARTS), "voiced": False}

    a = target.f0[on][target.voiced[on]]
    b = attempt.f0[spoken][attempt.voiced[spoken]]
    voiced = b.size >= MIN_WORD_FRAMES
    if a.size >= MIN_WORD_FRAMES and voiced:
        out["pitch"] = round(float(np.median(_semitones(b)) - np.median(_semitones(a))), 2)
    fa, fb = target.resonance_formants[1:, on], attempt.resonance_formants[1:, spoken]
    fa, fb = fa[:, np.isfinite(fa).all(axis=0)], fb[:, np.isfinite(fb).all(axis=0)]
    if min(fa.shape[1], fb.shape[1]) >= MIN_WORD_FRAMES:
        shift = float(_semitones(np.median(fb, axis=1) / np.median(fa, axis=1)).mean())
        out["resonance"] = round(shift, 2)
        out["percent"] = round((2 ** (shift / 12) - 1) * 100, 1)
    # The target's word is heard by itself, as the attempt is: the recogniser
    # hears a sound differently with the rest of a sentence around it.
    heard = target.tracks.setdefault("words", {})
    key = (round(word["start"], 2), round(word["end"], 2))
    if key not in heard:
        cut = target.y[max(0, int((word["start"] - 0.03) * SR)) : int((word["end"] + 0.03) * SR)]
        heard[key] = line.sounds(cut)
    a, b = heard[key], attempt.sounds
    _, path = librosa.sequence.dtw(X=a.T, Y=b.T, metric="cosine")
    steps = _cosine_steps(a[path[:, 0]], b[path[:, 1]])
    said = attempt.sound[path[:, 1]]
    out["articulation"] = round(float(steps[said].mean() if said.sum() >= 2 else steps.mean()), 3)

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
        said = _phrase(" ".join(check.take)) or "…"
        return [f"That doesn't sound like the target's line. It says “{_phrase(' '.join(check.target))}”; you said “{said}”."]
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


def _fry(target: float, take: float) -> Component:
    """Vocal fry in the take against the target, as a row among the
    measurements: 100 when both spend the same share of their voiced sound in
    it. It isn't weighted into the headline."""
    gap = take - target
    detail = f"the target is in vocal fry for {target:.0%} of its voiced sound, you for {take:.0%}"
    hint = None
    if gap <= -FRY_GAP:
        hint = (
            f"The target drops into vocal fry (a low creak) for about {target:.0%} of this line and you for {take:.0%}. "
            "Let the ends of phrases fall low and crackle, as the target does."
        )
    elif gap >= FRY_GAP:
        hint = (
            f"You drop into vocal fry (a low creak) for about {take:.0%} of this line and the target for {target:.0%}. "
            "Keep the ends of phrases on the note."
        )
    return Component("fry", "Vocal fry", max(0.0, 100.0 - 100.0 * abs(gap)), detail, hint)


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
        detail = "formants level with the target" if m.resonance < 0.5 else "vowel shapes differ"
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
