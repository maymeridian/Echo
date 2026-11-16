"""Long recordings: a YouTube video, a podcast, a compilation of a game
character's voice lines. Echo scores one line at a time, so a long recording is
transcribed once and split into lines to pick from.

A background job (`python -m echo_voice.sources <folder>`, started by the app
or by `echo-voice import`) does this:

1. Downloads the audio of a link (YouTube and the other sites yt-dlp
   supports), or takes the uploaded file.
2. Decodes it to 16 kHz mono FLAC, the rate everything else works at.
3. Transcribes it with Whisper (the same base.en as `line.py`), with voice
   activity filtering and word timestamps.
4. Splits the words into lines at sentence ends and pauses, and splits long
   sentences at a comma or a pause near the middle.
5. Measures each line: how much of it is voiced (a whisper has no pitch to
   compare), how sure Whisper was of the words, and whether it is the
   recording's main voice.

The voice is separated from music only for the line that gets picked
(`clean.py`). On the CPU separation runs at about 1.8x real time, which is
too slow for hours of audio, while Whisper does 25 minutes in about 35 s.

There is no background-noise flag. The level between words is a poor guide:
on a 25-minute video of one soft-spoken speaker it called 136 of 200 lines noisy, but separating
a sample of them took out ambience 8-12 dB under the voice, which the
separation handles (clean.py: music as loud as the voice comes out clean).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from .analysis import HOP, PITCH_CEILING, PITCH_FLOOR, SR

MAX_HOURS = 4
AUDIO = "audio.flac"
LINES = "lines.json"
META = "meta.json"
LOCK = "job.lock"

# Line splitting.
PAUSE = 0.7  # s of silence that always ends a line
MAX_LINE = 10.0  # s; longer stretches are split near the middle
MIN_PIECE = 1.5  # s; neither half of a split may be shorter
MIN_LINE = 0.6  # s; shorter lines ("Hm.") are dropped
LEAD, TAIL = 0.2, 0.35  # s added before the first word and after the last
SENTENCE_END = re.compile(r"[.?!…]['\"’”)\]]*$")
CLAUSE_END = re.compile(r"[,;:—–-]['\"’”)\]]*$")

# Flags. See `flags()` for what each means.
WHISPER_VOICED = 0.2  # share of the words' frames with a pitch
UNCLEAR_PROB = 0.5  # Whisper's mean word probability
# Cosine to the recording's main voice. In 36 mixes of VCC2018 studio
# speakers (30 sentences by one, 8 by three others) it caught 96.1% of the
# others' lines (94.7% of same-sex ones) and tagged 0.2% of the main
# speaker's (tools/validate.py other-voice). On a 25-minute video of one
# speaker it tagged 1 line of 200, a "hmm".
OTHER_VOICE = 0.4
EMBED_MIN_SECONDS = 1.5  # shorter lines get no speaker check
SPEAKER_SIMILARITY = 0.4  # average cosine at which groups of lines stop merging into one speaker
MIN_SPEAKER_SHARE = 0.05  # a smaller group isn't called a speaker


class SourceError(RuntimeError):
    """The recording can't be used; the message is safe to show the user."""


# ---------- getting the audio ----------


def is_link(text: str) -> bool:
    return bool(re.match(r"^https?://\S+$", text.strip()))


