'''
tests/test_sources.py
Author: maymeridian
Description: Tests for long recordings split into lines (sources.py), and the voices they make.
             - Splitting a transcript's words into lines, and padding each line's span.
             - The background job on real speech, its speakers, and the lines it suggests.
             - Naming a voice from a link, and cutting a line that starts mid-sentence.
'''

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
    """Each line's words joined back into its text."""
    return [" ".join(w["text"] for w in line) for line in lines]


# --- SPLITTING WORDS INTO LINES ---


def test_lines_end_at_sentences_and_pauses():
    """A line ends where a sentence does, or at a long pause within one."""
    lines = sources.split_lines(
        words(
            ("Can", 0.0, 0.2),
            ("you", 0.2, 0.4),
            ("hear", 0.4, 0.6),
            ("me?", 0.6, 0.9),
            ("Well,", 1.1, 1.4),
            ("you", 1.5, 1.6),
            ("see,", 1.6, 1.9),  # a pause ends a line mid-sentence
            ("it", 2.8, 2.9),
            ("was", 2.9, 3.1),
            ("late.", 3.1, 3.5),
        )
    )
    assert texts(lines) == ["Can you hear me?", "Well, you see,", "it was late."]


def test_a_one_word_sentence_runs_on_into_the_next():
    """A sentence of one word joins the one after it rather than standing alone."""
    lines = sources.split_lines(
        words(("Ready?", 0.0, 0.5), ("Here.", 0.6, 1.0), ("Now", 3.0, 3.3), ("go.", 3.3, 3.8))
    )
    assert texts(lines) == ["Ready? Here.", "Now go."]


def test_a_long_sentence_is_split_at_a_comma_near_the_middle():
    """A sentence too long for one line is split at a comma near its middle, not one far off."""
    spec = [(f"w{i}", 0.5 * i, 0.5 * i + 0.4) for i in range(28)]  # 14 s with no full stop
    spec[13] = ("comma,", 6.5, 6.9)
    spec[3] = ("early,", 1.5, 1.9)  # too far from the middle to be chosen
    lines = sources.split_lines(words(*spec))
    assert len(lines) == 2 and lines[0][-1]["text"] == "comma,"
    assert all(line[-1]["end"] - line[0]["start"] <= sources.MAX_LINE for line in lines)


def test_mumbles_too_short_to_practise_are_dropped():
    """A line too short to practise, such as "Hm.", is left out."""
    lines = sources.split_lines(
        words(("Hm.", 0.0, 0.3), ("I", 2.0, 2.2), ("see", 2.2, 2.5), ("now.", 2.5, 3.0))
    )
    assert texts(lines) == ["I see now."]


def test_spans_pad_the_words_without_overlapping():
    """Each line's span is padded around its words; where pads would overlap, they meet halfway."""
    lines = sources.split_lines(
        words(("One", 0.1, 0.5), ("two.", 0.5, 1.0), ("Three", 1.3, 1.7), ("four.", 1.7, 2.2))
    )
    (a1, b1), (a2, b2) = sources.spans(lines, duration=2.3)
    # Halfway between 1.0 and 1.3 s, and no further out than the file's two ends.
    assert a1 == 0.0 and b1 == pytest.approx(1.15) and a2 == pytest.approx(1.15) and b2 == 2.3


def test_flags():
    """A line is flagged as whispered, another voice or unclear from its measures."""
    row = {"voiced": 0.5, "same_voice": 0.8, "confidence": 0.9}
    assert sources.flags(row) == []
    assert sources.flags(row | {"voiced": 0.1}) == ["whispered"]
    assert sources.flags(row | {"same_voice": 0.1, "confidence": 0.3}) == ["other voice", "unclear"]
    assert sources.flags(row | {"same_voice": None}) == []  # too short to check


# --- READING A FILE, AND THE JOB ---


