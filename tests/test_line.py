from pathlib import Path

from echo_voice import analyze, compare, load
from echo_voice.line import compare_words, transcribe

LINE = "Author of the Danger Trail, Philip Steels, etc.".split()


def check(said: str):
    return compare_words(LINE, said.split())


def test_spelling_of_a_heard_name_still_counts():
    assert check("Author of the Danger Trail, Philip Steele's, etc.").match_rate == 1.0


def test_skipped_swapped_and_added_words_are_named():
    assert check("Author of the Danger Trail.").runs("miss") == ["Philip Steels, etc."]
    assert check("Author of the danger road, Philip Steels, etc.").swaps() == [("Trail,", "road,")]
    assert check("Author of the Danger Trail, Philip Steels, etc. Author").runs("extra") == ["Author"]


def test_non_words_from_the_recogniser_are_ignored():
    assert check("Author of the Danger Trail, Philip Steels, etc. ████").match_rate == 1.0


def test_a_take_cut_short_is_told_which_words_it_left_out():
    path = Path(__file__).parent / "data" / "arctic" / "bdl_arctic_a0001.wav"
    target = analyze(load(path))
    y = load(path)
    take = analyze(y[: int(len(y) * 0.6)])
    result = compare(target, take, transcribe(target.y), transcribe(take.y))
    assert result["line"]["match"] < 1
    assert result["hints"][0].startswith("You left out")



def test_word_edges_move_onto_the_audio():
    import numpy as np

    from echo_voice.line import refine_word_times

    sr = 16000
    t = np.arange(int(2.0 * sr)) / sr
    y = 0.001 * np.random.default_rng(0).standard_normal(len(t))
    for a, b in ((0.2, 0.7), (0.9, 1.6)):  # two "words", the second ending at 1.6 s
        on = (t >= a) & (t < b)
        y[on] += 0.5 * np.sin(2 * np.pi * 200 * t[on])
    # As a recogniser might time them: each ending a fifth of a second early.
    words = [{"text": "one", "start": 0.22, "end": 0.5}, {"text": "two", "start": 0.92, "end": 1.4}]
    out = refine_word_times(y, words)
    assert 0.68 <= out[0]["end"] <= 0.8 and 0.82 <= out[1]["start"] <= 0.92  # the pause between them
    assert 1.58 <= out[1]["end"] <= 1.7  # to where the sound stops
    assert out[0]["start"] <= 0.22 and all(w["refined"] for w in out)
    assert refine_word_times(y, out) == out  # moved once


def test_words_said_in_one_breath_become_one_unit():
    import numpy as np

    from echo_voice.line import speech_units

    sr = 16000
    t_ = np.arange(int(2.4 * sr)) / sr
    y = 0.001 * np.random.default_rng(0).standard_normal(len(t_))
    for a, b in ((0.1, 0.5), (0.8, 1.6), (1.9, 2.3)):  # three bursts of sound, with silence between
        on = (t_ >= a) & (t_ < b)
        y[on] += 0.5 * np.sin(2 * np.pi * 200 * t_[on])
    words = [
        {"text": "I'll", "start": 0.1, "end": 0.5},
        {"text": "let", "start": 0.8, "end": 1.0},  # these four share the second burst
        {"text": "you", "start": 1.0, "end": 1.05},
        {"text": "in", "start": 1.05, "end": 1.4},
        {"text": "on", "start": 1.4, "end": 1.6},
        {"text": "this.", "start": 1.9, "end": 2.3},
    ]
    units = speech_units(y, words)
    assert [u["text"] for u in units] == ["I'll", "let you in on", "this."]
    assert (units[1]["start"], units[1]["end"], units[1]["count"]) == (0.8, 1.6, 4)
    assert speech_units(y, []) == []