def download(url: str, folder: Path, progress=lambda share: None) -> tuple[Path, str, str | None]:
    """The audio of a video page, as the site serves it (no re-encoding, so no
    ffmpeg needed). Returns the file, the video's title and its channel.

    YouTube changes often and old yt-dlp versions stop working; when links
    start failing, update it:  .venv\\Scripts\\pip install -U "yt-dlp[default]"
    """
    try:
        import yt_dlp
    except ImportError as exc:
        raise SourceError('Links need yt-dlp: run  .venv\\Scripts\\pip install "yt-dlp[default]"') from exc

    def hook(d):
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if d.get("status") == "downloading" and total:
            progress(min(1.0, d.get("downloaded_bytes", 0) / total))

    options = {
        "format": "bestaudio/best",
        "outtmpl": str(folder / "download.%(ext)s"),
        "noplaylist": True,
        # YouTube needs a JavaScript runtime to unlock its formats; use any
        # that's installed (yt-dlp's default is Deno only).
        "js_runtimes": {name: {} for name in ("deno", "node", "bun", "quickjs")},
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
            if info.get("_type") == "playlist":
                raise SourceError("That's a playlist; paste the link of one video.")
            if (info.get("duration") or 0) > MAX_HOURS * 3600:
                raise SourceError(f"That video is over {MAX_HOURS} hours long.")
            info = ydl.process_ie_result(info, download=True)
    except yt_dlp.utils.DownloadError as exc:
        message = re.sub(r"^ERROR:\s*", "", str(exc)).strip()
        raise SourceError(f"Couldn't download that: {message}") from exc
    files = [Path(d["filepath"]) for d in info.get("requested_downloads", []) if d.get("filepath")]
    if not files or not files[0].is_file():
        raise SourceError("The download finished but no audio file appeared.")
    channel = info.get("channel") or info.get("uploader")
    return files[0], (info.get("title") or url).strip(), channel.strip() if channel else None


def video_info(url: str) -> tuple[str, str | None]:
    """A video page's title and channel, without downloading it."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise SourceError('Links need yt-dlp: run  .venv\\Scripts\\pip install "yt-dlp[default]"') from exc
    options = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": {name: {} for name in ("deno", "node", "bun", "quickjs")},
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False, process=False)
    except yt_dlp.utils.DownloadError as exc:
        message = re.sub(r"^ERROR:\s*", "", str(exc)).strip()
        raise SourceError(f"Couldn't read that link: {message}") from exc
    channel = info.get("channel") or info.get("uploader")
    return (info.get("title") or url).strip(), channel.strip() if channel else None


def clean_title(title: str) -> str:
    """A video title made into a voice's name: without [tags], (asides),
    #hashtags, emoji, or anything after a " | " or " - "; at most 60
    characters. "The lighthouse keeper's story (extended cut) [4K][HD]"
    becomes "The lighthouse keeper's story"."""
    text = re.sub(r"[\[(\{][^\])}]*[\])}]", " ", title)
    text = re.split(r"\s[|•·–—-]\s|\s#", text)[0]
    text = re.sub(r"[^\w\s'’&!?.,:]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -,:")
    if len(text) > 60:
        text = text[:60].rsplit(" ", 1)[0]
    return text or title[:60].strip()


def decode(path: Path, out: Path, progress=lambda share: None) -> float:
    """Any audio or video file to 16 kHz mono FLAC, a block at a time so hours
    of audio never sit in memory twice. Returns the length in seconds."""
    import av

    try:
        container = av.open(str(path))
    except Exception as exc:  # PyAV raises a family of FFmpeg errors
        raise SourceError("Couldn't read that file as audio or video.") from exc
    with container:
        if not container.streams.audio:
            raise SourceError("That file has no sound.")
        stream = container.streams.audio[0]
        total = container.duration / 1e6 if container.duration else None
        if total and total > MAX_HOURS * 3600:
            raise SourceError(f"That recording is over {MAX_HOURS} hours long.")
        # Keep the channels and average them here: FFmpeg's stereo-to-mono
        # matrix adds the channels at 0.71 each, which can clip.
        resampler = av.AudioResampler(format="fltp", rate=SR)
        written = 0
        with sf.SoundFile(out, "w", SR, 1, subtype="PCM_16", format="FLAC") as f:

            def write(frames):
                nonlocal written
                for frame in frames:
                    block = frame.to_ndarray().reshape(frame.layout.nb_channels, -1).mean(axis=0)
                    f.write(np.clip(block, -1, 1))
                    written += block.size

            try:
                for frame in container.decode(stream):
                    write(resampler.resample(frame))
                    if total and frame.time is not None:
                        progress(min(1.0, frame.time / total))
            except av.error.FFmpegError:
                if written < SR:  # a damaged tail is fine; a file that won't start isn't
                    raise SourceError("Couldn't read that file as audio or video.")
            write(resampler.resample(None))
    if written < SR:
        raise SourceError("That recording is under a second long.")
    if written > MAX_HOURS * 3600 * SR:
        raise SourceError(f"That recording is over {MAX_HOURS} hours long.")
    return written / SR


# ---------- words and lines ----------


def transcribe_words(y: np.ndarray, progress=lambda share: None) -> list[dict]:
    """Every word with its start and end in seconds and Whisper's confidence."""
    from faster_whisper import WhisperModel

    from .line import MODEL, TEMPERATURE, normalise

    model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=4)
    segments, _ = model.transcribe(
        np.ascontiguousarray(y, dtype=np.float32), language="en", beam_size=5,
        vad_filter=True, condition_on_previous_text=False, word_timestamps=True, temperature=TEMPERATURE,
    )
    words, duration = [], len(y) / SR
    for segment in segments:
        progress(min(1.0, segment.end / duration))
        for w in segment.words or []:
            text = w.word.strip()
            if normalise(text) and w.end > w.start:
                words.append({"start": float(w.start), "end": float(w.end), "text": text, "p": float(w.probability)})
    return words


