'''
tests/test_server.py
Author: maymeridian
Description: Tests for the web server's API: lines, takes, recordings, voices and the trainer.
             - Each test runs the app in-process with FastAPI's TestClient on a fresh folder.
             - Lines are the synthetic voice or CMU ARCTIC clips; recordings are compilations.
'''

import io
import json
import time

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from synth import SR, voice

from echo_voice.server import create_app

# The six parts of the score.
PARTS = {"identity", "pitch", "melody", "resonance", "articulation", "rhythm"}


def wav(samples) -> bytes:
    """`samples` as the bytes of a 16-bit WAV file."""
    buffer = io.BytesIO()
    sf.write(buffer, samples, SR, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def wait_ready(client, source_id, seconds=180):
    """Wait for a recording's job to finish, and return the recording, which must be ready."""
    finished = ("ready", "failed")
    deadline = time.time() + seconds
    while (source := client.get(f"/api/sources/{source_id}").json())["status"] not in finished:
        assert time.time() < deadline, source
        time.sleep(0.5)
    assert source["status"] == "ready", source
    return source


def synth_lines(client, voice_id, pitches) -> list[dict]:
    """A line of the synthetic voice at each pitch, named "line 0", "line 1" and so on."""
    return [
        client.post(
            "/api/targets",
            params={"name": f"line {i}", "voice": voice_id},
            content=wav(voice(f0=f0)),
        ).json()
        for i, f0 in enumerate(pitches)
    ]


def time_the_first_word(client, tmp_path, line_id) -> None:
    """Give a synthetic line the words "ah ee oo eh", the first timed over its first vowel."""
    client.patch(f"/api/targets/{line_id}", json={"line": "ah ee oo eh"})
    meta_path = tmp_path / "targets" / line_id / "meta.json"
    meta = json.loads(meta_path.read_text("utf-8"))
    meta["word_times"] = [{"text": "ah", "start": 0.0, "end": 0.4, "refined": True}]
    meta_path.write_text(json.dumps(meta), "utf-8")


def voice_with_takes(client):
    """A voice "V" with lines at 120, 125 and 130 Hz, and a take at 150 Hz on each."""
    voice_id = client.post("/api/voices", json={"name": "V"}).json()["id"]
    lines = [
        client.post(
            "/api/targets",
            params={"name": f"line {f0}", "voice": voice_id},
            content=wav(voice(f0=f0)),
        ).json()
        for f0 in (120, 125, 130)
    ]
    takes = [
        client.post(f"/api/targets/{line['id']}/takes", content=wav(voice(f0=150))).json()
        for line in lines
    ]
    return voice_id, lines, takes


# --- LINES AND TAKES ---


def test_add_target_record_take_and_read_it_back(tmp_path):
    """A line is added, practised, listed, corrected and deleted through the API."""
    # 1. The page loads. It is rechecked on every load, so an update shows up without a forced
    # reload.
    client = TestClient(create_app(tmp_path))
    assert client.get("/").status_code == 200
    assert client.get("/app.js").headers["cache-control"] == "no-cache"

    # 2. Add a line, and read back its audio and its pitch.
    target = client.post("/api/targets", params={"name": "Test voice"}, content=wav(voice())).json()
    assert target["name"] == "Test voice"
    assert client.get(f"/api/targets/{target['id']}/audio").headers["content-type"] == "audio/wav"
    profile = client.get(f"/api/targets/{target['id']}/profile").json()
    assert 110 < profile["median_f0"] < 130  # the synthetic voice sits at 120 Hz
    assert profile["low"] < profile["median_f0"] < profile["high"]

    # 3. Record a take, and read it back.
    take = client.post(f"/api/targets/{target['id']}/takes", content=wav(voice(f0=150))).json()
    assert 0 < take["overall"] < 100
    listed = client.get(f"/api/targets/{target['id']}/takes").json()
    assert [t["id"] for t in listed] == [take["id"]]
    assert client.get(f"/api/targets/{target['id']}/takes/{take['id']}").json() == take

    # 4. Correct the line's words.
    edited = client.patch(f"/api/targets/{target['id']}", json={"line": "  say this line  "}).json()
    assert edited["words"] == ["say", "this", "line"]
    assert client.get("/api/targets").json()[0]["words"] == ["say", "this", "line"]

    # 5. Delete it.
    assert client.delete(f"/api/targets/{target['id']}").status_code == 204
    assert client.get("/api/targets").json() == []


def test_bad_audio_and_unknown_ids_are_reported(tmp_path):
    """Audio that can't be read, a clip too long and an unknown line are each a clear error."""
    client = TestClient(create_app(tmp_path))
    bad = client.post("/api/targets", content=b"not audio")
    assert bad.status_code == 422
    assert bad.json()["detail"] == "Couldn't read that audio."
    assert client.post("/api/targets/nope/takes", content=wav(voice())).status_code == 404
    too_long = client.post("/api/targets", content=wav(np.tile(voice(), 25)))  # about 45 s
    assert too_long.status_code == 422 and "keep it under" in too_long.json()["detail"]
    # A path out of the data folder is only an unknown line.
    assert client.get("/api/targets/..%2F..%2Fx/audio").status_code == 404


# --- RECORDINGS SPLIT INTO LINES ---


def test_a_long_recording_is_split_into_lines_by_a_background_job(tmp_path):
    """A long recording is split into lines in the background, and its voice gets the best."""
    from synth import compilation

    # 1. Upload four lines by bdl and one by slt, and wait for the job.
    client = TestClient(create_app(tmp_path))
    meta = client.post(
        "/api/sources",
        params={"name": "", "filename": "voice lines.wav"},
        content=wav(compilation()),
    ).json()
    assert meta["name"] == "voice lines" and meta["status"] == "queued"
    source = wait_ready(client, meta["id"], seconds=120)

    # 2. The lines it found.
    # Whisper on the CPU sometimes ends a sentence at "Tom" in bdl's a0002, so 5 to 7 lines.
    n = len(source["lines"])
    assert 5 <= n <= 7, source["lines"]
    assert ["other voice" in r["flags"] for r in source["lines"]] == [False] * (n - 1) + [True]
    assert source["suggested"] and 4 not in source["suggested"]  # never the other voice
    assert 80 < source["summary"]["pitch"] < 160  # bdl, a man
    assert client.get("/api/sources").json()[0]["count"] == n

    # 3. The voice (named after the file) got the recording's best lines by itself:
    # bdl's four, not slt's, another voice.
    voice = client.get(f"/api/voices/{source['voice_id']}").json()
    assert voice["name"] == "voice lines" and voice["line_count"] == 4
    assert {line["source"] for line in voice["lines"]} == {meta["id"]}
    assert voice["suggestions"] == []
    assert voice["check_set"] == [line["id"] for line in voice["lines"]]
    autopicked = client.post(f"/api/voices/{voice['id']}/autopick", json={}).json()
    assert autopicked["added"] == []  # nothing clear left

    # 4. slt's line can still be added by hand; a start with no line there can't.
    slt = source["lines"][-1]
    added = client.post(
        f"/api/voices/{voice['id']}/lines", json={"source": meta["id"], "start": slt["start"]}
    )
    assert added.status_code == 201
    assert added.json()["line_start"] == slt["start"] and added.json()["voice_id"] == voice["id"]
    nowhere = client.post(
        f"/api/voices/{voice['id']}/lines", json={"source": meta["id"], "start": 999}
    )
    assert nowhere.status_code == 404

    # 5. A line's audio is cut from the recording; a stretch past its end is refused.
    line = source["lines"][0]
    audio = client.get(
        f"/api/sources/{meta['id']}/audio", params={"start": line["start"], "end": line["end"]}
    )
    assert audio.headers["content-type"] == "audio/wav"
    samples = sf.read(io.BytesIO(audio.content))[0]
    assert len(samples) == pytest.approx((line["end"] - line["start"]) * SR, abs=2)
    too_far = client.get(f"/api/sources/{meta['id']}/audio", params={"start": 0, "end": 500})
    assert too_far.status_code == 422

    # 6. Delete the recording.
    assert client.delete(f"/api/sources/{meta['id']}").status_code == 204
    assert client.get("/api/sources").json() == []


def test_bad_links_and_unknown_recordings_are_reported(tmp_path):
    """Something that isn't a link, and an unknown recording, are each a clear error."""
    client = TestClient(create_app(tmp_path))
    bad = client.post("/api/sources/link", json={"url": "not a link"})
    assert bad.status_code == 422 and "https://" in bad.json()["detail"]
    assert client.get("/api/sources/nope").status_code == 404
    # A path out of the data folder is only an unknown recording.
    escape = client.get("/api/sources/..%2F..%2Fx/audio", params={"start": 0, "end": 1})
    assert escape.status_code == 404


# --- WORD BY WORD, AND THE COACH ---


def test_takes_get_word_by_word_feedback_and_a_coach(tmp_path):
    """Takes on a real line are scored word by word and coached, and one word can be trained."""
    from synth import ARCTIC

    # 1. bdl's a0001 as the line: its words are timed, and it has a pitch guide.
    bdl = (ARCTIC / "bdl_arctic_a0001.wav").read_bytes()
    rms = (ARCTIC / "rms_arctic_a0001.wav").read_bytes()
    client = TestClient(create_app(tmp_path))
    target = client.post("/api/targets", params={"name": "bdl"}, content=bdl).json()
    assert target["word_times"] and target["takes"] == 0 and target["best"] is None
    guide = client.get(f"/api/targets/{target['id']}/profile").json()
    assert guide["duration"] > 2 and 0 <= guide["onset"] < 0.5
    assert len(guide["contour"]["t"]) == len(guide["contour"]["f0"]) > 50
    # "of the" is run together, so one unit.
    assert [w["text"] for w in guide["words"]][:2] == ["Author", "of the"]

    # 2. A first take by rms: scored word by word, with one part to work on.
    first = client.post(f"/api/targets/{target['id']}/takes", content=rms).json()
    assert len(first["words"]) >= 6  # rms says the same sentence
    word = first["words"][0]
    assert word["text"] == "Author" and word["take_start"] < word["take_end"]
    assert word["scores"]["articulation"] is not None
    assert first["coach"]["focus"] in ("pitch", "melody", "resonance", "articulation", "rhythm")
    assert first["coach"]["message"] and first["coach"]["progress"] is None

    # 3. A second take, working on melody by choice.
    second = client.post(
        f"/api/targets/{target['id']}/takes", params={"focus": "melody"}, content=rms
    ).json()
    assert second["coach"]["focus"] == "melody" and second["coach"]["why"] == "chosen"
    assert second["coach"]["progress"]["key"] == "melody"  # what this take worked on

    # 4. Training one word: bdl's "Danger", said by bdl himself, matches; said by rms, it's
    # darker and lower.
    k = next(i for i, w in enumerate(guide["words"]) if w["text"].lower().startswith("danger"))
    w = guide["words"][k]
    own, rate = sf.read(ARCTIC / "bdl_arctic_a0001.wav", dtype="float32")
    piece = own[int((w["start"] - 0.05) * rate) : int((w["end"] + 0.05) * rate)]
    drill = f"/api/targets/{target['id']}/drill"
    mine = client.post(drill, params={"word": k}, content=wav(piece)).json()
    assert mine["text"].lower().startswith("danger")
    assert mine["scores"]["resonance"] > 90 and mine["scores"]["pitch"] > 90
    assert client.post(drill, params={"word": 99}, content=wav(piece)).status_code == 404

    # 5. Both takes are kept with their parts and focus, and the list of lines sums them up.
    takes = client.get(f"/api/targets/{target['id']}/takes").json()
    assert set(takes[0]["parts"]) == PARTS | {"gender"}
    assert [t["focus"] for t in takes] == [first["coach"]["focus"], "melody"]
    listed = client.get("/api/targets").json()[0]
    assert listed["takes"] == 2 and listed["best"] == max(first["overall"], second["overall"])
    assert listed["last"] == second["created"] and listed["focus"] == "melody"  # for "Continue"
    assert second["measures"]["take_f0"] < second["measures"]["target_f0"]  # rms is lower than bdl
    assert second["coach"]["plateau"] is False


# --- VOICES ---


def test_a_line_from_a_recording_remembers_where_it_came_from(tmp_path):
    """A line cut from a recording keeps the recording, its voice and where in it the line is."""
    from echo_voice.store import Store

    store = Store(tmp_path)
    recording, _ = store.new_source("Lighthouse", status="failed")  # no job needed for this
    client = TestClient(create_app(tmp_path))
    target = client.post(
        "/api/targets",
        params={"name": "Lighthouse: Hello", "source": recording["id"], "line_start": 12.5},
        content=wav(voice()),
    ).json()
    came_from = (target["source"], target["voice"], target["line_start"])
    assert came_from == (recording["id"], "Lighthouse", 12.5)
    # A recording that doesn't exist is left out.
    gone = client.post(
        "/api/targets", params={"name": "x", "source": "deadbeef0000"}, content=wav(voice())
    ).json()
    assert gone["source"] is None and gone["line_start"] is None


def test_a_voice_gathers_lines_and_scores_them_together_in_a_voice_check(tmp_path):
    """A voice gathers lines, and a voice check scores one take on each of them together."""
    from synth import ARCTIC

    # 1. A voice needs a name; this one gets two lines by bdl and one by rms.
    client = TestClient(create_app(tmp_path))
    assert client.post("/api/voices", json={"name": "  "}).status_code == 422
    voice_id = client.post("/api/voices", json={"name": "bdl"}).json()["id"]
    lines = [
        client.post(
            "/api/targets",
            params={"name": name, "voice": voice_id},
            content=(ARCTIC / f"{name}.wav").read_bytes(),
        ).json()
        for name in ("bdl_arctic_a0001", "bdl_arctic_a0002", "rms_arctic_a0001")
    ]
    assert {line["voice_id"] for line in lines} == {voice_id}

    # 2. Its page before any voice check.
    voice = client.get(f"/api/voices/{voice_id}").json()
    assert voice["line_count"] == 3 and voice["score"] is None
    assert voice["check_set"] == [line["id"] for line in lines]
    # From its lines' pitch: two men.
    assert 80 < voice["summary"]["pitch"] < 160 and voice["you"] is None
    assert voice["gender"] < 25  # reads male

    # 3. One take on each line, as the page does in a voice check.
    slt = (ARCTIC / "slt_arctic_a0001.wav").read_bytes()

    def take(line):
        """A take by slt on `line`."""
        return client.post(f"/api/targets/{line['id']}/takes", content=slt).json()

    taken = [take(line) for line in lines]

    # 4. A check needs at least three takes; with three, it sums them up.
    too_few = client.post(
        f"/api/voices/{voice_id}/checks",
        json={"takes": [{"target": lines[0]["id"], "take": taken[0]["id"]}]},
    )
    assert too_few.status_code == 422 and "at least 3" in too_few.json()["detail"]
    check = client.post(
        f"/api/voices/{voice_id}/checks",
        json={"takes": [{"target": line["id"], "take": t["id"]} for line, t in zip(lines, taken)]},
    ).json()
    overalls = [t["overall"] for t in taken]
    assert check["average"] == pytest.approx(sum(overalls) / 3, abs=0.06)
    assert check["low"] == min(overalls) and check["high"] == max(overalls)
    assert set(check["parts"]) == PARTS
    assert check["habits"]["pitch"] > 3  # slt, a woman, against two men: higher in every line
    assert check["weakest"] in {line["id"] for line in lines} and len(check["lines"]) == 3

    # 5. The voice's score is the check's, and its page shows your habits.
    voice = client.get(f"/api/voices/{voice_id}").json()
    assert voice["score"] == check["average"] and len(voice["checks"]) == 1
    assert voice["you"]["takes"] == 3 and voice["you"]["pitch"] > 3
    saved = client.get(f"/api/voices/{voice_id}/checks/{check['id']}").json()
    assert saved["average"] == check["average"]
    assert client.get("/api/voices").json()[0]["score"] == check["average"]

    # 6. Rename it and choose the lines to check, leaving out an unknown one; then delete it,
    # with its lines.
    edited = client.patch(
        f"/api/voices/{voice_id}", json={"name": "Brad", "check_lines": [lines[2]["id"], "nope"]}
    ).json()
    assert edited["name"] == "Brad" and edited["check_set"] == [lines[2]["id"]]
    assert client.delete(f"/api/voices/{voice_id}").status_code == 204
    assert client.get("/api/voices").json() == [] and client.get("/api/targets").json() == []


def test_lines_and_recordings_from_before_voices_get_one(tmp_path):
    """Recordings and lines saved before voices existed are each given a voice when migrated."""
    from echo_voice.store import Store

    # 1. A recording from before voices gets one, named after it, when the app starts.
    store = Store(tmp_path)
    recording, _ = store.new_source("Lighthouse", status="ready")
    client = TestClient(create_app(tmp_path))  # migrates
    lighthouse = client.get("/api/voices").json()[0]
    assert lighthouse["name"] == "Lighthouse"
    assert store.source_meta(recording["id"])["voice_id"] == lighthouse["id"]

    # 2. A line saved before voices existed, named "<recording>: <words>".
    line = client.post(
        "/api/targets", params={"name": "Lighthouse: Hello there"}, content=wav(voice())
    ).json()
    path = tmp_path / "targets" / line["id"] / "meta.json"
    meta = json.loads(path.read_text("utf-8"))
    meta.pop("voice_id")
    path.write_text(json.dumps(meta), "utf-8")
    Store(tmp_path).migrate()
    assert json.loads(path.read_text("utf-8"))["voice_id"] == lighthouse["id"]


def test_a_recording_with_two_speakers_can_be_either_voice(tmp_path):
    """A recording with two speakers can be either one's voice, or give each a voice."""
    from synth import compilation

    # 1. Four lines by bdl, then three by slt.
    client = TestClient(create_app(tmp_path))
    voice_id = client.post("/api/voices", json={"name": "Two"}).json()["id"]
    two = ("bdl_arctic_a0001", "bdl_arctic_a0002") * 2 + ("slt_arctic_a0001",) * 3
    meta = client.post(
        "/api/sources",
        params={"filename": "two.wav", "voice": voice_id},
        content=wav(compilation(clips=two)),
    ).json()
    source = wait_ready(client, meta["id"])
    assert [s["lines"] for s in source["speakers"]][1:] == [3] and source["speaker"] == 0
    # slt, not the voice's speaker.
    assert all("other voice" in r["flags"] for r in source["lines"][-3:])

    # 2. The voice is bdl, who speaks most.
    voice = client.get(f"/api/voices/{voice_id}").json()
    # bdl's, picked by Echo.
    assert voice["line_count"] == 4 and all(line["auto"] for line in voice["lines"])
    speakers = voice["recordings"][0]["speakers"]
    assert len(speakers) == 2 and speakers[1]["sample"]["text"]

    # 3. Switch the voice to slt: Echo's untouched picks from bdl give way to hers.
    switched = client.patch(f"/api/sources/{meta['id']}", json={"speaker": 1}).json()
    assert switched["removed"] == 4 and len(switched["added"]) == 3
    source = client.get(f"/api/sources/{meta['id']}").json()
    assert source["speaker"] == 1
    assert not any("other voice" in r["flags"] for r in source["lines"][-3:])
    assert client.patch(f"/api/sources/{meta['id']}", json={"speaker": 7}).status_code == 422

    # 4. And bdl as a voice of his own.
    split = client.post(
        f"/api/sources/{meta['id']}/split", json={"speaker": 0, "name": "Brad"}
    ).json()
    assert split["voice"]["name"] == "Brad" and len(split["added"]) == 4
    brad = client.get(f"/api/voices/{split['voice']['id']}").json()
    assert brad["recordings"][0]["speaker"] == 0 and brad["line_count"] == 4


def test_a_pasted_link_makes_a_voice_named_after_the_video(tmp_path):
    """A pasted link makes a voice named after the video, and an upload can be given its link."""
    import functools
    import http.server
    import socketserver
    import threading

    from synth import compilation

    # 1. A compilation served over HTTP, to paste a link to.
    sf.write(tmp_path / "Voice lines.wav", compilation(), SR, subtype="PCM_16")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *args: None
    web = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    try:
        # 2. Paste the link: the voice is named after the video once its title is known.
        url = f"http://127.0.0.1:{web.server_address[1]}/Voice%20lines.wav"
        client = TestClient(create_app(tmp_path / "data"))
        meta = client.post("/api/sources/link", json={"url": url}).json()
        voice = client.get(f"/api/voices/{meta['voice_id']}").json()
        assert voice["name"] == "New voice" and voice["name_pending"]  # until the title is known
        wait_ready(client, meta["id"])
        voice = client.get(f"/api/voices/{meta['voice_id']}").json()
        assert voice["name"] == "Voice lines" and not voice["name_pending"]
        assert voice["named_from"] == "title"

        # 3. A name typed in is the voice's own, and the link stays with the voice.
        renamed = client.patch(f"/api/voices/{voice['id']}", json={"name": "Sally"}).json()
        assert renamed["name"] == "Sally" and renamed["named_from"] == "hand"
        assert voice["recordings"][0]["link"].endswith("/Voice%20lines.wav")

        # 4. A recording uploaded as a file can be given the link it came from.
        upload = client.post(
            "/api/sources",
            params={"filename": "clip.wav", "voice": voice["id"]},
            content=wav(compilation()),
        ).json()
        patched = client.patch(f"/api/sources/{upload['id']}", json={"link": url}).json()
        linked = patched["recording"]
        assert linked["link"] == url and linked["title"] == "Voice lines"
        not_a_link = client.patch(f"/api/sources/{upload['id']}", json={"link": "not a link"})
        assert not_a_link.status_code == 422
        wait_ready(client, upload["id"])
        assert voice["line_count"] == 4  # its best lines, picked
    finally:
        web.shutdown()


# --- THE TRAINER, AND THE FORMANT CEILING ---


def test_trainer_attempts_are_kept_and_progress_gathers_every_take(tmp_path):
    """Trainer attempts are kept with their audio, and progress gathers every take on the voice."""
    # 1. The last line sits an octave above the voice's usual pitch: not to be trusted, nor
    # checked.
    client = TestClient(create_app(tmp_path))
    voice_id = client.post("/api/voices", json={"name": "Synth"}).json()["id"]
    lines = synth_lines(client, voice_id, (120, 122, 118, 240))
    voice_page = client.get(f"/api/voices/{voice_id}").json()
    assert [line["pitch_suspect"] for line in voice_page["lines"]] == [False, False, False, True]
    assert lines[3]["id"] not in voice_page["check_set"]
    assert client.get(f"/api/targets/{lines[3]['id']}/profile").json()["pitch_suspect"] is True
    odd = client.post(f"/api/targets/{lines[3]['id']}/takes", content=wav(voice(f0=120))).json()
    assert {c["key"]: c["score"] for c in odd["components"]}["pitch"] is None

    # 2. Trainer attempts are saved with their audio.
    time_the_first_word(client, tmp_path, lines[0]["id"])
    attempt = client.post(
        f"/api/targets/{lines[0]['id']}/drill",
        params={"word": 0},
        content=wav(voice()[: int(0.45 * SR)]),
    ).json()
    assert attempt["id"] and attempt["word"] == 0 and attempt["scores"]["resonance"] > 90
    kept = client.get(f"/api/targets/{lines[0]['id']}/drills").json()
    assert [a["id"] for a in kept] == [attempt["id"]]
    audio = client.get(f"/api/targets/{lines[0]['id']}/drills/{attempt['id']}/audio")
    assert audio.headers["content-type"] == "audio/wav"

    # 3. Progress gathers every take on the voice's lines, oldest first.
    for line in lines[:2]:
        client.post(f"/api/targets/{line['id']}/takes", content=wav(voice(f0=130)))
    progress = client.get(f"/api/voices/{voice_id}/progress").json()
    # The take on the odd line, and one on each of the first two.
    assert len(progress["takes"]) == 3 and progress["drills"] == 1 and len(progress["days"]) == 1
    assert all("parts" in t and t["line"] for t in progress["takes"])
    created = [t["created"] for t in progress["takes"]]
    assert created == sorted(created)  # oldest first


def test_the_trainer_measures_a_word_at_the_ceiling_the_voice_and_your_takes_settle_on(tmp_path):
    """The trainer reads a word at the middle of the voice's ceiling and your takes' ceiling."""
    from echo_voice.analysis import FORMANT_CEILINGS, analyze, shared_ceiling

    # 1. analyze() uses a given ceiling as it is, and picks one when there is none.
    assert analyze(voice(), 6500).formant_ceiling == 6500.0
    assert analyze(voice()).formant_ceiling in FORMANT_CEILINGS

    # 2. A line with its first word timed, to train.
    client = TestClient(create_app(tmp_path))
    voice_id = client.post("/api/voices", json={"name": "Synth"}).json()["id"]
    line_id = client.post(
        "/api/targets", params={"name": "line", "voice": voice_id}, content=wav(voice())
    ).json()["id"]
    time_the_first_word(client, tmp_path, line_id)
    piece = wav(voice()[: int(0.45 * SR)])

    def drill():
        """An attempt at the first word in the trainer: the line's own first vowel."""
        return client.post(
            f"/api/targets/{line_id}/drill", params={"word": 0}, content=piece
        ).json()

    # 3. With no takes to go by, the attempt is read at the voice's ceiling: what its line picks.
    alone = drill()["ceiling"]
    assert alone == client.get("/api/targets").json()[0]["ceiling"]

    # 4. Every take keeps the ceiling it was measured with.
    for f0 in (118, 122, 126):
        take = client.post(f"/api/targets/{line_id}/takes", content=wav(voice(f0=f0))).json()
        assert take["measures"]["ceiling"] in FORMANT_CEILINGS
    files = sorted((tmp_path / "targets" / line_id / "takes").glob("*.json"))
    assert len(files) == 3

    # 5. The trainer then reads both at the middle of the voice's ceiling and yours (the middle
    # one of your takes), whatever the word alone would pick.
    for path, ceiling in zip(files, (6500.0, 6500.0, 7000.0), strict=True):
        record = json.loads(path.read_text("utf-8"))
        record["measures"]["ceiling"] = ceiling
        path.write_text(json.dumps(record), "utf-8")
    both = shared_ceiling(alone, 6500.0)
    assert alone != 6500.0 and drill()["ceiling"] == both

    # 6. A take from before ceilings were kept is measured once, and the result written back.
    record = json.loads(files[0].read_text("utf-8"))
    del record["measures"]["ceiling"]
    files[0].write_text(json.dumps(record), "utf-8")
    # Yours is still 6500: the middle of its own pick, 6500 and 7000.
    assert drill()["ceiling"] == both
    assert json.loads(files[0].read_text("utf-8"))["measures"]["ceiling"] in FORMANT_CEILINGS


def test_a_line_and_a_take_are_read_at_one_ceiling(tmp_path):
    """Resonance reads a line and a take at one shared ceiling; each keeps the one it picked."""
    from echo_voice.analysis import FORMANT_CEILINGS, shared_ceiling, speaker_ceiling

    # 1. Each line keeps the ceiling it picks for itself.
    client = TestClient(create_app(tmp_path))
    voice_id = client.post("/api/voices", json={"name": "Synth"}).json()["id"]
    lines = synth_lines(client, voice_id, (118, 122))
    assert all(line["ceiling"] in FORMANT_CEILINGS for line in lines)

    # 2. Three takes on the first line.
    first, second = lines[0]["id"], lines[1]["id"]
    picks = []
    for f0 in (120, 124, 128):
        take = client.post(f"/api/targets/{first}/takes", content=wav(voice(f0=f0))).json()
        picks.append(take["measures"]["ceiling"])
        # Its own pick is kept. Resonance read both sides at the middle of the voice's ceiling
        # (the middle of its lines' picks) and yours (the middle of your picks so far).
        theirs = speaker_ceiling(line["ceiling"] for line in lines)
        assert take["measures"]["ceiling_used"] == shared_ceiling(theirs, speaker_ceiling(picks))
    # One take is a rough reading of Resonance, so from the third take on the row also says
    # where the last three sit together.
    resonance = next(c for c in take["components"] if c["key"] == "resonance")
    assert "your last 3 takes together: " in resonance["detail"]
    assert take["measures"]["resonance_3"] is not None

    # 3. A line from before its pick was kept gets it on first use.
    meta_path = tmp_path / "targets" / lines[1]["id"] / "meta.json"
    meta = json.loads(meta_path.read_text("utf-8"))
    del meta["ceiling"]
    meta_path.write_text(json.dumps(meta), "utf-8")
    assert client.post(f"/api/targets/{second}/takes", content=wav(voice())).status_code == 201
    assert json.loads(meta_path.read_text("utf-8"))["ceiling"] == lines[1]["ceiling"]


# --- THE VERSION, AND FILES WRITTEN WHOLE ---


def test_the_app_says_which_version_it_is(tmp_path, capsys):
    """The version is one number everywhere: the package, its metadata, the API and the CLI."""
    import re
    from importlib.metadata import version

    import echo_voice
    from echo_voice import cli

    assert re.fullmatch(r"\d+\.\d+\.\d+", echo_voice.__version__)
    # The package is built from the same number.
    assert version("echo-voice") == echo_voice.__version__
    config = TestClient(create_app(tmp_path)).get("/api/config").json()
    assert config["version"] == echo_voice.__version__
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert capsys.readouterr().out.strip() == f"echo-voice {echo_voice.__version__}"


def test_a_damaged_file_is_left_out_and_files_are_written_whole(tmp_path):
    """A damaged file is left out of what is listed, and a failed write leaves no damage."""
    from echo_voice import store as store_module

    # 1. Two lines, and two takes on the first.
    client = TestClient(create_app(tmp_path))
    voice_id = client.post("/api/voices", json={"name": "Synth"}).json()["id"]
    lines = synth_lines(client, voice_id, (118, 122))
    for f0 in (120, 124):
        client.post(f"/api/targets/{lines[0]['id']}/takes", content=wav(voice(f0=f0)))
    takes = sorted((tmp_path / "targets" / lines[0]["id"] / "takes").glob("*.json"))
    assert len(takes) == 2

    # 2. Half a file, as a crash in the middle of an in-place write used to leave.
    takes[0].write_text(takes[0].read_text("utf-8")[:40], "utf-8")
    (tmp_path / "targets" / lines[1]["id"] / "meta.json").write_text("{", "utf-8")
    listed = client.get(f"/api/targets/{lines[0]['id']}/takes").json()
    assert [t["id"] for t in listed] == [takes[1].stem]
    assert [t["id"] for t in client.get("/api/targets").json()] == [lines[0]["id"]]
    voice_lines = client.get(f"/api/voices/{voice_id}").json()["lines"]
    assert [line["id"] for line in voice_lines] == [lines[0]["id"]]
    another = client.post(f"/api/targets/{lines[0]['id']}/takes", content=wav(voice(f0=126)))
    assert another.status_code == 201

    # 3. A write that fails part-way leaves the old file as it was, and nothing half-written
    # beside it.
    meta_path = tmp_path / "voices" / voice_id / "meta.json"
    before = meta_path.read_text("utf-8")
    with pytest.raises(TypeError):
        store_module._write(meta_path, None)
    assert meta_path.read_text("utf-8") == before
    store_module._write(meta_path, before.replace("Synth", "Synth 2"))
    assert client.get(f"/api/voices/{voice_id}").json()["name"] == "Synth 2"
    assert not list(tmp_path.rglob("*.tmp"))


# --- SCORING SAVED WORK AGAIN ---


def test_takes_from_an_earlier_scoring_are_scored_again(tmp_path):
    """When the scoring changes, saved takes and voice checks are worked out again from the
    takes' audio."""
    from echo_voice.scoring import SCORING

    # 1. Three lines with a take each, a trainer attempt and a voice check, all stamped with
    # the scoring that made them.
    client = TestClient(create_app(tmp_path))
    voice_id, lines, takes = voice_with_takes(client)
    assert all(t["scoring"] == SCORING for t in takes)
    # A trainer attempt at the first line's first word.
    first = tmp_path / "targets" / lines[0]["id"]
    time_the_first_word(client, tmp_path, lines[0]["id"])
    attempt = client.post(
        f"/api/targets/{lines[0]['id']}/drill",
        params={"word": 0},
        content=wav(voice(f0=150)[: int(0.45 * SR)]),
    ).json()
    assert attempt["scoring"] == SCORING and attempt["pitch"] is not None
    picked = [{"target": line["id"], "take": t["id"]} for line, t in zip(lines, takes, strict=True)]
    check = client.post(f"/api/voices/{voice_id}/checks", json={"takes": picked}).json()
    assert check["scoring"] == SCORING

    # 2. As an earlier scoring left them: no stamp, and numbers from another scale.
    for line, take in zip(lines, takes, strict=True):
        path = tmp_path / "targets" / line["id"] / "takes" / f"{take['id']}.json"
        old = json.loads(path.read_text("utf-8"))
        del old["scoring"]
        old |= {
            "overall": 1.0,
            "components": [c | {"score": 1.0} for c in old["components"]],
            "coach": {"focus": "pitch", "kept": True},
        }
        path.write_text(json.dumps(old), "utf-8")
    drill_path = first / "drills" / f"{attempt['id']}.json"
    old = json.loads(drill_path.read_text("utf-8")) | {"pitch": 99.0}
    del old["scoring"]
    drill_path.write_text(json.dumps(old), "utf-8")
    check_path = tmp_path / "voices" / voice_id / "checks" / f"{check['id']}.json"
    stale = json.loads(check_path.read_text("utf-8")) | {"average": 1.0}
    del stale["scoring"]
    check_path.write_text(json.dumps(stale), "utf-8")

    # 3. The next start scores them again, in the background.
    client = TestClient(create_app(tmp_path))
    for _ in range(300):
        again = [client.get(f"/api/targets/{p['target']}/takes/{p['take']}").json() for p in picked]
        redone = client.get(f"/api/voices/{voice_id}/checks/{check['id']}").json()
        drills = client.get(f"/api/targets/{lines[0]['id']}/drills").json()
        takes_done = all(a.get("scoring") == SCORING for a in again)
        if takes_done and redone.get("scoring") == SCORING and drills[0].get("scoring") == SCORING:
            break
        time.sleep(0.2)
    else:
        raise AssertionError("the takes were not scored again")

    # 4. They are the same takes, check and attempt, with the numbers worked out again.
    for before, after in zip(takes, again, strict=True):
        assert (after["id"], after["created"]) == (before["id"], before["created"])
        # The same audio under the same scoring.
        assert after["overall"] == pytest.approx(before["overall"], abs=0.5)
        # The advice given then stays as it was.
        assert after["coach"] == {"focus": "pitch", "kept": True}
    assert redone["id"] == check["id"]
    assert redone["average"] == pytest.approx(check["average"], abs=0.5)
    # The trainer's attempt was measured again too, and is still the same attempt.
    assert drills[0]["id"] == attempt["id"]
    assert drills[0]["pitch"] == pytest.approx(attempt["pitch"], abs=0.1)
    assert drills[0]["word"] == 0
    # Each line's best follows its take's new score.
    best = {t["id"]: t["best"] for t in client.get("/api/targets").json()}
    pairs = zip(picked, again, strict=True)
    assert all(best[p["target"]] == pytest.approx(a["overall"]) for p, a in pairs)


def test_re_scoring_carries_on_past_a_take_it_cannot_read(tmp_path):
    """Scoring again carries on past a take whose audio is gone, and leaves that take as it was."""
    from echo_voice.scoring import SCORING

    # 1. Three lines with a take each and a voice check, as an earlier scoring left them.
    client = TestClient(create_app(tmp_path))
    voice_id, lines, takes = voice_with_takes(client)
    picked = [{"target": line["id"], "take": t["id"]} for line, t in zip(lines, takes, strict=True)]
    check = client.post(f"/api/voices/{voice_id}/checks", json={"takes": picked}).json()
    for line, take in zip(lines, takes, strict=True):
        path = tmp_path / "targets" / line["id"] / "takes" / f"{take['id']}.json"
        old = json.loads(path.read_text("utf-8"))
        del old["scoring"]
        path.write_text(json.dumps(old), "utf-8")
    check_path = tmp_path / "voices" / voice_id / "checks" / f"{check['id']}.json"
    old = json.loads(check_path.read_text("utf-8"))
    del old["scoring"]
    check_path.write_text(json.dumps(old), "utf-8")

    # 2. The first take's audio is gone (deleted by hand, or a partial restore).
    (tmp_path / "targets" / lines[0]["id"] / "takes" / f"{takes[0]['id']}.wav").unlink()

    # 3. The next start scores the takes after it again.
    client = TestClient(create_app(tmp_path))
    for _ in range(300):
        last = client.get(f"/api/targets/{lines[2]['id']}/takes/{takes[2]['id']}").json()
        if last.get("scoring") == SCORING:
            break
        time.sleep(0.2)
    else:
        raise AssertionError("the takes after the unreadable one were not scored again")
    middle = client.get(f"/api/targets/{lines[1]['id']}/takes/{takes[1]['id']}").json()
    assert middle["scoring"] == SCORING
    unread = client.get(f"/api/targets/{lines[0]['id']}/takes/{takes[0]['id']}").json()
    assert "scoring" not in unread

    # 4. The voice check comes after the takes: it waits for its unreadable take, and stays as
    # it was.
    time.sleep(1)
    assert "scoring" not in client.get(f"/api/voices/{voice_id}/checks/{check['id']}").json()


# --- OTHER WEBSITES, AND CLEAR ERRORS ---


def test_other_websites_cannot_use_the_server(tmp_path):
    """A page on another site can't reach the API through a name of its own (DNS rebinding), and
    can't make it do anything from the user's browser (a cross-site POST)."""
    # 1. Only this computer's own names are answered.
    client = TestClient(create_app(tmp_path))
    assert client.get("/api/voices", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/api/voices", headers={"host": "127.0.0.1:8000"}).status_code == 200
    assert client.get("/api/voices", headers={"host": "localhost:8000"}).status_code == 200

    # 2. Changes need to come from Echo's own page, or from no page at all (a script).
    other = client.post(
        "/api/voices", json={"name": "x"}, headers={"origin": "https://evil.example"}
    )
    assert other.status_code == 403 and client.get("/api/voices").json() == []
    cross_site = client.post(
        "/api/voices", json={"name": "x"}, headers={"sec-fetch-site": "cross-site"}
    )
    assert cross_site.status_code == 403
    own_page = client.post(
        "/api/voices", json={"name": "x"}, headers={"origin": "http://testserver"}
    )
    assert own_page.status_code == 201
    assert client.post("/api/voices", json={"name": "y"}).status_code == 201

    # 3. An upload over the limit is turned down before it is read, and an empty file is a
    # clear error.
    big = client.post(
        "/api/targets", content=b"0" * 16, headers={"content-length": str(64 * 1024 * 1024)}
    )
    assert big.status_code == 413
    empty = client.post("/api/targets", content=wav(np.zeros(0, dtype=np.float32)))
    assert empty.status_code == 422

    # 4. Listening beyond this computer, any name is answered.
    open_app = TestClient(create_app(tmp_path / "open", hosts=None))
    assert open_app.get("/api/voices", headers={"host": "192.168.1.5:8000"}).status_code == 200


def test_a_name_typed_in_is_kept_and_bad_numbers_are_a_clear_error(tmp_path):
    """A name typed in ends the wait for a video's title, and a count that isn't one is a 422."""
    from echo_voice.store import Store

    client = TestClient(create_app(tmp_path))
    voice = client.post("/api/voices", json={"name": "New voice"}).json()
    # As a link's voice is, until its video is known.
    Store(tmp_path).update_voice(voice["id"], name_pending=True)
    renamed = client.patch(f"/api/voices/{voice['id']}", json={"name": "Mine"}).json()
    assert renamed["name"] == "Mine"
    assert Store(tmp_path).voice_meta(voice["id"])["name_pending"] is False
    autopick = client.post(f"/api/voices/{voice['id']}/autopick", json={"want": "five"})
    assert autopick.status_code == 422
