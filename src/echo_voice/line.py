"""Which words were said: speech recognition plus a word-level comparison.

The line-matching parts of the score (melody, articulation, rhythm) only mean
something when the take says the target's words. Sound-based alignment can't
check that across different voices (it flagged 65% of strangers who said the
whole line), so the words are transcribed with Whisper (base.en via
faster-whisper, on the CPU) and compared as text.
"""

from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import numpy as np

MODEL = "base.en"
_lock = threading.Lock()
_model = None


def _get_model():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel

            _model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=2)
        return _model


def transcribe(y: np.ndarray) -> list[str]:
    """Words spoken in a 16 kHz mono recording, as written (with punctuation)."""
    model = _get_model()
    with _lock:
        segments, _ = model.transcribe(
            np.ascontiguousarray(y, dtype=np.float32), language="en", beam_size=5,
            condition_on_previous_text=False, vad_filter=False,
        )
        text = " ".join(s.text for s in segments)
    # Whisper sometimes writes a cut-off word as a run of block characters;
    # anything without a letter or digit isn't a word.
    return [w for w in text.split() if normalise(w)]


def normalise(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower().replace("’", "'"))


def same_word(a: str, b: str) -> bool:
    """Spelling-tolerant: "steels" and "steeles" are the same name heard twice."""
    if a == b:
        return True
    if min(len(a), len(b)) < 3:
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.8


@dataclass
class LineCheck:
    target: list[str]  # target's words as transcribed
    take: list[str]
    ops: list[tuple[str, int | None, int | None]] = field(default_factory=list)  # (kind, target i, take j)

    @property
    def matched(self) -> int:
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
    target = [w for w in target if normalise(w)]
    take = [w for w in take if normalise(w)]
    a = [normalise(w) for w in target]
    b = [normalise(w) for w in take]
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
