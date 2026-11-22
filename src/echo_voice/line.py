'''
src/echo_voice/line.py
Author: maymeridian
Description: Which words were said: speech recognition plus a word-level comparison.
             - The line-matching parts of the score (melody, articulation, rhythm) only mean
               something when the take says the target's words. Sound-based alignment can't
               check that across different voices (it flagged 65% of strangers who said the
               whole line), so the words are transcribed with Whisper (base.en via
               faster-whisper, on the CPU) and compared as text.
             - The recogniser's encoder frames (sounds) are also what recordings are lined up
               by and what Articulation compares.
             - Whisper's word times are moved onto the audio (refine_word_times), and words the
               sound runs together are joined into units (speech_units).
'''

from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import numpy as np

# --- THE RECOGNISER ---

MODEL = "base.en"  # Whisper's English base model: about 30 s for 25 minutes on a CPU
# Beam search at temperature 0 only. faster-whisper's default
# retries an unsure pass with random sampling at rising temperatures, which
# made the same audio come back differently from run to run (4 transcripts in
# 6 runs of one test recording: "Steels", "steals", "et cetera"). With 0 alone
# all 6 matched. A measuring tool should give the same answer twice.
TEMPERATURE = 0.0
# The model, loaded on first use. One call into it at a time.
_lock = threading.Lock()
_model = None


def _get_model():
    """The Whisper model on the CPU (8-bit, two threads), loaded the first time."""
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel

            _model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=2)
        return _model


SOUND_STEP = 0.02  # s between the recogniser's frames


def sounds(y: np.ndarray) -> np.ndarray:
    """What the recogniser hears in a 16 kHz mono clip: the frames of its
    encoder, (frames, 512), one every 20 ms.

    The encoder is trained to turn any voice into the same words. Two people
    saying one line come out close and one person saying two lines comes out
    far, which is what lining two recordings up and comparing their *sounds*
    needs. MFCCs give the opposite: on RAVDESS a stranger saying the right line
    is as far as the right actor saying the other sentence (scoring.measure).
    Clips are scored up to 30 s, which is the encoder's window."""
    from faster_whisper.audio import pad_or_trim

    model = _get_model()
    with _lock:
        clip = np.ascontiguousarray(y, dtype=np.float32)
        features = pad_or_trim(model.feature_extractor(clip))
        out = np.asarray(model.encode(features))[0]
    # Only the frames the clip fills; the rest of the window is padding.
    frames = max(2, math.ceil(len(y) / 16000 / SOUND_STEP))
    return out[:frames].astype(np.float32)


def transcribe(y: np.ndarray) -> list[str]:
    """Words spoken in a 16 kHz mono recording, as written (with punctuation)."""
    model = _get_model()
    with _lock:
        segments, _ = model.transcribe(
            np.ascontiguousarray(y, dtype=np.float32),
            language="en",
            beam_size=5,
            condition_on_previous_text=False,
            vad_filter=False,
            temperature=TEMPERATURE,
        )
        text = " ".join(s.text for s in segments)
    # Whisper sometimes writes a cut-off word as a run of block characters;
    # anything without a letter or digit isn't a word.
    return [w for w in text.split() if normalise(w)]


def glue(words: list[dict]) -> list[dict]:
    """Join a piece that starts with punctuation onto the word before it.
    Whisper times "12,000" as two words, "12" and ",000". Shown, that reads
    "12 ,000"; counted, it is two words where a take that says the same thing
    has one, and the word check called that a slip."""
    out: list[dict] = []
    for w in words:
        if out and w["text"][:1] in ",.;:!?%":
            last = out[-1] | {
                "text": out[-1]["text"] + w["text"],
                "end": max(out[-1]["end"], w["end"]),
            }
            if "p" in w and "p" in last:
                last["p"] = min(last["p"], w["p"])
            out[-1] = last
        else:
            out.append(w)
    return out


