from __future__ import annotations

import argparse
import json
import sys

from .analysis import AnalysisError, analyze, load
from .scoring import compare


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="voicematch", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the practice app in your browser")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--data", help="where clips and takes are kept (default: ./data)")

    score = sub.add_parser("score", help="score one recording against a target clip")
    score.add_argument("target")
    score.add_argument("take")
    score.add_argument("--json", action="store_true", help="print the full result as JSON")

    args = parser.parse_args(argv)
    if args.command == "serve":
        import uvicorn

        from . import identity
        from .server import create_app

        identity.model_path()  # download now rather than during the first take
        uvicorn.run(create_app(args.data), host=args.host, port=args.port)
        return 0

    from . import line

    try:
        target, take = analyze(load(args.target)), analyze(load(args.take))
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


if __name__ == "__main__":
    sys.exit(main())
