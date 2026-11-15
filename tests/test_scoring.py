import numpy as np
import pytest
from synth import voice

from voicematch import AnalysisError, analyze, compare


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
    from voicematch import similarity

    base = np.array([0.3, 2.0, 0.5, 0.5, 0.5])  # see similarity.FEATURES
    for i, direction in enumerate([1, -1, -1, -1, -1]):
        for step in (0.1, 0.5, 2.0):
            hi = base.copy()
            hi[i] += step
            assert direction * (similarity.predict(hi) - similarity.predict(base)) >= 0
    no_pitch = base.copy()
    no_pitch[1] = np.nan  # too little voicing to measure pitch
    assert 1 <= similarity.predict(no_pitch) <= 6
