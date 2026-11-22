'''
src/echo_voice/cli.py
Author: maymeridian
Description: The echo-voice command: practise a voice impression and see how close you get.
             - serve: run the practice app in your browser (the speaker model is fetched first).
             - score: score one recording against a target clip, as text or as JSON.
             - import: split a long recording or a video link (YouTube and others) into lines
               to practise.
'''

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .analysis import AnalysisError, analyze, load
from .scoring import compare

NAME_CHARS = 80  # a voice's name is cut to this many characters
BELOW_NORMAL_PRIORITY = 0x4000  # Windows' BELOW_NORMAL_PRIORITY_CLASS


def main(argv: list[str] | None = None) -> int:
    """Run the echo-voice command; returns the exit code."""
    # 1. The commands and their flags
    parser = argparse.ArgumentParser(
        prog="echo-voice",
        description="Echo: practise a voice impression and see how close you get.",
    )
    parser.add_argument("--version", action="version", version=f"echo-voice {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the practice app in your browser")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--data", help="where clips and takes are kept (default: ./data)")

    score = sub.add_parser("score", help="score one recording against a target clip")
    score.add_argument("target")
    score.add_argument("take")
    score.add_argument("--json", action="store_true", help="print the full result as JSON")
    score.add_argument(
        "--no-clean",
        action="store_true",
        help="don't separate the target's voice from music first",
    )

    imp = sub.add_parser(
        "import",
        help="split a long recording or a video link (YouTube and others) into lines to practise",
    )
    imp.add_argument("source", help="an audio or video file, or a link")
    imp.add_argument(
        "--name",
        default="",
        help="the voice's name (default: the file name or video title)",
    )
    imp.add_argument("--data", help="where clips and takes are kept (default: ./data)")

    args = parser.parse_args(argv)

    # 2. serve: run the practice app until it is stopped
    if args.command == "serve":
        import uvicorn

        from . import identity
        from .server import LOCAL_HOSTS, create_app

        identity.model_path()  # download now rather than during the first take
        # Listening beyond this computer (--host 0.0.0.0, to use it from a
        # phone, say), the address the page is reached by can't be known.
        local = args.host in ("127.0.0.1", "localhost")
        uvicorn.run(
            create_app(args.data, hosts=LOCAL_HOSTS if local else None),
            host=args.host,
            port=args.port,
        )
        return 0

    # 3. import: split a long recording into lines
    if args.command == "import":
        return _import(args)

    # 4. score: one take against a target, its voice separated from any music first
    from . import clean, line

    try:
        raw = load(args.target)
        target = analyze(raw if args.no_clean else clean.vocals(raw))
        take = analyze(load(args.take))
        result = compare(target, take, line.transcribe(target.y), line.transcribe(take.y))
    except AnalysisError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result))
        return 0
    print(
        f"Match: {result['overall']:.0f} / 100  "
        "(the real person on another mic ~90, a stranger ~20)"
    )
    if result["accepted"]:
        print(
            "Passes a speaker check: a voice-verification model would take this "
            "for the same person."
        )
    if result["line"]:
        print(f"Line: {result['line']['target']}\nSaid: {result['line']['take']}")
    for c in result["components"]:
        score_text = "  –" if c["score"] is None else f"{c['score']:3.0f}"
        print(f"  {c['label']:<13}{score_text}  {c['detail']}")
    for hint in result["hints"]:
        print(f"- {hint}")
    return 0


def _import(args: argparse.Namespace) -> int:
    """Split a long recording or a video link into lines and pick the voice's best;
    returns the exit code."""
    from . import autopick, sources
    from .store import Store

    # 1. A few minutes of CPU: on Windows, stay out of the way of games
    if sys.platform == "win32":
        import ctypes

        kernel = ctypes.windll.kernel32
        kernel.SetPriorityClass(kernel.GetCurrentProcess(), BELOW_NORMAL_PRIORITY)

    # 2. The new source, from a link or from a file on this computer
    store = Store(Path(args.data or os.environ.get("ECHO_DATA", "data")))
    name = args.name.strip()[:NAME_CHARS]
    if sources.is_link(args.source):
        meta, folder = store.new_source(name, link=args.source.strip(), status="uploading")
    else:
        path = Path(args.source).resolve()
        if not path.is_file():
            print(f"No such file: {path}", file=sys.stderr)
            return 1
        meta, folder = store.new_source(
            name or path.stem[:NAME_CHARS],
            file=str(path),
            status="uploading",
        )

    def report(step: str, share: float | None) -> None:
        """Show the job's step, and how far along it is, over the last one."""
        print(f"\r{step}{'' if share is None else f' {share:4.0%}'}   ", end="", flush=True)

    # 3. Run the job. With a name known now, it picks the voice's best lines itself.
    if meta["name"]:
        store.update_source(meta["id"], voice_id=store.voice_named(meta["name"])["id"])
    code = sources.run(folder, report)
    print()
    store.migrate()  # a link's voice is named after the video
    meta = store.source_meta(meta["id"])
    if meta["status"] != "ready":
        print(meta.get("error") or "That didn't finish.", file=sys.stderr)
        return code or 1

    # 4. Pick the lines, if the job couldn't yet (a link's voice had no name), and report
    voice = store.voice_meta(meta["voice_id"])
    autopick.pick(store, voice["id"])
    lines = sum(1 for t in store.list_targets() if t.get("voice_id") == voice["id"])
    print(
        f'"{meta["name"]}": {meta["count"]} lines, {meta["good"]} of them good to practise on. '
        f'The voice "{voice["name"]}" now has {lines} lines picked. '
        "Open the app (echo-voice serve) to practise."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
