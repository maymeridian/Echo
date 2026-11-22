'''
src/echo_voice/models.py
Author: maymeridian
Description: Downloads model files once into the cache and checks each against a pinned SHA-256.
             - The cache is ~/.cache/echo, or the folder ECHO_CACHE names; models go in its
               models folder.
'''

from __future__ import annotations

import hashlib
import os
import secrets
import sys
import urllib.request
from pathlib import Path

TIMEOUT = 60  # s to wait on the server before giving up
CHUNK = 1 << 20  # bytes read and hashed at a time: 1 MB


def cache_dir() -> Path:
    """Where downloads are kept: ECHO_CACHE, or ~/.cache/echo."""
    return Path(os.environ.get("ECHO_CACHE") or Path.home() / ".cache" / "echo")


def fetch(name: str, url: str, sha256: str, what: str) -> Path:
    """Path to a model file, downloading it the first time.

    `what` says which model it is in the message shown while it downloads."""
    # 1. Already here
    path = cache_dir() / "models" / name
    if path.is_file():
        return path

    # 2. Download into a name of its own, so the server and a recording's job
    #    fetching the same model at once don't write into one file
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.{os.getpid()}-{secrets.token_hex(4)}.part")
    print(f"Downloading {what} ({name})...", file=sys.stderr, flush=True)
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response, open(partial, "wb") as out:
        while chunk := response.read(CHUNK):
            digest.update(chunk)
            out.write(chunk)

    # 3. Check it, and move it into place
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
