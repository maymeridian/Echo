"""Drive the real page in headless Edge or Chrome, with the microphone fed from a WAV.

Skipped when Playwright isn't installed (`pip install -e ".[e2e]"`) or neither
browser is on the machine.
"""

import socket
import threading
import time

import httpx
import numpy as np
import pytest
import soundfile as sf
import uvicorn
from synth import ARCTIC, SR, compilation, voice

from echo_voice.server import create_app

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
        # Confirm every question; a prompt gets its suggested answer.
        page.on("dialog", lambda dialog: dialog.accept(dialog.default_value) if dialog.type == "prompt" else dialog.accept())
        yield page
        browser.close()


def record(page, button: str, seconds: float = 2.5, check_live: bool = False) -> None:
    page.click(button)
    page.wait_for_function("document.querySelector('#status').textContent.startsWith('Recording')")
    page.wait_for_timeout(seconds * 1000)
    if check_live:  # the fake mic plays a 140 Hz voice; the target sits at 120 Hz
        text = page.text_content("#live-text")
        assert "You: 1" in text and "target about 12" in text and "go lower" in text, text
    page.click(button)


def make_voice(page, server, name: str) -> None:
    """From a fresh start: the first-voice form, then the voice's page."""
    page.goto(server)
    page.wait_for_selector("#empty:not([hidden])")
    page.fill("#first-voice-name", name)
    page.click("#first-voice-form button[type=submit]")
    page.wait_for_selector("#voice:not([hidden])")
    assert page.text_content("#voice-name") == name


def test_make_a_voice_add_a_clip_and_practise_it(server, page, tmp_path):
    clip = tmp_path / "target.wav"
    sf.write(clip, voice(), SR, subtype="PCM_16")

    make_voice(page, server, "Synthetic")
    assert page.text_content("#voices .voice-btn .name") == "Synthetic"
    assert "No voice score yet" in page.text_content("#score-card")
    assert page.is_disabled("#run-check")  # it needs three lines
    assert "Add 3 more lines" in page.text_content("#score-card")

    page.set_input_files("#new-file", str(clip))
    page.wait_for_selector("#trim:not([hidden])")
    page.click("#trim-use")
    page.wait_for_selector("#practice:not([hidden])", timeout=60000)
    assert page.text_content("#crumb") == "‹ Synthetic"
    assert not page.is_visible("#line-form") and not page.is_visible("#result")

    record(page, "#record", check_live=True)
    page.wait_for_selector("#result:not([hidden])", timeout=20000)
    assert not page.is_visible("#live")
    first = int(page.text_content("#overall"))
    assert 0 <= first <= 100
    assert page.locator("#components li").count() == 7  # six parts of the score, and gender read
    assert "Gender read" in page.text_content("#components li.extra")
    assert "baseline" in page.text_content("#score-note")
    # The coach names one thing to work on; it's highlighted among the parts.
    assert page.text_content("#coach-focus").startswith("Work on ")
    assert page.locator("#components li.focus").count() == 1

    # Pick the focus by hand: the next take reports on it.
    page.click("#focus-chips button:has-text('Melody')")
    assert page.text_content("#coach-focus") == "Work on melody (your pick)"
    record(page, "#record")
    page.wait_for_selector("#history:not([hidden])", timeout=20000)
    assert page.locator("#takes button").count() == 2
    assert "worked on melody" in page.text_content("#coach-progress")
    assert page.locator("#components .delta").first.text_content() != ""  # change since the last take
    page.click("#focus-chips button:has-text('Auto')")

    # Reloading brings the same line and its latest take back.
    page.reload()
    page.wait_for_selector("#result:not([hidden])")
    assert page.locator("#takes button").count() == 2

    # Correct the words by hand; they survive a reload.
    page.click("#edit-line")
    page.fill("#line-input", "a made up line")
    page.click("#line-form button[type=submit]")
    page.wait_for_function("document.querySelector('#target-line').textContent.includes('a made up line')")
    page.reload()
    page.wait_for_function("document.querySelector('#target-line').textContent.includes('a made up line')")

    # Back on the voice's page, the line is listed with its scores.
    page.click("#crumb")
    page.wait_for_selector("#voice:not([hidden])")
    assert "a made up line" in page.text_content("#voice-lines .vline")
    assert "2 takes" in page.text_content("#voice-lines .vline small")
    page.click("#voice-lines .vline .practise")
    page.wait_for_selector("#practice:not([hidden])")
    page.click("#delete-target")
    page.wait_for_selector("#voice:not([hidden])")
    assert "No lines yet" in page.text_content("#voice-lines")
    assert page.errors == []


