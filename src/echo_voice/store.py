"""Voices, their lines (targets), takes and long recordings on disk.

    <data>/voices/<voice_id>/meta.json           name, which lines the voice check uses
                            /checks/<check_id>.json
    <data>/targets/<target_id>/meta.json         one line; `voice_id` says whose
                              /target.wav
                              /takes/<take_id>.wav
                              /takes/<take_id>.json
    <data>/sources/<source_id>/meta.json     status of the job (sources.py)
                              /audio.flac    16 kHz mono
                              /lines.json
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import time
from datetime import datetime
from pathlib import Path

import soundfile as sf

from . import gender
from .analysis import SR, Voice

_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")


class NotFound(LookupError):
    pass


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Store:
    def __init__(self, root: Path):
        self.root = Path(root) / "targets"
        self.root.mkdir(parents=True, exist_ok=True)
        self.sources = Path(root) / "sources"
        self.sources.mkdir(parents=True, exist_ok=True)
        self.voices = Path(root) / "voices"
        self.voices.mkdir(parents=True, exist_ok=True)

    # ---------- voices: a person or character, with lines and recordings ----------

    def new_voice(self, name: str) -> dict:
        meta = {"id": secrets.token_hex(6), "name": name, "created": _now(), "check_lines": None}
        (self.voices / meta["id"] / "checks").mkdir(parents=True)
        (self.voices / meta["id"] / "meta.json").write_text(json.dumps(meta, indent=2), "utf-8")
        return meta

    def voice_dir(self, voice_id: str) -> Path:
        path = self.voices / voice_id
        if not _ID.match(voice_id) or not (path / "meta.json").is_file():
            raise NotFound(voice_id)
        return path

    def voice_meta(self, voice_id: str) -> dict:
        return json.loads((self.voice_dir(voice_id) / "meta.json").read_text("utf-8"))

    def update_voice(self, voice_id: str, **fields) -> dict:
        meta = self.voice_meta(voice_id) | fields
        (self.voice_dir(voice_id) / "meta.json").write_text(json.dumps(meta, indent=2), "utf-8")
        return meta

    def list_voices(self) -> list[dict]:
        """Every voice, oldest first; not one merged into another."""
        metas = [json.loads(p.read_text("utf-8")) for p in self.voices.glob("*/meta.json")]
        return sorted((m for m in metas if not m.get("merged_into")), key=lambda m: m["created"])

    def voice_named(self, name: str) -> dict:
        """The voice with this name, made if there isn't one."""
        name = name.strip()[:80] or "Untitled"
        for meta in self.list_voices():
            if meta["name"] == name:
                return meta
        return self.new_voice(name)

    def delete_voice(self, voice_id: str) -> None:
        shutil.rmtree(self.voice_dir(voice_id))

    def tick_line(self, voice_id: str, line_id: str, limit: int) -> None:
        """Put a new line in the voice check if lines were picked for it by
        hand and there's room. (With none picked, the check takes the first
        lines anyway.)"""
        meta = self.voice_meta(voice_id)
        chosen = meta.get("check_lines")
        if chosen is None:
            return
        existing = {p.parent.name for p in self.root.glob("*/meta.json")}
        chosen = [i for i in chosen if i in existing]
        if len(chosen) < limit and line_id not in chosen:
            self.update_voice(voice_id, check_lines=chosen + [line_id])

    def add_check(self, voice_id: str, record: dict) -> dict:
        record = {"id": f"{datetime.now():%Y%m%d-%H%M%S-%f}-{secrets.token_hex(2)}", "created": _now(), **record}
        folder = self.voice_dir(voice_id) / "checks"
        folder.mkdir(exist_ok=True)
        (folder / f"{record['id']}.json").write_text(json.dumps(record), "utf-8")
        return record

    def list_checks(self, voice_id: str) -> list[dict]:
        return [
            json.loads(path.read_text("utf-8"))
            for path in sorted((self.voice_dir(voice_id) / "checks").glob("*.json"))
        ]

    def check(self, voice_id: str, check_id: str) -> dict:
        path = self.voice_dir(voice_id) / "checks" / f"{check_id}.json"
        if not _ID.match(check_id) or not path.is_file():
            raise NotFound(check_id)
        return json.loads(path.read_text("utf-8"))

    def migrate(self) -> None:
        """Give every recording and line a voice. Before voices existed, a
        recording's name stood for the voice, and a line cut from it was named
        "<recording>: <words>"; a clip's own name was the voice's."""
        for meta in self.list_sources():
            if not meta.get("voice_id") and meta["status"] != "uploading":
                self.update_source(meta["id"], voice_id=self.voice_named(meta["name"] or "Untitled")["id"])
        recordings = {m["id"]: m for m in self.list_sources()}
        for path in self.root.glob("*/meta.json"):
            meta = json.loads(path.read_text("utf-8"))
            if meta.get("voice_id"):
                continue
            recording = recordings.get(meta.get("source") or "")
            if recording and recording.get("voice_id"):
                voice_id = recording["voice_id"]
            else:
                voice_id = self.voice_for_line(meta["name"])["id"]
            self.update_target(meta["id"], voice_id=voice_id)
        self._link_old_lines(recordings)

    def _link_old_lines(self, recordings: dict) -> None:
        """Lines cut from a recording before lines kept a link to it. Their
        name is "<recording>: <the line's text, cut at 50 characters…>", so the
        recording's line that starts with that text is the one, if only one does."""
        by_name = {m["name"]: m for m in recordings.values() if m["status"] == "ready"}
        for path in self.root.glob("*/meta.json"):
            meta = json.loads(path.read_text("utf-8"))
            if meta.get("source") or ": " not in meta["name"]:
                continue
            prefix, text = meta["name"].split(": ", 1)
            recording = by_name.get(prefix)
            text = text.rstrip("…").strip()
            if not recording or len(text) < 8:
                continue
            matches = [r for r in self.source_lines(recording["id"]) or [] if r["text"].startswith(text)]
            if len(matches) == 1:
                self.update_target(
                    meta["id"], source=recording["id"], voice=recording["name"], line_start=matches[0]["start"]
                )

    def voice_for_line(self, name: str) -> dict:
        """The voice a line named the old way belongs to: "<voice>: <words>",
        or a clip named after its voice."""
        names = {v["name"]: v for v in self.list_voices()}
        prefix = name.split(": ", 1)[0]
        return names.get(prefix) or names.get(name) or self.voice_named(name)

    # ---------- long recordings ----------

    def new_source(
        self, name: str, link: str | None = None, file: str | None = None, status: str = "queued"
    ) -> tuple[dict, Path]:
        """A folder for a recording that a job (sources.py) will process."""
        meta = {
            "id": secrets.token_hex(6),
            "name": name,
            "created": _now(),
            "link": link,
            "file": file,
            "status": status,
            "progress": None,
            "duration": None,
            "count": None,  # lines found
            "good": None,  # lines without flags
            "error": None,
        }
        path = self.sources / meta["id"]
        path.mkdir(parents=True)
        (path / "meta.json").write_text(json.dumps(meta, indent=2), "utf-8")
        return meta, path

    def source_dir(self, source_id: str) -> Path:
        path = self.sources / source_id
        if not _ID.match(source_id) or not (path / "meta.json").is_file():
            raise NotFound(source_id)
        return path

    def source_meta(self, source_id: str) -> dict:
        path = self.source_dir(source_id) / "meta.json"
        for attempt in range(5):
            # The job replaces the file atomically, but Windows can refuse a
            # read during the replace.
            try:
                return json.loads(path.read_text("utf-8"))
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)

    def update_source(self, source_id: str, **fields) -> dict:
        meta = self.source_meta(source_id) | fields
        (self.source_dir(source_id) / "meta.json").write_text(json.dumps(meta, indent=2), "utf-8")
        return meta

    def list_sources(self) -> list[dict]:
        metas = [self.source_meta(p.parent.name) for p in self.sources.glob("*/meta.json")]
        return sorted(metas, key=lambda m: m["created"])

    def source_lines(self, source_id: str) -> list[dict] | None:
        path = self.source_dir(source_id) / "lines.json"
        return json.loads(path.read_text("utf-8")) if path.is_file() else None

    def delete_source(self, source_id: str) -> None:
        path = self.source_dir(source_id)
        for attempt in range(50):
            # A job that has just finished can hold its log open for a moment
            # while Python shuts down, and Windows won't delete an open file.
            try:
                shutil.rmtree(path)
                return
            except PermissionError:
                if attempt == 49:
                    raise
                time.sleep(0.2)

    def _target_dir(self, target_id: str) -> Path:
        # Ids come straight from URLs; the pattern keeps them inside the root.
        path = self.root / target_id
        if not _ID.match(target_id) or not path.is_dir():
            raise NotFound(target_id)
        return path

    def _take_file(self, target_id: str, take_id: str, suffix: str) -> Path:
        path = self._target_dir(target_id) / "takes" / f"{take_id}{suffix}"
        if not _ID.match(take_id) or not path.is_file():
            raise NotFound(take_id)
        return path

    def list_targets(self) -> list[dict]:
        metas = []
        for p in self.root.glob("*/meta.json"):
            meta = json.loads(p.read_text("utf-8"))
            if "best" not in meta:  # targets from before the count was kept
                overalls = [json.loads(t.read_text("utf-8"))["overall"] for t in (p.parent / "takes").glob("*.json")]
                meta |= {"takes": len(overalls), "best": max(overalls, default=None)}
            metas.append(meta)
        return sorted(metas, key=lambda m: m["created"])

    def add_target(
        self,
        name: str,
        voice: Voice,
        words: list[str] | None = None,
        original=None,
        word_times: list[dict] | None = None,
        source: str | None = None,
        voice_name: str | None = None,
        line_start: float | None = None,
        voice_id: str | None = None,
        auto: bool = False,
    ) -> dict:
        """`voice` is the cleaned clip that gets scored; `original` (samples at
        SR) is the upload as it came, kept for reference. `source`, `voice_name`
        and `line_start` say which recording's line it came from, if any."""
        meta = {
            "id": secrets.token_hex(6),
            "name": name,
            "created": _now(),
            "duration": round(voice.duration, 2),
            "words": words,
            "word_times": word_times,
            "source": source,
            "voice": voice_name,
            "line_start": line_start,
            "voice_id": voice_id,
            "auto": auto,
            "median_f0": None if voice.median_f0 is None else round(voice.median_f0, 1),
            "gender": gender.score(voice),
            "takes": 0,
            "best": None,
        }
        path = self.root / meta["id"]
        (path / "takes").mkdir(parents=True)
        sf.write(path / "target.wav", voice.y, SR, subtype="PCM_16")
        if original is not None:
            sf.write(path / "original.wav", original, SR, subtype="PCM_16")
        (path / "meta.json").write_text(json.dumps(meta, indent=2), "utf-8")
        return meta

    def target_meta(self, target_id: str) -> dict:
        return json.loads((self._target_dir(target_id) / "meta.json").read_text("utf-8"))

    def update_target(self, target_id: str, **fields) -> dict:
        meta = self.target_meta(target_id) | fields
        (self._target_dir(target_id) / "meta.json").write_text(json.dumps(meta, indent=2), "utf-8")
        return meta

    def delete_target(self, target_id: str) -> None:
        shutil.rmtree(self._target_dir(target_id))

    def target_audio(self, target_id: str) -> Path:
        return self._target_dir(target_id) / "target.wav"

    def add_take(self, target_id: str, voice: Voice, result: dict) -> dict:
        takes = self._target_dir(target_id) / "takes"
        take_id = f"{datetime.now():%Y%m%d-%H%M%S-%f}-{secrets.token_hex(2)}"
        record = {"id": take_id, "created": _now(), **result}
        sf.write(takes / f"{take_id}.wav", voice.y, SR, subtype="PCM_16")
        (takes / f"{take_id}.json").write_text(json.dumps(record), "utf-8")
        overalls = [json.loads(p.read_text("utf-8"))["overall"] for p in takes.glob("*.json")]
        self.update_target(
            target_id,
            takes=len(overalls),
            best=max(overalls),
            last=record["created"],
            last_score=record["overall"],
            focus=(record.get("coach") or {}).get("focus"),
        )
        return record

    def takes_for_voice(self, voice_id: str, limit: int = 10) -> list[dict]:
        """The latest takes on any of a voice's lines, newest last."""
        records = []
        for meta in self.list_targets():
            if meta.get("voice_id") != voice_id:
                continue
            for path in (self.root / meta["id"] / "takes").glob("*.json"):
                records.append(json.loads(path.read_text("utf-8")))
        return sorted(records, key=lambda r: r["created"])[-limit:]

    def list_takes(self, target_id: str) -> list[dict]:
        """Oldest first: the headline and each part's score, for trends."""
        takes = []
        for path in sorted((self._target_dir(target_id) / "takes").glob("*.json")):
            record = json.loads(path.read_text("utf-8"))
            takes.append(
                {k: record[k] for k in ("id", "created", "overall")}
                | {
                    "parts": {c["key"]: c["score"] for c in record["components"]},
                    "focus": (record.get("coach") or {}).get("focus"),
                }
            )
        return takes

    def add_drill(self, target_id: str, voice: Voice, record: dict) -> dict:
        """One trainer attempt at one word, kept with its audio."""
        folder = self._target_dir(target_id) / "drills"
        folder.mkdir(exist_ok=True)
        record = {"id": f"{datetime.now():%Y%m%d-%H%M%S-%f}-{secrets.token_hex(2)}", "created": _now(), **record}
        sf.write(folder / f"{record['id']}.wav", voice.y, SR, subtype="PCM_16")
        (folder / f"{record['id']}.json").write_text(json.dumps(record), "utf-8")
        return record

    def list_drills(self, target_id: str) -> list[dict]:
        """Every trainer attempt on the line, oldest first."""
        folder = self._target_dir(target_id) / "drills"
        return [json.loads(p.read_text("utf-8")) for p in sorted(folder.glob("*.json"))] if folder.is_dir() else []

    def drill_audio(self, target_id: str, drill_id: str) -> Path:
        path = self._target_dir(target_id) / "drills" / f"{drill_id}.wav"
        if not _ID.match(drill_id) or not path.is_file():
            raise NotFound(drill_id)
        return path

    def last_take(self, target_id: str) -> dict | None:
        paths = sorted((self._target_dir(target_id) / "takes").glob("*.json"))
        return json.loads(paths[-1].read_text("utf-8")) if paths else None

    def take(self, target_id: str, take_id: str) -> dict:
        return json.loads(self._take_file(target_id, take_id, ".json").read_text("utf-8"))

    def take_audio(self, target_id: str, take_id: str) -> Path:
        return self._take_file(target_id, take_id, ".wav")
