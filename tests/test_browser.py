'''
tests/test_browser.py
Author: maymeridian
Description: Drive the real page in headless Edge or Chrome, with the microphone fed from a WAV.
             - Skipped when Playwright isn't installed (`pip install -e ".[e2e]"`) or neither
               browser is on the machine.
             - The server runs in a thread on a free port, on a fresh data folder per test.
'''

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
    """The app on a free local port, run by uvicorn in a thread; yields its address."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(
        uvicorn.Config(
            create_app(tmp_path / "data"), host="127.0.0.1", port=port, log_level="warning"
        )
    )
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=5)


def open_page(tmp_path, samples):
    """A page in headless Edge or Chrome whose microphone plays `samples` in a loop."""
    mic = tmp_path / "mic.wav"
    sf.write(mic, samples, SR, subtype="PCM_16")
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
            except Exception:  # noqa: BLE001, S112 (whichever of the two browsers is installed)
                continue
        if browser is None:
            pytest.skip("neither Edge nor Chrome is installed")
        context = browser.new_context(permissions=["microphone"])
        page = context.new_page()
        page.errors = []
        page.on("pageerror", lambda exc: page.errors.append(str(exc)))
        page.on("console", lambda msg: msg.type == "error" and page.errors.append(msg.text))
        page.on("response", lambda r: r.status >= 400 and page.errors.append(f"{r.status} {r.url}"))

        def answer(dialog):
            """Confirm every question; a prompt gets its suggested answer."""
            if dialog.type == "prompt":
                dialog.accept(dialog.default_value)
            else:
                dialog.accept()

        page.on("dialog", answer)
        # Every time the page asks the browser for a microphone, and which one it asked for.
        page.add_init_script(
            "window.micAsks = [];"
            " const real = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);"
            "navigator.mediaDevices.getUserMedia = (c) => {"
            " window.micAsks.push(c.audio.deviceId?.exact ?? null); return real(c); };"
        )
        yield page
        browser.close()


@pytest.fixture
def page(tmp_path):
    """A page whose microphone plays the synthetic voice at 140 Hz."""
    yield from open_page(tmp_path, voice(f0=140))


@pytest.fixture
def silent_page(tmp_path):
    """A page whose microphone hears nothing."""
    yield from open_page(tmp_path, np.zeros(SR, dtype=np.float32))


def record(
    page,
    button: str,
    seconds: float = 2.5,
    check_live: bool = False,
    fresh: bool = False,
) -> None:
    """Record for `seconds` with `button`, clicking it to start and again to stop.

    `check_live` checks the live pitch while recording; `fresh` restarts the microphone first."""
    if fresh and page.is_visible("#mic-off"):
        # The microphone stays open between takes, and the fake one keeps looping its file.
        # Turning it off first starts the file again, so two takes hear the same thing.
        # The browser needs a moment to let go of the device, or it carries on where it was.
        page.click("#mic-off")
        page.wait_for_timeout(1500)
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


def a_line(server, tmp_path, name: str = "Mic") -> None:
    """A voice called `name` with the synthetic voice as its one line, added through the API."""
    made = httpx.post(f"{server}/api/voices", json={"name": name}).json()
    clip = tmp_path / "target.wav"
    sf.write(clip, voice(), SR, subtype="PCM_16")
    httpx.post(
        f"{server}/api/targets",
        params={"name": "clip", "voice": made["id"]},
        content=clip.read_bytes(),
        timeout=120,
    )


def a_real_line(server) -> dict:
    """A voice "Real" with bdl's a0001 as its one line, added through the API; the line."""
    made = httpx.post(f"{server}/api/voices", json={"name": "Real"}).json()
    clip = ARCTIC / "bdl_arctic_a0001.wav"
    return httpx.post(
        f"{server}/api/targets",
        params={"name": "clip", "voice": made["id"]},
        content=clip.read_bytes(),
        timeout=180,
    ).json()


def open_the_line(page, server) -> None:
    """From the home page: the first voice, then its first line to practise."""
    page.goto(server)
    page.click("#voices .voice-btn")
    page.wait_for_selector("#voice:not([hidden])")
    page.click("#voice-lines .vline .practise")
    page.wait_for_selector("#practice:not([hidden])")


