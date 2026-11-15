"""Downloaded model files: fetched once into the cache and checked against a
pinned SHA-256."""

from __future__ import annotations

import hashlib
import os
import sys
import urllib.request
from pathlib import Path


def cache_dir() -> Path:
    return Path(os.environ.get("VOICEMATCH_CACHE") or Path.home() / ".cache" / "voicematch")


def fetch(name: str, url: str, sha256: str, what: str) -> Path:
    """Path to a model file, downloading it the first time."""
    path = cache_dir() / "models" / name
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    print(f"Downloading {what} ({name})...", file=sys.stderr, flush=True)
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=60) as response, open(partial, "wb") as out:
        while chunk := response.read(1 << 20):
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest() != sha256:
        partial.unlink()
        raise RuntimeError(f"The downloaded {name} doesn't match its expected hash.")
    partial.replace(path)
    return path
