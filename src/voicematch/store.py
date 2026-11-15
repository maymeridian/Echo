"""Targets and takes on disk.

    <data>/targets/<target_id>/meta.json
                              /target.wav
                              /takes/<take_id>.wav
                              /takes/<take_id>.json
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
from datetime import datetime
from pathlib import Path

import soundfile as sf

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
        metas = [json.loads(p.read_text("utf-8")) for p in self.root.glob("*/meta.json")]
        return sorted(metas, key=lambda m: m["created"])

    def add_target(
        self, name: str, voice: Voice, words: list[str] | None = None, original=None
    ) -> dict:
        """`voice` is the cleaned clip that gets scored; `original` (samples at
        SR) is the upload as it came, kept for reference."""
        meta = {
            "id": secrets.token_hex(6),
            "name": name,
            "created": _now(),
            "duration": round(voice.duration, 2),
            "words": words,
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
        take_id = f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
        record = {"id": take_id, "created": _now(), **result}
        sf.write(takes / f"{take_id}.wav", voice.y, SR, subtype="PCM_16")
        (takes / f"{take_id}.json").write_text(json.dumps(record), "utf-8")
        return record

    def list_takes(self, target_id: str) -> list[dict]:
        """Oldest first, without the per-frame contour."""
        takes = []
        for path in sorted((self._target_dir(target_id) / "takes").glob("*.json")):
            record = json.loads(path.read_text("utf-8"))
            takes.append({k: record[k] for k in ("id", "created", "overall")})
        return takes

    def take(self, target_id: str, take_id: str) -> dict:
        return json.loads(self._take_file(target_id, take_id, ".json").read_text("utf-8"))

    def take_audio(self, target_id: str, take_id: str) -> Path:
        return self._take_file(target_id, take_id, ".wav")