def warm_up(page) -> None:
    """Warm up until the live pitch shows, then stop."""
    page.click("#warmup")
    page.wait_for_function("document.querySelector('#status').textContent.startsWith('Warming up')")
    page.wait_for_function(
        "document.querySelector('#live-text').textContent.startsWith('You:')", timeout=10000
    )
    page.click("#warmup")
    page.wait_for_function(
        "document.querySelector('#status').textContent.startsWith('Warm-up done')"
    )


# --- A VOICE AND ITS LINES ---


def test_make_a_voice_add_a_clip_and_practise_it(server, page, tmp_path):
    """A first voice gets a clip as its line, takes on it are scored, and the line is edited."""
    clip = tmp_path / "target.wav"
    sf.write(clip, voice(), SR, subtype="PCM_16")

    # 1. Make a voice: it has no score, and needs three lines for one.
    make_voice(page, server, "Synthetic")
    assert page.text_content("#voices .voice-btn .name") == "Synthetic"
    assert "No voice score yet" in page.text_content("#score-card")
    assert page.is_disabled("#run-check")  # it needs three lines
    assert "Add 3 more lines" in page.text_content("#score-card")

    # 2. Add the clip as its first line.
    page.set_input_files("#new-file", str(clip))
    page.wait_for_selector("#trim:not([hidden])")
    page.click("#trim-use")
    page.wait_for_selector("#practice:not([hidden])", timeout=60000)
    assert page.text_content("#crumb") == "‹ Synthetic"
    assert not page.is_visible("#line-form") and not page.is_visible("#result")

    # 3. Record a take: the score, its parts, and one thing to work on.
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

    # 4. Pick the focus by hand: the next take reports on it.
    page.click("#focus-chips button:has-text('Melody')")
    assert page.text_content("#coach-focus") == "Work on melody (your pick)"
    record(page, "#record")
    page.wait_for_selector("#history:not([hidden])", timeout=20000)
    assert page.locator("#takes button").count() == 2
    assert "worked on melody" in page.text_content("#coach-progress")
    # Each part shows its change since the last take.
    assert page.locator("#components .delta").first.text_content() != ""
    page.click("#focus-chips button:has-text('Auto')")

    # 5. Reloading brings the same line and its latest take back.
    page.reload()
    page.wait_for_selector("#result:not([hidden])")
    assert page.locator("#takes button").count() == 2

    # 6. Correct the words by hand; they survive a reload.
    edited = "document.querySelector('#target-line').textContent.includes('a made up line')"
    page.click("#edit-line")
    page.fill("#line-input", "a made up line")
    page.click("#line-form button[type=submit]")
    page.wait_for_function(edited)
    page.reload()
    page.wait_for_function(edited)

    # 7. Back on the voice's page, the line is listed with its scores, and can be deleted.
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
    """A clip over the length limit is trimmed by hand to the part that is the line."""
    # 1. About 45 s: over the limit, but under a minute.
    clip = tmp_path / "long.wav"
    sf.write(clip, np.tile(voice(), 25), SR, subtype="PCM_16")
    make_voice(page, server, "Long clip")
    page.set_input_files("#new-file", str(clip))
    page.wait_for_selector("#trim:not([hidden])")
    assert page.eval_on_selector("#trim-end", "e => Number(e.value)") == 30  # starts at the limit

    # 2. 40 s is too long; 2 s to 8 s will do.
    page.eval_on_selector(
        "#trim-end", "e => { e.value = 40; e.dispatchEvent(new Event('input')); }"
    )
    assert page.is_disabled("#trim-use")
    page.eval_on_selector(
        "#trim-start", "e => { e.value = 2; e.dispatchEvent(new Event('input')); }"
    )
    page.eval_on_selector("#trim-end", "e => { e.value = 8; e.dispatchEvent(new Event('input')); }")
    assert "6.0 s selected" in page.text_content("#trim-len")

    # 3. The line is what was chosen.
    page.click("#trim-use")
    page.wait_for_selector("#practice:not([hidden])", timeout=60000)
    duration = page.evaluate("fetch('/api/targets').then(r => r.json()).then(t => t[0].duration)")
    assert 4 < duration <= 6.05  # trimmed of silence at the edges too
    assert page.errors == []


