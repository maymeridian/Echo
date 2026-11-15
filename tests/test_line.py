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