def test_a_long_clip_is_trimmed_to_the_line(server, page, tmp_path):
    clip = tmp_path / "long.wav"
    sf.write(clip, np.tile(voice(), 25), SR, subtype="PCM_16")  # about 45 s, over the limit but under a minute

    make_voice(page, server, "Long clip")
    page.set_input_files("#new-file", str(clip))
    page.wait_for_selector("#trim:not([hidden])")
    assert page.eval_on_selector("#trim-end", "e => Number(e.value)") == 30  # starts at the limit
    page.eval_on_selector("#trim-end", "e => { e.value = 40; e.dispatchEvent(new Event('input')); }")
    assert page.is_disabled("#trim-use")  # 40 s is too long
    page.eval_on_selector("#trim-start", "e => { e.value = 2; e.dispatchEvent(new Event('input')); }")
    page.eval_on_selector("#trim-end", "e => { e.value = 8; e.dispatchEvent(new Event('input')); }")
    assert "6.0 s selected" in page.text_content("#trim-len")
    page.click("#trim-use")
    page.wait_for_selector("#practice:not([hidden])", timeout=60000)
    duration = page.evaluate("fetch('/api/targets').then(r => r.json()).then(t => t[0].duration)")
    assert 4 < duration <= 6.05  # trimmed of silence at the edges too
    assert page.errors == []


def test_record_a_clip_from_the_microphone(server, page):
    make_voice(page, server, "From mic")
    record(page, "#new-record")
    page.wait_for_selector("#trim:not([hidden])", timeout=20000)
    page.click("#trim-use")
    page.wait_for_selector("#practice:not([hidden])", timeout=60000)
    assert page.text_content("#crumb") == "‹ From mic"
    assert page.errors == []