def test_record_a_clip_from_the_microphone(server, page):
    """A clip recorded from the microphone goes through the trimmer to become the line."""
    make_voice(page, server, "From mic")
    record(page, "#new-record")
    page.wait_for_selector("#trim:not([hidden])", timeout=20000)
    page.click("#trim-use")
    page.wait_for_selector("#practice:not([hidden])", timeout=60000)
    assert page.text_content("#crumb") == "‹ From mic"
    assert page.errors == []


def test_a_long_recording_becomes_lines_to_add_to_the_voice(server, page, tmp_path):
    """A long recording becomes lines: the best are added by themselves, and practised."""
    # 1. A recording of about 72 s: over a minute, so it is split into lines.
    path = tmp_path / "Voice lines.wav"
    sf.write(path, compilation(tail=50.0), SR, subtype="PCM_16")
    make_voice(page, server, "Brad")
    page.set_input_files("#new-file", str(path))
    page.wait_for_selector("#recordings .recording")
    assert "Voice lines" in page.text_content("#recordings")
    page.wait_for_selector("#recordings .btn:not([disabled])", timeout=180000)

    # 2. Its best lines were added by themselves: bdl's four, enough for a voice check.
    page.wait_for_function("document.querySelectorAll('#voice-lines .vline').length === 4")
    found = page.evaluate("fetch('/api/sources').then(r => r.json()).then(s => s[0].count)")
    assert 5 <= found <= 7  # Whisper sometimes ends a sentence at "Tom" in bdl's a0002
    assert f"{found} lines found · 4 added" in page.text_content("#recordings")
    assert page.locator("#voice-lines .vline input:checked").count() == 4
    assert page.is_enabled("#run-check")
    assert "Every clear line" in page.text_content("#suggestions")  # slt's is another voice
    assert page.is_hidden("#autopick")

    # 3. The recording's own list of lines, searched and played.
    page.click("#recordings .btn")
    page.wait_for_selector("#source:not([hidden]) #lines .line-item")
    assert page.text_content("#source-crumb") == "‹ Brad"
    assert f"{found} lines in 1:12" in page.text_content("#source-status")
    # Never slt's line, another voice.
    assert 1 <= page.locator("#suggested .line-item").count() <= 4
    # slt's line is hidden as another voice.
    assert page.locator("#lines .line-item").count() == found - 1
    page.uncheck("#only-good")
    assert page.locator("#lines .line-item").count() == found
    assert "other voice" in page.text_content("#lines .line-item:last-child .tag")
    page.fill("#line-search", "danger trail")
    assert page.locator("#lines .line-item").count() == 3  # bdl reads a0001 twice, slt once
    page.fill("#line-search", "")
    page.click("#lines .line-item:first-child .play")
    page.wait_for_function(
        "document.querySelector('#lines .play').textContent === '▶'", timeout=10000
    )

    # 4. A line not added yet opens in the trimmer; one added already opens to practise.
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

    # 5. Before the first take, a checklist: listen, find the note, record.
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

    # 6. Before a take, the words are there to hear; after it, each is coloured.
    page.wait_for_selector("#words:not([hidden]) .word")
    record(page, "#record", fresh=True)
    page.wait_for_selector("#result:not([hidden])", timeout=30000)
    words = page.locator("#words .word")
    assert words.count() >= 6
    coloured = page.locator("#words .word.good, #words .word.mid, #words .word.poor")
    assert coloured.count() == words.count()
    words.nth(3).click()
    page.wait_for_selector("#word-note:not([hidden])")
    assert page.text_content("#word-note").startswith("“")
    page.click("#play-both")
    page.wait_for_selector("#play-both.on")
    page.click("#play-both")  # a second click stops it
    page.wait_for_selector("#play-both:not(.on)")

    # 7. Train one part on its own: resonance, word by word.
    page.click("#components li:has-text('Resonance') .train-btn")
    page.wait_for_selector("#train:not([hidden])")
    assert page.text_content("#train-title").startswith("Resonance on")
    assert page.locator("#train-words .word").count() >= 6
    assert page.locator("#train-tips li").count() >= 3
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
    assert page.locator("#train-attempts li").count() == 1
    assert page.locator("#train-tips li").count() >= 1
    page.click("#train-back")
    page.wait_for_selector("#practice:not([hidden])")
    # The next take is about the weaker of the two.
    pressed = page.locator("#focus-chips button[aria-pressed=true]").text_content()
    assert pressed in ("Pitch", "Resonance")
    page.click("#focus-chips button:has-text('Auto')")

    # 8. After the take: a drill for the focus, and the scale from a stranger to the real person.
    assert page.is_hidden("#checklist") and page.is_visible("#drill")
    assert page.locator("#drill-actions button").count() >= 1
    assert page.is_visible("#ladder-now") and page.is_hidden("#ladder-best")  # no earlier best yet

    # 9. The fake mic says the same thing every time: after four takes the line has
    # stopped improving, and the coach offers a fresh one.
    for n in (2, 3, 4):
        record(page, "#record", fresh=True)
        page.wait_for_function(
            f"document.querySelectorAll('#takes button').length === {n}", timeout=30000
        )
    page.wait_for_selector("#plateau:not([hidden])")
    assert page.is_visible("#ladder-best")
    page.click("#plateau-actions button:has-text('Back to Brad')")

    # 10. The voice's page: its lines, and the habits across its takes.
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
    assert first.locator(".practise").text_content() == "Open"
    assert "best" in first.locator(".tag.done").text_content()
    page.click("#delete-source")
    page.wait_for_selector("#voice:not([hidden])")  # the lines stay with the voice
    assert page.locator("#voice-lines .vline").count() == 4 and page.is_hidden("#recordings-wrap")
    assert page.is_hidden("#suggest-wrap")
    assert page.errors == []