def split_lines(words: list[dict]) -> list[list[dict]]:
    """Group words into lines someone could say in one go."""
    lines, current = [], []
    for w in words:
        if current:
            gap = w["start"] - current[-1]["end"]
            ended = SENTENCE_END.search(current[-1]["text"])
            # A sentence of one or two words ("Ready?") runs on into the next
            # one when there's barely a breath between them.
            if gap >= PAUSE or (ended and (len(current) >= 3 or gap >= 0.25)):
                lines.append(current)
                current = []
        current.append(w)
    if current:
        lines.append(current)
    out = [piece for line in lines for piece in _shorten(line)]
    return [line for line in out if line[-1]["end"] - line[0]["start"] >= MIN_LINE]


def _shorten(line: list[dict]) -> list[list[dict]]:
    """Split a long stretch where a speaker would breathe: at a comma or a
    pause, as near the middle as possible."""
    duration = line[-1]["end"] - line[0]["start"]
    if duration <= MAX_LINE:
        return [line]
    middle = (line[0]["start"] + line[-1]["end"]) / 2
    best, best_cost = None, np.inf
    for i in range(1, len(line)):
        left = line[i - 1]["end"] - line[0]["start"]
        right = line[-1]["end"] - line[i]["start"]
        if min(left, right) < MIN_PIECE:
            continue
        gap = line[i]["start"] - line[i - 1]["end"]
        prev = line[i - 1]["text"]
        mark = 0.4 if SENTENCE_END.search(prev) else 0.3 if CLAUSE_END.search(prev) else 0.0
        cost = abs(line[i]["start"] - middle) / duration - mark - 0.5 * min(gap, 0.6)
        if cost < best_cost:
            best, best_cost = i, cost
    if best is None:
        return [line]
    return _shorten(line[:best]) + _shorten(line[best:])


def spans(lines: list[list[dict]], duration: float) -> list[tuple[float, float]]:
    """Where each line's audio starts and ends: a little before the first word
    and after the last, without running into the neighbouring lines."""
    out = []
    for i, line in enumerate(lines):
        start = line[0]["start"] - LEAD
        end = line[-1]["end"] + TAIL
        if i > 0:
            start = max(start, (lines[i - 1][-1]["end"] + line[0]["start"]) / 2)
        if i + 1 < len(lines):
            end = min(end, (line[-1]["end"] + lines[i + 1][0]["start"]) / 2)
        out.append((max(0.0, start), min(duration, end)))
    return out


# ---------- measuring lines ----------