def test_a_long_recording_becomes_lines_to_add_to_the_voice(server, page, tmp_path):
    path = tmp_path / "Voice lines.wav"
    sf.write(path, compilation(tail=50.0), SR, subtype="PCM_16")  # about 72 s: over a minute

    make_voice(page, server, "Brad")
    page.set_input_files("#new-file", str(path))
    page.wait_for_selector("#recordings .recording")
    assert "Voice lines" in page.text_content("#recordings")
    page.wait_for_selector("#recordings .btn:not([disabled])", timeout=180000)
    # Its best lines were added by themselves: bdl's four, enough for a voice check.
    page.wait_for_function("document.querySelectorAll('#voice-lines .vline').length === 4")
    found = page.evaluate("fetch('/api/sources').then(r => r.json()).then(s => s[0].count)")
    assert 5 <= found <= 7  # Whisper sometimes ends a sentence at "Tom" in bdl's a0002
    assert f"{found} lines found · 4 added" in page.text_content("#recordings")
    assert page.locator("#voice-lines .vline input:checked").count() == 4 and page.is_enabled("#run-check")
    assert "Every clear line" in page.text_content("#suggestions")  # slt's is another voice
    assert page.is_hidden("#autopick")
    page.click("#recordings .btn")
    page.wait_for_selector("#source:not([hidden]) #lines .line-item")
    assert page.text_content("#source-crumb") == "‹ Brad"
    assert f"{found} lines in 1:12" in page.text_content("#source-status")
    assert 1 <= page.locator("#suggested .line-item").count() <= 4  # never slt's line, another voice
    assert page.locator("#lines .line-item").count() == found - 1  # slt's line is hidden as another voice
    page.uncheck("#only-good")
    assert page.locator("#lines .line-item").count() == found
    assert "other voice" in page.text_content("#lines .line-item:last-child .tag")
    page.fill("#line-search", "danger trail")
    assert page.locator("#lines .line-item").count() == 3  # bdl reads a0001 twice, slt once
    page.fill("#line-search", "")

    page.click("#lines .line-item:first-child .play")
    page.wait_for_function("document.querySelector('#lines .play').textContent === '▶'", timeout=10000)

    page.uncheck("#only-good")
    assert page.text_content("#lines .line-item:last-child .practise") == "Add"  # slt's, not added
    page.click("#lines .line-item:last-child .practise")
    page.wait_for_selector("#trim:not([hidden])")
    start = page.eval_on_selector("#trim-start", "e => Number(e.value)")
    assert start == 1.5  # the line, with context either side to move its ends into
    page.click("#trim-cancel")
    page.wait_for_selector("#source:not([hidden])")  # back to the list
    page.check("#only-good")

    assert page.text_content("#lines .line-item:first-child .practise") == "Open"  # added already
    page.click("#lines .line-item:first-child .practise")
    page.wait_for_selector("#practice:not([hidden])", timeout=60000)
    assert page.text_content("#crumb") == "‹ Brad"
    assert "danger trail" in page.text_content("#target-line").lower()

    # Before the first take, a checklist: listen, find the note, record.
    assert page.locator("#checklist li").count() == 3
    assert page.get_attribute("#checklist li:nth-child(1)", "class") == "now"
    for _ in range(2):
        page.click("#checklist li:nth-child(1) [data-action=listen]")
        page.wait_for_selector("#play-target.on")
        page.click("#play-target")  # stop it
    page.wait_for_selector("#checklist li:nth-child(1).done")
    assert page.get_attribute("#checklist li:nth-child(2)", "class") == "now"
    page.click("#checklist li:nth-child(2) [data-action=note]")
    page.wait_for_selector("#checklist li:nth-child(3).now")

    # Before a take, the words are there to hear; after it, each is coloured.
    page.wait_for_selector("#words:not([hidden]) .word")
    record(page, "#record")
    page.wait_for_selector("#result:not([hidden])", timeout=30000)
    words = page.locator("#words .word")
    assert words.count() >= 6
    assert page.locator("#words .word.good, #words .word.mid, #words .word.poor").count() == words.count()
    words.nth(3).click()
    page.wait_for_selector("#word-note:not([hidden])")
    assert page.text_content("#word-note").startswith("“")
    page.click("#play-both")
    page.wait_for_selector("#play-both.on")
    page.click("#play-both")  # a second click stops it
    page.wait_for_selector("#play-both:not(.on)")
    # Train one part on its own: resonance, word by word.
    page.click("#components li:has-text('Resonance') .train-btn")
    page.wait_for_selector("#train:not([hidden])")
    assert page.text_content("#train-title").startswith("Resonance on")
    assert page.locator("#train-words .word").count() >= 6 and page.locator("#train-tips li").count() >= 3
    page.click("#train-listen")
    page.click("#train-say")
    page.wait_for_timeout(1500)
    page.click("#train-say")
    page.wait_for_selector("#train-attempts li", timeout=20000)
    assert page.text_content("#train-result") != ""
    assert page.locator("#train-map .map-dot").count() == 1  # placed on pitch and resonance at once
    # The attempt is kept: after a reload, the trainer picks up where it was.
    page.reload()
    page.wait_for_selector("#practice:not([hidden]) #result:not([hidden])")
    page.click("#components li:has-text('Resonance') .train-btn")
    page.wait_for_selector("#train-attempts li")
    assert page.is_enabled("#train-mine")  # your last attempt, to hear again
    # Pitch and resonance together: the same attempt, judged on both.
    page.click("#train-modes button[data-part=both]")
    assert page.text_content("#train-title").startswith("Pitch and resonance")
    assert page.locator("#train-attempts li").count() == 1 and page.locator("#train-tips li").count() >= 1
    page.click("#train-back")
    page.wait_for_selector("#practice:not([hidden])")
    # The next take is about the weaker of the two.
    pressed = page.locator("#focus-chips button[aria-pressed=true]").text_content()
    assert pressed in ("Pitch", "Resonance")
    page.click("#focus-chips button:has-text('Auto')")

    # After the take: a drill for the focus, and the scale from a stranger to the real person.
    assert page.is_hidden("#checklist") and page.is_visible("#drill")
    assert page.locator("#drill-actions button").count() >= 1
    assert page.is_visible("#ladder-now") and page.is_hidden("#ladder-best")  # no earlier best yet

    # The fake mic says the same thing every time: after four takes the line has
    # stopped improving, and the coach offers a fresh one.
    for n in (2, 3, 4):
        record(page, "#record")
        page.wait_for_function(f"document.querySelectorAll('#takes button').length === {n}", timeout=30000)
    page.wait_for_selector("#plateau:not([hidden])")
    assert page.is_visible("#ladder-best")
    page.click("#plateau-actions button:has-text('Back to Brad')")

    # The voice's page: its lines, and the habits across its takes.
    page.wait_for_selector("#voice:not([hidden])")
    assert page.locator("#voice-lines .vline").count() == 4
    assert "words a second" in page.text_content("#glance-voice")
    assert page.text_content("#glance-you").startswith("Over your last 4 takes")
    assert f"{found} lines found in it, 4 added" in page.text_content("#suggestions .more")
    assert page.is_hidden("#suggestions")  # on the "Add lines" tab
    page.click("#voice-tabs [data-tab=add]")
    page.click("#suggestions .more-btn")  # picking by hand, from the recording's whole list
    page.wait_for_selector("#source:not([hidden]) #lines .line-item")
    first = page.locator("#lines .line-item").first
    assert first.locator(".practise").text_content() == "Open" and "best" in first.locator(".tag.done").text_content()
    page.click("#delete-source")
    page.wait_for_selector("#voice:not([hidden])")  # the lines stay with the voice
    assert page.locator("#voice-lines .vline").count() == 4 and page.is_hidden("#recordings-wrap")
    assert page.is_hidden("#suggest-wrap")
    assert page.errors == []


