import numpy as np
import pytest
from synth import voice

from echo_voice import AnalysisError, analyze, compare


def score(take: np.ndarray, target: np.ndarray | None = None) -> dict:
    result = compare(analyze(voice() if target is None else target), analyze(take))
    result["by_key"] = {c["key"]: c for c in result["components"]}
    return result


def test_identical_recordings_match_fully():
    result = score(voice())
    assert result["overall"] > 99
    assert result["hints"] == []


def test_higher_pitch_costs_pitch_score_and_says_so():
    result = score(voice(f0=120 * 2 ** (4 / 12)))
    pitch = result["by_key"]["pitch"]
    assert pitch["score"] < 40
    assert "4.0 semitones higher" in pitch["detail"]
    assert result["by_key"]["melody"]["score"] > 90  # same shape, just transposed


def test_flat_delivery_costs_melody_not_pitch():
    result = score(voice(glide_semitones=0.0), target=voice(glide_semitones=10.0))
    assert result["by_key"]["melody"]["score"] < 70
    assert result["by_key"]["pitch"]["score"] > 95
    assert any("Exaggerate" in hint for hint in result["hints"])


def test_shorter_vocal_tract_reads_as_brighter():
    result = score(voice(formant_scale=1.15))
    resonance = result["by_key"]["resonance"]
    assert resonance["score"] < 60
    assert "brighter" in resonance["detail"]
    assert result["by_key"]["pitch"]["score"] > 95


def test_slower_take_costs_rhythm():
    result = score(voice(vowel_seconds=0.6))
    rhythm = result["by_key"]["rhythm"]
    assert rhythm["score"] < 90
    assert "slower" in rhythm["detail"]


def test_contour_is_on_the_targets_timeline():
    contour = score(voice(vowel_seconds=0.6))["contour"]
    assert len(contour["t"]) == len(contour["target"]) == len(contour["take"])
    assert contour["t"][-1] == pytest.approx(1.78, abs=0.05)  # the target's length


def test_silence_is_rejected():
    with pytest.raises(AnalysisError):
        analyze(np.zeros(16000, dtype=np.float32))


def test_noisy_lead_in_and_tail_are_trimmed():
    clean = voice()
    rng = np.random.default_rng(0)
    pad = np.zeros(16000, dtype=np.float32)
    padded = np.concatenate([pad, clean, pad])
    speech_rms = np.sqrt(np.mean(clean**2))
    noisy = padded + rng.standard_normal(len(padded)).astype(np.float32) * speech_rms / 10  # 20 dB SNR
    assert abs(analyze(noisy).duration - analyze(clean).duration) < 0.15


def test_listener_model_is_monotonic_and_handles_gaps():
    from echo_voice import similarity

    base = np.array([0.3, 2.0, 0.5, 0.5, 0.5])  # see similarity.FEATURES
    for i, direction in enumerate([1, -1, -1, -1, -1]):
        for step in (0.1, 0.5, 2.0):
            hi = base.copy()
            hi[i] += step
            assert direction * (similarity.predict(hi) - similarity.predict(base)) >= 0
    no_pitch = base.copy()
    no_pitch[1] = np.nan  # too little voicing to measure pitch
    assert 1 <= similarity.predict(no_pitch) <= 6


# The synthetic voice's four vowels, as a recogniser would time them.
VOWEL_TIMES = [
    {"text": t, "start": round(0.46 * i, 2), "end": round(0.46 * i + 0.4, 2)}
    for i, t in enumerate(["ah", "ee", "oo", "eh"])
]


