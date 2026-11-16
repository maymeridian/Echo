"""Pick a voice's best lines from its recordings and add them.

A recording can hold hundreds of lines, and the voice check wants five. So
when a recording is ready, its job adds the best lines (`sources.rank`) to
the voice until it has five, and the voice's page suggests the next best
ones, each one click to add.

Adding a line here does what the page does when a line is picked by hand,
without the trim step: the line's span (with its lead-in and tail) is cut
from the recording, the voice is separated from any background, and the
words are transcribed with their timings. Each line takes a few seconds on
the CPU.
"""

from __future__ import annotations

from . import clean, line, sources
from .analysis import AnalysisError, analyze
from .scoring import CHECK_LINES
from .store import Store


def _added(store: Store, voice_id: str) -> set[tuple[str, float]]:
    """(recording, line start) of every line the voice already has."""
    return {
        (t["source"], round(t["line_start"], 2))
        for t in store.list_targets()
        if t.get("voice_id") == voice_id and t.get("source") and t.get("line_start") is not None
    }


def suggestions(store: Store, voice_id: str, n: int = 8) -> list[dict]:
    """The best lines from the voice's recordings that it doesn't have yet."""
    added = _added(store, voice_id)
    out = []
    for recording in store.list_sources():
        if recording.get("voice_id") != voice_id:
            continue
        # Any recording whose lines are found: also one whose job is still
        # picking from it, which is how the job finds its own lines.
        rows = store.source_lines(recording["id"]) or []
        # Only the speaker the voice is of, in a recording with several.
        for fit, i in sources.rank(rows, recording.get("speaker") or 0):
            row = rows[i]
            if (recording["id"], round(row["start"], 2)) in added:
                continue
            out.append({
                "fit": fit,
                "source": recording["id"],
                "recording": recording["name"],
                "start": row["start"],
                "end": row["end"],
                "text": row["text"],
                "pitch": row["pitch"],
            })
    out.sort(key=lambda s: -s["fit"])
    return out[:n]


def add_from_recording(store: Store, recording: dict, row: dict, auto: bool = False) -> dict:
    """One line of a recording, added to the recording's voice. `auto`: picked
    by Echo, not by hand, so it may be swapped out if the speaker changes."""
    folder = store.source_dir(recording["id"])
    original = sources.read_audio(folder, row["start"], row["end"])
    cleaned = analyze(clean.vocals(original))
    timed = line.transcribe_timed(cleaned.y)
    meta = store.add_target(
        row["text"][:80], cleaned, [w["text"] for w in timed], original, word_times=timed,
        source=recording["id"], voice_name=recording["name"], line_start=row["start"],
        voice_id=recording["voice_id"], auto=auto,
    )
    store.tick_line(recording["voice_id"], meta["id"], CHECK_LINES)
    return meta


def pick(store: Store, voice_id: str, want: int = CHECK_LINES, report=lambda done, total: None) -> list[dict]:
    """Add the best suggested lines until the voice has `want` lines."""
    have = sum(1 for t in store.list_targets() if t.get("voice_id") == voice_id)
    picks = suggestions(store, voice_id, max(0, want - have))
    recordings = {r["id"]: r for r in store.list_sources()}
    added = []
    for done, s in enumerate(picks, start=1):
        rows = store.source_lines(s["source"]) or []
        row = next((r for r in rows if r["start"] == s["start"]), None)
        if row is None:
            continue
        try:
            added.append(add_from_recording(store, recordings[s["source"]], row, auto=True))
        except AnalysisError:
            pass  # silent once the background is gone, say; the next pick will do
        report(done, len(picks))
    return added