def test_a_voice_check_averages_one_take_on_each_line(server, page):
    made = httpx.post(f"{server}/api/voices", json={"name": "Brad"}).json()
    for name in ("bdl_arctic_a0001", "bdl_arctic_a0002", "rms_arctic_a0001"):
        r = httpx.post(f"{server}/api/targets", params={"name": name, "voice": made["id"]}, content=(ARCTIC / f"{name}.wav").read_bytes(), timeout=120)
        assert r.status_code == 201

    page.goto(server)
    page.wait_for_selector("#home:not([hidden])")
    page.click("#voice-cards .btn")
    page.wait_for_selector("#voice:not([hidden])")
    assert page.locator("#voice-lines .vline input:checked").count() == 3
    page.click("#run-check")

    scores = []
    for n in (1, 2, 3):
        page.wait_for_selector("#check-banner:not([hidden])")
        assert page.text_content("#check-step").startswith(f"Line {n} of 3")
        assert page.is_hidden("#coach") and page.is_hidden("#crumb")  # nothing to lean on but the line
        record(page, "#record")
        page.wait_for_selector("#check-next:not([hidden])", timeout=30000)
        assert page.is_disabled("#record")  # one take each
        scores.append(int(page.text_content("#check-step").split(":")[1].split(".")[0]))
        assert page.is_hidden("#result")
        page.click("#check-next")

    page.wait_for_selector("#check-result:not([hidden])", timeout=30000)
    assert page.locator(".check-lines li").count() == 3
    average = int(page.text_content("#check-result .score span"))
    assert abs(average - sum(scores) / 3) <= 1
    assert "average of 3 lines" in page.text_content("#check-result .score small")
    assert "baseline" in page.text_content("#check-result")
    page.click("#check-result .btn:has-text('Back to Brad')")
    page.wait_for_selector("#voice:not([hidden])")
    assert page.text_content("#voices .voice-btn .score") == str(average)
    assert page.is_visible("#score-card .ring")

    page.click("#home-link")
    page.wait_for_selector("#continue:not([hidden])")  # where to pick up
    assert f"{average} voice score" in page.text_content("#voice-cards")
    assert page.errors == []


