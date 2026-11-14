"""Drive the real page in headless Edge or Chrome, with the microphone fed from a WAV.

Skipped when Playwright isn't installed (`pip install -e ".[e2e]"`) or neither
browser is on the machine.
"""

import socket
import threading
import time

import pytest
import soundfile as sf
import uvicorn
from synth import SR, voice

from voicematch.server import create_app

sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def server(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(
        uvicorn.Config(create_app(tmp_path / "data"), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def page(tmp_path):
    mic = tmp_path / "mic.wav"
    sf.write(mic, voice(f0=140), SR, subtype="PCM_16")  # the browser loops this as the microphone
    args = [
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        f"--use-file-for-fake-audio-capture={mic}",
    ]
    with sync_api.sync_playwright() as p:
        browser = None
        for channel in ("msedge", "chrome"):
            try:
                browser = p.chromium.launch(channel=channel, headless=True, args=args)
                break
            except Exception:
                continue
        if browser is None:
            pytest.skip("neither Edge nor Chrome is installed")
        context = browser.new_context(permissions=["microphone"])
        page = context.new_page()
        page.errors = []
        page.on("pageerror", lambda exc: page.errors.append(str(exc)))
        page.on("console", lambda msg: msg.type == "error" and page.errors.append(msg.text))
        page.on("response", lambda r: r.status >= 400 and page.errors.append(f"{r.status} {r.url}"))
        yield page
        browser.close()


def record(page, button: str, seconds: float = 2.5) -> None:
    page.click(button)
    page.wait_for_function("document.querySelector('#status').textContent.startsWith('Recording')")
    page.wait_for_timeout(seconds * 1000)
    page.click(button)


def test_upload_target_record_takes_and_delete(server, page, tmp_path):
    clip = tmp_path / "target.wav"
    sf.write(clip, voice(), SR, subtype="PCM_16")

    page.goto(server)
    page.wait_for_selector("#empty:not([hidden])")

    page.fill("#new-name", "Synthetic")
    page.set_input_files("#new-file", str(clip))
    page.wait_for_selector("#practice:not([hidden])")
    assert page.text_content("#target-name") == "Synthetic"

    record(page, "#record")
    page.wait_for_selector("#result:not([hidden])", timeout=20000)
    first = int(page.text_content("#overall"))
    assert 0 <= first <= 100
    assert page.locator("#components li").count() == 5
    assert "baseline" in page.text_content("#score-note")

    record(page, "#record")
    page.wait_for_selector("#history:not([hidden])", timeout=20000)
    assert page.locator("#takes button").count() == 2

    # Reloading brings the same target and its latest take back.
    page.reload()
    page.wait_for_selector("#result:not([hidden])")
    assert page.locator("#takes button").count() == 2

    page.on("dialog", lambda dialog: dialog.accept())
    page.click("#delete-target")
    page.wait_for_selector("#empty:not([hidden])")
    assert page.errors == []


def test_record_a_target_from_the_microphone(server, page):
    page.goto(server)
    page.click("#new-record")
    assert "Name the clip first" in page.text_content("#status")

    page.fill("#new-name", "From mic")
    record(page, "#new-record")
    page.wait_for_selector("#practice:not([hidden])", timeout=20000)
    assert page.text_content("#target-name") == "From mic"
    assert page.errors == []
