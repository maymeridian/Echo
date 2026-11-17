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

A line should start where its sentence does. The recording's transcript can
miss a sentence's first words when they are soft or quick, and the line then
starts after them: on a 25-minute video the fifth-best suggestion began three
words into its sentence. It shows once the cut is read by itself, because its
first word isn't the line's. The cut is then
widened back, never into the line before, and read again. A line that still
starts mid-sentence is left out of auto-pick and of the suggestions.
"""

from __future__ import annotations

from . import clean, line, sources
from .analysis import AnalysisError, analyze
from .scoring import CHECK_LINES
from .store import Store

WIDEN = 0.8  # s further back to look for the start of a sentence


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
        skipped = {round(s, 2) for s in recording.get("skipped") or []}  # found to start mid-sentence, or silent
        # Only the speaker the voice is of, in a recording with several.
        for fit, i in sources.rank(rows, recording.get("speaker") or 0):
            row = rows[i]
            if (recording["id"], round(row["start"], 2)) in added or round(row["start"], 2) in skipped:
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


def _first(words: list[str]) -> str:
    return line.normalise(words[0]) if words else ""


def _starts_as(timed: list[dict], row: dict) -> bool:
    """Does the cut, read by itself, begin with the line's first word?"""
    return line.same_word(_first([w["text"] for w in timed]), _first(row["text"].split()))


def _cut(store: Store, recording: dict, row: dict):
    """The line's span of the recording: as it came, cleaned, its words with
    their timings, and whether it starts where its sentence does."""
    folder = store.source_dir(recording["id"])
    original = sources.read_audio(folder, row["start"], row["end"])
    cleaned = analyze(clean.vocals(original))
    timed = line.transcribe_timed(cleaned.y)
    if _starts_as(timed, row):
        return original, cleaned, timed, True
    # Words before the line's first: look further back, as far as the line before allows.
    rows = store.source_lines(recording["id"]) or []
    before = max((r["end"] for r in rows if r["end"] <= row["start"] + 0.01), default=0.0)
    start = max(row["start"] - WIDEN, before)
    if start < row["start"] - 0.1:
        try:
            wider = sources.read_audio(folder, start, row["end"])
            wider_cleaned = analyze(clean.vocals(wider))
            wider_timed = line.transcribe_timed(wider_cleaned.y)
        except AnalysisError:
            wider_timed = []
        first = _first(row["text"].split())
        heard = [line.normalise(w["text"]) for w in wider_timed]
        # The sentence's own start is in it now: the line's first word comes after others.
        if len(wider_timed) > len(timed) and any(line.same_word(w, first) for w in heard[1:]):
            return wider, wider_cleaned, wider_timed, True
    return original, cleaned, timed, False


def _skip(store: Store, recording: dict, row: dict) -> None:
    """Remember a line that can't be used, so it isn't suggested again."""
    skipped = sorted({*(store.source_meta(recording["id"]).get("skipped") or []), row["start"]})
    store.update_source(recording["id"], skipped=skipped)


def add_from_recording(store: Store, recording: dict, row: dict, auto: bool = False) -> dict | None:
    """One line of a recording, added to the recording's voice. `auto`: picked
    by Echo, not by hand, so it may be swapped out if the speaker changes, and
    it is only added if it starts where its sentence does (None otherwise).
    A line picked by hand is added as it is heard."""
    original, cleaned, timed, whole = _cut(store, recording, row)
    if auto and not whole:
        _skip(store, recording, row)
        return None
    words = [w["text"] for w in timed]
    name = row["text"] if _starts_as(timed, row) else " ".join(words)
    meta = store.add_target(
        name[:80], cleaned, words, original, word_times=timed,
        source=recording["id"], voice_name=recording["name"], line_start=row["start"],
        voice_id=recording["voice_id"], auto=auto,
    )
    store.tick_line(recording["voice_id"], meta["id"], CHECK_LINES)
    return meta


def pick(store: Store, voice_id: str, want: int = CHECK_LINES, report=lambda done, total: None) -> list[dict]:
    """Add the best suggested lines until the voice has `want` lines."""
    have = sum(1 for t in store.list_targets() if t.get("voice_id") == voice_id)
    need = max(0, want - have)
    added = []
    for _ in range(3 * need):  # a line that can't be used is skipped for the next best, within reason
        if len(added) >= need:
            break
        best = suggestions(store, voice_id, 1)
        if not best:
            break
        s = best[0]
        recording = store.source_meta(s["source"])
        row = next((r for r in store.source_lines(s["source"]) or [] if r["start"] == s["start"]), None)
        if row is None:
            break
        try:
            meta = add_from_recording(store, recording, row, auto=True)
        except AnalysisError:
            _skip(store, recording, row)  # silent once the background is gone, say
            meta = None
        if meta:
            added.append(meta)
        report(len(added), need)
    return added
