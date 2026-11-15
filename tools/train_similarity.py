"""Train the listener-similarity model (src/echo_voice/similarity.py) on VoxSim.

    python tools/train_similarity.py --voxsim cache/voxsim --audio cache/voxceleb1

Uses VoxSim training pairs whose clips are in --audio (fetch them with
`tools/validate.py voxsim-fetch`) and reports results on the VoxSim test split,
which training never sees. Writes src/echo_voice/similarity_model.json: the
fitted trees as plain JSON, checked to give the same predictions as
scikit-learn, so the app needs no pickle and no scikit-learn.

VoxSim ratings: Ahn et al., Interspeech 2024, CC BY 4.0.
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from scipy.stats import pearsonr, spearmanr

sys.path.insert(0, str(Path(__file__).parent))
from validate import voxsim_pairs  # noqa: E402

from echo_voice import similarity  # noqa: E402
from echo_voice.analysis import MAX_SECONDS, SR, AnalysisError, analyze, load  # noqa: E402

OUT = Path(__file__).parents[1] / "src" / "echo_voice" / "similarity_model.json"
MONOTONIC = [1, -1, -1, -1, -1]  # see similarity.FEATURES


def _low_priority():
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)


def _analyse(path: str):
    """Just what similarity.features needs from a Voice."""
    try:
        v = analyze(load(path)[: int((MAX_SECONDS - 1) * SR)])
    except AnalysisError:
        return path, None
    return path, SimpleNamespace(embedding=v.embedding, median_f0=v.median_f0, median_formants=v.median_formants)


def clip_features(paths: list[str], cache: Path, workers: int) -> dict:
    done = pickle.loads(cache.read_bytes()) if cache.exists() else {}
    todo = [p for p in paths if p not in done]
    if todo:
        print(f"analysing {len(todo)} clips...", file=sys.stderr)
        with ProcessPoolExecutor(workers, initializer=_low_priority) as pool:
            for i, (path, clip) in enumerate(pool.map(_analyse, todo, chunksize=8), 1):
                done[path] = clip
                if i % 2000 == 0:
                    cache.write_bytes(pickle.dumps(done))
        cache.write_bytes(pickle.dumps(done))
    return done


def table(pairs, clips, audio: Path):
    X, y, same = [], [], []
    for p in pairs:
        a, b = clips.get(str(audio / p["a"])), clips.get(str(audio / p["b"]))
        if a is None or b is None:
            continue
        X.append(similarity.features(a, b))
        y.append(p["rating"])
        same.append(p["same"])
    return np.array(X), np.array(y), np.array(same)


def export(model) -> dict:
    """The fitted trees as plain lists. Leaf values already include the
    learning rate in scikit-learn's HistGradientBoostingRegressor."""
    trees = []
    for (predictor,) in model._predictors:
        n = predictor.nodes
        trees.append({
            "feature": n["feature_idx"].tolist(),
            "threshold": n["num_threshold"].tolist(),
            "missing_left": n["missing_go_to_left"].astype(bool).tolist(),
            "left": n["left"].tolist(),
            "right": n["right"].tolist(),
            "leaf": n["is_leaf"].astype(bool).tolist(),
            "value": n["value"].tolist(),
        })
    return {"baseline": float(np.ravel(model._baseline_prediction)[0]), "trees": trees}


def _speaker(clip: str) -> str:
    return clip.split("/")[0]


