'''
tests/test_scoring.py
Author: maymeridian
Description: Tests for the scorer: each part's distance, word by word, the coach and resonance.
             - Most use the synthetic voice from synth.py, changing one property at a time.
             - The gender read is also checked on two real CMU ARCTIC speakers.
'''

import numpy as np
import pytest
from synth import voice

from echo_voice import AnalysisError, analyze, compare


def score(take: np.ndarray, target: np.ndarray | None = None) -> dict:
    """Compare `take` with `target` (the default synthetic voice if None), parts by key."""
    result = compare(analyze(voice() if target is None else target), analyze(take))
    result["by_key"] = {c["key"]: c for c in result["components"]}
    return result


# --- THE PARTS OF THE SCORE ---


def test_identical_recordings_match_fully():
    """A recording against itself scores at the top, with nothing to fix."""
    result = score(voice())
    assert result["overall"] > 99
    assert result["hints"] == []


def test_higher_pitch_costs_pitch_score_and_says_so():
    """A take 4 semitones higher loses on pitch and says by how much; melody holds."""
    result = score(voice(f0=120 * 2 ** (4 / 12)))
    pitch = result["by_key"]["pitch"]
    assert pitch["score"] < 40
    assert "4.0 semitones higher" in pitch["detail"]
    assert result["by_key"]["melody"]["score"] > 90  # same shape, just transposed


def test_flat_delivery_costs_melody_not_pitch():
    """A flat take against a lively target loses on melody, not pitch, and is told to exaggerate."""
    result = score(voice(glide_semitones=0.0), target=voice(glide_semitones=10.0))
    assert result["by_key"]["melody"]["score"] < 70
    assert result["by_key"]["pitch"]["score"] > 95
    assert any("Exaggerate" in hint for hint in result["hints"])


def test_shorter_vocal_tract_reads_as_brighter():
    """Formants 15% higher read as a brighter resonance, and leave the pitch score alone."""
    result = score(voice(formant_scale=1.15))
    resonance = result["by_key"]["resonance"]
    assert resonance["score"] < 60
    assert "brighter" in resonance["detail"]
    assert result["by_key"]["pitch"]["score"] > 95


def test_slower_take_costs_rhythm():
    """Vowels half as long again cost rhythm, and the detail calls the take slower."""
    result = score(voice(vowel_seconds=0.6))
    rhythm = result["by_key"]["rhythm"]
    assert rhythm["score"] < 90
    assert "slower" in rhythm["detail"]


def test_contour_is_on_the_targets_timeline():
    """The contour runs on the target's timeline, even when the take is longer."""
    contour = score(voice(vowel_seconds=0.6))["contour"]
    assert len(contour["t"]) == len(contour["target"]) == len(contour["take"])
    # The target's length: four 0.4 s vowels and three 0.06 s gaps.
    assert contour["t"][-1] == pytest.approx(1.78, abs=0.05)


def test_silence_is_rejected():
    """A second of silence is refused with an AnalysisError rather than scored."""
    with pytest.raises(AnalysisError):
        analyze(np.zeros(16000, dtype=np.float32))


def test_noisy_lead_in_and_tail_are_trimmed():
    """A second of noise before and after the voice is trimmed: the duration barely changes."""
    clean = voice()
    rng = np.random.default_rng(0)
    pad = np.zeros(16000, dtype=np.float32)
    padded = np.concatenate([pad, clean, pad])
    speech_rms = np.sqrt(np.mean(clean**2))
    noise = rng.standard_normal(len(padded)).astype(np.float32) * speech_rms / 10
    noisy = padded + noise  # 20 dB SNR
    assert abs(analyze(noisy).duration - analyze(clean).duration) < 0.15


def test_listener_model_is_monotonic_and_handles_gaps():
    """The listener model moves one way only as each feature grows, and copes with a gap."""
    from echo_voice import similarity

    base = np.array([0.3, 2.0, 0.5, 0.5, 0.5])  # see similarity.FEATURES
    for i, direction in enumerate([1, -1, -1, -1, -1]):
        for step in (0.1, 0.5, 2.0):
            hi = base.copy()
            hi[i] += step
            assert direction * (similarity.predict(hi) - similarity.predict(base)) >= 0
    no_pitch = base.copy()
    no_pitch[1] = np.nan  # too little voicing to measure pitch
    assert 1 <= similarity.predict(no_pitch) <= 6  # still a rating on the 1 to 6 scale