# --- THE VOICE CHECK AND WARMING UP ---


def test_a_voice_check_averages_one_take_on_each_line(server, page):
    """A voice check takes one take on each line, with nothing to lean on, and averages them."""
    # 1. A voice with three real lines, added through the API.
    made = httpx.post(f"{server}/api/voices", json={"name": "Brad"}).json()
    for name in ("bdl_arctic_a0001", "bdl_arctic_a0002", "rms_arctic_a0001"):
        r = httpx.post(
            f"{server}/api/targets",
            params={"name": name, "voice": made["id"]},
            content=(ARCTIC / f"{name}.wav").read_bytes(),
            timeout=120,
        )
        assert r.status_code == 201

    # 2. Run the check: one take on each line.
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
        # Nothing to lean on but the line.
        assert page.is_hidden("#coach") and page.is_hidden("#crumb")
        record(page, "#record")
        page.wait_for_selector("#check-next:not([hidden])", timeout=30000)
        assert page.is_disabled("#record")  # one take each
        scores.append(int(page.text_content("#check-step").split(":")[1].split(".")[0]))
        assert page.is_hidden("#result")
        page.click("#check-next")

    # 3. The result is the average of the three, and becomes the voice's score.
    page.wait_for_selector("#check-result:not([hidden])", timeout=30000)
    assert page.locator(".check-lines li").count() == 3
    average = int(page.text_content("#check-result .score span"))
    assert abs(average - sum(scores) / 3) <= 1  # the scores shown are whole numbers
    assert "average of 3 lines" in page.text_content("#check-result .score small")
    assert "baseline" in page.text_content("#check-result")
    page.click("#check-result .btn:has-text('Back to Brad')")
    page.wait_for_selector("#voice:not([hidden])")
    assert page.text_content("#voices .voice-btn .score") == str(average)
    assert page.is_visible("#score-card .ring")

    # 4. The home page shows it, and where to pick up.
    page.click("#home-link")
    page.wait_for_selector("#continue:not([hidden])")  # where to pick up
    assert f"{average} voice score" in page.text_content("#voice-cards")
    assert page.errors == []


