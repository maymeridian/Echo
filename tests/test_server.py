import io

import soundfile as sf
from fastapi.testclient import TestClient
from synth import SR, voice

from voicematch.server import create_app


def wav(samples) -> bytes:
    buffer = io.BytesIO()
    sf.write(buffer, samples, SR, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def test_add_target_record_take_and_read_it_back(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert client.get("/").status_code == 200

    target = client.post("/api/targets", params={"name": "Test voice"}, content=wav(voice())).json()
    assert target["name"] == "Test voice"
    assert client.get(f"/api/targets/{target['id']}/audio").headers["content-type"] == "audio/wav"

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
    assert client.get("/api/targets/..%2F..%2Fx/audio").status_code == 404
