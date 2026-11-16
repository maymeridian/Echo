"""Long recordings split into lines (sources.py)."""

import json

import numpy as np
import pytest
import soundfile as sf
from synth import SR, compilation

from echo_voice import sources
from echo_voice.store import Store


def words(*spec):
    """(text, start, end) triples to Whisper-style words."""
    return [{"text": t, "start": a, "end": b, "p": 0.9} for t, a, b in spec]


def texts(lines):
    return [" ".join(w["text"] for w in line) for line in lines]


def test_lines_end_at_sentences_and_pauses():
    lines = sources.split_lines(
        words(
            ("Can", 0.0, 0.2), ("you", 0.2, 0.4), ("hear", 0.4, 0.6), ("me?", 0.6, 0.9),
            ("Well,", 1.1, 1.4), ("you", 1.5, 1.6), ("see,", 1.6, 1.9),  # a pause ends a line mid-sentence
            ("it", 2.8, 2.9), ("was", 2.9, 3.1), ("late.", 3.1, 3.5),
        )
    )
    assert texts(lines) == ["Can you hear me?", "Well, you see,", "it was late."]


def test_a_one_word_sentence_runs_on_into_the_next():
    lines = sources.split_lines(words(("Ready?", 0.0, 0.5), ("Here.", 0.6, 1.0), ("Now", 3.0, 3.3), ("go.", 3.3, 3.8)))
    assert texts(lines) == ["Ready? Here.", "Now go."]


def test_a_long_sentence_is_split_at_a_comma_near_the_middle():
    spec = [(f"w{i}", 0.5 * i, 0.5 * i + 0.4) for i in range(28)]  # 14 s with no full stop
    spec[13] = ("comma,", 6.5, 6.9)
    spec[3] = ("early,", 1.5, 1.9)  # too far from the middle to be chosen
    lines = sources.split_lines(words(*spec))
    assert len(lines) == 2 and lines[0][-1]["text"] == "comma,"
    assert all(line[-1]["end"] - line[0]["start"] <= sources.MAX_LINE for line in lines)


def test_mumbles_too_short_to_practise_are_dropped():
    lines = sources.split_lines(words(("Hm.", 0.0, 0.3), ("I", 2.0, 2.2), ("see", 2.2, 2.5), ("now.", 2.5, 3.0)))
    assert texts(lines) == ["I see now."]


def test_spans_pad_the_words_without_overlapping():
    lines = sources.split_lines(words(("One", 0.1, 0.5), ("two.", 0.5, 1.0), ("Three", 1.3, 1.7), ("four.", 1.7, 2.2)))
    (a1, b1), (a2, b2) = sources.spans(lines, duration=2.3)
    assert a1 == 0.0 and b1 == pytest.approx(1.15) and a2 == pytest.approx(1.15) and b2 == 2.3


def test_flags():
    row = {"voiced": 0.5, "same_voice": 0.8, "confidence": 0.9}
    assert sources.flags(row) == []
    assert sources.flags(row | {"voiced": 0.1}) == ["whispered"]
    assert sources.flags(row | {"same_voice": 0.1, "confidence": 0.3}) == ["other voice", "unclear"]
    assert sources.flags(row | {"same_voice": None}) == []  # too short to check