def measure(y: np.ndarray, lines: list[list[dict]]) -> list[dict]:
    """Each line's text, span and the numbers behind its flags."""
    import parselmouth

    rows = []
    for line, (start, end) in zip(lines, spans(lines, len(y) / SR)):
        clip = y[int(start * SR) : int(end * SR)]
        snd = parselmouth.Sound(clip.astype(np.float64), sampling_frequency=SR)
        pitch = snd.to_pitch_ac(time_step=HOP / SR, pitch_floor=PITCH_FLOOR, pitch_ceiling=PITCH_CEILING)
        f0 = pitch.selected_array["frequency"]
        t = start + pitch.xs()
        # Voicing is counted only inside words, so pauses don't make a line
        # look whispered.
        spoken = np.zeros(len(t), dtype=bool)
        for w in line:
            spoken |= (t >= w["start"]) & (t < w["end"])
        voiced = (f0 > 0) & spoken
        rows.append(
            {
                "start": round(start, 2),
                "end": round(end, 2),
                "text": " ".join(w["text"] for w in line),
                "confidence": round(float(np.mean([w["p"] for w in line])), 2),
                "voiced": round(float(voiced.sum() / spoken.sum()), 2) if spoken.any() else 0.0,
                "pitch": round(float(np.median(f0[voiced]))) if voiced.sum() >= 20 else None,
                "same_voice": None,
            }
        )
    emb = embed_lines(y, rows)
    _main_voice(rows, emb)
    find_speakers(rows, emb)
    return rows


def embed_lines(y: np.ndarray, rows: list[dict]) -> dict[int, np.ndarray]:
    """A speaker embedding for every line long enough to judge."""
    from . import identity

    return {
        i: identity.embed(y[int(r["start"] * SR) : int(r["end"] * SR)])
        for i, r in enumerate(rows)
        if r["end"] - r["start"] >= EMBED_MIN_SECONDS
    }


def _main_voice(rows: list[dict], emb: dict[int, np.ndarray]) -> None:
    """How close each line is to the voice that most of the recording is in.

    The main voice's centre is the average embedding of the half of the lines
    that agree best with all the others, so a few lines by someone else, or a
    song in the background, don't pull it."""
    keep = sorted(emb)
    if len(keep) < 4:
        return
    vectors = np.stack([emb[i] for i in keep])
    typical = np.median(vectors @ vectors.T, axis=1)
    centre = vectors[typical >= np.median(typical)].mean(axis=0)
    centre /= np.linalg.norm(centre)
    for i, s in zip(keep, vectors @ centre):
        rows[i]["same_voice"] = round(float(s), 2)


def find_speakers(rows: list[dict], emb: dict[int, np.ndarray], threshold: float | None = None) -> list[dict]:
    """Who speaks in the recording: each line's `speaker` (0 is the one with
    the most speech; None for a line too short to judge or not clearly anyone's),
    and one summary per speaker.

    Lines are grouped by average-linkage clustering of their speaker
    embeddings, merging while the average cosine between two groups is at
    least `threshold` (SPEAKER_SIMILARITY; tools/validate.py speakers). A group
    of fewer than 3 lines, or under 5% of them, is too small to call a
    speaker: its lines join the nearest speaker if they're that close to it,
    and are left unsure if not."""
    from scipy.cluster.hierarchy import fcluster, linkage

    threshold = SPEAKER_SIMILARITY if threshold is None else threshold
    for r in rows:
        r["speaker"] = None
    keep = sorted(emb)
    if not keep:
        return []
    vectors = np.stack([emb[i] for i in keep])
    if len(keep) < 4:
        labels = np.ones(len(keep), dtype=int)
    else:
        labels = fcluster(linkage(vectors, method="average", metric="cosine"), t=1 - threshold, criterion="distance")
    seconds = np.array([rows[i]["end"] - rows[i]["start"] for i in keep])
    groups = {label: np.flatnonzero(labels == label) for label in np.unique(labels)}
    big = [g for g in groups.values() if len(g) >= min(3, len(keep)) and len(g) >= MIN_SPEAKER_SHARE * len(keep)]
    big.sort(key=lambda g: -seconds[g].sum())
    centres = []
    for g in big:
        c = vectors[g].mean(axis=0)
        centres.append(c / np.linalg.norm(c))
    member = {int(j): k for k, g in enumerate(big) for j in g}
    for j, i in enumerate(keep):
        if j in member:
            rows[i]["speaker"] = member[j]
        elif centres:
            sims = np.array([vectors[j] @ c for c in centres])
            if sims.max() >= threshold:
                rows[i]["speaker"] = int(sims.argmax())
    return speaker_summary(rows)