def test_warming_up_shows_live_pitch_and_saves_nothing(server, page, tmp_path):
    made = httpx.post(f"{server}/api/voices", json={"name": "Warm"}).json()
    clip = tmp_path / "target.wav"
    sf.write(clip, voice(), SR, subtype="PCM_16")
    httpx.post(f"{server}/api/targets", params={"name": "clip", "voice": made["id"]}, content=clip.read_bytes(), timeout=120)

    page.goto(server)
    page.click("#voices .voice-btn")
    page.wait_for_selector("#voice:not([hidden])")
    page.click("#voice-lines .vline .practise")
    page.wait_for_selector("#practice:not([hidden])")

    # The synthetic voice sits at 120 Hz, right between A♯2 and B2.
    page.wait_for_function("/^(A♯2|B2)$/.test(document.querySelector('#note-name').textContent)")
    page.click("#play-note")
    page.click("#warmup")
    page.wait_for_function("document.querySelector('#status').textContent.startsWith('Warming up')")
    page.wait_for_function("document.querySelector('#live-text').textContent.startsWith('You:')", timeout=10000)
    assert page.is_disabled("#record")
    page.click("#warmup")
    page.wait_for_function("document.querySelector('#status').textContent.startsWith('Warm-up done')")
    assert page.is_hidden("#result") and page.is_hidden("#history")
    assert page.evaluate("fetch('/api/targets').then(r => r.json()).then(t => t[0].takes)") == 0

    # Space records a take when nothing else has the keyboard.
    page.evaluate("document.activeElement.blur()")
    page.keyboard.press("Space")
    page.wait_for_function("document.querySelector('#status').textContent.startsWith('Recording')")
    page.wait_for_timeout(2500)
    page.keyboard.press("Space")
    page.wait_for_selector("#result:not([hidden])", timeout=20000)
    assert page.errors == []


def test_a_pasted_link_becomes_a_voice_and_its_speakers_are_told_apart(server, page, tmp_path):
    import functools
    import http.server
    import socketserver

    two = ("bdl_arctic_a0001", "bdl_arctic_a0002") * 2 + ("slt_arctic_a0001",) * 3
    sf.write(tmp_path / "Brad and Sally.wav", compilation(clips=two, tail=50.0), SR, subtype="PCM_16")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *args: None
    web = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    try:
        page.goto(server)
        page.wait_for_selector("#empty:not([hidden])")
        page.fill("#first-voice-name", f"http://127.0.0.1:{web.server_address[1]}/Brad%20and%20Sally.wav")
        page.click("#first-voice-form button[type=submit]")
        page.wait_for_selector("#voice:not([hidden]) #voice-progress:not([hidden])")
        assert "Getting voice lines" in page.text_content("#voice-progress")
        page.wait_for_selector("#voice-progress[hidden]", state="attached", timeout=180000)
        page.wait_for_function("document.querySelector('#voice-name').textContent === 'Brad and Sally'")
        link = f"http://127.0.0.1:{web.server_address[1]}/Brad%20and%20Sally.wav"
        assert page.get_attribute("#voice-links a", "href") == link  # where it came from
        assert page.is_hidden("#channel-rename")  # a plain file has no channel to offer
        assert page.locator("#voice-lines .vline").count() == 4  # bdl's: he speaks most

        # Two speakers, told apart: bdl (a man) and slt (a woman).
        page.wait_for_selector("#speakers-card:not([hidden])")
        speakers = page.locator("#speakers-card .speaker")
        assert speakers.count() == 2 and "3 lines" in speakers.nth(1).text_content()
        assert "This voice" in speakers.nth(0).text_content()
        assert "2 speakers" in page.text_content("#recordings")

        # Make the voice slt instead: Echo's picks of bdl give way to hers.
        speakers.nth(1).locator("button:has-text('Use this one')").click()
        page.wait_for_function("document.querySelectorAll('#voice-lines .vline').length === 3", timeout=60000)
        assert "This voice" in page.locator("#speakers-card .speaker").nth(1).text_content()

        # And bdl as a voice of his own.
        page.locator("#speakers-card .speaker").nth(0).locator("button:has-text('Make a separate voice')").click()
        page.wait_for_function("document.querySelector('#voice-name').textContent === 'Brad and Sally, speaker 1'", timeout=60000)
        assert page.locator("#voice-lines .vline").count() == 4
        assert page.locator("#voices .voice-btn").count() == 2

        # In the recording's own list, each line says whose it is.
        page.click("#voice-tabs [data-tab=add]")
        page.click("#recordings .btn")
        page.wait_for_selector("#source:not([hidden]) #lines .line-item")
        assert page.is_visible("#source-speakers")
        assert page.locator("#lines .speaker-tag").count() >= 4
        assert page.errors == []
    finally:
        web.shutdown()