def transcribe_timed(y: np.ndarray) -> list[dict]:
    """Like `transcribe`, with where each word starts and ends in seconds.
    Used for targets, so a take can be compared word by word."""
    model = _get_model()
    with _lock:
        segments, _ = model.transcribe(
            np.ascontiguousarray(y, dtype=np.float32),
            language="en",
            beam_size=5,
            condition_on_previous_text=False,
            vad_filter=False,
            word_timestamps=True,
            temperature=TEMPERATURE,
        )
        words = [
            {
                "text": w.word.strip(),
                "start": round(float(w.start), 2),
                "end": round(float(w.end), 2),
            }
            for s in segments
            for w in s.words or []
        ]
    # Words only (with a letter or digit), and none that Whisper timed at no length.
    kept = [w for w in glue(words) if normalise(w["text"]) and w["end"] > w["start"]]
    return refine_word_times(y, kept)


# --- WORD TIMINGS ---


def _levels(y: np.ndarray, hop: int) -> np.ndarray:
    """Loudness in dB over 400-sample (25 ms) frames, one every `hop` samples."""
    import librosa

    rms = librosa.feature.rms(y=np.asarray(y, dtype=np.float32), frame_length=400, hop_length=hop)
    return 20 * np.log10(np.maximum(rms[0], 1e-6))


def refine_word_times(y: np.ndarray, words: list[dict], sr: int = 16000) -> list[dict]:
    """Move word edges onto the audio. Whisper's word times are approximate,
    and it often ends a word early, which cuts the word off when it's played
    on its own. Between two words the edge goes to the quietest point between
    them; the first word starts, and the last ends, where the sound does.
    Each word is marked `refined`, so stored timings are only moved once."""
    if not words or all(w.get("refined") for w in words):
        return words
    # 1. Loudness every 10 ms, and the level that counts as quiet
    hop = 160
    db = _levels(y, hop)
    floor, peak = float(np.percentile(db, 10)), float(db.max())
    quiet = floor + max(6.0, 0.2 * (peak - floor))  # near the noise floor: the sound has stopped

    def frame(t: float) -> int:
        """The loudness frame at `t` seconds, kept inside the recording."""
        return int(np.clip(round(t * sr / hop), 0, len(db) - 1))

    def time(f: int) -> float:
        """The time of loudness frame `f`, in seconds to two places."""
        return round(f * hop / sr, 2)

    duration = len(y) / sr
    out = [dict(w) for w in words]

    # 2. Each edge between two words
    for i in range(len(out) - 1):
        # The valley between this word and the next, searched a little past
        # where Whisper put each edge (80 ms before, 250 ms after).
        a = frame(min(out[i]["end"], out[i + 1]["start"]) - 0.08)
        b = frame(max(out[i]["end"], out[i + 1]["start"]) + 0.25)
        b = max(a + 1, min(b, frame(out[i + 1]["end"]) - 1))
        valley = a + int(np.argmin(db[a : b + 1]))
        if db[valley] < quiet:
            # A real pause: end this word where it falls quiet, start the next where it rises.
            lo = valley
            while lo > a and db[lo - 1] < quiet:
                lo -= 1
            hi = valley
            while hi < b and db[hi + 1] < quiet:
                hi += 1
            out[i]["end"], out[i + 1]["start"] = time(lo + 2), time(max(lo + 2, hi - 1))
        else:
            out[i]["end"] = out[i + 1]["start"] = time(valley)

    # 3. The last word runs until the sound stops (within half a second).
    last = frame(out[-1]["end"])
    stop = frame(min(duration, out[-1]["end"] + 0.5))
    while last < stop and db[last] >= quiet:
        last += 1
    out[-1]["end"] = max(out[-1]["end"], time(min(last + 3, len(db) - 1)))

    # 4. The first word starts where the sound does (within a quarter second).
    first = frame(out[0]["start"])
    start = frame(max(0.0, out[0]["start"] - 0.25))
    while first > start and db[first - 1] >= quiet:
        first -= 1
    out[0]["start"] = min(out[0]["start"], time(max(0, first - 2)))

    # 5. Mark each word, and make none shorter than 50 ms
    for w in out:
        w["refined"] = True
        w["end"] = max(w["end"], round(w["start"] + 0.05, 2))
    return out


