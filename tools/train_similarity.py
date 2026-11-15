"""Train the listener-similarity model (src/voicematch/similarity.py) on VoxSim.

    python tools/train_similarity.py --voxsim cache/voxsim --audio cache/voxceleb1

Uses VoxSim training pairs whose clips are in --audio (fetch them with
`tools/validate.py voxsim-fetch`) and reports results on the VoxSim test split,
which training never sees. Writes src/voicematch/similarity_model.json: the
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
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).parent))
from validate import voxsim_pairs  # noqa: E402

from voicematch import similarity  # noqa: E402
from voicematch.analysis import MAX_SECONDS, SR, AnalysisError, analyze, load  # noqa: E402

OUT = Path(__file__).parents[1] / "src" / "voicematch" / "similarity_model.json"
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


def main() -> None:
    from sklearn.ensemble import HistGradientBoostingRegressor

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voxsim", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    train = [p for p in voxsim_pairs(args.voxsim, "train")
             if (args.audio / p["a"]).exists() and (args.audio / p["b"]).exists()]
    test = voxsim_pairs(args.voxsim, "test")
    paths = sorted({str(args.audio / c) for p in train + test for c in (p["a"], p["b"])})
    clips = clip_features([p for p in paths if Path(p).exists()], args.audio / "clips.pkl", args.workers)
    Xtr, ytr, _ = table(train, clips, args.audio)
    Xte, yte, ste = table(test, clips, args.audio)
    print(f"training on {len(ytr)} VoxSim training pairs, testing on {len(yte)} test pairs")

    model = HistGradientBoostingRegressor(max_depth=3, max_iter=200, learning_rate=0.05,
                                          monotonic_cst=MONOTONIC, random_state=0).fit(Xtr, ytr)
    exported = export(model)
    ours = np.array([similarity.predict(x, exported) for x in Xtr])
    theirs = np.clip(model.predict(Xtr), 1, 6)
    assert np.max(np.abs(ours - theirs)) < 1e-9, "exported trees disagree with scikit-learn"

    pred = np.array([similarity.predict(x, exported) for x in Xte])
    cos = Xte[:, 0]
    print("Spearman with listeners on the VoxSim test split:")
    for label, v in (("embedding similarity alone", cos), ("listener-similarity model", pred)):
        print(f"  {label:28s} all {spearmanr(v, yte)[0]:.3f}  strangers {spearmanr(v[~ste], yte[~ste])[0]:.3f}"
              f"  same speaker {spearmanr(v[ste], yte[ste])[0]:.3f}")
    exported.update(
        source=f"VoxSim, {len(ytr)} training pairs (tools/train_similarity.py)",
        features=list(similarity.FEATURES),
        test_spearman={"all": round(float(spearmanr(pred, yte)[0]), 3),
                       "strangers": round(float(spearmanr(pred[~ste], yte[~ste])[0]), 3)},
    )
    OUT.write_text(json.dumps(exported, separators=(",", ":")) + "\n", "utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(exported['trees'])} trees)")


if __name__ == "__main__":
    main()