def test_warming_up_shows_live_pitch_and_saves_nothing(server, page, tmp_path):
    """Warming up shows pitch and resonance live and keeps no take; Space then records one."""
    a_line(server, tmp_path, name="Warm")
    open_the_line(page, server)

    # 1. The synthetic voice sits at 120 Hz, right between A♯2 and B2.
    page.wait_for_function("/^(A♯2|B2)$/.test(document.querySelector('#note-name').textContent)")
    page.click("#play-note")

    # 2. Warm up: the live pitch shows.
    page.click("#warmup")
    page.wait_for_function("document.querySelector('#status').textContent.startsWith('Warming up')")
    page.wait_for_function(
        "document.querySelector('#live-text').textContent.startsWith('You:')", timeout=10000
    )
    # Resonance shows live too, read in the browser. The microphone plays the
    # target's own vowels at another pitch, so it sits close to level.
    page.wait_for_function("!document.querySelector('#live-res').hidden", timeout=10000)
    said = page.text_content("#live-res-text")
    assert said.startswith("Resonance so far: about "), said
    assert "level" in said or int(said.split("about ")[1].split("%")[0]) <= 6, said
    assert page.is_disabled("#record")

    # 3. Stopped, nothing was kept.
    page.click("#warmup")
    page.wait_for_function(
        "document.querySelector('#status').textContent.startsWith('Warm-up done')"
    )
    assert page.is_hidden("#result") and page.is_hidden("#history")
    assert page.evaluate("fetch('/api/targets').then(r => r.json()).then(t => t[0].takes)") == 0

    # 4. Space records a take when nothing else has the keyboard.
    page.evaluate("document.activeElement.blur()")
    page.keyboard.press("Space")
    page.wait_for_function("document.querySelector('#status').textContent.startsWith('Recording')")
    page.wait_for_timeout(2500)
    page.keyboard.press("Space")
    page.wait_for_selector("#result:not([hidden])", timeout=20000)
    assert page.errors == []


# --- A PASTED LINK AND ITS SPEAKERS ---


def test_a_pasted_link_becomes_a_voice_and_its_speakers_are_told_apart(server, page, tmp_path):
    """A pasted link becomes a voice named after the file, and its two speakers are told apart."""
    import functools
    import http.server
    import socketserver

    # 1. A recording of two speakers, served over HTTP: bdl's four lines, then three by slt.
    two = ("bdl_arctic_a0001", "bdl_arctic_a0002") * 2 + ("slt_arctic_a0001",) * 3
    audio = compilation(clips=two, tail=50.0)
    sf.write(tmp_path / "Brad and Sally.wav", audio, SR, subtype="PCM_16")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *args: None
    web = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    try:
        # 2. The link pasted as the first voice's name: the voice is named after the file.
        link = f"http://127.0.0.1:{web.server_address[1]}/Brad%20and%20Sally.wav"
        page.goto(server)
        page.wait_for_selector("#empty:not([hidden])")
        page.fill("#first-voice-name", link)
        page.click("#first-voice-form button[type=submit]")
        page.wait_for_selector("#voice:not([hidden]) #voice-progress:not([hidden])")
        assert "Getting voice lines" in page.text_content("#voice-progress")
        page.wait_for_selector("#voice-progress[hidden]", state="attached", timeout=180000)
        page.wait_for_function(
            "document.querySelector('#voice-name').textContent === 'Brad and Sally'"
        )
        assert page.get_attribute("#voice-links a", "href") == link  # where it came from
        assert page.is_hidden("#channel-rename")  # a plain file has no channel to offer
        assert page.locator("#voice-lines .vline").count() == 4  # bdl's: he speaks most

        # 3. Two speakers, told apart: bdl (a man) and slt (a woman).
        page.wait_for_selector("#speakers-card:not([hidden])")
        speakers = page.locator("#speakers-card .speaker")
        assert speakers.count() == 2 and "3 lines" in speakers.nth(1).text_content()
        assert "This voice" in speakers.nth(0).text_content()
        assert "2 speakers" in page.text_content("#recordings")

        # 4. Make the voice slt instead: Echo's picks of bdl give way to hers.
        speakers.nth(1).locator("button:has-text('Use this one')").click()
        page.wait_for_function(
            "document.querySelectorAll('#voice-lines .vline').length === 3", timeout=60000
        )
        assert "This voice" in page.locator("#speakers-card .speaker").nth(1).text_content()

        # 5. And bdl as a voice of his own.
        bdl = page.locator("#speakers-card .speaker").nth(0)
        bdl.locator("button:has-text('Make a separate voice')").click()
        page.wait_for_function(
            "document.querySelector('#voice-name').textContent === 'Brad and Sally, speaker 1'",
            timeout=60000,
        )
        assert page.locator("#voice-lines .vline").count() == 4
        assert page.locator("#voices .voice-btn").count() == 2

        # 6. In the recording's own list, each line says whose it is.
        page.click("#voice-tabs [data-tab=add]")
        page.click("#recordings .btn")
        page.wait_for_selector("#source:not([hidden]) #lines .line-item")
        assert page.is_visible("#source-speakers")
        assert page.locator("#lines .speaker-tag").count() >= 4
        assert page.errors == []
    finally:
        web.shutdown()