def test_any_file_is_decoded_to_16k_mono(tmp_path):
    """Any file, here a 44.1 kHz stereo MP3, is decoded to 16 kHz mono."""
    t = np.arange(int(2.0 * 44100)) / 44100
    left, right = 0.5 * np.sin(2 * np.pi * 440 * t), np.zeros_like(t)
    path = tmp_path / "stereo.mp3"
    sf.write(path, np.stack([left, right], axis=1), 44100, format="MP3")
    seconds = sources.decode(path, tmp_path / "out.flac")
    y, sr = sf.read(tmp_path / "out.flac")
    assert sr == SR and y.ndim == 1 and seconds == pytest.approx(2.0, abs=0.1)
    # The two channels averaged: a tone peaking at 0.5 and silence give about 0.25.
    assert 0.2 < np.max(np.abs(y[SR // 2 : SR])) < 0.3


def test_unreadable_files_are_reported(tmp_path):
    """A file that isn't audio raises a SourceError saying it couldn't be read."""
    path = tmp_path / "notes.txt"
    path.write_text("not audio")
    with pytest.raises(sources.SourceError, match="Couldn't read"):
        sources.decode(path, tmp_path / "out.flac")


def test_the_job_finds_each_line_and_the_odd_voice_out(tmp_path):
    """The job splits a compilation into its lines, and flags slt's among bdl's as another voice."""
    # 1. Run the job on four lines by bdl and one by slt.
    path = tmp_path / "lines.wav"
    sf.write(path, compilation(), SR, subtype="PCM_16")
    store = Store(tmp_path / "data")
    meta, folder = store.new_source("Compilation", file=str(path))
    assert sources.run(folder) == 0

    # 2. The lines it found.
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

    # 3. The file is left where it was, and the job is over.
    assert path.is_file()  # a file on this computer is read, not moved
    assert not sources.job_running(folder)


def test_a_failed_job_says_why(tmp_path):
    """A job on half a second of silence fails, and says the recording is under a second."""
    path = tmp_path / "empty.wav"
    sf.write(path, np.zeros(SR // 2, dtype=np.float32), SR)
    store = Store(tmp_path / "data")
    meta, folder = store.new_source("Too short", file=str(path))
    assert sources.run(folder) == 1
    meta = store.source_meta(meta["id"])
    assert meta["status"] == "failed" and "under a second" in meta["error"]


# --- SUGGESTIONS, THE VOICE AT A GLANCE, AND OLDER LINES ---


def row(text, start, end, voiced=0.5, confidence=0.9, same_voice=0.8, flags=(), pitch=200):
    """A line as the job saves it: clear, voiced and the voice's own unless told otherwise."""
    return {
        "text": text,
        "start": start,
        "end": end,
        "voiced": voiced,
        "confidence": confidence,
        "same_voice": same_voice,
        "flags": list(flags),
        "pitch": pitch,
    }


def test_suggested_lines_are_clean_mid_length_and_clearly_voiced():
    """Suggestions are clean lines of a middling length, the more clearly voiced first."""
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
    """The summary gives the voice's pitch, range, speaking rate and voicing from its own lines."""
    rows = [row(f"line number {i} here", 4 * i, 4 * i + 3, pitch=180 + 5 * i) for i in range(10)]
    rows.append(row("someone else entirely", 50, 53, pitch=100, flags=["other voice"]))
    s = sources.summary(rows)
    assert s["pitch"] == 202 and s["low"] < s["pitch"] < s["high"]  # the other voice doesn't count
    # Four words in each 3 s line, less the padding around its words.
    assert s["rate"] == pytest.approx(4 / (3 - sources.LEAD - sources.TAIL), abs=0.1)
    assert s["voiced"] == 0.5


def test_a_line_saved_before_lines_kept_their_recording_is_linked_to_it(tmp_path):
    """A line saved before lines kept their recording is linked to it by its name on migration."""
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
    old = {
        "id": "abc123",
        "name": "Lighthouse: the hills ran up clear above the vegetation, in…",
        "created": "2026-11-15T21:42:17-05:00",
    }
    (line_dir / "meta.json").write_text(json.dumps(old), "utf-8")
    store.migrate()
    linked = store.target_meta("abc123")
    assert (linked["source"], linked["line_start"]) == (meta["id"], 23.46)
    assert linked["voice_id"] == store.source_meta(meta["id"])["voice_id"]


def test_suggestions_keep_to_the_voices_usual_pitch():
    """Suggestions leave out lines an octave below or above the voice's usual pitch."""
    rows = [row(f"a clear line number {i}", 4 * i, 4 * i + 3.5, pitch=190 + i) for i in range(8)]
    rows.append(row("the same words in creaky voice", 40, 43.5, pitch=98))  # an octave down
    rows.append(row("a squeal an octave up somewhere", 44, 47.5, pitch=400))
    picked = [rows[i]["text"] for _, i in sources.rank(rows)]
    assert len(picked) == 8 and not any("creaky" in t or "squeal" in t for t in picked)


# --- SPEAKERS ---

# bdl's four lines, then three by slt.
TWO_SPEAKERS = ("bdl_arctic_a0001", "bdl_arctic_a0002") * 2 + ("slt_arctic_a0001",) * 3


def test_each_speaker_in_a_recording_is_found():
    """Two speakers in one recording are told apart, and lines can be suggested for either."""
    y = compilation(clips=TWO_SPEAKERS)
    rows = sources.find_lines(y)
    found = sources.speaker_summary(rows)
    assert [s["lines"] for s in found][:1] == [len(rows) - 3] and len(found) == 2  # bdl talks most
    assert [r["speaker"] for r in rows[-3:]] == [1, 1, 1]  # slt's three
    assert found[1]["pitch"] > found[0]["pitch"] * 1.3  # a woman after a man
    # Picking for slt: her lines, none of his.
    assert {rows[i]["speaker"] for i in sources.suggest(rows, speaker=1)} == {1}
    assert all(
        sources.line_flags(r, 1) == (["other voice"] if r["speaker"] == 0 else [])
        for r in rows
        if r["voiced"] > 0.2
    )


def test_a_recording_from_before_speakers_gets_them_without_moving_its_lines(tmp_path):
    """A recording split before speakers were told apart gets them, and keeps its lines' times."""
    # 1. A recording split into lines, with the speakers taken out again.
    path = tmp_path / "lines.wav"
    sf.write(path, compilation(), SR, subtype="PCM_16")
    store = Store(tmp_path / "data")
    meta, folder = store.new_source("Old", file=str(path))
    sources.run(folder)
    rows = json.loads((folder / sources.LINES).read_text("utf-8"))
    for r in rows:
        del r["speaker"]
    (folder / sources.LINES).write_text(json.dumps(rows), "utf-8")

    # 2. Run again, it adds the speakers to the lines it has.
    store.update_source(meta["id"], status="queued")
    assert sources.run(folder) == 0
    again = json.loads((folder / sources.LINES).read_text("utf-8"))
    assert [r["start"] for r in again] == [r["start"] for r in rows]  # not transcribed again
    assert all("speaker" in r for r in again) and store.source_meta(meta["id"])["speakers"] == 1


# --- NAMING A VOICE ---


def test_titles_become_voice_names():
    """A video's title is cut down to a voice's name: tags, brackets, emoji and the rest go."""
    long_title = "The lighthouse keeper's story (extended cut) [4K][HD][subtitles]"
    assert sources.clean_title(long_title) == "The lighthouse keeper's story"
    assert sources.clean_title("Ellie - All Voice Lines | The Last of Us Part II") == "Ellie"
    assert sources.clean_title("🔥 Voice lines #shorts") == "Voice lines"


def test_a_links_voice_is_named_after_its_channel_and_a_second_video_joins_it(tmp_path):
    """A link's voice takes its channel's name, and a later video from that channel joins it."""
    store = Store(tmp_path / "data")

    def pasted(channel, title):
        """Paste a link to a video: a voice waiting for a name, then named as the job names it."""
        voice = store.update_voice(store.new_voice("New voice")["id"], name_pending=True)
        meta, folder = store.new_source(
            sources.clean_title(title), link="https://example.com/v", status="downloading"
        )
        meta = store.update_source(meta["id"], voice_id=voice["id"], channel=channel, title=title)
        sources._name_voice(folder, meta)
        return voice, meta

    # 1. The first video from a channel names its voice after the channel.
    first, meta = pasted("Harbour Tales", "The lighthouse keeper's story (extended cut) [4K]")
    named = store.voice_meta(first["id"])
    assert named["name"] == "Harbour Tales" and named["named_from"] == "channel"
    assert meta["voice_id"] == first["id"]

    # 2. The next joins the channel's voice, rather than making a second "Harbour Tales".
    second, meta = pasted("Harbour Tales", "Another story [4K]")
    assert meta["voice_id"] == first["id"] and meta["joined"]
    assert store.voice_meta(second["id"])["merged_into"] == first["id"]
    assert [v["name"] for v in store.list_voices()] == ["Harbour Tales"]

    # 3. With no channel known, the voice is named from the title.
    third, meta = pasted(None, "Ellie - All Voice Lines | The Last of Us Part II")
    named = store.voice_meta(third["id"])
    assert named["name"] == "Ellie" and named["named_from"] == "title"


# --- CUTTING A LINE FROM A RECORDING ---


def test_a_line_that_starts_mid_sentence_is_widened_or_left_out(tmp_path, monkeypatch):
    """The recording's transcript can miss a sentence's first words. The cut, read by itself,
    shows it."""
    from synth import voice

    from echo_voice import autopick

    # 1. A recording of the synthetic voice, and three of its lines as the transcript has them.
    store = Store(tmp_path)
    owner = store.new_voice("Someone")
    meta, folder = store.new_source("a recording", status="ready")
    store.update_source(meta["id"], voice_id=owner["id"])
    sf.write(folder / sources.AUDIO, np.tile(voice(), 6), SR, format="FLAC")

    def recorded(text, start, end):
        """A line of the recording, clear and the voice's own."""
        return {
            "text": text,
            "start": start,
            "end": end,
            "flags": [],
            "pitch": 120,
            "voiced": 0.6,
            "confidence": 0.9,
            "same_voice": 0.9,
            "speaker": 0,
        }

    rows = [
        recorded("So there is that.", 0.2, 1.4),
        # "Most of the" was missed, 0.5 s before it.
        recorded("Boats came back before dark.", 3.0, 5.5),
        recorded("Than me, at least.", 5.5, 7.6),  # the same breath: no room to look back
    ]
    (folder / sources.LINES).write_text(json.dumps(rows), "utf-8")
    recording = store.source_meta(meta["id"])

    # 2. Stand-ins for cleaning, the recogniser, analysis and saving: a cut is heard by its
    # length, so only the cutting itself is tested.
    monkeypatch.setattr(autopick.clean, "vocals", lambda y: y)

    def heard(y):
        """The words a recogniser would hear in a cut of `y`, known by its length."""
        seconds = len(y) / SR
        said = {
            2.5: "the boats came back before dark.",  # the cut as the recording gave it
            3.3: "Most of the boats came back before dark.",  # looked 0.8 s further back
            1.2: "So there is that.",
        }.get(round(seconds, 1), "then me, at least.")
        return [
            {"text": w, "start": i * 0.2, "end": i * 0.2 + 0.2} for i, w in enumerate(said.split())
        ]

    def analyzed(y):
        """What adding a line reads from analyze(), without analysing anything."""
        fields = {"y": y, "duration": len(y) / SR, "median_f0": 120.0, "formant_ceiling": 5000.0}
        return type("V", (), fields)()

    def add_target(name, cleaned, words, original, **more):
        """The line Store.add_target would save, without saving it."""
        return {
            "id": f"line{len(words)}",
            "name": name,
            "words": words,
            "seconds": len(original) / SR,
            **more,
        }

    monkeypatch.setattr(autopick.line, "transcribe_timed", heard)
    monkeypatch.setattr(autopick, "analyze", analyzed)
    monkeypatch.setattr(store, "add_target", add_target)
    monkeypatch.setattr(store, "tick_line", lambda *a: None)

    # 3. A line that starts where its sentence does is cut as the recording says.
    whole = autopick.add_from_recording(store, recording, rows[0], auto=True)
    assert whole["name"] == "So there is that." and whole["seconds"] == pytest.approx(1.2, abs=0.01)

    # 4. One that starts mid-sentence is widened back to the sentence's start, and named as it
    # is heard.
    widened = autopick.add_from_recording(store, recording, rows[1], auto=True)
    assert widened["words"][:3] == ["Most", "of", "the"]
    assert widened["seconds"] == pytest.approx(3.3, abs=0.01)
    assert widened["name"].startswith("Most of the boats") and widened["line_start"] == 3.0

    # 5. With no room to look back, auto-pick leaves it out and remembers, so it isn't
    # suggested again.
    assert autopick.add_from_recording(store, recording, rows[2], auto=True) is None
    assert store.source_meta(meta["id"])["skipped"] == [5.5]
    monkeypatch.setattr(autopick.sources, "rank", lambda rows, speaker=0: [(3.0, 2), (2.0, 0)])
    assert [s["text"] for s in autopick.suggestions(store, owner["id"])] == ["So there is that."]

    # 6. Picked by hand it is still added, as heard.
    by_hand = autopick.add_from_recording(store, recording, rows[2])
    assert by_hand["name"] == "then me, at least." and by_hand["auto"] is False
