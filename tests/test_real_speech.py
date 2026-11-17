"""Checks on real speech: four CMU ARCTIC clips (see data/arctic/COPYING).

bdl and rms are two American men and slt an American woman, all reading
sentence a0001; bdl also reads a0002. None of them were used to calibrate.
"""

from pathlib import Path

import librosa
import numpy as np
import pytest
from calibrate import channel

from echo_voice import analyze, compare, identity, load

DATA = Path(__file__).parent / "data" / "arctic"


@pytest.fixture(scope="module")
def clip():
    cache = {}

    def get(name: str):
        if name not in cache:
            cache[name] = analyze(load(DATA / f"{name}.wav"))
        return cache[name]

    return get


def test_speaker_check_accepts_the_same_man_and_nobody_else(clip):
    target = clip("bdl_arctic_a0001")
    assert compare(target, clip("bdl_arctic_a0002"))["accepted"]  # other sentence, same man
    assert not compare(target, clip("rms_arctic_a0001"))["accepted"]
    assert not compare(target, clip("slt_arctic_a0001"))["accepted"]


def test_same_voice_through_a_mic_scores_high_and_strangers_low(clip):
    target = clip("bdl_arctic_a0001")
    through_mic = analyze(channel(load(DATA / "bdl_arctic_a0001.wav"), seed=5))
    assert compare(target, through_mic)["overall"] > 85
    assert compare(target, clip("rms_arctic_a0001"))["overall"] < 45
    assert compare(target, clip("slt_arctic_a0001"))["overall"] < 45


def test_different_words_are_flagged(clip):
    result = compare(clip("bdl_arctic_a0001"), clip("bdl_arctic_a0002"))
    scores = {c["key"]: c["score"] for c in result["components"]}
    assert scores["articulation"] < 35  # another sentence: the sounds are not the line's
    assert any("same words" in hint for hint in result["hints"])


def test_articulation_hears_the_sounds_and_not_the_voice(clip):
    """A stranger saying the line scores well above the same man saying another sentence."""
    target = clip("bdl_arctic_a0001")
    part = lambda take: next(c["score"] for c in compare(target, take)["components"] if c["key"] == "articulation")
    same_line = [part(clip("rms_arctic_a0001")), part(clip("slt_arctic_a0001"))]  # another man, and a woman
    assert min(same_line) > 60 and part(clip("bdl_arctic_a0002")) < 35


def test_noise_gated_clip_keeps_its_voice(clip):
    # Clips cut from videos often have their pauses gated to exact silence,
    # which without dither in the features knocks the embedding off.
    y = clip("bdl_arctic_a0001").y
    rms = librosa.feature.rms(y=y, frame_length=400, hop_length=160)[0]
    keep = np.repeat(20 * np.log10(rms / rms.max() + 1e-12) > -30, 160)
    keep = np.pad(keep, (0, max(0, len(y) - len(keep))))[: len(y)]
    gated = np.where(keep, y, 0).astype(np.float32)
    assert np.mean(gated == 0) > 0.1
    assert identity.similarity(identity.embed(y), identity.embed(gated)) > 0.95



def test_a_take_that_stops_early_asks_about_the_whole_line(clip):
    y = load(DATA / "bdl_arctic_a0001.wav")
    result = compare(clip("bdl_arctic_a0001"), analyze(y[: int(len(y) * 0.6)]))
    assert any("Did you say the whole line?" in hint for hint in result["hints"])


def test_cleaning_recovers_a_target_with_music_under_it(clip):
    from validate import beat

    from echo_voice import clean
    from echo_voice.analysis import _prepare

    speech = _prepare(load(DATA / "bdl_arctic_a0001.wav"))
    music = beat(len(speech), seed=1)
    mix = speech + music * np.sqrt(np.mean(speech**2) / np.mean(music**2))  # music as loud as the voice
    reference = analyze(speech)
    # With music under it the pitch tracker follows the music: Pitch and Melody are lost, and
    # 20 points of the headline with them. (The parts that go by what a recogniser hears, and
    # Resonance, hold up.)
    dirty = compare(reference, analyze(mix))
    parts = {c["key"]: c["score"] for c in dirty["components"]}
    assert dirty["overall"] < 85 and parts["pitch"] < 20 and parts["melody"] < 20
    assert compare(reference, analyze(clean.vocals(mix)))["overall"] > 95


def test_formant_ceiling_follows_the_vocal_tract(clip):
    # Shorter vocal tracts need a higher ceiling (tools/validate.py formant-lines).
    men = [clip("bdl_arctic_a0001").formant_ceiling, clip("rms_arctic_a0001").formant_ceiling]
    assert clip("slt_arctic_a0001").formant_ceiling > max(men)


def test_listener_rating_puts_the_same_man_well_above_strangers(clip):
    target = clip("bdl_arctic_a0001")
    assert compare(target, clip("bdl_arctic_a0002"))["listener"] > 4.5  # "probably the same"
    assert compare(target, clip("rms_arctic_a0001"))["listener"] < 2.5
    assert compare(target, clip("slt_arctic_a0001"))["listener"] < 2.5
