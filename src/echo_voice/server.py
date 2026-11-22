'''
src/echo_voice/server.py
Author: maymeridian
Description: The local web server: serves the page and answers its API.
             - The browser records; this scores each take and keeps the history: lines, takes,
               trainer attempts, long recordings and voices, in the data folder (store.py).
             - Security: when it listens on this computer only, a request for any Host but
               LOCAL_HOSTS is refused (DNS rebinding), and a request that changes something must
               come from Echo's own page, not from another site.
             - Each long recording is processed by a job in a separate process at below-normal
               priority (sources.py), started again if it stops early.
             - Takes from before the scoring last changed are scored again in the background
               when the app starts.
'''

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__, autopick, clean, gender, sources
from . import line as transcript
from .analysis import (
    MAX_SECONDS,
    SR,
    AnalysisError,
    Voice,
    analyze,
    decode,
    load,
    shared_ceiling,
    speaker_ceiling,
    steadied,
)
from .scoring import (
    CHECK_LINES,
    MIN_CHECK_LINES,
    SCORING,
    coach,
    compare,
    voice_check,
    word_drill,
)
from .store import NotFound, Store

MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 30 s of 16-bit mono at 16 kHz is under 1 MB
MAX_SOURCE_BYTES = 4 * 1024**3  # 4 GB: a long video
MAX_SLICE_SECONDS = 90.0  # of a long recording, sent to the page at once
# A line this far from its voice's usual pitch has an untrustworthy pitch reading.
SUSPECT_SEMITONES = 7.0
JOB_ATTEMPTS = 3  # starts of a recording's job before it is marked failed
# The names the page may be reached by when the server listens on this computer
# only. Any other Host is refused, which stops DNS rebinding: a web page whose
# own domain is made to point at 127.0.0.1 could otherwise read the API, your
# takes' audio included. ("testserver" is the name Starlette's test client uses.)
LOCAL_HOSTS = ("127.0.0.1", "localhost", "testserver")
FINISHED = {"ready", "failed"}  # a recording's statuses once its job is done with it
STATIC = Path(__file__).parent / "static"  # the page's files


def number(body: dict, key: str, default, kind=int):
    """A number from a request's JSON body; a clear 422 if it isn't one."""
    try:
        return kind(body.get(key, default))
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, f"`{key}` must be a number.") from exc