def test_each_word_says_where_the_take_is_off():
    higher = compare(analyze(voice()), analyze(voice(f0=150)), word_times=VOWEL_TIMES)["words"]
    assert [w["text"] for w in higher] == ["ah", "ee", "oo", "eh"]
    for w in higher:  # 150 Hz against 120 Hz is 3.9 semitones, the same on every word
        assert w["pitch"] == pytest.approx(3.86, abs=0.4)
        assert abs(w["melody"]) < 0.4 and w["worst"] == "pitch"
        assert w["take_start"] < w["take_end"]

    brighter = compare(analyze(voice()), analyze(voice(formant_scale=1.12)), word_times=VOWEL_TIMES)["words"]
    assert all(w["resonance"] > 1.0 for w in brighter)  # 12% higher formants is about 2 semitones
    assert all(abs(w["pitch"]) < 0.3 for w in brighter)


def parts(**scores):
    return {"components": [{"key": k, "label": k.title(), "score": s, "hint": f"fix {k}"} for k, s in scores.items()]}


def test_the_coach_picks_what_costs_most_and_sticks_with_it_until_it_is_good():
    from echo_voice.scoring import coach

    base = dict(identity=50, pitch=40, melody=90, resonance=90, articulation=90, rhythm=90)
    first = parts(**base) | {"coach": coach(parts(**base))}
    assert first["coach"]["focus"] == "pitch" and first["coach"]["message"] == "fix pitch"
    assert first["coach"]["progress"] is None

    # Better, and resonance is now worse, but not by enough to switch.
    second_scores = base | {"pitch": 70, "resonance": 60}
    second = parts(**second_scores) | {"coach": coach(parts(**second_scores), first)}
    assert second["coach"]["focus"] == "pitch" and second["coach"]["why"] == "keep"
    assert second["coach"]["progress"] == {"key": "pitch", "label": "Pitch", "before": 40, "after": 70}

    # Pitch is good now, so the next weakest takes over.
    third = coach(parts(**(second_scores | {"pitch": 85})), second)
    assert third["focus"] == "resonance" and third["why"] == "new"

    assert coach(parts(**base), first, chosen="melody")["focus"] == "melody"
    done = coach(parts(**dict.fromkeys(base, 90)))
    assert done["focus"] is None and done["why"] == "all good"


def test_the_coach_names_the_words_furthest_off():
    from echo_voice.scoring import coach

    result = compare(analyze(voice()), analyze(voice(f0=150)), word_times=VOWEL_TIMES)
    advice = coach(result)
    assert advice["focus"] == "pitch"
    assert advice["words"] and set(advice["words"]) <= {"ah", "ee", "oo", "eh"}


def test_the_coach_says_when_a_line_has_stopped_improving():
    from echo_voice.scoring import coach

    def take(overall, history):
        return coach(parts(identity=50, pitch=40, melody=90, resonance=90, articulation=90, rhythm=90) | {"overall": overall}, history=history)

    assert take(30, [30, 31, 29])["plateau"]  # four takes within a couple of points
    assert take(40, [40, 41, 39, 40, 41, 40])["plateau"]
    assert not take(36, [30, 30, 31])["plateau"]  # still climbing
    assert not take(45, [20, 30, 40])["plateau"]
    assert not take(30, [30, 31])["plateau"]  # too few takes to tell
    done = take(88, [87, 88, 87])
    assert done["mastered"] and not done["plateau"]  # that's a finished line, not a stuck one


def test_a_take_keeps_its_raw_gaps():
    result = compare(analyze(voice()), analyze(voice(f0=150)))
    assert result["measures"]["pitch"] == pytest.approx(3.86, abs=0.3)
    assert result["measures"]["take_f0"] > result["measures"]["target_f0"]
    assert result["measures"]["tempo"] == pytest.approx(1.0, abs=0.05)


def test_one_word_against_the_same_word_says_which_way_to_move():
    from echo_voice.scoring import word_drill

    target = analyze(voice())
    first_vowel = int(0.45 * 16000)
    for scale, sign in ((1.1, 1), (0.9, -1)):
        attempt = analyze(voice(formant_scale=scale)[:first_vowel])
        r = word_drill(target, VOWEL_TIMES[0], attempt)
        assert 7 < sign * r["percent"] < 13 and r["scores"]["resonance"] < 30  # 10% brighter, or darker
        assert abs(r["pitch"]) < 0.3 and r["voiced"]
    same = word_drill(target, VOWEL_TIMES[0], analyze(voice()[:first_vowel]))
    assert abs(same["percent"]) < 2 and same["scores"]["resonance"] > 95
    higher = word_drill(target, VOWEL_TIMES[0], analyze(voice(f0=150)[:first_vowel]))
    assert higher["pitch"] == pytest.approx(3.86, abs=0.4) and higher["scores"]["pitch"] < 40


