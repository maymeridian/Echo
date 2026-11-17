"""Local web app: the browser records, this scores and keeps the history."""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import autopick, clean, gender, line, sources
from .analysis import MAX_SECONDS, SR, AnalysisError, Voice, analyze, decode, load
from .scoring import CHECK_LINES, MIN_CHECK_LINES, coach, compare, voice_check, word_drill
from .store import NotFound, Store

MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 30 s of 16-bit mono at 16 kHz is under 1 MB
MAX_SOURCE_BYTES = 4 * 1024**3  # a long video
MAX_SLICE_SECONDS = 90.0  # of a long recording, sent to the page at once
SUSPECT_SEMITONES = 7.0  # a line this far from its voice's usual pitch has an untrustworthy pitch reading
JOB_ATTEMPTS = 3
FINISHED = {"ready", "failed"}
STATIC = Path(__file__).parent / "static"


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    store = Store(Path(data_dir or os.environ.get("ECHO_DATA", "data")))
    targets: dict[str, Voice] = {}  # analysed targets, so each take only analyses itself
    jobs: dict[str, subprocess.Popen] = {}  # source id -> the job processing it
    app = FastAPI(title="Echo")

    def start_job(meta: dict) -> dict:
        """Make sure an unfinished recording has a job working on it.

        A job is a separate process at below-normal priority (sources.py), so
        it keeps going if the page closes. If one stopped early, for example
        because the computer was shut down, it starts again, up to three times."""
        if meta["status"] in FINISHED or meta["status"] == "uploading":
            return meta
        job = jobs.get(meta["id"])
        if job is not None and job.poll() is None:
            return meta
        folder = store.source_dir(meta["id"])
        if sources.job_running(folder):  # a previous server's job, or `echo-voice import`
            return meta
        attempts = meta.get("attempts", 0)
        if attempts >= JOB_ATTEMPTS:
            return store.update_source(meta["id"], status="failed", error="Processing kept stopping before it finished.")
        meta = store.update_source(meta["id"], attempts=attempts + 1)
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW
        with open(folder / "job.log", "ab") as log:
            jobs[meta["id"]] = subprocess.Popen(
                [sys.executable, "-m", "echo_voice.sources", str(folder)],
                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags,
            )
        return meta

    store.migrate()  # data from before voices existed

    # Pick up recordings that were still being processed when the app last stopped.
    for meta in store.list_sources():
        if meta["status"] == "uploading":
            store.update_source(meta["id"], status="failed", error="The upload didn't finish.")
            continue
        if meta["status"] == "ready" and any("speaker" not in r for r in store.source_lines(meta["id"]) or []):
            # From before speakers were found: the job finds them, keeping the lines.
            meta = store.update_source(meta["id"], status="queued", attempts=0)
        start_job(meta)

    @app.exception_handler(NotFound)
    async def not_found(_: Request, __: NotFound) -> JSONResponse:
        return JSONResponse({"detail": "Not found."}, status_code=404)

    @app.middleware("http")
    async def always_check_for_a_newer_page(request: Request, call_next):
        # Without this the browser may keep using its saved copy of the page
        # for hours after an update (it guesses from the files' age).
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    async def uploaded_audio(request: Request):
        data = await request.body()
        if not data:
            raise HTTPException(400, "No audio received.")
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "That file is too large.")
        try:
            return await run_in_threadpool(decode, data)
        except AnalysisError as exc:
            raise HTTPException(422, str(exc)) from exc

    async def analysed(y) -> Voice:
        try:
            return await run_in_threadpool(analyze, y)
        except AnalysisError as exc:
            raise HTTPException(422, str(exc)) from exc

    async def uploaded_voice(request: Request) -> Voice:
        return await analysed(await uploaded_audio(request))

    def target_voice(target_id: str) -> Voice:
        if target_id not in targets:
            targets[target_id] = analyze(load(store.target_audio(target_id)))
        return targets[target_id]

    @app.get("/api/targets")
    def list_targets() -> list[dict]:
        return store.list_targets()

    def target_words(target_id: str, voice: Voice) -> tuple[list[str], list[dict]]:
        """The target's words, and its speech units: the words as they're said,
        with ones run together as one unit (line.speech_units). Both are worked
        out once and kept with the clip. The words can be corrected by hand;
        the units stay as heard, for the word-by-word comparison."""
        meta = store.target_meta(target_id)
        if meta.get("word_times") is None:  # targets from before timings were kept
            timed = line.transcribe_timed(voice.y)
            words = meta["words"] if meta.get("words") is not None else [w["text"] for w in timed]
            meta = store.update_target(target_id, words=words, word_times=timed)
        elif meta["word_times"] and not all(w.get("refined") for w in meta["word_times"]):
            # Timings from before word edges were moved onto the audio.
            meta = store.update_target(target_id, word_times=line.refine_word_times(voice.y, meta["word_times"]))
        if meta.get("units_v") != line.UNITS_VERSION:
            units = line.speech_units(voice.y, meta["word_times"])
            meta = store.update_target(target_id, units=units, units_v=line.UNITS_VERSION)
        return meta["words"], meta["units"]

    def known_voice(voice_id: str) -> dict | None:
        try:
            return store.voice_meta(voice_id) if voice_id else None
        except NotFound:
            return None

    @app.post("/api/targets", status_code=201)
    async def add_target(
        request: Request, name: str = "", source: str = "", line_start: float | None = None, voice: str = ""
    ) -> dict:
        """A new line. `voice`: whose it is; `source` and `line_start`: the
        recording and line it was cut from, which decide the voice if given."""
        original = await uploaded_audio(request)
        if len(original) > (MAX_SECONDS + 5) * SR:  # check before the slow cleaning step
            raise HTTPException(422, f"That clip is {len(original) / SR:.0f} s long — keep it under {MAX_SECONDS:.0f} s.")
        recording = None
        if source:
            try:
                recording = store.source_meta(source)
            except NotFound:
                recording = None  # deleted meanwhile; keep the clip anyway
        # Targets usually come from films or videos: take the voice out of any
        # music or background first (clean.py), and score that.
        cleaned = await analysed(await run_in_threadpool(clean.vocals, original))
        timed = await run_in_threadpool(line.transcribe_timed, cleaned.y)
        owner = (recording and known_voice(recording.get("voice_id") or "")) or known_voice(voice)
        if owner is None:  # older clients named a clip after its voice
            owner = store.voice_for_line(name or "Untitled")
        meta = store.add_target(
            name.strip()[:80] or "Untitled", cleaned, [w["text"] for w in timed], original, word_times=timed,
            source=recording and recording["id"], voice_name=recording and recording["name"],
            line_start=line_start if recording else None, voice_id=owner["id"],
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
        store.delete_target(target_id)
        targets.pop(target_id, None)
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
        drawn = voice.f0.copy()
        if f0.size >= 20:
            drawn[np.abs(12 * np.log2(np.where(np.isfinite(drawn), drawn, np.median(f0)) / np.median(f0))) > 7] = np.nan
        out = {
            "pitch_suspect": pitch_suspect(store.target_meta(target_id)),
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
        }
        if f0.size >= 20:
            low, median, high = np.percentile(f0, [10, 50, 90])
            out |= {"median_f0": round(float(median), 1), "low": round(float(low), 1), "high": round(float(high), 1)}
        return out

    @app.get("/api/targets/{target_id}/audio")
    def target_audio(target_id: str) -> FileResponse:
        return FileResponse(store.target_audio(target_id), media_type="audio/wav")

    @app.get("/api/targets/{target_id}/takes")
    def list_takes(target_id: str) -> list[dict]:
        return store.list_takes(target_id)

    @app.post("/api/targets/{target_id}/takes", status_code=201)
    async def add_take(target_id: str, request: Request, focus: str = "") -> dict:
        """Score a take and say what to work on next (`focus` picks it by hand)."""
        target = await run_in_threadpool(target_voice, target_id)
        take = await uploaded_voice(request)
        said, timed = await run_in_threadpool(target_words, target_id, target)
        words = await run_in_threadpool(line.transcribe, take.y)
        suspect = pitch_suspect(store.target_meta(target_id))
        result = await run_in_threadpool(compare, target, take, said, words, timed, suspect)
        earlier = store.list_takes(target_id)
        history = [t["overall"] for t in earlier]
        parts = {k: [t["parts"].get(k) for t in earlier] for k in ("pitch", "melody", "resonance", "articulation", "rhythm")}
        result["coach"] = coach(result, store.last_take(target_id), focus or None, history, parts)
        return store.add_take(target_id, take, result)

    @app.post("/api/targets/{target_id}/drill")
    async def drill(target_id: str, request: Request, word: int) -> dict:
        """An attempt at one word of the line, measured against that word, to
        train one part at a time. Nothing is saved."""
        target = await run_in_threadpool(target_voice, target_id)
        _, timed = await run_in_threadpool(target_words, target_id, target)
        if not 0 <= word < len(timed):
            raise HTTPException(404, "That word isn't in the line.")
        attempt = await uploaded_voice(request)
        result = await run_in_threadpool(word_drill, target, timed[word], attempt)
        # Kept, with its audio, so training builds up across sessions.
        return store.add_drill(target_id, attempt, result | {"word": word, "start": timed[word]["start"]})

    @app.get("/api/targets/{target_id}/drills")
    def list_drills(target_id: str) -> list[dict]:
        return store.list_drills(target_id)

    @app.get("/api/targets/{target_id}/drills/{drill_id}/audio")
    def drill_audio(target_id: str, drill_id: str) -> FileResponse:
        return FileResponse(store.drill_audio(target_id, drill_id), media_type="audio/wav")

    def typical_pitch(voice_id: str | None, but: str | None = None) -> float | None:
        """The voice's usual pitch: from its recordings, else its other lines."""
        if not voice_id:
            return None
        rows = []
        for s in store.list_sources():
            if s.get("voice_id") == voice_id and s["status"] == "ready":
                chosen = s.get("speaker") or 0
                rows += [r for r in store.source_lines(s["id"]) or [] if r.get("speaker") in (None, chosen) and r["pitch"]]
        if len(rows) >= 5:
            return float(np.median([r["pitch"] for r in rows]))
        others = [t["median_f0"] for t in store.list_targets() if t.get("voice_id") == voice_id and t["id"] != but and t.get("median_f0")]
        return float(np.median(others)) if len(others) >= 3 else None

    def pitch_suspect(line: dict) -> bool:
        """A line whose pitch sits far from its voice's usual: creaky voice, or
        a pitch tracker off by an octave. Its pitch can't be trusted to score."""
        usual = typical_pitch(line.get("voice_id"), but=line["id"])
        f0 = line.get("median_f0")
        return bool(usual and f0 and abs(12 * np.log2(f0 / usual)) > SUSPECT_SEMITONES)

    @app.get("/api/targets/{target_id}/takes/{take_id}")
    def get_take(target_id: str, take_id: str) -> dict:
        return store.take(target_id, take_id)

    @app.get("/api/targets/{target_id}/takes/{take_id}/audio")
    def take_audio(target_id: str, take_id: str) -> FileResponse:
        return FileResponse(store.take_audio(target_id, take_id), media_type="audio/wav")

    # ---------- long recordings: split into lines to pick from ----------

    @app.get("/api/sources")
    def list_sources() -> list[dict]:
        return [start_job(meta) for meta in store.list_sources()]

    @app.post("/api/sources", status_code=201)
    async def upload_source(request: Request, name: str = "", filename: str = "", voice: str = "") -> dict:
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
            with open(folder / f"upload.{ext}", "wb") as f:
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
        extra = {"lines": lines, "suggested": [], "summary": None, "speakers": [], "speaker": meta.get("speaker") or 0}
        if lines:
            chosen = extra["speaker"]
            for row in lines:  # "other voice" means: not the speaker this voice is of
                row["flags"] = sources.line_flags(row, chosen)
            extra |= {
                "suggested": sources.suggest(lines, speaker=chosen),
                "summary": sources.summary([r for r in lines if r.get("speaker") in (None, chosen)]),
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
        return await choose_speaker(meta, int(body.get("speaker", meta.get("speaker") or 0)))

    async def choose_speaker(meta: dict, speaker: int) -> dict:
        """Which speaker of the recording its voice is of. Lines Echo picked
        from the old one, and never practised, are swapped for the new one's."""
        source_id = meta["id"]
        lines = store.source_lines(source_id) or []
        if speaker not in {r.get("speaker") for r in lines}:
            raise HTTPException(422, "There's no such speaker in that recording.")
        meta = store.update_source(source_id, speaker=speaker)
        removed = 0
        for line in voice_lines(meta["voice_id"]):
            if line.get("source") == source_id and line.get("auto") and not line.get("takes"):
                store.delete_target(line["id"])
                targets.pop(line["id"], None)
                removed += 1
        added = await run_in_threadpool(autopick.pick, store, meta["voice_id"])
        return {"recording": recording_speakers(meta), "removed": removed, "added": added}

    @app.post("/api/sources/{source_id}/split", status_code=201)
    async def split_speaker(source_id: str, request: Request) -> dict:
        """Make one speaker of a recording into a voice of its own: a copy of
        the recording for the new voice, with that speaker chosen, and its best
        lines picked."""
        meta = store.source_meta(source_id)
        body = await request.json()
        speaker = int(body.get("speaker", 0))
        lines = store.source_lines(source_id) or []
        if speaker not in {r.get("speaker") for r in lines}:
            raise HTTPException(422, "There's no such speaker in that recording.")
        name = str(body.get("name", "")).strip()[:80] or f"{meta['name']}, speaker {speaker + 1}"
        voice = store.new_voice(name)
        copy, folder = store.new_source(meta["name"], link=meta.get("link"), file=meta.get("file"), status="ready")
        old = store.source_dir(source_id)
        for part in (sources.AUDIO, sources.LINES):
            shutil.copy2(old / part, folder / part)
        keep = {k: meta.get(k) for k in ("duration", "count", "title", "channel", "speakers")}
        store.update_source(copy["id"], voice_id=voice["id"], speaker=speaker, **keep)
        added = await run_in_threadpool(autopick.pick, store, voice["id"])
        return {"voice": voice_card(store.voice_meta(voice["id"])), "added": added}

    def you_against(voice_id: str) -> dict | None:
        """Your typical gaps from this voice over your latest takes on any of
        its lines: the habits that show up in every line."""
        measured = [r["measures"] for r in store.takes_for_voice(voice_id) if r.get("measures")]
        if not measured:
            return None

        def typical(key):
            values = [m[key] for m in measured if m.get(key) is not None]
            return round(float(np.median(values)), 2) if values else None

        return {
            "takes": len(measured),
            "pitch": typical("pitch"),
            "resonance": typical("resonance"),
            "tempo": typical("tempo"),
            "take_f0": typical("take_f0"),
        }

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
        remove_source(source_id)
        return Response(status_code=204)

    def remove_source(source_id: str) -> None:
        folder = store.source_dir(source_id)
        job = jobs.pop(source_id, None)
        if job is not None and job.poll() is None:
            # Our own CPU job; nothing is lost but this recording. A venv's
            # python.exe on Windows is a launcher with the real interpreter as
            # its child, so the whole tree has to go.
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(job.pid)], capture_output=True)
            else:
                job.terminate()
            job.wait(timeout=10)
        if sources.job_running(folder):
            raise HTTPException(409, "Another program is still processing that recording; try again when it's done.")
        try:
            store.delete_source(source_id)
        except PermissionError as exc:
            raise HTTPException(409, "That recording is still in use; try again in a moment.") from exc

    # ---------- voices: lines and recordings of one person, and the voice check ----------

    def voice_lines(voice_id: str) -> list[dict]:
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
        lines = voice_lines(meta["id"])
        checks = store.list_checks(meta["id"])
        return meta | {
            "line_count": len(lines),
            "recording_count": sum(1 for s in store.list_sources() if s.get("voice_id") == meta["id"]),
            "score": checks[-1]["average"] if checks else None,
            "previous": checks[-2]["average"] if len(checks) > 1 else None,
            "checked": checks[-1]["created"] if checks else None,
            "last_practised": max((line["last"] for line in lines if line.get("last")), default=None),
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
        return {"pitch": round(float(median)), "low": round(float(low)), "high": round(float(high)), "rate": None, "voiced": None}

    @app.get("/api/voices")
    def list_voices() -> list[dict]:
        store.migrate()  # a link's voice is named once its title is known
        return [voice_card(meta) for meta in store.list_voices()]

    @app.post("/api/voices", status_code=201)
    async def add_voice(request: Request) -> dict:
        name = str((await request.json()).get("name", "")).strip()[:80]
        if not name:
            raise HTTPException(422, "Give the voice a name.")
        return voice_card(store.new_voice(name))

    @app.get("/api/voices/{voice_id}")
    def get_voice(voice_id: str) -> dict:
        meta = store.voice_meta(voice_id)
        if meta.get("merged_into"):  # its video turned out to be a channel that had a voice
            return get_voice(meta["merged_into"])
        lines = voice_lines(voice_id)
        for line in lines:
            if "median_f0" not in line or "gender" not in line:  # lines from before these were kept
                analysed = target_voice(line["id"])
                f0 = analysed.median_f0
                line |= store.update_target(
                    line["id"], median_f0=None if f0 is None else round(f0, 1), gender=gender.score(analysed)
                )
        reads = [line["gender"] for line in lines if line.get("gender") is not None and not line.get("pitch_suspect")]
        recordings = [recording_speakers(start_job(s)) for s in store.list_sources() if s.get("voice_id") == voice_id]
        checks = store.list_checks(voice_id)
        return voice_card(meta) | {
            "lines": lines,
            "suggestions": autopick.suggestions(store, voice_id),
            "recordings": recordings,
            "summary": voice_summary(lines, recordings),
            "you": you_against(voice_id),
            "gender": round(float(np.median(reads)), 1) if reads else None,
            "checks": [{k: c[k] for k in ("id", "created", "average", "low", "high")} for c in checks],
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
        body = await request.json()
        fields = {}
        if "name" in body:
            name = str(body["name"]).strip()[:80]
            if not name:
                raise HTTPException(422, "Give the voice a name.")
            fields["name"] = name
            fields["named_from"] = "hand"  # no more suggestions
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
            targets.pop(line["id"], None)
        store.delete_voice(voice_id)
        return Response(status_code=204)

    @app.post("/api/voices/{voice_id}/lines", status_code=201)
    async def add_suggested_line(voice_id: str, request: Request) -> dict:
        """Add one line of one of the voice's recordings, as it is (no trim)."""
        body = await request.json()
        recording = store.source_meta(str(body.get("source", "")))
        if recording.get("voice_id") != voice_id:
            raise HTTPException(422, "That recording belongs to another voice.")
        start = float(body.get("start", -1))
        row = next((r for r in store.source_lines(recording["id"]) or [] if abs(r["start"] - start) < 0.01), None)
        if row is None:
            raise HTTPException(404, "That line isn't in the recording.")
        try:
            return await run_in_threadpool(autopick.add_from_recording, store, recording, row)
        except AnalysisError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/voices/{voice_id}/autopick")
    async def autopick_lines(voice_id: str, request: Request) -> dict:
        """Add the best suggested lines until the voice has `want` (5)."""
        store.voice_meta(voice_id)
        want = max(1, min(12, int((await request.json()).get("want", CHECK_LINES))))
        return {"added": await run_in_threadpool(autopick.pick, store, voice_id, want)}

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
            rows.append({
                "target": line["id"],
                "take": take["id"],
                "line": " ".join(line.get("words") or []) or line["name"],
                "overall": take["overall"],
                "parts": {c["key"]: c["score"] for c in take["components"]},
                "measures": take.get("measures"),
            })
        if len({r["target"] for r in rows}) < MIN_CHECK_LINES:
            raise HTTPException(422, f"A voice check needs at least {MIN_CHECK_LINES} different lines.")
        return store.add_check(voice_id, voice_check(rows))

    @app.get("/api/voices/{voice_id}/checks/{check_id}")
    def get_check(voice_id: str, check_id: str) -> dict:
        return store.check(voice_id, check_id)

    @app.get("/api/config")
    def config() -> dict:
        return {"sample_rate": SR}

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app
