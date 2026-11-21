"""Echo: practise a voice impression and see how close you get."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .analysis import AnalysisError, analyze, load
from .scoring import compare


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="echo-voice", description=__doc__)
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
    score.add_argument("--no-clean", action="store_true", help="don't separate the target's voice from music first")

    imp = sub.add_parser(
        "import", help="split a long recording or a video link (YouTube and others) into lines to practise"
    )
    imp.add_argument("source", help="an audio or video file, or a link")
    imp.add_argument("--name", default="", help="the voice's name (default: the file name or video title)")
    imp.add_argument("--data", help="where clips and takes are kept (default: ./data)")

    args = parser.parse_args(argv)
    if args.command == "serve":
        import uvicorn

        from . import identity
        from .server import create_app

        identity.model_path()  # download now rather than during the first take
        from .server import LOCAL_HOSTS

        # Listening beyond this computer (--host 0.0.0.0, to use it from a
        # phone, say), the address the page is reached by can't be known.
        local = args.host in ("127.0.0.1", "localhost")
        uvicorn.run(create_app(args.data, hosts=LOCAL_HOSTS if local else None), host=args.host, port=args.port)
        return 0
    if args.command == "import":
        return _import(args)

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
    print(f"Match: {result['overall']:.0f} / 100  (the real person on another mic ~90, a stranger ~20)")
    if result["accepted"]:
        print("Passes a speaker check: a voice-verification model would take this for the same person.")
    if result["line"]:
        print(f"Line: {result['line']['target']}\nSaid: {result['line']['take']}")
    for c in result["components"]:
        score_text = "  –" if c["score"] is None else f"{c['score']:3.0f}"
        print(f"  {c['label']:<13}{score_text}  {c['detail']}")
    for hint in result["hints"]:
        print(f"- {hint}")
    return 0


def _import(args) -> int:
    import os
    from pathlib import Path

    from . import sources
    from .store import Store

    if sys.platform == "win32":  # a few minutes of CPU: stay out of the way of games
        import ctypes

        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    store = Store(Path(args.data or os.environ.get("ECHO_DATA", "data")))
    if sources.is_link(args.source):
        meta, folder = store.new_source(args.name.strip()[:80], link=args.source.strip(), status="uploading")
    else:
        path = Path(args.source).resolve()
        if not path.is_file():
            print(f"No such file: {path}", file=sys.stderr)
            return 1
        meta, folder = store.new_source(args.name.strip()[:80] or path.stem[:80], file=str(path), status="uploading")

    def report(step: str, share: float | None) -> None:
        print(f"\r{step}{'' if share is None else f' {share:4.0%}'}   ", end="", flush=True)

    if meta["name"]:  # with a name known now, the job picks the voice's best lines itself
        store.update_source(meta["id"], voice_id=store.voice_named(meta["name"])["id"])
    code = sources.run(folder, report)
    print()
    store.migrate()  # a link's voice is named after the video
    meta = store.source_meta(meta["id"])
    if meta["status"] != "ready":
        print(meta.get("error") or "That didn't finish.", file=sys.stderr)
        return code or 1
    from . import autopick

    voice = store.voice_meta(meta["voice_id"])
    autopick.pick(store, voice["id"])  # if the job couldn't yet: a link's voice had no name
    lines = sum(1 for t in store.list_targets() if t.get("voice_id") == voice["id"])
    print(
        f'"{meta["name"]}": {meta["count"]} lines, {meta["good"]} of them good to practise on. '
        f'The voice "{voice["name"]}" now has {lines} lines picked. Open the app (echo-voice serve) to practise.'
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