# --- WORD BY WORD ---

# The synthetic voice's four vowels, as a recogniser would time them: 0.4 s each, 0.06 s apart.
VOWEL_TIMES = [
    {"text": t, "start": round(0.46 * i, 2), "end": round(0.46 * i + 0.4, 2)}
    for i, t in enumerate(["ah", "ee", "oo", "eh"])
]


def test_each_word_says_where_the_take_is_off():
    """Each word says how far off it is: pitch for a higher take, resonance for a brighter one."""
    # 1. A higher take.
    higher = compare(analyze(voice()), analyze(voice(f0=150)), word_times=VOWEL_TIMES)["words"]
    assert [w["text"] for w in higher] == ["ah", "ee", "oo", "eh"]
    for w in higher:  # 150 Hz against 120 Hz is 3.9 semitones, the same on every word
        assert w["pitch"] == pytest.approx(3.86, abs=0.4)
        assert abs(w["melody"]) < 0.4 and w["worst"] == "pitch"
        assert w["take_start"] < w["take_end"]

    # 2. A brighter take at the same pitch.
    target, bright = analyze(voice()), analyze(voice(formant_scale=1.12))
    brighter = compare(target, bright, word_times=VOWEL_TIMES)["words"]
    assert all(w["resonance"] > 1.0 for w in brighter)  # 12% higher formants is about 2 semitones
    assert all(abs(w["pitch"]) < 0.3 for w in brighter)


# --- THE COACH ---


def parts(**scores):
    """A result with only these part scores, each part's hint saying "fix <part>"."""
    return {
        "components": [
            {"key": k, "label": k.title(), "score": s, "hint": f"fix {k}"}
            for k, s in scores.items()
        ]
    }


def test_the_coach_picks_what_costs_most_and_sticks_with_it_until_it_is_good():
    """The coach picks the part that costs most, and keeps to it until it is good."""
    from echo_voice.scoring import coach

    # 1. Pitch costs most, so it is the focus; there's no earlier take to show progress on.
    base = {
        "identity": 50,
        "pitch": 40,
        "melody": 90,
        "resonance": 90,
        "articulation": 90,
        "rhythm": 90,
    }
    first = parts(**base) | {"coach": coach(parts(**base))}
    assert first["coach"]["focus"] == "pitch" and first["coach"]["message"] == "fix pitch"
    assert first["coach"]["progress"] is None

    # 2. Better, and resonance is now worse, but not by enough to switch.
    second_scores = base | {"pitch": 70, "resonance": 65}
    second = parts(**second_scores) | {"coach": coach(parts(**second_scores), first)}
    assert second["coach"]["focus"] == "pitch" and second["coach"]["why"] == "keep"
    progress = second["coach"]["progress"]
    assert progress == {"key": "pitch", "label": "Pitch", "before": 40, "after": 70}

    # 3. Pitch is good now, so the next weakest takes over.
    third = coach(parts(**(second_scores | {"pitch": 85})), second)
    assert third["focus"] == "resonance" and third["why"] == "new"

    # 4. A part picked by hand is the focus; with every part good, there is none.
    assert coach(parts(**base), first, chosen="melody")["focus"] == "melody"
    done = coach(parts(**dict.fromkeys(base, 90)))
    assert done["focus"] is None and done["why"] == "all good"


def test_the_coach_names_the_words_furthest_off():
    """Given word times, the coach names words to work on, from the line's own words."""
    from echo_voice.scoring import coach

    result = compare(analyze(voice()), analyze(voice(f0=150)), word_times=VOWEL_TIMES)
    advice = coach(result)
    assert advice["focus"] == "pitch"
    assert advice["words"] and set(advice["words"]) <= {"ah", "ee", "oo", "eh"}


