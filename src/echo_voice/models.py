"""Downloaded model files: fetched once into the cache and checked against a
pinned SHA-256."""

from __future__ import annotations

import hashlib
import os
import secrets
import sys
import urllib.request
from pathlib import Path


def cache_dir() -> Path:
    return Path(os.environ.get("ECHO_CACHE") or Path.home() / ".cache" / "echo")


def fetch(name: str, url: str, sha256: str, what: str) -> Path:
    """Path to a model file, downloading it the first time."""
    path = cache_dir() / "models" / name
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    # A name of its own, so the server and a recording's job fetching the same
    # model at once don't write into one file.
    partial = path.with_name(f"{path.name}.{os.getpid()}-{secrets.token_hex(4)}.part")
    print(f"Downloading {what} ({name})...", file=sys.stderr, flush=True)
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=60) as response, open(partial, "wb") as out:
        while chunk := response.read(1 << 20):
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest() != sha256:
        partial.unlink()
        raise RuntimeError(f"The downloaded {name} doesn't match its expected hash.")
    try:
        partial.replace(path)
    except PermissionError:  # Windows: the other download got there first and the file is in use
        partial.unlink(missing_ok=True)
        if not path.is_file():
            raise
    return path