# --- SPEECH UNITS ---

JOIN_DIP = 6.0  # dB: with less of a dip than this between them, two words are run together
MIN_UNIT = 0.15  # s: anything shorter can't be played or measured on its own
MAX_UNIT = 1.3  # s: a longer run is split at its deepest dip
UNITS_VERSION = 1  # goes up when units would come out differently, so stored ones are redone


def speech_units(y: np.ndarray, words: list[dict], sr: int = 16000) -> list[dict]:
    """Words as they're actually said: ones run together become one unit.

    Speech isn't a string of separate words. In "The proper course to pursue
    is to offer your name and address", "to pursue is to" comes out as one
    stretch of sound, and the recogniser's times inside it are guesses. A unit
    that can't be heard on its own can't be practised or measured on its own,
    so adjacent words are joined when the sound doesn't dip between them: less
    than 6 dB below the quieter of the two. On two real practice lines, every
    boundary heard as a break dipped 6.5 dB or more (6.5 to 34), and every
    run-together one under 5.2. A run over 1.3 s is split at its deepest dip,
    and a unit under 0.15 s joins the neighbour it dips least from.

    Each unit has `text` (its words), `start` and `end`."""
    if not words:
        return []
    hop = 160
    db = _levels(y, hop)

    def frame(t: float) -> int:
        """The loudness frame at `t` seconds, kept inside the recording."""
        return int(np.clip(round(t * sr / hop), 0, len(db) - 1))

    def level(w: dict) -> float:
        """How loud a word is: the median over its frames, in dB."""
        a, b = frame(w["start"]), frame(w["end"])
        return float(np.median(db[a : max(b, a + 1)]))

    def dip(i: int) -> float:
        """How far the sound drops between word i and word i + 1, below the quieter one."""
        a, b = words[i], words[i + 1]
        # The quietest point in the gap, looked for 40 ms either side of it.
        lo, hi = frame(min(a["end"], b["start"]) - 0.04), frame(max(a["end"], b["start"]) + 0.04)
        return min(level(a), level(b)) - float(db[lo : hi + 1].min())

    # 1. Join neighbours with little dip between them and under 60 ms apart
    dips = [dip(i) for i in range(len(words) - 1)]
    groups = [[0]]
    for i in range(1, len(words)):
        joined = dips[i - 1] < JOIN_DIP and words[i]["start"] - words[i - 1]["end"] < 0.06
        if joined:
            groups[-1].append(i)
        else:
            groups.append([i])

    def length(g: list[int]) -> float:
        """Seconds from the start of a group's first word to the end of its last."""
        return words[g[-1]]["end"] - words[g[0]]["start"]

    def split(g: list[int]) -> list[list[int]]:
        """A group split at its deepest dip, and each piece again, while it runs over
        MAX_UNIT and a cut leaves a playable piece either side."""
        if length(g) <= MAX_UNIT or len(g) < 2:
            return [g]
        # The deepest dip that leaves a playable piece (0.25 s) either side.
        cuts = [k for k in range(1, len(g)) if length(g[:k]) >= 0.25 and length(g[k:]) >= 0.25]
        if not cuts:
            return [g]
        k = max(cuts, key=lambda k: dips[g[k - 1]])
        return split(g[:k]) + split(g[k:])

    # 2. Split long runs, then join each short unit to the neighbour it dips least from
    groups = [piece for g in groups for piece in split(g)]
    while len(groups) > 1:
        short = next((k for k, g in enumerate(groups) if length(g) < MIN_UNIT), None)
        if short is None:
            break
        left = dips[groups[short][0] - 1] if short > 0 else np.inf
        right = dips[groups[short][-1]] if short + 1 < len(groups) else np.inf
        if left <= right:
            groups[short - 1 : short + 1] = [groups[short - 1] + groups[short]]
        else:
            groups[short : short + 2] = [groups[short] + groups[short + 1]]

    # 3. One unit per group, none shorter than 50 ms
    return [
        {
            "text": " ".join(words[i]["text"] for i in g),
            "start": words[g[0]]["start"],
            "end": max(words[g[-1]]["end"], round(words[g[0]]["start"] + 0.05, 2)),
            "count": len(g),
        }
        for g in groups
    ]