# --- THE TRAINER ---


def test_the_trainer_follows_your_voice_live_and_keeps_nothing(server, page):
    """Trying it live follows the microphone on the trainer's map and meter, and keeps nothing."""
    # 1. A real line, and a take on it, to open the trainer from.
    line = a_real_line(server)
    open_the_line(page, server)
    record(page, "#record")
    page.wait_for_selector("#result:not([hidden])", timeout=30000)
    page.click("#components li:has-text('Resonance') .train-btn")
    page.wait_for_selector("#train:not([hidden])")

    # 2. "Try it live": a ring on the map and a mark on the meter follow the
    # microphone's pitch and resonance, read in the browser.
    page.click("#train-try")
    page.wait_for_function(
        "document.querySelector('#train-live').textContent.startsWith('Now:')", timeout=15000
    )
    page.wait_for_function(
        "/brighter|darker|level/.test(document.querySelector('#train-live').textContent)",
        timeout=15000,
    )
    assert page.is_visible("#train-map .map-live") and page.is_visible("#train-meter .mark.now")
    assert page.is_disabled("#train-say") and page.text_content("#train-try-label") == "Stop"
    page.click("#train-try")
    page.wait_for_function(
        "document.querySelector('#train-try-label').textContent === 'Try it live'"
    )

    # 3. Nothing was scored or kept.
    assert page.is_hidden("#train-live") and page.locator("#train-attempts li").count() == 0
    assert httpx.get(f"{server}/api/targets/{line['id']}/drills").json() == []

    # 4. A scored attempt shows the ring while it records, then the server's dot.
    page.click("#train-say")
    page.wait_for_function(
        "document.querySelector('#train-live').textContent.startsWith('Now:')", timeout=15000
    )
    assert page.is_disabled("#train-try")
    page.wait_for_timeout(1500)
    page.click("#train-say")
    page.wait_for_selector("#train-attempts li", timeout=20000)
    assert page.locator("#train-map .map-dot").count() == 1
    assert page.is_hidden("#train-map .map-live")
    assert page.errors == []


# --- THE MICROPHONE ---


def test_the_microphone_is_opened_once_chosen_once_and_remembered(server, page, tmp_path):
    """The microphone is opened once and kept open; one chosen by name is remembered."""
    # 1. Nothing chosen yet: the browser decides which microphone.
    a_line(server, tmp_path)
    open_the_line(page, server)
    assert page.input_value("#mic-select") == "" and page.is_hidden("#mic-off")
    warm_up(page)
    assert page.evaluate("window.micAsks") == [None]
    # It stays on between takes, so a browser that asks each time asks once.
    assert page.is_visible("#mic-off") and page.text_content("#mic-note").startswith("On: ")
    record(page, "#record")
    page.wait_for_selector("#result:not([hidden])", timeout=20000)
    assert page.evaluate("window.micAsks") == [None]

    # 2. The microphones are listed by name now. Choosing one opens it and shows its level.
    names = page.locator("#mic-select option").all_text_contents()
    assert names[0] == "The browser's choice" and len(names) >= 2 and all(names)
    page.select_option("#mic-select", index=1)
    chosen = page.input_value("#mic-select")
    page.wait_for_function(
        "document.querySelector('#mic-note').textContent.startsWith('Say something')", timeout=10000
    )
    page.wait_for_function(
        "parseFloat(document.querySelector('#mic-level').style.width) > 0", timeout=10000
    )
    assert page.evaluate("window.micAsks") == [None, chosen]
    assert page.evaluate("localStorage.getItem('echo.mic')") == chosen
    page.click("#mic-test")  # stop the test
    page.wait_for_function("document.querySelector('#mic-test').textContent === 'Test'")
    assert "No sound" not in page.text_content("#mic-note")

    # 3. Next time it is asked for by its id, whatever the browser's own default is.
    page.reload()
    page.wait_for_selector("#practice:not([hidden])")
    assert page.input_value("#mic-select") == chosen
    warm_up(page)
    assert page.evaluate("window.micAsks") == [chosen]

    # 4. "Turn off" lets go of it.
    page.click("#mic-off")
    assert page.is_hidden("#mic-off") and not page.text_content("#mic-note").startswith("On: ")
    assert page.errors == []