def create_app(
    data_dir: str | Path | None = None,
    hosts: tuple[str, ...] | None = LOCAL_HOSTS,
) -> FastAPI:
    """The app: the page, and its API over the data folder `data_dir` (else
    $ECHO_DATA, else ./data). `hosts`: the Host names to answer to, or None
    for any (the server was asked to listen beyond this computer, so its
    address isn't known)."""
    store = Store(Path(data_dir or os.environ.get("ECHO_DATA", "data")))
    targets: dict[str, Voice] = {}  # analysed targets, so each take only analyses itself
    steady: dict[tuple[str, float], Voice] = {}  # the same, with formants at their voice's ceiling
    jobs: dict[str, subprocess.Popen] = {}  # source id -> the job processing it
    app = FastAPI(title="Echo")

    # --- SECURITY ---

    if hosts is not None:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(hosts))

    @app.middleware("http")
    async def only_this_page_changes_things(request: Request, call_next):
        """A page on another site can send a request here (a form or a
        text/plain fetch needs no permission), and the server would act on
        it: queue downloads, delete voices. Browsers say where a request comes
        from, so anything that changes something must come from this page.
        Requests from outside a browser (scripts, the tests) say nothing and
        are let through."""
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            own = f"{request.url.scheme}://{request.headers.get('host', '')}"
            cross_site = request.headers.get("sec-fetch-site") == "cross-site"
            if cross_site or (origin is not None and origin != own):
                return JSONResponse(
                    {"detail": "Only Echo's own page can do that."},
                    status_code=403,
                )
        return await call_next(request)

    # --- RECORDING JOBS ---

    job_lock = threading.Lock()  # requests are served on several threads

    def start_job(meta: dict) -> dict:
        """Make sure an unfinished recording has a job working on it. Returns
        the recording's meta as it now is.

        A job is a separate process at below-normal priority (sources.py), so
        it keeps going if the page closes. If one stopped early, for example
        because the computer was shut down, it starts again, up to three times."""
        if meta["status"] in FINISHED or meta["status"] == "uploading":
            return meta
        with job_lock:  # two polls at once would start two jobs
            # 1. Nothing to do if it's finished, or a job is already on it.
            meta = store.source_meta(meta["id"])
            if meta["status"] in FINISHED or meta["status"] == "uploading":
                return meta
            job = jobs.get(meta["id"])
            if job is not None and job.poll() is None:
                return meta
            folder = store.source_dir(meta["id"])
            if sources.job_running(folder):  # a previous server's job, or `echo-voice import`
                return meta
            # 2. One that kept stopping is given up on.
            attempts = meta.get("attempts", 0)
            if attempts >= JOB_ATTEMPTS:
                return store.update_source(
                    meta["id"],
                    status="failed",
                    error="Processing kept stopping before it finished.",
                )
            # 3. Start the job, logging to job.log in the recording's folder.
            meta = store.update_source(meta["id"], attempts=attempts + 1)
            flags = 0
            if sys.platform == "win32":
                flags = subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW
            with open(folder / "job.log", "ab") as log:
                jobs[meta["id"]] = subprocess.Popen(
                    [sys.executable, "-m", "echo_voice.sources", str(folder)],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    creationflags=flags,
                )
            return meta

    # --- SCORING AGAIN ---

    def rescore() -> int:
        """Score again every saved take that an earlier scoring scored, from
        its own audio, measure the trainer's saved attempts again the same
        way, and work the voice checks out again from the takes. A take keeps
        its id, its time and the coaching it was given. Returns how many takes
        were scored."""
        done = 0
        for meta in store.list_targets():
            try:
                done += rescore_line(meta)
            except Exception as exc:  # noqa: BLE001
                # A line deleted meanwhile, say: the others still get done.
                print(
                    f"Couldn't score the takes on line {meta.get('id')} again: {exc!r}",
                    file=sys.stderr,
                    flush=True,
                )
        for voice in store.list_voices():
            for check in store.list_checks(voice["id"]):
                try:
                    rescore_check(voice["id"], check)
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"Couldn't work voice check {check.get('id')} out again: {exc!r}",
                        file=sys.stderr,
                        flush=True,
                    )
        return done

    def rescore_line(meta: dict) -> int:
        """Score a line's takes again and measure its trainer attempts again,
        those an earlier scoring did. Returns how many takes were scored."""
        done = 0
        # 1. The takes.
        for record in store.take_records(meta["id"]):
            if record.get("scoring") == SCORING:
                continue
            try:
                take = analyze(load(store.take_audio(meta["id"], record["id"])), trim=False)
                ceiling = pair_ceiling(meta["id"], take.formant_ceiling)
                target = steady_target(meta["id"], ceiling)
                said, timed = target_words(meta["id"], target)
                heard = (record.get("line") or {}).get("take")
                words = heard.split() if heard else transcript.transcribe(take.y)
                voice_id = meta.get("voice_id")
                usual = (typical_pitch(voice_id), my_pitch(voice_id, take.median_f0))
                steadied_take = steadied(take, ceiling)
                suspect = pitch_suspect(meta)
                result = compare(target, steadied_take, said, words, timed, suspect, usual)
            except Exception as exc:  # noqa: BLE001
                # Its audio is missing, say: the next take still gets done.
                print(
                    f"Couldn't score take {record.get('id')} again: {exc!r}",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            store.replace_take(meta["id"], record | result)
            done += 1
        # 2. The trainer attempts.
        for record in store.list_drills(meta["id"]):
            if record.get("scoring") == SCORING:
                continue
            try:
                ceiling = pair_ceiling(meta["id"])
                target = steady_target(meta["id"], ceiling)
                _, timed = target_words(meta["id"], target)
                # The word it was an attempt at: by where it starts (within 0.02 s), should
                # the words have been regrouped since, else by its place in the line.
                start = record.get("start", -1)
                word = next((w for w in timed if abs(w["start"] - start) < 0.02), None)
                if not word:
                    word = timed[record["word"]]
                audio = load(store.drill_audio(meta["id"], record["id"]))
                attempt = analyze(audio, ceiling, trim=False)
                result = word_drill(target, word, attempt)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"Couldn't measure trainer attempt {record.get('id')} again: {exc!r}",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            store.replace_drill(meta["id"], record | result | {"ceiling": attempt.formant_ceiling})
        return done

    def rescore_check(voice_id: str, check: dict) -> None:
        """A voice check worked out again from its takes, once every one of
        them that still exists has been scored again; until then it stays as
        it was, and the next start tries again."""
        if check.get("scoring") == SCORING:
            return
        rows = []
        for row in check.get("lines", []):
            try:
                take = store.take(row["target"], row["take"])
            except (NotFound, OSError, ValueError):
                rows.append(row)  # its line is gone: as it was
                continue
            if take.get("scoring") != SCORING:
                return
            scored = {
                "overall": take["overall"],
                "parts": {c["key"]: c["score"] for c in take["components"]},
                "measures": take.get("measures"),
            }
            rows.append(row | scored)
        if rows:
            store.replace_check(voice_id, check | voice_check(rows) | {"scoring": SCORING})

    # --- START-UP ---

    store.migrate()  # data from before voices existed

    # Pick up recordings that were still being processed when the app last stopped.
    for meta in store.list_sources():
        if meta["status"] == "uploading":
            store.update_source(meta["id"], status="failed", error="The upload didn't finish.")
            continue
        ready = meta["status"] == "ready"
        if ready and any("speaker" not in r for r in store.source_lines(meta["id"]) or []):
            # From before speakers were found: the job finds them, keeping the lines.
            meta = store.update_source(meta["id"], status="queued", attempts=0)
        start_job(meta)

    # --- ERRORS AND CACHING ---

    @app.exception_handler(NotFound)
    async def not_found(_: Request, __: NotFound) -> JSONResponse:
        """A voice, line, take, check or recording that isn't there: 404."""
        return JSONResponse({"detail": "Not found."}, status_code=404)

    @app.middleware("http")
    async def always_check_for_a_newer_page(request: Request, call_next):
        """Have the browser check for a newer copy of the page's files each time.
        Without this the browser may keep using its saved copy of the page
        for hours after an update (it guesses from the files' age)."""
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    # --- UPLOADS ---

    async def uploaded_audio(request: Request):
        """The request body's audio as samples at SR: 413 if it's over
        MAX_UPLOAD_BYTES, 400 if there's none, 422 if it isn't audio or has no
        sound."""
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "That file is too large.")
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "That file is too large.")
        if not data:
            raise HTTPException(400, "No audio received.")
        try:
            y = await run_in_threadpool(decode, bytes(data))
        except AnalysisError as exc:
            raise HTTPException(422, str(exc)) from exc
        if y.size == 0:
            raise HTTPException(422, "There's no sound in that file.")
        return y

    async def analysed(y) -> Voice:
        """The samples analysed, on a worker thread; 422 if they can't be."""
        try:
            return await run_in_threadpool(analyze, y)
        except AnalysisError as exc:
            raise HTTPException(422, str(exc)) from exc

    async def uploaded_voice(request: Request) -> Voice:
        """The request body's audio, analysed."""
        return await analysed(await uploaded_audio(request))

    # --- ANALYSED LINES AND FORMANT CEILINGS ---

    def target_voice(target_id: str) -> Voice:
        """The line's clip analysed, once, then kept (`targets`)."""
        if target_id not in targets:
            targets[target_id] = analyze(load(store.target_audio(target_id)), trim=False)
        return targets[target_id]

    def forget(target_id: str) -> None:
        """Drop a deleted line from the caches."""
        targets.pop(target_id, None)
        for key in [k for k in steady if k[0] == target_id]:
            del steady[key]

    def voice_ceiling(voice_id: str) -> float | None:
        """One formant ceiling for a voice: the middle of what its lines pick."""
        picks = []
        for meta in store.list_targets():
            if voice_id and meta.get("voice_id") == voice_id:
                if meta.get("ceiling") is None:  # a line from before its pick was kept
                    ceiling = target_voice(meta["id"]).formant_ceiling
                    meta = store.update_target(meta["id"], ceiling=ceiling)
                picks.append(meta["ceiling"])
        return speaker_ceiling(picks)

    def steady_target(target_id: str, ceiling: float | None) -> Voice:
        """The line for scoring, with its formants also read at `ceiling`, the
        one a take against it is read at (analysis.steadied)."""
        voice = target_voice(target_id)
        if ceiling is None:
            return voice
        if (target_id, ceiling) not in steady:
            steady[(target_id, ceiling)] = steadied(voice, ceiling)
        return steady[(target_id, ceiling)]

    def pair_ceiling(target_id: str, own: float | None = None) -> float | None:
        """The one formant ceiling Resonance reads a line and a take against
        it at: the middle of the voice's ceiling and that of whoever is
        practising it. Each of those is the middle of what several recordings
        pick, so the reading moves when a voice does, not when the tracker
        changes its mind."""
        voice_id = store.target_meta(target_id).get("voice_id") or ""
        return shared_ceiling(
            voice_ceiling(voice_id) or target_voice(target_id).formant_ceiling,
            my_ceiling(target_id, own),
        )

    def my_ceiling(target_id: str, own: float | None = None) -> float | None:
        """The ceiling for whoever is practising a voice: the middle of what
        their latest takes on it picked, with this take's pick (`own`) among them."""
        voice_id = store.target_meta(target_id).get("voice_id") or ""
        # The latest 15 picks: 14 earlier ones and this take's, when there is one.
        picks = store.take_ceilings(voice_id, 14 if own else 15)
        return speaker_ceiling([*picks, own])

    # --- USUAL PITCH ---

    def typical_pitch(voice_id: str | None, but: str | None = None) -> float | None:
        """The voice's usual pitch: from its recordings (at least 5 lines),
        else its other lines (at least 3; `but` is one to leave out)."""
        if not voice_id:
            return None
        rows = []
        for s in store.list_sources():
            if s.get("voice_id") == voice_id and s["status"] == "ready":
                chosen = s.get("speaker") or 0
                lines = store.source_lines(s["id"]) or []
                rows += [r for r in lines if r.get("speaker") in (None, chosen) and r["pitch"]]
        if len(rows) >= 5:
            return float(np.median([r["pitch"] for r in rows]))
        others = [
            t["median_f0"]
            for t in store.list_targets()
            if t.get("voice_id") == voice_id and t["id"] != but and t.get("median_f0")
        ]
        return float(np.median(others)) if len(others) >= 3 else None

    def my_pitch(voice_id: str | None, own: float | None) -> float | None:
        """The usual pitch of whoever is practising a voice: the middle of
        their latest takes on it, with this take (`own`) among them. One or
        two takes aren't enough to tell fry from a low take, so then it is
        this take's own."""
        if not voice_id:
            return own
        takes = store.takes_for_voice(voice_id, 15)
        earlier = [(r.get("measures") or {}).get("take_f0") for r in takes]
        values = [v for v in (*earlier, own) if v]
        return float(np.median(values)) if len(values) >= 3 else own

    def pitch_suspect(line: dict) -> bool:
        """A line whose pitch sits far from its voice's usual: creaky voice, or
        a pitch tracker off by an octave. Its pitch can't be trusted to score."""
        usual = typical_pitch(line.get("voice_id"), but=line["id"])
        f0 = line.get("median_f0")
        return bool(usual and f0 and abs(12 * np.log2(f0 / usual)) > SUSPECT_SEMITONES)

    # --- LINES ---

    @app.get("/api/targets")
    def list_targets() -> list[dict]:
        """Every line, oldest first."""
        return store.list_targets()

    def target_words(target_id: str, voice: Voice) -> tuple[list[str], list[dict]]:
        """The target's words, and its speech units: the words as they're said,
        with ones run together as one unit (transcript.speech_units). Both are worked
        out once and kept with the clip. The words can be corrected by hand;
        the units stay as heard, for the word-by-word comparison."""
        meta = store.target_meta(target_id)
        if meta.get("word_times") is None:  # targets from before timings were kept
            timed = transcript.transcribe_timed(voice.y)
            words = meta["words"] if meta.get("words") is not None else [w["text"] for w in timed]
            meta = store.update_target(target_id, words=words, word_times=timed)
        elif meta["word_times"] and not all(w.get("refined") for w in meta["word_times"]):
            # Timings from before word edges were moved onto the audio.
            refined = transcript.refine_word_times(voice.y, meta["word_times"])
            meta = store.update_target(target_id, word_times=refined)
        if meta.get("units_v") != transcript.UNITS_VERSION:
            units = transcript.speech_units(voice.y, meta["word_times"])
            meta = store.update_target(target_id, units=units, units_v=transcript.UNITS_VERSION)
        return meta["words"], meta["units"]

    def known_voice(voice_id: str) -> dict | None:
        """The voice, or the one it was merged into (a page opened before the
        merge still holds the old id)."""
        seen = set()
        while voice_id and voice_id not in seen:
            seen.add(voice_id)
            try:
                meta = store.voice_meta(voice_id)
            except NotFound:
                return None
            if not meta.get("merged_into"):
                return meta
            voice_id = meta["merged_into"]
        return None

    @app.post("/api/targets", status_code=201)
    async def add_target(
        request: Request,
        name: str = "",
        source: str = "",
        line_start: float | None = None,
        voice: str = "",
    ) -> dict:
        """A new line. `voice`: whose it is; `source` and `line_start`: the
        recording and line it was cut from, which decide the voice if given."""
        # 1. The clip, not much over MAX_SECONDS (5 s to spare): checked before the slow
        #    cleaning step.
        original = await uploaded_audio(request)
        if len(original) > (MAX_SECONDS + 5) * SR:
            seconds = len(original) / SR
            raise HTTPException(
                422,
                f"That clip is {seconds:.0f} s long — keep it under {MAX_SECONDS:.0f} s.",
            )
        # 2. The recording it was cut from, if any.
        recording = None
        if source:
            try:
                recording = store.source_meta(source)
            except NotFound:
                recording = None  # deleted meanwhile; keep the clip anyway
        # 3. Targets usually come from films or videos: take the voice out of any
        #    music or background first (clean.py), and score that.
        cleaned = await analysed(await run_in_threadpool(clean.vocals, original))
        timed = await run_in_threadpool(transcript.transcribe_timed, cleaned.y)
        # 4. Whose it is, and keep it.
        owner = (recording and known_voice(recording.get("voice_id") or "")) or known_voice(voice)
        if owner is None:  # older clients named a clip after its voice
            owner = store.voice_for_line(name or "Untitled")
        meta = store.add_target(
            name.strip()[:80] or "Untitled",
            cleaned,
            [w["text"] for w in timed],
            original,
            word_times=timed,
            source=recording and recording["id"],
            voice_name=recording and recording["name"],
            line_start=line_start if recording else None,
            voice_id=owner["id"],
        )
        targets[meta["id"]] = cleaned
        return meta

    @app.patch("/api/targets/{target_id}")
    async def edit_target(target_id: str, request: Request) -> dict:
        """Correct the line when the recogniser misheard the target."""
        body = await request.json()
        text = str(body.get("line", "")).strip()[:500]
        return store.update_target(target_id, words=text.split())

    @app.delete("/api/targets/{target_id}")
    def delete_target(target_id: str) -> Response:
        """Delete a line, with its takes and trainer attempts."""
        store.delete_target(target_id)
        forget(target_id)
        return Response(status_code=204)

    @app.get("/api/targets/{target_id}/profile")
    def target_profile(target_id: str) -> dict:
        """What the page draws the guide from: the target's pitch band and
        contour, where its voice starts, and its words on the timeline."""
        voice = target_voice(target_id)
        _, timed = target_words(target_id, voice)
        f0 = voice.f0[voice.voiced]
        step = 2  # 20 ms is plenty for a chart
        voiced = np.flatnonzero(voice.voiced)
        # For drawing, leave out the pitch tracker's slips: points more than 7
        # semitones from the line's median, nearly always an octave error on a
        # creaky or breathy stretch. They'd stretch the chart and aren't a
        # tune anyone should follow. Scoring uses medians, which ignore them.
        # Pitch is read only from 20 voiced frames (0.2 s) up.
        drawn = voice.f0.copy()
        if f0.size >= 20:
            middle = np.median(f0)
            filled = np.where(np.isfinite(drawn), drawn, middle)
            drawn[np.abs(12 * np.log2(filled / middle)) > 7] = np.nan
        suspect = pitch_suspect(store.target_meta(target_id))
        # For the live resonance meter (static/formants.js): the formant ceilings
        # Resonance uses for this voice and for whoever practises it. Both are the
        # pair's one ceiling (pair_ceiling).
        ceiling = pair_ceiling(target_id)
        out = {
            "pitch_suspect": suspect,
            "duration": round(voice.duration, 2),
            "onset": round(float(voice.times[voiced[0]]), 2) if voiced.size else 0.0,
            "contour": {
                "t": [round(float(t), 2) for t in voice.times[::step]],
                "f0": [round(float(v), 1) if np.isfinite(v) else None for v in drawn[::step]],
            },
            "words": timed,
            "median_f0": None,
            "low": None,
            "high": None,
            "ceiling": ceiling,
            "my_ceiling": ceiling,
        }
        if f0.size >= 20:
            low, median, high = np.percentile(f0, [10, 50, 90])
            out |= {
                "median_f0": round(float(median), 1),
                "low": round(float(low), 1),
                "high": round(float(high), 1),
            }
        return out

    @app.get("/api/targets/{target_id}/audio")
    def target_audio(target_id: str) -> FileResponse:
        """The line's clip, as WAV."""
        return FileResponse(store.target_audio(target_id), media_type="audio/wav")

    # --- TAKES ---

    @app.get("/api/targets/{target_id}/takes")
    def list_takes(target_id: str) -> list[dict]:
        """Every take on the line, oldest first: the headline and each part's score."""
        return store.list_takes(target_id)

    @app.post("/api/targets/{target_id}/takes", status_code=201)
    async def add_take(target_id: str, request: Request, focus: str = "") -> dict:
        """Score a take and say what to work on next (`focus` picks it by hand)."""
        # 1. Read the take and the line at the one formant ceiling they share.
        take = await uploaded_voice(request)
        ceiling = await run_in_threadpool(pair_ceiling, target_id, take.formant_ceiling)
        target = await run_in_threadpool(steady_target, target_id, ceiling)
        take = await run_in_threadpool(steadied, take, ceiling)
        # 2. Score it against the line.
        said, timed = await run_in_threadpool(target_words, target_id, target)
        words = await run_in_threadpool(transcript.transcribe, take.y)
        suspect = pitch_suspect(store.target_meta(target_id))
        voice_id = store.target_meta(target_id).get("voice_id")
        usual = (typical_pitch(voice_id), my_pitch(voice_id, take.median_f0))
        result = await run_in_threadpool(compare, target, take, said, words, timed, suspect, usual)
        over_takes(result, store.take_records(target_id)[-2:])
        # 3. What to work on next, from this take and the ones before it.
        earlier = store.list_takes(target_id)
        history = [t["overall"] for t in earlier]
        keys = ("pitch", "melody", "resonance", "articulation", "rhythm")
        parts = {k: [t["parts"].get(k) for t in earlier] for k in keys}
        result["coach"] = coach(result, store.last_take(target_id), focus or None, history, parts)
        # 4. Keep it.
        return store.add_take(target_id, take, result)

    def over_takes(result: dict, before: list[dict]) -> None:
        """Resonance on one short take is a rough reading: small things move
        it by 3 to 8 points, and by 19 to 42 for one take in ten (README, the
        audit). Three takes together are steadier, so once this take and the
        two before it on the line all have a reading, the row also says where
        the three sit: the middle of their shifts."""
        shifts = [
            (r.get("measures") or {}).get("resonance")
            for r in before
            if r.get("scoring") == SCORING
        ]
        shifts.append(result["measures"]["resonance"])
        if len(shifts) < 3 or None in shifts:
            return
        middle = float(np.median(shifts))
        percent = (2 ** (middle / 12) - 1) * 100
        if abs(percent) < 2:  # within 2% reads as level
            where = "level with the target"
        else:
            where = f"about {abs(percent):.0f}% {'brighter' if percent > 0 else 'darker'}"
        result["measures"]["resonance_3"] = round(middle, 2)
        for part in result["components"]:
            if part["key"] == "resonance":
                part["detail"] += f"; your last 3 takes together: {where}"

    @app.get("/api/targets/{target_id}/takes/{take_id}")
    def get_take(target_id: str, take_id: str) -> dict:
        """One take's whole record."""
        return store.take(target_id, take_id)

    @app.get("/api/targets/{target_id}/takes/{take_id}/audio")
    def take_audio(target_id: str, take_id: str) -> FileResponse:
        """One take's audio, as WAV."""
        return FileResponse(store.take_audio(target_id, take_id), media_type="audio/wav")

    # --- TRAINER ---

    @app.post("/api/targets/{target_id}/drill")
    async def drill(target_id: str, request: Request, word: int) -> dict:
        """An attempt at one word of the line, measured against that word, to
        train one part at a time. The attempt is kept, with its audio."""
        # A single word is too short to pick its own formant ceiling
        # dependably, so it's measured at the one your full takes on this
        # voice and the voice's lines settle on, as the line is: readings then
        # move only when your voice does.
        ceiling = await run_in_threadpool(pair_ceiling, target_id)
        target = await run_in_threadpool(steady_target, target_id, ceiling)
        _, timed = await run_in_threadpool(target_words, target_id, target)
        if not 0 <= word < len(timed):
            raise HTTPException(404, "That word isn't in the line.")
        y = await uploaded_audio(request)
        try:
            attempt = await run_in_threadpool(analyze, y, ceiling)
        except AnalysisError as exc:
            raise HTTPException(422, str(exc)) from exc
        result = await run_in_threadpool(word_drill, target, timed[word], attempt)
        result["ceiling"] = attempt.formant_ceiling
        # Kept, with its audio, so training builds up across sessions.
        record = result | {"word": word, "start": timed[word]["start"]}
        return store.add_drill(target_id, attempt, record)

    @app.get("/api/targets/{target_id}/drills")
    def list_drills(target_id: str) -> list[dict]:
        """Every trainer attempt on the line, oldest first."""
        return store.list_drills(target_id)

    @app.get("/api/targets/{target_id}/drills/{drill_id}/audio")
    def drill_audio(target_id: str, drill_id: str) -> FileResponse:
        """One trainer attempt's audio, as WAV."""
        return FileResponse(store.drill_audio(target_id, drill_id), media_type="audio/wav")

    # --- LONG RECORDINGS ---
    # Split into lines to pick from.

    @app.get("/api/sources")
    def list_sources() -> list[dict]:
        """Every recording, oldest first, each unfinished one with a job working on it."""
        return [start_job(meta) for meta in store.list_sources()]

    @app.post("/api/sources", status_code=201)
    async def upload_source(
        request: Request,
        name: str = "",
        filename: str = "",
        voice: str = "",
    ) -> dict:
        """A long audio or video file, streamed to disk as it arrives."""
        ext = Path(filename).suffix.lower().lstrip(".")
        if not re.fullmatch(r"[a-z0-9]{1,5}", ext):
            ext = "bin"  # FFmpeg reads the format from the contents anyway
        title = name.strip()[:80] or Path(filename).stem.strip()[:80] or "Untitled"
        meta, folder = store.new_source(title, status="uploading")
        owner = known_voice(voice) or store.voice_named(title)
        store.update_source(meta["id"], voice_id=owner["id"])
        size = 0
        try:
            # Small writes, one per awaited chunk.
            with open(folder / f"upload.{ext}", "wb") as f:  # noqa: ASYNC230
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_SOURCE_BYTES:
                        raise HTTPException(413, "That file is over 4 GB.")
                    f.write(chunk)
            if not size:
                raise HTTPException(400, "No file received.")
        except BaseException:
            store.delete_source(meta["id"])
            raise
        return start_job(store.update_source(meta["id"], status="queued"))

    @app.post("/api/sources/link", status_code=201)
    async def add_link(request: Request) -> dict:
        """A video page to download, such as a YouTube link."""
        body = await request.json()
        url = str(body.get("url", "")).strip()
        if not sources.is_link(url):
            raise HTTPException(422, "Paste the whole link, starting with https://")
        name = str(body.get("name", "")).strip()[:80]
        meta, _ = store.new_source(name, link=url)
        owner = known_voice(str(body.get("voice", "")))
        if owner is None:
            # A new voice straight away, so its page can show the progress; the
            # job names it after the video once the title is known.
            owner = store.new_voice(name or "New voice")
            if not name:
                owner = store.update_voice(owner["id"], name_pending=True)
        meta = store.update_source(meta["id"], voice_id=owner["id"])
        return start_job(meta)

    @app.get("/api/sources/{source_id}")
    def get_source(source_id: str) -> dict:
        """The recording, and once it's ready: its lines, the ones to start
        with, the voice at a glance, and how your takes on it compare."""
        meta = start_job(store.source_meta(source_id))
        lines = store.source_lines(source_id) if meta["status"] == "ready" else None
        extra = {
            "lines": lines,
            "suggested": [],
            "summary": None,
            "speakers": [],
            "speaker": meta.get("speaker") or 0,
        }
        if lines:
            chosen = extra["speaker"]
            for row in lines:  # "other voice" means: not the speaker this voice is of
                row["flags"] = sources.line_flags(row, chosen)
            theirs = [r for r in lines if r.get("speaker") in (None, chosen)]
            extra |= {
                "suggested": sources.suggest(lines, speaker=chosen),
                "summary": sources.summary(theirs),
                "speakers": sources.speaker_summary(lines),
                "good": sum(1 for r in lines if not r["flags"]),
            }
        return meta | extra

    def recording_speakers(meta: dict) -> dict:
        """A recording as the voice's page lists it, with who speaks in it."""
        lines = store.source_lines(meta["id"]) if meta["status"] == "ready" else None
        found = sources.speaker_summary(lines) if lines else []
        for s in found:
            row = lines[s.pop("sample")]
            s["sample"] = {"start": row["start"], "end": row["end"], "text": row["text"]}
        return meta | {"speakers": found, "speaker": meta.get("speaker") or 0}

    @app.patch("/api/sources/{source_id}")
    async def edit_source(source_id: str, request: Request) -> dict:
        """Change a recording: `link`, the video it came from (for a file
        that was downloaded elsewhere), or `speaker`."""
        meta = store.source_meta(source_id)
        body = await request.json()
        if "link" in body:
            url = str(body["link"]).strip()
            if not sources.is_link(url):
                raise HTTPException(422, "Paste the whole link, starting with https://")
            try:
                title, channel = await run_in_threadpool(sources.video_info, url)
            except sources.SourceError as exc:
                raise HTTPException(422, str(exc)) from exc
            meta = store.update_source(source_id, link=url, title=title, channel=channel)
            if "speaker" not in body:
                return {"recording": recording_speakers(meta), "removed": 0, "added": []}
        return await choose_speaker(meta, number(body, "speaker", meta.get("speaker") or 0))

    async def choose_speaker(meta: dict, speaker: int) -> dict:
        """Which speaker of the recording its voice is of. Lines Echo picked
        from the old one, and never practised, are swapped for the new one's."""
        # 1. The speaker, if the recording has one by that number.
        source_id = meta["id"]
        lines = store.source_lines(source_id) or []
        if speaker not in {r.get("speaker") for r in lines}:
            raise HTTPException(422, "There's no such speaker in that recording.")
        meta = store.update_source(source_id, speaker=speaker)
        # 2. Remove the lines Echo picked from this recording that were never practised.
        removed = 0
        for line in voice_lines(meta["voice_id"]):
            # Practised: it has takes, or attempts in the trainer.
            practised = line.get("takes") or store.list_drills(line["id"])
            if line.get("source") == source_id and line.get("auto") and not practised:
                store.delete_target(line["id"])
                forget(line["id"])
                removed += 1
        # 3. Pick the best lines again.
        added = await run_in_threadpool(autopick.pick, store, meta["voice_id"])
        return {"recording": recording_speakers(meta), "removed": removed, "added": added}

    @app.post("/api/sources/{source_id}/split", status_code=201)
    async def split_speaker(source_id: str, request: Request) -> dict:
        """Make one speaker of a recording into a voice of its own: a copy of
        the recording for the new voice, with that speaker chosen, and its best
        lines picked."""
        # 1. The speaker, and a voice for them.
        meta = store.source_meta(source_id)
        body = await request.json()
        speaker = number(body, "speaker", 0)
        lines = store.source_lines(source_id) or []
        if speaker not in {r.get("speaker") for r in lines}:
            raise HTTPException(422, "There's no such speaker in that recording.")
        name = str(body.get("name", "")).strip()[:80] or f"{meta['name']}, speaker {speaker + 1}"
        voice = store.new_voice(name)
        # 2. Copy the recording. "uploading" until the copy is whole: if the app
        #    stops part-way, it is marked failed on the next start instead of
        #    passing for ready.
        copy, folder = store.new_source(
            meta["name"],
            link=meta.get("link"),
            file=meta.get("file"),
            status="uploading",
        )
        old = store.source_dir(source_id)
        try:
            for part in (sources.AUDIO, sources.LINES):
                shutil.copy2(old / part, folder / part)
        except OSError as exc:
            store.delete_source(copy["id"])
            store.delete_voice(voice["id"])
            raise HTTPException(507, f"Couldn't copy the recording: {exc}") from exc
        keep = {k: meta.get(k) for k in ("duration", "count", "title", "channel", "speakers")}
        store.update_source(
            copy["id"],
            voice_id=voice["id"],
            speaker=speaker,
            status="ready",
            **keep,
        )
        # 3. Pick the new voice's best lines.
        added = await run_in_threadpool(autopick.pick, store, voice["id"])
        return {"voice": voice_card(store.voice_meta(voice["id"])), "added": added}

    @app.get("/api/sources/{source_id}/audio")
    def source_audio(source_id: str, start: float, end: float) -> Response:
        """A stretch of the recording as WAV, to play a line or trim it."""
        folder = store.source_dir(source_id)
        if not (folder / sources.AUDIO).is_file():
            raise HTTPException(409, "That recording isn't ready yet.")
        start = max(0.0, start)
        if not end > start or end - start > MAX_SLICE_SECONDS:
            raise HTTPException(422, f"Ask for 0 to {MAX_SLICE_SECONDS:.0f} s of the recording.")
        y = sources.read_audio(folder, start, end)
        buffer = io.BytesIO()
        sf.write(buffer, y, SR, format="WAV", subtype="PCM_16")
        return Response(buffer.getvalue(), media_type="audio/wav")

    @app.delete("/api/sources/{source_id}")
    def delete_source(source_id: str) -> Response:
        """Delete a recording, stopping its job."""
        remove_source(source_id)
        return Response(status_code=204)

    def remove_source(source_id: str) -> None:
        """Stop the recording's job if it's ours, then delete the recording; 409
        if another program is processing it or its files are still in use."""
        folder = store.source_dir(source_id)
        job = jobs.pop(source_id, None)
        if job is not None and job.poll() is None:
            # Our own CPU job; nothing is lost but this recording. A venv's
            # python.exe on Windows is a launcher with the real interpreter as
            # its child, so the whole tree has to go (/T), forced (/F).
            if sys.platform == "win32":
                kill = ["taskkill", "/F", "/T", "/PID", str(job.pid)]
                subprocess.run(kill, capture_output=True, check=False)
            else:
                job.terminate()
            job.wait(timeout=10)
        if sources.job_running(folder):
            raise HTTPException(
                409,
                "Another program is still processing that recording; try again when it's done.",
            )
        try:
            store.delete_source(source_id)
        except PermissionError as exc:
            raise HTTPException(
                409,
                "That recording is still in use; try again in a moment.",
            ) from exc

    # --- VOICES ---
    # A voice's lines and recordings are of one person.

    def voice_lines(voice_id: str) -> list[dict]:
        """The voice's lines, oldest first, each saying whether its pitch can be
        trusted (`pitch_suspect`)."""
        lines = [t for t in store.list_targets() if t.get("voice_id") == voice_id]
        for line in lines:
            line["pitch_suspect"] = pitch_suspect(line)
        return lines

    def check_set(meta: dict, lines: list[dict]) -> list[str]:
        """The lines the voice check uses: the ones picked, else the first five
        whose pitch can be trusted."""
        ids = [line["id"] for line in lines]
        if meta.get("check_lines") is None:
            return [line["id"] for line in lines if not line.get("pitch_suspect")][:CHECK_LINES]
        return [i for i in meta["check_lines"] if i in ids]

    def voice_card(meta: dict) -> dict:
        """The voice as the list of voices shows it: how many lines and
        recordings, its latest voice check's score and the one before, when it
        was last practised, and the lines its voice check uses."""
        lines = voice_lines(meta["id"])
        checks = store.list_checks(meta["id"])
        recordings = sum(1 for s in store.list_sources() if s.get("voice_id") == meta["id"])
        practised = (line["last"] for line in lines if line.get("last"))
        return meta | {
            "line_count": len(lines),
            "recording_count": recordings,
            "score": checks[-1]["average"] if checks else None,
            "previous": checks[-2]["average"] if len(checks) > 1 else None,
            "checked": checks[-1]["created"] if checks else None,
            "last_practised": max(practised, default=None),
            "check_set": check_set(meta, lines),
            "min_check_lines": MIN_CHECK_LINES,
        }

    def voice_summary(lines: list[dict], recordings: list[dict]) -> dict | None:
        """The voice at a glance: from its recordings' lines if it has any,
        else the pitch of its own lines."""
        rows = []
        for s in recordings:
            if s["status"] != "ready":
                continue
            chosen = s.get("speaker") or 0  # only the speaker the voice is of
            rows += [
                r | {"flags": sources.line_flags(r, chosen)}
                for r in store.source_lines(s["id"]) or []
                if r.get("speaker") in (None, chosen)
            ]
        if rows:
            return sources.summary(rows)
        pitches = [line["median_f0"] for line in lines if line.get("median_f0")]
        if not pitches:
            return None
        low, median, high = np.percentile(pitches, [10, 50, 90])
        return {
            "pitch": round(float(median)),
            "low": round(float(low)),
            "high": round(float(high)),
            "rate": None,
            "voiced": None,
        }

    def you_against(voice_id: str) -> dict | None:
        """Your typical gaps from this voice over your latest takes on any of
        its lines: the habits that show up in every line."""
        measured = [r["measures"] for r in store.takes_for_voice(voice_id) if r.get("measures")]
        if not measured:
            return None

        def typical(key):
            """The middle of one measure over the takes; None if none has it."""
            values = [m[key] for m in measured if m.get(key) is not None]
            return round(float(np.median(values)), 2) if values else None

        return {
            "takes": len(measured),
            "pitch": typical("pitch"),
            "resonance": typical("resonance"),
            "tempo": typical("tempo"),
            "take_f0": typical("take_f0"),
        }

    @app.get("/api/voices")
    def list_voices() -> list[dict]:
        """Every voice as the list of voices shows it, oldest first."""
        store.migrate()  # a link's voice is named once its title is known
        return [voice_card(meta) for meta in store.list_voices()]

    @app.post("/api/voices", status_code=201)
    async def add_voice(request: Request) -> dict:
        """A new voice, with no lines yet."""
        name = str((await request.json()).get("name", "")).strip()[:80]
        if not name:
            raise HTTPException(422, "Give the voice a name.")
        return voice_card(store.new_voice(name))

    @app.get("/api/voices/{voice_id}")
    def get_voice(voice_id: str) -> dict:
        """The voice's page: its lines, suggested lines, recordings, the voice
        at a glance, your typical gaps from it, how it reads on the gender
        scale, and its voice checks."""
        # 1. A voice merged into another is shown as that one.
        meta = store.voice_meta(voice_id)
        if meta.get("merged_into"):  # its video turned out to be a channel that had a voice
            return get_voice(meta["merged_into"])
        # 2. Its lines, with the pitch and gender reading filled in for lines from before
        #    these were kept.
        lines = voice_lines(voice_id)
        for line in lines:
            if "median_f0" not in line or "gender" not in line:
                clip = target_voice(line["id"])
                f0 = clip.median_f0
                line |= store.update_target(
                    line["id"],
                    median_f0=None if f0 is None else round(f0, 1),
                    gender=gender.score(clip),
                )
        # 3. The rest of the page.
        reads = [
            line["gender"]
            for line in lines
            if line.get("gender") is not None and not line.get("pitch_suspect")
        ]
        recordings = [
            recording_speakers(start_job(s))
            for s in store.list_sources()
            if s.get("voice_id") == voice_id
        ]
        checks = store.list_checks(voice_id)
        history = [{k: c[k] for k in ("id", "created", "average", "low", "high")} for c in checks]
        return voice_card(meta) | {
            "lines": lines,
            "suggestions": autopick.suggestions(store, voice_id),
            "recordings": recordings,
            "summary": voice_summary(lines, recordings),
            "you": you_against(voice_id),
            "gender": round(float(np.median(reads)), 1) if reads else None,
            "checks": history,
        }

    @app.get("/api/voices/{voice_id}/progress")
    def voice_progress(voice_id: str) -> dict:
        """Every take on the voice's lines, oldest first, and how much training:
        what the progress chart is drawn from."""
        store.voice_meta(voice_id)
        takes, drills = [], 0
        for line in voice_lines(voice_id):
            text = " ".join(line.get("words") or []) or line["name"]
            for take in store.list_takes(line["id"]):
                takes.append(take | {"target": line["id"], "line": text})
            drills += len(store.list_drills(line["id"]))
        takes.sort(key=lambda t: (t["created"], t["id"]))
        days = sorted({t["created"][:10] for t in takes})
        return {"takes": takes, "drills": drills, "days": days}

    @app.patch("/api/voices/{voice_id}")
    async def edit_voice(voice_id: str, request: Request) -> dict:
        """Rename the voice, or choose the lines its voice check uses."""
        body = await request.json()
        fields = {}
        if "name" in body:
            name = str(body["name"]).strip()[:80]
            if not name:
                raise HTTPException(422, "Give the voice a name.")
            fields["name"] = name
            fields["named_from"] = "hand"  # no more suggestions
            fields["name_pending"] = False  # and the video's title or channel won't replace it
        if "check_lines" in body:
            ids = {line["id"] for line in voice_lines(voice_id)}
            fields["check_lines"] = [str(i) for i in body["check_lines"] if str(i) in ids]
        return voice_card(store.update_voice(voice_id, **fields))

    @app.delete("/api/voices/{voice_id}")
    def delete_voice(voice_id: str) -> Response:
        """The voice, its lines with their takes, and its recordings."""
        store.voice_meta(voice_id)
        for s in store.list_sources():
            if s.get("voice_id") == voice_id:
                remove_source(s["id"])
        for line in voice_lines(voice_id):
            store.delete_target(line["id"])
            forget(line["id"])
        store.delete_voice(voice_id)
        return Response(status_code=204)

    @app.post("/api/voices/{voice_id}/lines", status_code=201)
    async def add_suggested_line(voice_id: str, request: Request) -> dict:
        """Add one line of one of the voice's recordings, as it is (no trim)."""
        body = await request.json()
        recording = store.source_meta(str(body.get("source", "")))
        if recording.get("voice_id") != voice_id:
            raise HTTPException(422, "That recording belongs to another voice.")
        # The line that starts there, to within 0.01 s.
        start = number(body, "start", -1.0, float)
        rows = store.source_lines(recording["id"]) or []
        row = next((r for r in rows if abs(r["start"] - start) < 0.01), None)
        if row is None:
            raise HTTPException(404, "That line isn't in the recording.")
        try:
            return await run_in_threadpool(autopick.add_from_recording, store, recording, row)
        except AnalysisError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/voices/{voice_id}/autopick")
    async def autopick_lines(voice_id: str, request: Request) -> dict:
        """Add the best suggested lines until the voice has `want` (5; 1 to 12)."""
        store.voice_meta(voice_id)
        want = max(1, min(12, number(await request.json(), "want", CHECK_LINES)))
        return {"added": await run_in_threadpool(autopick.pick, store, voice_id, want)}

    # --- VOICE CHECKS ---

    @app.post("/api/voices/{voice_id}/checks", status_code=201)
    async def add_check(voice_id: str, request: Request) -> dict:
        """Record a voice check from one take on each of several lines."""
        store.voice_meta(voice_id)
        rows = []
        for item in (await request.json()).get("takes", []):
            line = store.target_meta(str(item.get("target", "")))
            if line.get("voice_id") != voice_id:
                raise HTTPException(422, "That line belongs to another voice.")
            take = store.take(line["id"], str(item.get("take", "")))
            rows.append(
                {
                    "target": line["id"],
                    "take": take["id"],
                    "line": " ".join(line.get("words") or []) or line["name"],
                    "overall": take["overall"],
                    "parts": {c["key"]: c["score"] for c in take["components"]},
                    "measures": take.get("measures"),
                }
            )
        if len({r["target"] for r in rows}) < MIN_CHECK_LINES:
            raise HTTPException(
                422,
                f"A voice check needs at least {MIN_CHECK_LINES} different lines.",
            )
        return store.add_check(voice_id, voice_check(rows) | {"scoring": SCORING})

    @app.get("/api/voices/{voice_id}/checks/{check_id}")
    def get_check(voice_id: str, check_id: str) -> dict:
        """One voice check, whole."""
        return store.check(voice_id, check_id)

    # --- THE PAGE ---

    @app.get("/api/config")
    def config() -> dict:
        """What the page needs to know: the sample rate to record at, and Echo's version."""
        return {"sample_rate": SR, "version": __version__}

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    # Takes from before the scoring last changed are scored again in the background:
    # the app is usable meanwhile.
    threading.Thread(target=rescore, daemon=True, name="rescore").start()
    return app