def speaker_summary(rows: list[dict]) -> list[dict]:
    """One entry per speaker found: how many lines and seconds, typical pitch,
    and a sample line to play (clear, near 3.5 s long)."""
    out = []
    for k in sorted({r["speaker"] for r in rows if r.get("speaker") is not None}):
        mine = [i for i, r in enumerate(rows) if r.get("speaker") == k]
        pitches = [rows[i]["pitch"] for i in mine if rows[i]["pitch"]]
        sample = max(mine, key=lambda i: rows[i]["voiced"] + rows[i]["confidence"] - 0.2 * abs(rows[i]["end"] - rows[i]["start"] - 3.5))
        out.append({
            "speaker": k,
            "lines": len(mine),
            "seconds": round(sum(rows[i]["end"] - rows[i]["start"] for i in mine), 1),
            "pitch": round(float(np.median(pitches))) if pitches else None,
            "sample": sample,
        })
    return out


def flags(row: dict) -> list[str]:
    """Why a line may be a poor one to practise on."""
    out = []
    if row["voiced"] < WHISPER_VOICED:
        out.append("whispered")  # no pitch: Pitch, Melody and Resonance can't be compared
    if row["same_voice"] is not None and row["same_voice"] < OTHER_VOICE:
        out.append("other voice")
    if row["confidence"] < UNCLEAR_PROB:
        out.append("unclear")  # Whisper wasn't sure of the words
    return out


TYPICAL_PITCH = 4.0  # semitones from the voice's median that a suggested line may sit


def line_flags(row: dict, speaker: int = 0) -> list[str]:
    """A line's flags with "other voice" meaning: not the chosen speaker. Where
    a line's speaker is known (find_speakers) that decides it; for a line too
    short to judge, the comparison with the main voice does."""
    out = [f for f in row["flags"] if f != "other voice"]
    if row.get("speaker") is not None:
        other = row["speaker"] != speaker
    else:
        other = "other voice" in row["flags"]
    return out + ["other voice"] if other else out


def rank(rows: list[dict], speaker: int = 0) -> list[tuple[float, int]]:
    """(fit, index) for every line worth suggesting, best first.

    A good line to practise is untagged, has at least three words, is near
    3.5 s long (long enough to have a tune, short enough to hold in your
    head), clearly voiced, sure of the words, most like the recording's main
    voice, a whole sentence rather than a piece of one (its own start and
    ending make its tune), and in the voice's usual range: within 4 semitones
    of its median pitch. A line far outside it is usually creaky voice or a pitch tracker
    off by an octave, and makes a poor target either way. On a 25-minute
    soft-spoken video that rules out 19 of 185 untagged lines, including two of
    the five it suggested before, both at 98-99 Hz against a typical 191."""
    clean = [r["pitch"] for r in rows if not line_flags(r, speaker) and r["pitch"]]
    typical = float(np.median(clean)) if len(clean) >= 5 else None
    ranked = []
    for i, r in enumerate(rows):
        seconds = r["end"] - r["start"]
        if line_flags(r, speaker) or len(r["text"].split()) < 3 or not 1.5 <= seconds <= 8:
            continue
        if typical and (not r["pitch"] or abs(12 * np.log2(r["pitch"] / typical)) > TYPICAL_PITCH):
            continue
        length = 1 - min(abs(seconds - 3.5) / 3.5, 1)
        fit = length + r["voiced"] + r["confidence"] + (r["same_voice"] if r["same_voice"] is not None else 0.5)
        text = r["text"].strip()
        fit += 0.3 * bool(SENTENCE_END.search(text)) + 0.1 * text[:1].isupper()  # a whole sentence
        ranked.append((round(fit, 3), i))
    return sorted(ranked, reverse=True)