def test_the_coach_says_when_a_line_has_stopped_improving():
    """Takes that stay within a couple of points are a plateau; climbing or mastered ones aren't."""
    from echo_voice.scoring import coach

    def take(overall, history):
        """The coach's advice on a take scoring `overall`, after takes scoring `history`."""
        scores = parts(identity=50, pitch=40, melody=90, resonance=90, articulation=90, rhythm=90)
        return coach(scores | {"overall": overall}, history=history)

    assert take(30, [30, 31, 29])["plateau"]  # four takes within a couple of points
    assert take(40, [40, 41, 39, 40, 41, 40])["plateau"]
    assert not take(36, [30, 30, 31])["plateau"]  # still climbing
    assert not take(45, [20, 30, 40])["plateau"]
    assert not take(30, [30, 31])["plateau"]  # too few takes to tell
    done = take(88, [87, 88, 87])
    assert done["mastered"] and not done["plateau"]  # that's a finished line, not a stuck one


def test_the_coach_moves_on_from_a_part_that_isnt_moving():
    """A part whose score hasn't moved over the last takes gives way, unless it was picked."""
    from echo_voice.scoring import coach

    scores = parts(identity=50, pitch=60, melody=90, resonance=70, articulation=5, rhythm=90)
    now = scores | {"overall": 40}
    fresh = coach(now)
    assert fresh["focus"] == "articulation" and fresh["stuck"] == []  # it costs most, and it's new
    stuck = coach(now, part_history={"articulation": [6, 4]})
    assert stuck["focus"] != "articulation" and stuck["stuck"] == ["articulation"]
    # Unless it is picked by hand.
    picked = coach(now, chosen="articulation", part_history={"articulation": [6, 4]})
    assert picked["focus"] == "articulation"


# --- RAW MEASURES, ONE WORD AND AN UNRELIABLE PITCH ---


def test_a_take_keeps_its_raw_gaps():
    """A result keeps its raw measures: the pitch gap, both pitches, and the tempo."""
    result = compare(analyze(voice()), analyze(voice(f0=150)))
    assert result["measures"]["pitch"] == pytest.approx(3.86, abs=0.3)  # 150 Hz against 120 Hz
    assert result["measures"]["take_f0"] > result["measures"]["target_f0"]
    assert result["measures"]["tempo"] == pytest.approx(1.0, abs=0.05)


def test_one_word_against_the_same_word_says_which_way_to_move():
    """Training one word: it says which way, and how far, the word's resonance and pitch are."""
    from echo_voice.scoring import word_drill

    target = analyze(voice())
    first_vowel = int(0.45 * 16000)  # the first 0.4 s vowel, and most of the gap after it
    for scale, sign in ((1.1, 1), (0.9, -1)):
        attempt = analyze(voice(formant_scale=scale)[:first_vowel])
        r = word_drill(target, VOWEL_TIMES[0], attempt)
        # 10% brighter, or darker.
        assert 7 < sign * r["percent"] < 13 and r["scores"]["resonance"] < 30
        assert abs(r["pitch"]) < 0.3 and r["voiced"]
    same = word_drill(target, VOWEL_TIMES[0], analyze(voice()[:first_vowel]))
    assert abs(same["percent"]) < 2 and same["scores"]["resonance"] > 95
    higher = word_drill(target, VOWEL_TIMES[0], analyze(voice(f0=150)[:first_vowel]))
    assert higher["pitch"] == pytest.approx(3.86, abs=0.4) and higher["scores"]["pitch"] < 40


def test_an_attempt_at_a_word_with_too_little_sound_has_every_field():
    """A word span with too little sound to measure still gives the attempt's length
    against it, like any other attempt: the page reads it for every attempt."""
    from echo_voice.scoring import word_drill

    target = analyze(voice())
    attempt = analyze(voice()[: int(0.45 * 16000)])
    beyond = {"text": "gone", "start": 50.0, "end": 50.4}  # past the end of the target
    measured = word_drill(target, VOWEL_TIMES[0], attempt)
    empty = word_drill(target, beyond, attempt)
    assert empty.keys() == measured.keys() and not empty["voiced"]
    assert empty["duration"] == pytest.approx(attempt.spoken_duration / 0.4, abs=0.01)