def test_any_file_is_decoded_to_16k_mono(tmp_path):
    t = np.arange(int(2.0 * 44100)) / 44100
    left, right = 0.5 * np.sin(2 * np.pi * 440 * t), np.zeros_like(t)
    path = tmp_path / "stereo.mp3"
    sf.write(path, np.stack([left, right], axis=1), 44100, format="MP3")
    seconds = sources.decode(path, tmp_path / "out.flac")
    y, sr = sf.read(tmp_path / "out.flac")
    assert sr == SR and y.ndim == 1 and seconds == pytest.approx(2.0, abs=0.1)
    assert 0.2 < np.max(np.abs(y[SR // 2 : SR])) < 0.3  # the two channels averaged


def test_unreadable_files_are_reported(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("not audio")
    with pytest.raises(sources.SourceError, match="Couldn't read"):
        sources.decode(path, tmp_path / "out.flac")


def test_the_job_finds_each_line_and_the_odd_voice_out(tmp_path):
    path = tmp_path / "lines.wav"
    sf.write(path, compilation(), SR, subtype="PCM_16")
    store = Store(tmp_path / "data")
    meta, folder = store.new_source("Compilation", file=str(path))
    assert sources.run(folder) == 0

    meta = store.source_meta(meta["id"])
    # Whisper on the CPU sometimes ends a sentence at "Tom" in bdl's a0002, so 5 to 7 lines.
    assert meta["status"] == "ready" and 5 <= meta["count"] <= 7
    rows = json.loads((folder / sources.LINES).read_text("utf-8"))
    # ARCTIC a0001 is "Author of the danger trail, Philip Steels, etc." Whisper
    # on the CPU isn't exactly repeatable: the name comes back spelled several ways.
    assert "danger trail" in rows[0]["text"].lower()
    # slt, a woman, among bdl's lines; "unclear" depends on Whisper's run.
    assert ["other voice" in r["flags"] for r in rows] == [False] * (len(rows) - 1) + [True]
    assert all(r["voiced"] > 0.4 and "whispered" not in r["flags"] for r in rows)
    assert path.is_file()  # a file on this computer is read, not moved
    assert not sources.job_running(folder)


def test_a_failed_job_says_why(tmp_path):
    path = tmp_path / "empty.wav"
    sf.write(path, np.zeros(SR // 2, dtype=np.float32), SR)
    store = Store(tmp_path / "data")
    meta, folder = store.new_source("Too short", file=str(path))
    assert sources.run(folder) == 1
    meta = store.source_meta(meta["id"])
    assert meta["status"] == "failed" and "under a second" in meta["error"]


def row(text, start, end, voiced=0.5, confidence=0.9, same_voice=0.8, flags=(), pitch=200):
    return {"text": text, "start": start, "end": end, "voiced": voiced, "confidence": confidence,
            "same_voice": same_voice, "flags": list(flags), "pitch": pitch}


def test_suggested_lines_are_clean_mid_length_and_clearly_voiced():
    rows = [
        row("Hm, yes.", 0, 1.2),  # too few words
        row("Can you hear me now, my dear?", 2, 5.5),  # ideal
        row("This one rambles on and on for a very long while indeed.", 6, 15),  # too long
        row("Whispered words are hard to score.", 16, 19, voiced=0.1, flags=["whispered"]),
        row("A breathier line of decent length.", 20, 23.5, voiced=0.3),
        row("Another clear line to start with.", 24, 27, voiced=0.55),
    ]
    picks = sources.suggest(rows)
    assert set(picks) == {1, 4, 5} and picks[-1] == 4  # the breathy one ranks last


def test_the_voice_at_a_glance():
    rows = [row(f"line number {i} here", 4 * i, 4 * i + 3, pitch=180 + 5 * i) for i in range(10)]
    rows.append(row("someone else entirely", 50, 53, pitch=100, flags=["other voice"]))
    s = sources.summary(rows)
    assert s["pitch"] == 202 and s["low"] < s["pitch"] < s["high"]  # the other voice doesn't count
    assert s["rate"] == pytest.approx(4 / (3 - sources.LEAD - sources.TAIL), abs=0.1)
    assert s["voiced"] == 0.5


def test_a_line_saved_before_lines_kept_their_recording_is_linked_to_it(tmp_path):
    store = Store(tmp_path / "data")
    meta, folder = store.new_source("Lighthouse", status="ready")
    rows = [
        row("Can you hear me?", 13.0, 14.8),
        row("the hills ran up clear above the vegetation, in spires of naked rock", 23.46, 32.0),
    ]
    (folder / sources.LINES).write_text(json.dumps(rows), "utf-8")
    line_dir = store.root / "abc123"
    line_dir.mkdir()
    # How the page named a line then: the recording, and the text cut at 50 characters.
    old = {"id": "abc123", "name": "Lighthouse: the hills ran up clear above the vegetation, in…", "created": "2026-11-15T21:42:17-05:00"}
    (line_dir / "meta.json").write_text(json.dumps(old), "utf-8")
    store.migrate()
    linked = store.target_meta("abc123")
    assert (linked["source"], linked["line_start"]) == (meta["id"], 23.46)
    assert linked["voice_id"] == store.source_meta(meta["id"])["voice_id"]


def test_suggestions_keep_to_the_voices_usual_pitch():
    rows = [row(f"a clear line number {i}", 4 * i, 4 * i + 3.5, pitch=190 + i) for i in range(8)]
    rows.append(row("the same words in creaky voice", 40, 43.5, pitch=98))  # an octave down
    rows.append(row("a squeal an octave up somewhere", 44, 47.5, pitch=400))
    picked = [rows[i]["text"] for _, i in sources.rank(rows)]
    assert len(picked) == 8 and not any("creaky" in t or "squeal" in t for t in picked)


TWO_SPEAKERS = ("bdl_arctic_a0001", "bdl_arctic_a0002") * 2 + ("slt_arctic_a0001",) * 3


def test_each_speaker_in_a_recording_is_found():
    y = compilation(clips=TWO_SPEAKERS)
    rows = sources.find_lines(y)
    found = sources.speaker_summary(rows)
    assert [s["lines"] for s in found][:1] == [len(rows) - 3] and len(found) == 2  # bdl talks most
    assert [r["speaker"] for r in rows[-3:]] == [1, 1, 1]  # slt's three
    assert found[1]["pitch"] > found[0]["pitch"] * 1.3  # a woman after a man
    # Picking for slt: her lines, none of his.
    assert {rows[i]["speaker"] for i in sources.suggest(rows, speaker=1)} == {1}
    assert all(sources.line_flags(r, 1) == (["other voice"] if r["speaker"] == 0 else []) for r in rows if r["voiced"] > 0.2)


def test_a_recording_from_before_speakers_gets_them_without_moving_its_lines(tmp_path):
    path = tmp_path / "lines.wav"
    sf.write(path, compilation(), SR, subtype="PCM_16")
    store = Store(tmp_path / "data")
    meta, folder = store.new_source("Old", file=str(path))
    sources.run(folder)
    rows = json.loads((folder / sources.LINES).read_text("utf-8"))
    for r in rows:
        del r["speaker"]
    (folder / sources.LINES).write_text(json.dumps(rows), "utf-8")
    store.update_source(meta["id"], status="queued")
    assert sources.run(folder) == 0
    again = json.loads((folder / sources.LINES).read_text("utf-8"))
    assert [r["start"] for r in again] == [r["start"] for r in rows]  # not transcribed again
    assert all("speaker" in r for r in again) and store.source_meta(meta["id"])["speakers"] == 1


def test_titles_become_voice_names():
    assert sources.clean_title("The lighthouse keeper's story (extended cut) [4K][HD][subtitles]") == "The lighthouse keeper's story"
    assert sources.clean_title("Ellie - All Voice Lines | The Last of Us Part II") == "Ellie"
    assert sources.clean_title("🔥 Voice lines #shorts") == "Voice lines"


def test_a_links_voice_is_named_after_its_channel_and_a_second_video_joins_it(tmp_path):
    store = Store(tmp_path / "data")

    def pasted(channel, title):
        voice = store.update_voice(store.new_voice("New voice")["id"], name_pending=True)
        meta, folder = store.new_source(sources.clean_title(title), link="https://example.com/v", status="downloading")
        meta = store.update_source(meta["id"], voice_id=voice["id"], channel=channel, title=title)
        sources._name_voice(folder, meta)
        return voice, meta

    first, meta = pasted("Harbour Tales", "The lighthouse keeper's story (extended cut) [4K]")
    named = store.voice_meta(first["id"])
    assert named["name"] == "Harbour Tales" and named["named_from"] == "channel" and meta["voice_id"] == first["id"]

    second, meta = pasted("Harbour Tales", "Another story [4K]")
    assert meta["voice_id"] == first["id"] and meta["joined"]  # the channel's voice, not a second "Harbour Tales"
    assert store.voice_meta(second["id"])["merged_into"] == first["id"]
    assert [v["name"] for v in store.list_voices()] == ["Harbour Tales"]

    third, meta = pasted(None, "Ellie - All Voice Lines | The Last of Us Part II")  # no channel known
    assert store.voice_meta(third["id"])["name"] == "Ellie" and store.voice_meta(third["id"])["named_from"] == "title"
