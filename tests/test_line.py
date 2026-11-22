'''
tests/test_line.py
Author: maymeridian
Description: Tests for the word check (line.py): what was said against the line, and word timing.
             - The line is CMU ARCTIC a0001, "Author of the Danger Trail, Philip Steels, etc."
             - Word timing is checked on tones in faint noise, standing in for words.
'''

from pathlib import Path

from echo_voice import analyze, compare, load
from echo_voice.line import compare_words, transcribe

# The words of CMU ARCTIC a0001, as the target line's transcript has them.
LINE = ["Author", "of", "the", "Danger", "Trail,", "Philip", "Steels,", "etc."]


def check(said: str):
    """The word check of `said` against LINE."""
    return compare_words(LINE, said.split())


def test_spelling_of_a_heard_name_still_counts():
    """A name the recogniser spells another way (Steele's for Steels) still counts as said."""
    assert check("Author of the Danger Trail, Philip Steele's, etc.").match_rate == 1.0


def test_skipped_swapped_and_added_words_are_named():
    """Words left out, swapped or added are named, neighbouring ones together as one run."""
    assert check("Author of the Danger Trail.").runs("miss") == ["Philip Steels, etc."]
    assert check("Author of the danger road, Philip Steels, etc.").swaps() == [("Trail,", "road,")]
    added = check("Author of the Danger Trail, Philip Steels, etc. Author")
    assert added.runs("extra") == ["Author"]


def test_non_words_from_the_recogniser_are_ignored():
    """A token with no letters or digits, as a recogniser sometimes puts out, isn't a word."""
    assert check("Author of the Danger Trail, Philip Steels, etc. ████").match_rate == 1.0


def test_a_take_cut_short_is_told_which_words_it_left_out():
    """A take of the line's first 60% gets, as its first hint, the words it left out."""
    path = Path(__file__).parent / "data" / "arctic" / "bdl_arctic_a0001.wav"
    target = analyze(load(path))
    y = load(path)
    take = analyze(y[: int(len(y) * 0.6)])
    result = compare(target, take, transcribe(target.y), transcribe(take.y))
    assert result["line"]["match"] < 1
    assert result["hints"][0].startswith("You left out")


def test_word_edges_move_onto_the_audio():
    """A recogniser's word times are moved to where each word's sound starts and stops, once."""
    import numpy as np

    from echo_voice.line import refine_word_times

    # 1. Two "words" of a 200 Hz tone in faint noise, the second ending at 1.6 s.
    sr = 16000
    t = np.arange(int(2.0 * sr)) / sr
    y = 0.001 * np.random.default_rng(0).standard_normal(len(t))
    for a, b in ((0.2, 0.7), (0.9, 1.6)):
        on = (t >= a) & (t < b)
        y[on] += 0.5 * np.sin(2 * np.pi * 200 * t[on])

    # 2. As a recogniser might time them: each ending a fifth of a second early.
    words = [{"text": "one", "start": 0.22, "end": 0.5}, {"text": "two", "start": 0.92, "end": 1.4}]
    out = refine_word_times(y, words)

    # 3. The ends meet in the pause between them (0.7 to 0.9 s), and the last runs to where the
    # sound stops. Refined words aren't moved again.
    assert 0.68 <= out[0]["end"] <= 0.8 and 0.82 <= out[1]["start"] <= 0.92
    assert 1.58 <= out[1]["end"] <= 1.7
    assert out[0]["start"] <= 0.22 and all(w["refined"] for w in out)
    assert refine_word_times(y, out) == out


def test_words_said_in_one_breath_become_one_unit():
    """Words with no pause between them are joined into one unit, with its own start and end."""
    import numpy as np

    from echo_voice.line import speech_units

    sr = 16000
    t = np.arange(int(2.4 * sr)) / sr
    y = 0.001 * np.random.default_rng(0).standard_normal(len(t))
    for a, b in ((0.1, 0.5), (0.8, 1.6), (1.9, 2.3)):  # three bursts of sound, with silence between
        on = (t >= a) & (t < b)
        y[on] += 0.5 * np.sin(2 * np.pi * 200 * t[on])
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


def test_a_piece_that_starts_with_punctuation_joins_the_word_before():
    """A recogniser's piece such as ",000" or "!" is glued onto the word before it."""
    from echo_voice.line import glue

    words = [
        {"text": "12", "start": 0.0, "end": 0.3, "p": 0.9},
        {"text": ",000", "start": 0.3, "end": 0.8, "p": 0.6},
        {"text": "people", "start": 0.9, "end": 1.4, "p": 0.95},
        {"text": "!", "start": 1.4, "end": 1.45, "p": 0.5},
    ]
    glued = glue(words)
    assert [w["text"] for w in glued] == ["12,000", "people!"]
    # The joined word spans both pieces and keeps the lower of their two confidences.
    assert (glued[0]["start"], glued[0]["end"], glued[0]["p"]) == (0.0, 0.8, 0.6)
    # A piece with nothing before it stays as it is.
    lone = [{"text": ",", "start": 0.0, "end": 0.1}]
    assert glue(lone) == [{"text": ",", "start": 0.0, "end": 0.1}]
    assert words[0]["text"] == "12"  # the list handed in is left as it was