def test_a_microphone_that_hears_nothing_is_called_out(server, silent_page, tmp_path):
    """A microphone that hears nothing is called out while warming up, and when tested."""
    # 1. Warming up says no sound is coming in, and to choose the microphone.
    page = silent_page
    a_line(server, tmp_path)
    open_the_line(page, server)
    page.click("#warmup")
    page.wait_for_function(
        "document.querySelector('#status').textContent.includes('No sound is coming from')",
        timeout=10000,
    )
    assert "Choose your microphone" in page.text_content("#status")
    page.click("#warmup")
    page.wait_for_function(
        "document.querySelector('#status').textContent.startsWith('Warm-up done')"
    )

    # 2. The test button says so too, and the box asks for attention.
    page.click("#mic-test")
    page.wait_for_function(
        "document.querySelector('#mic-note').textContent.startsWith('Say something')", timeout=10000
    )
    page.wait_for_timeout(500)
    page.click("#mic-test")
    page.wait_for_function(
        "document.querySelector('#mic-note').textContent.startsWith('No sound came from')"
    )
    assert "attention" in page.get_attribute("#mic-box", "class")
    assert page.errors == []


# --- STOPPING BY ITSELF ---


@pytest.fixture
def pausing_page(tmp_path):
    """Someone who says the line and then says nothing: half a second of quiet, the voice, six
    seconds of quiet."""
    quiet = np.zeros(SR // 2, dtype=np.float32)
    yield from open_page(tmp_path, np.concatenate([quiet, voice(f0=140), np.tile(quiet, 12)]))


def test_a_take_stops_by_itself_when_the_line_has_been_said(server, pausing_page, tmp_path):
    """A take stops by itself soon after the voice does, unless that is switched off."""
    # 1. Stopping by itself is on to begin with, and the page says so.
    page = pausing_page
    a_line(server, tmp_path)
    open_the_line(page, server)
    assert page.is_checked("#auto-stop")
    assert "it stops when you finish" in page.inner_text(".recorder .keys")

    # 2. No second click: the take ends about a second after the voice does, long before the
    # 30 s limit.
    page.click("#record")
    page.wait_for_function("document.querySelector('#status').textContent.startsWith('Recording')")
    assert "It stops when you finish" in page.text_content("#status")
    page.wait_for_selector("#result:not([hidden])", timeout=20000)
    assert page.text_content("#record-label") == "Record a take"
    line = httpx.get(f"{server}/api/targets").json()[0]
    assert line["takes"] == 1

    # 3. Switched off in the sidebar, a take runs until it is stopped by hand. The choice is
    # remembered.
    page.uncheck("#auto-stop")
    assert page.evaluate("localStorage.getItem('echo.autostop')") == "off"
    assert "starts and stops" in page.inner_text(".recorder .keys")
    page.click("#mic-off")  # start the microphone's file again: the voice, then the quiet
    page.wait_for_timeout(1500)
    page.click("#record")
    page.wait_for_function("document.querySelector('#status').textContent.startsWith('Recording')")
    page.wait_for_timeout(6000)  # the voice ended more than three seconds ago
    assert page.text_content("#record-label") == "Stop"
    assert "It stops" not in page.text_content("#status")
    page.click("#record")
    page.wait_for_function(
        "document.querySelector('#record-label').textContent === 'Record a take'"
    )
    for _ in range(100):
        if httpx.get(f"{server}/api/targets").json()[0]["takes"] == 2:
            break
        time.sleep(0.2)
    else:
        raise AssertionError("the take stopped by hand was not scored")
    page.reload()
    page.wait_for_selector("#practice:not([hidden])")
    assert not page.is_checked("#auto-stop")
    assert page.errors == []


def test_a_trainer_attempt_stops_by_itself(server, pausing_page):
    """A trainer attempt stops by itself once the voice has ended, well before its time is up."""
    # 1. A real line, a take that stops by itself, and the trainer.
    page = pausing_page
    line = a_real_line(server)
    open_the_line(page, server)
    page.click("#record")
    page.wait_for_selector("#result:not([hidden])", timeout=30000)
    page.click("#components li:has-text('Resonance') .train-btn")
    page.wait_for_selector("#train:not([hidden])")

    # 2. Start the microphone's file again, and say the word.
    page.click("#mic-off")
    page.wait_for_timeout(1500)
    page.click("#train-say")
    page.wait_for_function("document.querySelector('#train-say-label').textContent === 'Stop'")
    # The voice lasts 1.8 s. The attempt ends well before the 6 s it is allowed.
    page.wait_for_function(
        "document.querySelector('#train-say-label').textContent === 'Say it'", timeout=4500
    )
    page.wait_for_selector("#train-attempts li", timeout=20000)
    assert len(httpx.get(f"{server}/api/targets/{line['id']}/drills").json()) == 1
    assert page.errors == []


def test_hearing_the_end_of_a_line(server, page):
    """ending.js on made-up levels: speech at one level, a room at another, 60 readings a second."""
    page.goto(server)
    run = """async ({ lead, speech, room, voice, wait, enough, before }) => {
        const { Ending, waitFor, spoken } = await import('/ending.js');
        const ending = new Ending({ wait, enough });
        let t = 0;
        const hear = (seconds, level) => {
            for (let i = 0; i < seconds * 60; i++) {
                t += 1 / 60;
                if (ending.hear(level * (0.7 + 0.6 * ((i * 7919) % 13) / 13), t)) return true;
            }
            return false;
        };
        const early = hear(lead, before ?? room) || hear(speech, voice);
        const from = t;
        const over = hear(6, room);
        const words = [{ start: 0, end: 1 }, { start: 1.4, end: 2 }, { start: 2.1, end: 3 }];
        return {
            early,
            after: over ? t - from : null,
            wait: waitFor(words),
            none: waitFor([]),
            spoken: spoken(words),
        };
    }"""
    # 1. The whole line, in a quiet room and in a noisy one: it ends after the quiet it was told
    # to wait for.
    for room in (0.0, 0.0005, 0.01):
        heard = page.evaluate(
            run,
            {"lead": 0.5, "speech": 2.0, "room": room, "voice": 0.1, "wait": 1.0, "enough": 1.0},
        )
        assert not heard["early"] and 1.0 <= heard["after"] < 1.15, (room, heard)

    # 2. A device that starts with a second of digital silence, in a room with a fan: the
    # silence isn't the room, and the fan isn't speech.
    heard = page.evaluate(
        run,
        {
            "lead": 1.0,
            "before": 0.0,
            "speech": 2.0,
            "room": 0.0005,
            "voice": 0.1,
            "wait": 1.0,
            "enough": 1.0,
        },
    )
    assert not heard["early"] and 1.0 <= heard["after"] < 1.15, heard

    # 3. Speaking from the first moment, with no quiet before it to measure the room by.
    heard = page.evaluate(
        run, {"lead": 0, "speech": 2.0, "room": 0.0005, "voice": 0.1, "wait": 1.0, "enough": 1.0}
    )
    assert not heard["early"] and 1.0 <= heard["after"] < 1.15, heard

    # 4. A false start: half a second of a three-second line. It waits longer before giving up.
    heard = page.evaluate(
        run, {"lead": 0.5, "speech": 0.5, "room": 0.0005, "voice": 0.1, "wait": 1.0, "enough": 1.5}
    )
    assert not heard["early"] and 2.5 <= heard["after"] < 2.65, heard

    # 5. A click is not speech: nothing ends on it.
    heard = page.evaluate(
        run, {"lead": 0.5, "speech": 0.1, "room": 0.0005, "voice": 0.1, "wait": 1.0, "enough": 0}
    )
    assert not heard["early"] and heard["after"] is None, heard

    # 6. The quiet to wait for comes from the line: a little more than its longest pause,
    # within limits.
    assert heard["wait"] == pytest.approx(1.3)
    assert heard["none"] == pytest.approx(0.9)
    assert heard["spoken"] == pytest.approx(2.5)