def suggest(rows: list[dict], n: int = 5, speaker: int = 0) -> list[int]:
    """The `n` lines to start with, best first (see `rank`)."""
    return [i for _, i in rank(rows, speaker)[:n]]


def summary(rows: list[dict]) -> dict | None:
    """The voice at a glance, from its untagged lines: typical pitch and range
    (10th to 90th percentile of the lines' medians), pace in words a second,
    and how much of the speech is voiced. Read speech (CMU ARCTIC) is voiced
    on 53-60% of its word frames; a soft, breathy voice on about 45%."""
    good = [r for r in rows if not r["flags"]] or rows
    pitches = [r["pitch"] for r in good if r["pitch"]]
    if not good:
        return None
    words = sum(len(r["text"].split()) for r in good)
    seconds = sum(max(0.3, r["end"] - r["start"] - LEAD - TAIL) for r in good)
    out = {
        "pitch": None,
        "low": None,
        "high": None,
        "rate": round(words / seconds, 1),
        "voiced": round(float(np.median([r["voiced"] for r in good])), 2),
    }
    if len(pitches) >= 3:
        low, median, high = np.percentile(pitches, [10, 50, 90])
        out |= {"pitch": round(float(median)), "low": round(float(low)), "high": round(float(high))}
    return out


def find_lines(y: np.ndarray, progress=lambda share: None) -> list[dict]:
    words = transcribe_words(y, progress)
    rows = measure(y, split_lines(words))
    for row in rows:
        row["flags"] = flags(row)
    return rows


def read_audio(folder: Path, start: float = 0.0, end: float | None = None) -> np.ndarray:
    with sf.SoundFile(folder / AUDIO) as f:
        a = max(0, int(start * SR))
        f.seek(min(a, f.frames))
        count = -1 if end is None else max(0, int(end * SR) - a)
        return f.read(count, dtype="float32")


# ---------- the job ----------


def _lock(folder: Path):
    """Hold the folder's lock for as long as this process runs; the OS lets go
    when the process ends, however it ends. None if another process has it."""
    handle = open(folder / LOCK, "a+b")
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def job_running(folder: Path) -> bool:
    handle = _lock(folder)
    if handle is None:
        return True
    handle.close()  # closing releases the lock
    return False


def _write_meta(folder: Path, meta: dict) -> None:
    tmp = folder / (META + ".tmp")
    tmp.write_text(json.dumps(meta, indent=2), "utf-8")
    for attempt in range(20):
        try:  # Windows refuses the replace while the server is reading the file
            os.replace(tmp, folder / META)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)


def _name_voice(folder: Path, meta: dict) -> None:
    """Name a link's voice once the video is known: after its channel, which
    for a YouTuber is the person, else after its title. A second video from a
    channel that already has a voice joins that voice instead of making
    another. A recording with no voice yet gets one named after it."""
    from .store import Store

    store = Store(folder.parent.parent)
    if meta.get("voice_id"):
        try:
            voice = store.voice_meta(meta["voice_id"])
        except LookupError:
            return
        if not voice.get("name_pending"):
            return
        name = meta.get("channel") or meta.get("name")
        if not name:
            return
        existing = next(
            (v for v in store.list_voices() if v["name"] == name and v["id"] != voice["id"] and meta.get("channel")),
            None,
        )
        if existing:
            store.update_voice(voice["id"], merged_into=existing["id"], name_pending=False)
            meta["voice_id"] = existing["id"]
            meta["joined"] = True  # pick some of its lines even if the voice has five already
        else:
            store.update_voice(
                voice["id"], name=name, name_pending=False, named_from="channel" if meta.get("channel") else "title"
            )
    elif meta.get("name"):
        meta["voice_id"] = store.voice_named(meta["name"])["id"]