def test_a_target_with_an_unreliable_pitch_is_scored_without_pitch_and_melody():
    """A target whose pitch can't be trusted is scored without pitch and melody, and says why."""
    target, take = analyze(voice()), analyze(voice(f0=240))  # an octave apart
    plain = compare(target, take)
    fair = compare(target, take, pitch_unreliable=True)
    by_key = {c["key"]: c for c in fair["components"]}
    assert by_key["pitch"]["score"] is None and by_key["melody"]["score"] is None
    assert "unreliable" in by_key["pitch"]["detail"]
    assert fair["overall"] > plain["overall"]  # an octave off no longer sinks it


# --- GENDER AND RESONANCE ---


def test_gender_read_follows_pitch_and_resonance():
    """The gender read follows pitch and resonance, on real voices and on the synthetic one."""
    from synth import ARCTIC

    from echo_voice import gender, load

    assert gender.score(analyze(load(ARCTIC / "slt_arctic_a0001.wav"))) > 90  # a woman
    assert gender.score(analyze(load(ARCTIC / "bdl_arctic_a0001.wav"))) < 25  # a man
    low, raised = analyze(voice()), analyze(voice(f0=220, formant_scale=1.18))
    assert gender.score(low) < 30 and gender.score(raised) > 70
    result = compare(low, raised)
    assert result["gender"]["take"] > result["gender"]["target"]
    assert gender.describe(85) == "female"
    assert gender.describe(50) == "in between"
    assert gender.describe(10) == "male"


def test_resonance_reads_both_recordings_at_one_ceiling_from_f2_and_f3():
    """Resonance reads both recordings at one shared formant ceiling, from F2 and F3."""
    from echo_voice.analysis import FORMANT_CEILINGS, shared_ceiling, speaker_ceiling, steadied
    from echo_voice.scoring import RESONANCE_FRAMES, measure, resonance_ceilings

    # 1. The middle of what the recordings of a speaker pick, on the grid; nothing with nothing
    # to go by.
    assert speaker_ceiling([4500, 5000, 6000]) == 5000.0
    assert speaker_ceiling([4500, 5000]) == 4750.0
    assert speaker_ceiling([None, 5500.0]) == 5500.0
    assert speaker_ceiling([]) is None
    # A pair is read at the middle of its two speakers' ceilings, on the grid.
    assert shared_ceiling(4750, 5250) == 5000.0 and shared_ceiling(5000, 5250) == 5000.0
    assert shared_ceiling(None, 5500) == 5500.0 and shared_ceiling(None, None) is None

    # 2. The pick of the recording and its own tracks stay; Resonance gets the tracks at the
    # given ceiling.
    target = analyze(voice())
    other = next(c for c in FORMANT_CEILINGS if c != target.formant_ceiling and c >= 6000)
    steady = steadied(target, other)
    assert steady.formant_ceiling == target.formant_ceiling and steady.steady_ceiling == other
    assert np.array_equal(steady.formants, target.formants, equal_nan=True)
    assert not np.array_equal(steady.resonance_formants, target.formants, equal_nan=True)
    assert steadied(target, None) is target and steadied(steady, other) is steady
    own = steadied(target, target.formant_ceiling)
    assert np.array_equal(own.resonance_formants, target.formants, equal_nan=True)

    # 3. A recording against itself is a perfect match whatever ceiling each side was last read
    # at: both are read at one, and at four more around it (the grid stops at 7000 Hz).
    assert measure(steady, steady).resonance == 0
    assert measure(target, steady).resonance == 0
    middle, around = resonance_ceilings(steady, steady)
    nearby = (other - 500, other - 250, other, other + 250, other + 500)
    assert middle == other and around == [c for c in nearby if c <= 7000]
    assert np.array_equal(target.at(other), steady.resonance_formants, equal_nan=True)

    # 4. A vocal tract 10% shorter reads 1.65 semitones brighter, from F2 and F3.
    brighter = measure(target, analyze(voice(formant_scale=1.1)))
    assert brighter.resonance == pytest.approx(1.65, abs=0.1)
    assert brighter.resonance_shift == pytest.approx(1.65, abs=0.1)

    # 5. Too little voiced sound in common, and it is not read at all.
    short = analyze(voice(vowel_seconds=0.1))
    assert short.voiced.sum() < RESONANCE_FRAMES and measure(short, short).resonance is None