# --- COMPARING WORDS ---


def normalise(word: str) -> str:
    """A word as compared: lower case, letters and digits only."""
    return re.sub(r"[^a-z0-9]", "", word.lower())


def same_word(a: str, b: str) -> bool:
    """Spelling-tolerant: "steels" and "steeles" are the same name heard twice."""
    if a == b:
        return True
    if min(len(a), len(b)) < 3:
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.8


@dataclass
class LineCheck:
    """A take's words lined up with the target's."""

    target: list[str]  # target's words as transcribed
    take: list[str]
    # One (kind, target i, take j) per step; kind is match, sub, miss or extra.
    ops: list[tuple[str, int | None, int | None]] = field(default_factory=list)

    @property
    def matched(self) -> int:
        """Target words the take said."""
        return sum(1 for kind, _, _ in self.ops if kind == "match")

    @property
    def errors(self) -> int:
        """Target words not said, plus words added."""
        return sum(1 for kind, _, _ in self.ops if kind != "match")

    @property
    def flagged(self) -> bool:
        """Worth telling the user. One misheard word happens to correct takes
        often ("Docs" for "Dogs"); two or more rarely do. On RAVDESS through a
        simulated mic this flags 7.1% of takes that said the line, all of the
        other sentence, and 90-96% of takes cut to 60% (tools/validate.py line)."""
        return self.errors >= max(2, math.ceil(0.15 * len(self.target)))

    @property
    def match_rate(self) -> float:
        """Share of the target's words the take said, 0..1."""
        return self.matched / len(self.target) if self.target else 1.0

    def runs(self, kind: str) -> list[str]:
        """Consecutive words of one kind, as phrases."""
        out, current = [], []
        for k, i, j in self.ops + [("end", None, None)]:
            if k == kind:
                current.append(self.take[j] if kind == "extra" else self.target[i])
            elif current:
                out.append(" ".join(current))
                current = []
        return out

    def swaps(self) -> list[tuple[str, str]]:
        """(what the line has, what was said) for runs of substituted words."""
        out, said, line = [], [], []
        for k, i, j in self.ops + [("end", None, None)]:
            if k == "sub":
                line.append(self.target[i])
                said.append(self.take[j])
            elif line:
                out.append((" ".join(line), " ".join(said)))
                said, line = [], []
        return out


def compare_words(target: list[str], take: list[str]) -> LineCheck:
    """Word-level edit-distance alignment with spelling-tolerant matches."""
    # 1. Only words count, compared in their normal form
    target = [w for w in target if normalise(w)]
    take = [w for w in take if normalise(w)]
    a = [normalise(w) for w in target]
    b = [normalise(w) for w in take]

    # 2. The edit distance between every pair of beginnings
    n, m = len(a), len(b)
    cost = np.zeros((n + 1, m + 1), dtype=np.int32)
    cost[:, 0] = np.arange(n + 1)
    cost[0, :] = np.arange(m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost[i, j] = min(
                cost[i - 1, j - 1] + (0 if same_word(a[i - 1], b[j - 1]) else 1),
                cost[i - 1, j] + 1,
                cost[i, j - 1] + 1,
            )

    # 3. The cheapest path, traced back from the end
    ops = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            hit = same_word(a[i - 1], b[j - 1])
            if cost[i, j] == cost[i - 1, j - 1] + (0 if hit else 1):
                ops.append(("match" if hit else "sub", i - 1, j - 1))
                i, j = i - 1, j - 1
                continue
        if i > 0 and cost[i, j] == cost[i - 1, j] + 1:
            ops.append(("miss", i - 1, None))
            i -= 1
        else:
            ops.append(("extra", None, j - 1))
            j -= 1
    return LineCheck(target=target, take=take, ops=ops[::-1])