def test_a_target_with_an_unreliable_pitch_is_scored_without_pitch_and_melody():
    target, take = analyze(voice()), analyze(voice(f0=240))  # an octave apart
    plain = compare(target, take)
    fair = compare(target, take, pitch_unreliable=True)
    by_key = {c["key"]: c for c in fair["components"]}
    assert by_key["pitch"]["score"] is None and by_key["melody"]["score"] is None
    assert "unreliable" in by_key["pitch"]["detail"]
    assert fair["overall"] > plain["overall"]  # an octave off no longer sinks it


def test_the_coach_moves_on_from_a_part_that_isnt_moving():
    from echo_voice.scoring import coach

    now = parts(identity=50, pitch=60, melody=90, resonance=70, articulation=5, rhythm=90) | {"overall": 40}
    fresh = coach(now)
    assert fresh["focus"] == "articulation" and fresh["stuck"] == []  # it costs most, and it's new
    stuck = coach(now, part_history={"articulation": [6, 4]})
    assert stuck["focus"] != "articulation" and stuck["stuck"] == ["articulation"]
    assert coach(now, chosen="articulation", part_history={"articulation": [6, 4]})["focus"] == "articulation"  # unless picked


def test_gender_read_follows_pitch_and_resonance():
    from synth import ARCTIC

    from echo_voice import gender, load

    assert gender.score(analyze(load(ARCTIC / "slt_arctic_a0001.wav"))) > 90  # a woman
    assert gender.score(analyze(load(ARCTIC / "bdl_arctic_a0001.wav"))) < 25  # a man
    low, raised = analyze(voice()), analyze(voice(f0=220, formant_scale=1.18))
    assert gender.score(low) < 30 < 70 < gender.score(raised)
    result = compare(low, raised)
    assert result["gender"]["take"] > result["gender"]["target"]
    assert gender.describe(85) == "female" and gender.describe(50) == "in between" and gender.describe(10) == "male"


def test_resonance_reads_formants_at_one_ceiling_per_speaker():
    import numpy as np

    from echo_voice.analysis import FORMANT_CEILINGS, speaker_ceiling, steadied
    from echo_voice.scoring import measure

    # The middle of what the recordings of a speaker pick, on the grid; nothing with nothing to go by.
    assert speaker_ceiling([4500, 5000, 6000]) == 5000.0
    assert speaker_ceiling([4500, 5000]) == 4750.0
    assert speaker_ceiling([None, 5500.0]) == 5500.0
    assert speaker_ceiling([]) is None

    target = analyze(voice())
    other = next(c for c in FORMANT_CEILINGS if c != target.formant_ceiling and c >= 6000)
    steady = steadied(target, other)
    # The pick of the recording and its own tracks stay; Resonance gets the tracks at the given ceiling.
    assert steady.formant_ceiling == target.formant_ceiling and steady.steady_ceiling == other
    assert np.array_equal(steady.formants, target.formants, equal_nan=True)
    assert not np.array_equal(steady.resonance_formants, target.formants, equal_nan=True)
    assert steadied(target, None) is target and steadied(steady, other) is steady
    assert np.array_equal(steadied(target, target.formant_ceiling).resonance_formants, target.formants, equal_nan=True)

    # A recording against itself at one ceiling is still a perfect match, whatever that ceiling is.
    assert measure(steady, steady).resonance == 0
    # Measured at two different ceilings, the same voice no longer reads as identical.
    assert measure(target, steady).resonance > 0