def tune(train: list[dict], clips, audio: Path) -> dict:
    """Pick tree settings on held-out training speakers, never the test split.

    10% of training speakers are set aside; pairs where both speakers are set
    aside form the validation set, pairs where neither is form the fitting set,
    and pairs that straddle the two are dropped, so no voice is in both."""
    import itertools
    import random

    from sklearn.ensemble import HistGradientBoostingRegressor

    speakers = sorted({_speaker(c) for p in train for c in (p["a"], p["b"])})
    held = set(random.Random(0).sample(speakers, len(speakers) // 10))
    fit = [p for p in train if _speaker(p["a"]) not in held and _speaker(p["b"]) not in held]
    val = [p for p in train if _speaker(p["a"]) in held and _speaker(p["b"]) in held]
    Xf, yf, _ = table(fit, clips, audio)
    Xv, yv, sv = table(val, clips, audio)
    print(f"tuning: fit on {len(yf)} pairs, validate on {len(yv)} pairs from {len(held)} held-out speakers")
    best = None
    for depth, iters, rate, leaf in itertools.product((2, 3, 4, 6), (200, 500), (0.03, 0.1), (20, 100)):
        params = dict(max_depth=depth, max_iter=iters, learning_rate=rate, min_samples_leaf=leaf)
        model = HistGradientBoostingRegressor(monotonic_cst=MONOTONIC, random_state=0, **params).fit(Xf, yf)
        pred = model.predict(Xv)
        # Both jobs count: ranking everyone, and ranking strangers.
        score = (spearmanr(pred, yv)[0] + spearmanr(pred[~sv], yv[~sv])[0]) / 2
        if best is None or score > best[0]:
            best = (score, params)
    print(f"  best on validation: {best[1]} (mean of all-pairs and stranger Spearman {best[0]:.3f})")
    return best[1]


def main() -> None:
    from sklearn.ensemble import HistGradientBoostingRegressor

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voxsim", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--analyse-only", action="store_true",
                        help="just analyse the clips that are there (to warm the cache during a download)")
    args = parser.parse_args()

    train = [p for p in voxsim_pairs(args.voxsim, "train")
             if (args.audio / p["a"]).exists() and (args.audio / p["b"]).exists()]
    test = voxsim_pairs(args.voxsim, "test")
    paths = sorted({str(args.audio / c) for p in train + test for c in (p["a"], p["b"])})
    clips = clip_features([p for p in paths if Path(p).exists()], args.audio / "clips.pkl", args.workers)
    if args.analyse_only:
        print(f"{len(clips)} clips analysed")
        return
    params = tune(train, clips, args.audio)
    Xtr, ytr, _ = table(train, clips, args.audio)
    Xte, yte, ste = table(test, clips, args.audio)
    print(f"training on {len(ytr)} VoxSim training pairs, testing on {len(yte)} test pairs")

    model = HistGradientBoostingRegressor(monotonic_cst=MONOTONIC, random_state=0, **params).fit(Xtr, ytr)
    exported = export(model)
    ours = np.array([similarity.predict(x, exported) for x in Xtr])
    theirs = np.clip(model.predict(Xtr), 1, 6)
    assert np.max(np.abs(ours - theirs)) < 1e-9, "exported trees disagree with scikit-learn"

    pred = np.array([similarity.predict(x, exported) for x in Xte])
    cos = Xte[:, 0]
    print("Pearson / Spearman with listeners on the VoxSim test split:")
    for label, v in (("embedding similarity alone", cos), ("listener-similarity model", pred)):
        cells = []
        for sel in (slice(None), ~ste, ste):
            cells.append(f"{pearsonr(v[sel], yte[sel])[0]:.3f} / {spearmanr(v[sel], yte[sel])[0]:.3f}")
        print(f"  {label:28s} all {cells[0]}   strangers {cells[1]}   same speaker {cells[2]}")
    exported.update(
        source=f"VoxSim, {len(ytr)} training pairs (tools/train_similarity.py)",
        params=params,
        features=list(similarity.FEATURES),
        test_spearman={"all": round(float(spearmanr(pred, yte)[0]), 3),
                       "strangers": round(float(spearmanr(pred[~ste], yte[~ste])[0]), 3)},
    )
    OUT.write_text(json.dumps(exported, separators=(",", ":")) + "\n", "utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(exported['trees'])} trees)")


if __name__ == "__main__":
    main()