def run(folder: Path, report=lambda step, share: None) -> int:
    """Process one source folder, keeping meta.json's status up to date."""
    folder = Path(folder)
    lock = _lock(folder)
    if lock is None:
        return 0  # another process is already on it
    meta = json.loads((folder / META).read_text("utf-8"))
    last = [0.0]

    def status(step: str, share: float | None = None, force: bool = False):
        now = time.monotonic()
        if not force and now - last[0] < 1.0:
            return
        last[0] = now
        meta.update(status=step, progress=None if share is None else round(share, 3))
        _write_meta(folder, meta)
        report(step, share)

    try:
        if not (folder / AUDIO).is_file():
            owned = True  # a download or upload copy, deleted once decoded
            if meta.get("link"):
                status("downloading", 0.0, force=True)
                path, title, channel = download(meta["link"], folder, lambda s: status("downloading", s))
                if not meta.get("name"):
                    meta["name"] = clean_title(title)
                meta["title"] = title
                meta["channel"] = channel
                _name_voice(folder, meta)
            elif meta.get("file"):  # `echo-voice import` of a file on this computer
                path, owned = Path(meta["file"]), False
            else:
                path = next(folder.glob("upload.*"))
            status("reading", 0.0, force=True)
            tmp = folder / ("partial." + AUDIO)
            meta["duration"] = round(decode(path, tmp, lambda s: status("reading", s)), 2)
            os.replace(tmp, folder / AUDIO)
            if owned:
                path.unlink()
        y = read_audio(folder)
        meta["duration"] = round(len(y) / SR, 2)
        if (folder / LINES).is_file():
            # Found before (the job stopped part-way, or the recording is
            # from before speakers were found): keep them, so lines already
            # added keep their place.
            rows = json.loads((folder / LINES).read_text("utf-8"))
        else:
            status("transcribing", 0.0, force=True)
            rows = find_lines(y, lambda s: status("transcribing", s))
            (folder / LINES).write_text(json.dumps(rows), "utf-8")
        if any("speaker" not in r for r in rows):
            status("speakers", None, force=True)
            find_speakers(rows, embed_lines(y, rows))
            (folder / LINES).write_text(json.dumps(rows), "utf-8")
        meta.update(count=len(rows), good=sum(1 for r in rows if not line_flags(r, meta.get("speaker") or 0)), error=None)
        meta["speakers"] = len(speaker_summary(rows))
        if not meta.get("voice_id"):
            _name_voice(folder, meta)
        if meta.get("voice_id"):
            # Give the voice its best lines, so practice can start at once.
            status("picking", 0.0, force=True)
            try:
                from . import autopick
                from .store import Store

                store = Store(folder.parent.parent)
                want = None
                if meta.pop("joined", False):  # a new video for a voice that has lines: some of its own
                    have = sum(1 for t in store.list_targets() if t.get("voice_id") == meta["voice_id"])
                    want = max(autopick.CHECK_LINES, have + 3)
                autopick.pick(store, meta["voice_id"], want or autopick.CHECK_LINES, report=lambda d, n: status("picking", d / n))
            except Exception as exc:  # the lines are found either way; picking is a bonus
                print(f"Couldn't pick lines: {exc!r}", file=sys.stderr, flush=True)
        status("ready", None, force=True)
        return 0
    except Exception as exc:  # the job must always leave a status the page can show
        meta["error"] = str(exc) if isinstance(exc, SourceError) else f"Something went wrong: {exc!r}"
        status("failed", None, force=True)
        if not isinstance(exc, SourceError):
            raise
        return 1
    finally:
        lock.close()


if __name__ == "__main__":
    if sys.platform == "win32":  # stay out of the way of games and other work
        import ctypes

        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    sys.exit(run(Path(sys.argv[1])))
