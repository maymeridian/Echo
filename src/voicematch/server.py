"""Local web app: the browser records, this scores and keeps the history."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import clean, line
from .analysis import MAX_SECONDS, SR, AnalysisError, Voice, analyze, decode, load
from .scoring import compare
from .store import NotFound, Store

MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 30 s of 16-bit mono at 16 kHz is under 1 MB
STATIC = Path(__file__).parent / "static"


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    store = Store(Path(data_dir or os.environ.get("VOICEMATCH_DATA", "data")))
    targets: dict[str, Voice] = {}  # analysed targets, so each take only analyses itself
    app = FastAPI(title="Voice Match")

    @app.exception_handler(NotFound)
    async def not_found(_: Request, __: NotFound) -> JSONResponse:
        return JSONResponse({"detail": "Not found."}, status_code=404)

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

    def target_words(target_id: str, voice: Voice) -> list[str]:
        """The target's words, transcribed once and kept with the clip."""
        meta = store.target_meta(target_id)
        if meta.get("words") is None:
            meta = store.update_target(target_id, words=line.transcribe(voice.y))
        return meta["words"]

    @app.post("/api/targets", status_code=201)
    async def add_target(request: Request, name: str = "") -> dict:
        original = await uploaded_audio(request)
        if len(original) > (MAX_SECONDS + 5) * SR:  # check before the slow cleaning step
            raise HTTPException(422, f"That clip is {len(original) / SR:.0f} s long — keep it under {MAX_SECONDS:.0f} s.")
        # Targets usually come from films or videos: take the voice out of any
        # music or background first (clean.py), and score that.
        voice = await analysed(await run_in_threadpool(clean.vocals, original))
        words = await run_in_threadpool(line.transcribe, voice.y)
        meta = store.add_target(name.strip()[:80] or "Untitled", voice, words, original)
        targets[meta["id"]] = voice
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
        """The target's pitch band, for the live view while recording."""
        voice = target_voice(target_id)
        f0 = voice.f0[voice.voiced]
        if f0.size < 20:
            return {"median_f0": None, "low": None, "high": None}
        low, median, high = np.percentile(f0, [10, 50, 90])
        return {"median_f0": round(float(median), 1), "low": round(float(low), 1), "high": round(float(high), 1)}

    @app.get("/api/targets/{target_id}/audio")
    def target_audio(target_id: str) -> FileResponse:
        return FileResponse(store.target_audio(target_id), media_type="audio/wav")

    @app.get("/api/targets/{target_id}/takes")
    def list_takes(target_id: str) -> list[dict]:
        return store.list_takes(target_id)

    @app.post("/api/targets/{target_id}/takes", status_code=201)
    async def add_take(target_id: str, request: Request) -> dict:
        target = await run_in_threadpool(target_voice, target_id)
        take = await uploaded_voice(request)
        said = await run_in_threadpool(target_words, target_id, target)
        words = await run_in_threadpool(line.transcribe, take.y)
        result = await run_in_threadpool(compare, target, take, said, words)
        return store.add_take(target_id, take, result)

    @app.get("/api/targets/{target_id}/takes/{take_id}")
    def get_take(target_id: str, take_id: str) -> dict:
        return store.take(target_id, take_id)

    @app.get("/api/targets/{target_id}/takes/{take_id}/audio")
    def take_audio(target_id: str, take_id: str) -> FileResponse:
        return FileResponse(store.take_audio(target_id, take_id), media_type="audio/wav")

    @app.get("/api/config")
    def config() -> dict:
        return {"sample_rate": SR}

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app
