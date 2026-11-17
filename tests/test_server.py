import io

import numpy as np
import pytest

import soundfile as sf
from fastapi.testclient import TestClient
from synth import SR, voice

from echo_voice.server import create_app


def wav(samples) -> bytes:
    buffer = io.BytesIO()
    sf.write(buffer, samples, SR, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def test_add_target_record_take_and_read_it_back(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert client.get("/").status_code == 200
    # The page is rechecked on every load, so an update shows up without a forced reload.
    assert client.get("/app.js").headers["cache-control"] == "no-cache"

    target = client.post("/api/targets", params={"name": "Test voice"}, content=wav(voice())).json()
    assert target["name"] == "Test voice"
    assert client.get(f"/api/targets/{target['id']}/audio").headers["content-type"] == "audio/wav"

    profile = client.get(f"/api/targets/{target['id']}/profile").json()
    assert 110 < profile["median_f0"] < 130 and profile["low"] < profile["median_f0"] < profile["high"]

    take = client.post(f"/api/targets/{target['id']}/takes", content=wav(voice(f0=150))).json()
    assert 0 < take["overall"] < 100

    listed = client.get(f"/api/targets/{target['id']}/takes").json()
    assert [t["id"] for t in listed] == [take["id"]]
    assert client.get(f"/api/targets/{target['id']}/takes/{take['id']}").json() == take

    edited = client.patch(f"/api/targets/{target['id']}", json={"line": "  say this line  "}).json()
    assert edited["words"] == ["say", "this", "line"]
    assert client.get("/api/targets").json()[0]["words"] == ["say", "this", "line"]

    assert client.delete(f"/api/targets/{target['id']}").status_code == 204
    assert client.get("/api/targets").json() == []


def test_bad_audio_and_unknown_ids_are_reported(tmp_path):
    client = TestClient(create_app(tmp_path))
    bad = client.post("/api/targets", content=b"not audio")
    assert bad.status_code == 422
    assert bad.json()["detail"] == "Couldn't read that audio."
    assert client.post("/api/targets/nope/takes", content=wav(voice())).status_code == 404
    too_long = client.post("/api/targets", content=wav(np.tile(voice(), 25)))  # about 45 s
    assert too_long.status_code == 422 and "keep it under" in too_long.json()["detail"]
    assert client.get("/api/targets/..%2F..%2Fx/audio").status_code == 404


def test_a_long_recording_is_split_into_lines_by_a_background_job(tmp_path):
    import time

    from synth import compilation

    client = TestClient(create_app(tmp_path))
    meta = client.post(
        "/api/sources", params={"name": "", "filename": "voice lines.wav"}, content=wav(compilation())
    ).json()
    assert meta["name"] == "voice lines" and meta["status"] == "queued"
    deadline = time.time() + 120
    while (source := client.get(f"/api/sources/{meta['id']}").json())["status"] not in ("ready", "failed"):
        assert time.time() < deadline, source
        time.sleep(0.5)
    assert source["status"] == "ready", source
    # Whisper on the CPU sometimes ends a sentence at "Tom" in bdl's a0002, so 5 to 7 lines.
    n = len(source["lines"])
    assert 5 <= n <= 7, source["lines"]
    assert ["other voice" in r["flags"] for r in source["lines"]] == [False] * (n - 1) + [True]
    assert source["suggested"] and 4 not in source["suggested"]  # never the other voice
    assert 80 < source["summary"]["pitch"] < 160  # bdl, a man
    assert client.get("/api/sources").json()[0]["count"] == n

    # The voice (named after the file) got the recording's best lines by itself:
    # bdl's four, not slt's, another voice.
    voice = client.get(f"/api/voices/{source['voice_id']}").json()
    assert voice["name"] == "voice lines" and voice["line_count"] == 4
    assert {line["source"] for line in voice["lines"]} == {meta["id"]}
    assert voice["suggestions"] == [] and voice["check_set"] == [line["id"] for line in voice["lines"]]
    assert client.post(f"/api/voices/{voice['id']}/autopick", json={}).json()["added"] == []  # nothing clear left
    slt = source["lines"][-1]
    added = client.post(f"/api/voices/{voice['id']}/lines", json={"source": meta["id"], "start": slt["start"]})
    assert added.status_code == 201 and added.json()["line_start"] == slt["start"] and added.json()["voice_id"] == voice["id"]
    assert client.post(f"/api/voices/{voice['id']}/lines", json={"source": meta["id"], "start": 999}).status_code == 404

    line = source["lines"][0]
    audio = client.get(f"/api/sources/{meta['id']}/audio", params={"start": line["start"], "end": line["end"]})
    assert audio.headers["content-type"] == "audio/wav"
    assert len(sf.read(io.BytesIO(audio.content))[0]) == pytest.approx((line["end"] - line["start"]) * SR, abs=2)
    assert client.get(f"/api/sources/{meta['id']}/audio", params={"start": 0, "end": 500}).status_code == 422

    assert client.delete(f"/api/sources/{meta['id']}").status_code == 204
    assert client.get("/api/sources").json() == []


def test_bad_links_and_unknown_recordings_are_reported(tmp_path):
    client = TestClient(create_app(tmp_path))
    bad = client.post("/api/sources/link", json={"url": "not a link"})
    assert bad.status_code == 422 and "https://" in bad.json()["detail"]
    assert client.get("/api/sources/nope").status_code == 404
    assert client.get("/api/sources/..%2F..%2Fx/audio", params={"start": 0, "end": 1}).status_code == 404


def test_takes_get_word_by_word_feedback_and_a_coach(tmp_path):
    from synth import ARCTIC

    client = TestClient(create_app(tmp_path))
    target = client.post("/api/targets", params={"name": "bdl"}, content=(ARCTIC / "bdl_arctic_a0001.wav").read_bytes()).json()
    assert target["word_times"] and target["takes"] == 0 and target["best"] is None

    guide = client.get(f"/api/targets/{target['id']}/profile").json()
    assert guide["duration"] > 2 and 0 <= guide["onset"] < 0.5
    assert len(guide["contour"]["t"]) == len(guide["contour"]["f0"]) > 50
    assert [w["text"] for w in guide["words"]][:2] == ["Author", "of the"]  # run together, so one unit

    first = client.post(f"/api/targets/{target['id']}/takes", content=(ARCTIC / "rms_arctic_a0001.wav").read_bytes()).json()
    assert len(first["words"]) >= 6  # rms says the same sentence
    word = first["words"][0]
    assert word["text"] == "Author" and word["take_start"] < word["take_end"] and word["scores"]["articulation"] is not None
    assert first["coach"]["focus"] in ("pitch", "melody", "resonance", "articulation", "rhythm")
    assert first["coach"]["message"] and first["coach"]["progress"] is None

    second = client.post(
        f"/api/targets/{target['id']}/takes", params={"focus": "melody"}, content=(ARCTIC / "rms_arctic_a0001.wav").read_bytes()
    ).json()
    assert second["coach"]["focus"] == "melody" and second["coach"]["why"] == "chosen"
    assert second["coach"]["progress"]["key"] == "melody"  # what this take worked on

    # Training one word: bdl's "Danger", said by bdl himself, matches; said by rms, it's darker and lower.
    import soundfile as sf_

    k = next(i for i, w in enumerate(guide["words"]) if w["text"].lower().startswith("danger"))
    w = guide["words"][k]
    own, rate = sf_.read(ARCTIC / "bdl_arctic_a0001.wav", dtype="float32")
    piece = own[int((w["start"] - 0.05) * rate) : int((w["end"] + 0.05) * rate)]
    mine = client.post(f"/api/targets/{target['id']}/drill", params={"word": k}, content=wav(piece)).json()
    assert mine["text"].lower().startswith("danger") and mine["scores"]["resonance"] > 90 and mine["scores"]["pitch"] > 90
    assert client.post(f"/api/targets/{target['id']}/drill", params={"word": 99}, content=wav(piece)).status_code == 404

    takes = client.get(f"/api/targets/{target['id']}/takes").json()
    assert set(takes[0]["parts"]) == {"identity", "pitch", "melody", "resonance", "articulation", "rhythm", "gender"}
    assert [t["focus"] for t in takes] == [first["coach"]["focus"], "melody"]
    listed = client.get("/api/targets").json()[0]
    assert listed["takes"] == 2 and listed["best"] == max(first["overall"], second["overall"])
    assert listed["last"] == second["created"] and listed["focus"] == "melody"  # for "Continue"
    assert second["measures"]["take_f0"] < second["measures"]["target_f0"]  # rms is lower than bdl
    assert second["coach"]["plateau"] is False


def test_a_line_from_a_recording_remembers_where_it_came_from(tmp_path):
    from echo_voice.store import Store

    store = Store(tmp_path)
    recording, _ = store.new_source("Lighthouse", status="failed")  # no job needed for this
    client = TestClient(create_app(tmp_path))
    target = client.post(
        "/api/targets", params={"name": "Lighthouse: Hello", "source": recording["id"], "line_start": 12.5}, content=wav(voice())
    ).json()
    assert (target["source"], target["voice"], target["line_start"]) == (recording["id"], "Lighthouse", 12.5)
    gone = client.post("/api/targets", params={"name": "x", "source": "deadbeef0000"}, content=wav(voice())).json()
    assert gone["source"] is None and gone["line_start"] is None


def test_a_voice_gathers_lines_and_scores_them_together_in_a_voice_check(tmp_path):
    from synth import ARCTIC

    client = TestClient(create_app(tmp_path))
    assert client.post("/api/voices", json={"name": "  "}).status_code == 422
    voice_id = client.post("/api/voices", json={"name": "bdl"}).json()["id"]
    lines = [
        client.post("/api/targets", params={"name": name, "voice": voice_id}, content=(ARCTIC / f"{name}.wav").read_bytes()).json()
        for name in ("bdl_arctic_a0001", "bdl_arctic_a0002", "rms_arctic_a0001")
    ]
    assert {line["voice_id"] for line in lines} == {voice_id}

    voice = client.get(f"/api/voices/{voice_id}").json()
    assert voice["line_count"] == 3 and voice["score"] is None and voice["check_set"] == [line["id"] for line in lines]
    assert 80 < voice["summary"]["pitch"] < 160 and voice["you"] is None  # from its lines' pitch: two men
    assert voice["gender"] < 25  # reads male

    # One take on each line, as the page does in a voice check.
    take = lambda line: client.post(  # noqa: E731
        f"/api/targets/{line['id']}/takes", content=(ARCTIC / "slt_arctic_a0001.wav").read_bytes()
    ).json()
    taken = [take(line) for line in lines]
    too_few = client.post(f"/api/voices/{voice_id}/checks", json={"takes": [{"target": lines[0]["id"], "take": taken[0]["id"]}]})
    assert too_few.status_code == 422 and "at least 3" in too_few.json()["detail"]
    check = client.post(
        f"/api/voices/{voice_id}/checks",
        json={"takes": [{"target": line["id"], "take": t["id"]} for line, t in zip(lines, taken)]},
    ).json()
    overalls = [t["overall"] for t in taken]
    assert check["average"] == pytest.approx(sum(overalls) / 3, abs=0.06)
    assert check["low"] == min(overalls) and check["high"] == max(overalls)
    assert set(check["parts"]) == {"identity", "pitch", "melody", "resonance", "articulation", "rhythm"}
    assert check["habits"]["pitch"] > 3  # slt, a woman, against two men: higher in every line
    assert check["weakest"] in {line["id"] for line in lines} and len(check["lines"]) == 3

    voice = client.get(f"/api/voices/{voice_id}").json()
    assert voice["score"] == check["average"] and len(voice["checks"]) == 1
    assert voice["you"]["takes"] == 3 and voice["you"]["pitch"] > 3
    assert client.get(f"/api/voices/{voice_id}/checks/{check['id']}").json()["average"] == check["average"]
    assert client.get("/api/voices").json()[0]["score"] == check["average"]

    edited = client.patch(f"/api/voices/{voice_id}", json={"name": "Brad", "check_lines": [lines[2]["id"], "nope"]}).json()
    assert edited["name"] == "Brad" and edited["check_set"] == [lines[2]["id"]]

    assert client.delete(f"/api/voices/{voice_id}").status_code == 204
    assert client.get("/api/voices").json() == [] and client.get("/api/targets").json() == []


def test_lines_and_recordings_from_before_voices_get_one(tmp_path):
    import json

    from echo_voice.store import Store

    store = Store(tmp_path)
    recording, _ = store.new_source("Lighthouse", status="ready")
    client = TestClient(create_app(tmp_path))  # migrates
    lighthouse = client.get("/api/voices").json()[0]
    assert lighthouse["name"] == "Lighthouse"
    assert store.source_meta(recording["id"])["voice_id"] == lighthouse["id"]

    # A line saved before voices existed, named "<recording>: <words>".
    line = client.post("/api/targets", params={"name": "Lighthouse: Hello there"}, content=wav(voice())).json()
    path = tmp_path / "targets" / line["id"] / "meta.json"
    meta = json.loads(path.read_text("utf-8"))
    meta.pop("voice_id")
    path.write_text(json.dumps(meta), "utf-8")
    Store(tmp_path).migrate()
    assert json.loads(path.read_text("utf-8"))["voice_id"] == lighthouse["id"]


def wait_ready(client, source_id, seconds=180):
    import time

    deadline = time.time() + seconds
    while (source := client.get(f"/api/sources/{source_id}").json())["status"] not in ("ready", "failed"):
        assert time.time() < deadline, source
        time.sleep(0.5)
    assert source["status"] == "ready", source
    return source


def test_a_recording_with_two_speakers_can_be_either_voice(tmp_path):
    from synth import compilation

    client = TestClient(create_app(tmp_path))
    voice_id = client.post("/api/voices", json={"name": "Two"}).json()["id"]
    two = ("bdl_arctic_a0001", "bdl_arctic_a0002") * 2 + ("slt_arctic_a0001",) * 3
    meta = client.post(
        "/api/sources", params={"filename": "two.wav", "voice": voice_id}, content=wav(compilation(clips=two))
    ).json()
    source = wait_ready(client, meta["id"])
    assert [s["lines"] for s in source["speakers"]][1:] == [3] and source["speaker"] == 0
    assert all("other voice" in r["flags"] for r in source["lines"][-3:])  # slt, not the voice's speaker

    voice = client.get(f"/api/voices/{voice_id}").json()
    assert voice["line_count"] == 4 and all(line["auto"] for line in voice["lines"])  # bdl's, picked by Echo
    assert len(voice["recordings"][0]["speakers"]) == 2 and voice["recordings"][0]["speakers"][1]["sample"]["text"]

    # Switch the voice to slt: Echo's untouched picks from bdl give way to hers.
    switched = client.patch(f"/api/sources/{meta['id']}", json={"speaker": 1}).json()
    assert switched["removed"] == 4 and len(switched["added"]) == 3
    source = client.get(f"/api/sources/{meta['id']}").json()
    assert source["speaker"] == 1 and not any("other voice" in r["flags"] for r in source["lines"][-3:])
    assert client.patch(f"/api/sources/{meta['id']}", json={"speaker": 7}).status_code == 422

    # And bdl as a voice of his own.
    split = client.post(f"/api/sources/{meta['id']}/split", json={"speaker": 0, "name": "Brad"}).json()
    assert split["voice"]["name"] == "Brad" and len(split["added"]) == 4
    brad = client.get(f"/api/voices/{split['voice']['id']}").json()
    assert brad["recordings"][0]["speaker"] == 0 and brad["line_count"] == 4


def test_a_pasted_link_makes_a_voice_named_after_the_video(tmp_path):
    import functools
    import http.server
    import socketserver
    import threading

    from synth import compilation

    sf.write(tmp_path / "Voice lines.wav", compilation(), SR, subtype="PCM_16")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *args: None
    web = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    try:
        client = TestClient(create_app(tmp_path / "data"))
        meta = client.post("/api/sources/link", json={"url": f"http://127.0.0.1:{web.server_address[1]}/Voice%20lines.wav"}).json()
        voice = client.get(f"/api/voices/{meta['voice_id']}").json()
        assert voice["name"] == "New voice" and voice["name_pending"]  # until the title is known
        wait_ready(client, meta["id"])
        voice = client.get(f"/api/voices/{meta['voice_id']}").json()
        assert voice["name"] == "Voice lines" and not voice["name_pending"] and voice["named_from"] == "title"
        renamed = client.patch(f"/api/voices/{voice['id']}", json={"name": "Sally"}).json()
        assert renamed["name"] == "Sally" and renamed["named_from"] == "hand"
        assert voice["recordings"][0]["link"].endswith("/Voice%20lines.wav")  # the link stays with the voice

        # A recording uploaded as a file can be given the link it came from.
        upload = client.post(
            "/api/sources", params={"filename": "clip.wav", "voice": voice["id"]}, content=wav(compilation())
        ).json()
        url = f"http://127.0.0.1:{web.server_address[1]}/Voice%20lines.wav"
        linked = client.patch(f"/api/sources/{upload['id']}", json={"link": url}).json()["recording"]
        assert linked["link"] == url and linked["title"] == "Voice lines"
        assert client.patch(f"/api/sources/{upload['id']}", json={"link": "not a link"}).status_code == 422
        wait_ready(client, upload["id"])
        assert voice["line_count"] == 4  # its best lines, picked
    finally:
        web.shutdown()



def test_trainer_attempts_are_kept_and_progress_gathers_every_take(tmp_path):
    client = TestClient(create_app(tmp_path))
    voice_id = client.post("/api/voices", json={"name": "Synth"}).json()["id"]
    lines = [
        client.post("/api/targets", params={"name": f"line {i}", "voice": voice_id}, content=wav(voice(f0=f0))).json()
        for i, f0 in enumerate((120, 122, 118, 240))
    ]
    voice_page = client.get(f"/api/voices/{voice_id}").json()
    # The last line sits an octave above the voice's usual pitch: not to be trusted, nor checked.
    assert [line["pitch_suspect"] for line in voice_page["lines"]] == [False, False, False, True]
    assert lines[3]["id"] not in voice_page["check_set"]
    assert client.get(f"/api/targets/{lines[3]['id']}/profile").json()["pitch_suspect"] is True
    odd = client.post(f"/api/targets/{lines[3]['id']}/takes", content=wav(voice(f0=120))).json()
    assert {c["key"]: c["score"] for c in odd["components"]}["pitch"] is None

    # Trainer attempts are saved with their audio.
    import numpy as np

    word = {"text": "ah", "start": 0.0, "end": 0.4}
    client.patch(f"/api/targets/{lines[0]['id']}", json={"line": "ah ee oo eh"})
    import json as json_

    meta_path = tmp_path / "targets" / lines[0]["id"] / "meta.json"
    meta = json_.loads(meta_path.read_text("utf-8"))
    meta["word_times"] = [word | {"refined": True}]
    meta_path.write_text(json_.dumps(meta), "utf-8")
    attempt = client.post(f"/api/targets/{lines[0]['id']}/drill", params={"word": 0}, content=wav(voice()[: int(0.45 * SR)])).json()
    assert attempt["id"] and attempt["word"] == 0 and attempt["scores"]["resonance"] > 90
    kept = client.get(f"/api/targets/{lines[0]['id']}/drills").json()
    assert [a["id"] for a in kept] == [attempt["id"]]
    assert client.get(f"/api/targets/{lines[0]['id']}/drills/{attempt['id']}/audio").headers["content-type"] == "audio/wav"

    for line in lines[:2]:
        client.post(f"/api/targets/{line['id']}/takes", content=wav(voice(f0=130)))
    progress = client.get(f"/api/voices/{voice_id}/progress").json()
    assert len(progress["takes"]) == 3 and progress["drills"] == 1 and len(progress["days"]) == 1
    assert all("parts" in t and t["line"] for t in progress["takes"])
    created = [t["created"] for t in progress["takes"]]
    assert created == sorted(created)  # oldest first
