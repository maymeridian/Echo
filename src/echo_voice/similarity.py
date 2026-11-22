'''
src/echo_voice/similarity.py
Author: maymeridian
Description: Predicted listener similarity: how alike two voices sound to people.
             - Model: a small tree ensemble (tools/train_similarity.py) trained on VoxSim,
               where listeners rated pairs of real-world clips from 1 (definitely different
               speakers) to 6 (definitely the same).
             - Its inputs don't depend on the words: speaker-embedding similarity, the gap in
               median pitch, and the gaps in median F1-F3. It is constrained to be monotonic:
               more embedding similarity can only raise the prediction, and a bigger pitch or
               formant gap can only lower it.
             - Why not the embedding alone: on VoxSim the embedding tells people apart well
               but barely orders strangers by how close they sound (Spearman 0.15), and every
               impression is a stranger trying to sound like someone. The model reaches 0.63
               there (see the README).
'''

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from . import identity

FEATURES = ("embedding similarity", "pitch gap", "F1 gap", "F2 gap", "F3 gap")  # in order
# The trained trees; None until tools/train_similarity.py has written them.
_PATH = Path(__file__).parent / "similarity_model.json"
_MODEL = json.loads(_PATH.read_text("utf-8")) if _PATH.is_file() else None


def features(a, b) -> np.ndarray:
    """Word-independent features of two analysed recordings (Voice objects)."""
    pitch = math.nan
    if a.median_f0 is not None and b.median_f0 is not None:
        pitch = abs(12 * math.log2(a.median_f0 / b.median_f0))
    gaps = np.abs(12 * np.log2(a.median_formants / b.median_formants))
    return np.array([identity.similarity(a.embedding, b.embedding), pitch, *gaps], dtype=np.float64)


def predict(x: np.ndarray, model: dict | None = None) -> float:
    """Expected listener rating on VoxSim's 1-6 scale."""
    model = model or _MODEL
    if model is None:
        raise RuntimeError("similarity_model.json is missing; run tools/train_similarity.py")
    # Each tree is walked from its root to a leaf, and the leaves are added up.
    total = model["baseline"]
    for tree in model["trees"]:
        node = 0
        while not tree["leaf"][node]:
            v = x[tree["feature"][node]]
            if math.isnan(v):
                left = tree["missing_left"][node]
            else:
                left = v <= tree["threshold"][node]
            node = tree["left"][node] if left else tree["right"][node]
        total += tree["value"][node]
    return float(min(6.0, max(1.0, total)))


def rating(a, b) -> float:
    """The rating listeners would likely give two analysed recordings, 1-6."""
    return predict(features(a, b))
