'''
tools/train_gender.py
Author: maymeridian
Description: Fits gender_model.json: how female or male a voice reads, from pitch and resonance.
             - Usage: python tools/train_gender.py
             - Perceived voice gender rests mostly on two things a voice trainer works on: pitch
               (median f0) and resonance (formants, which follow vocal-tract length).
             - A logistic regression on those two learns the speakers' sex from RAVDESS (24
               actors: odd-numbered men, even women) and VCC2018 (12 speakers), and is tested on
               PTDB-TUG (10 women, 10 men), a different corpus, recorded differently, with no
               speaker in common.
             - The score is 100 times the probability of "female": 0 reads male, 100 female,
               around 50 in between. The labels are the speakers' sex, not listeners' judgments
               of each clip.
'''

from __future__ import annotations

import json
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache"  # where the corpora are downloaded
OUT = ROOT / "src" / "echo_voice" / "gender_model.json"


def _low_priority():
    """Pool initializer: run workers below normal priority on Windows (0x4000 is
    BELOW_NORMAL_PRIORITY_CLASS), so the machine stays usable."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)


def _features(item):
    """One clip's (corpus, speaker, female, [pitch, resonance]), or None if it can't be used."""
    path, corpus, speaker, female = item
    from echo_voice import analyze, load
    from echo_voice.gender import features

    try:
        x = features(analyze(load(path)))
    except Exception:  # a clip that can't be read or measured is left out
        x = None
    return None if x is None else (corpus, speaker, female, x.tolist())


def files() -> list[tuple]:
    """The clips to measure, as (path, corpus, speaker, female): 8 random takes per RAVDESS
    actor, every PTDB-TUG microphone recording, and 10 random sentences per VCC2018 speaker."""
    rng = random.Random(0)
    items = []
    for actor in sorted((CACHE / "ravdess").glob("Actor_*")):
        n = int(actor.name.split("_")[1])
        for f in rng.sample(sorted(actor.glob("*.wav")), 8):
            items.append((str(f), "ravdess", actor.name, n % 2 == 0))
    for f in sorted((CACHE / "ptdb").glob("mic_*.wav")):
        speaker = f.name.split("_")[1]
        items.append((str(f), "ptdb", speaker, speaker.startswith("F")))
    for folder in ("vcc2018_evaluation", "vcc2018_reference"):
        for d in sorted((CACHE / "vcc2018" / folder).iterdir()):
            if d.is_dir():
                for f in rng.sample(sorted(d.glob("*.wav")), 10):
                    items.append((str(f), "vcc2018", d.name, d.name[5] == "F"))
    return items


def main() -> None:
    """Measure the clips, report each corpus held out in turn, and write the model fitted on all."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score

    # 1. Measure every clip, in 6 worker processes.
    items = files()
    with ProcessPoolExecutor(6, initializer=_low_priority) as pool:
        rows = [r for r in pool.map(_features, items, chunksize=4) if r is not None]
    print(f"{len(rows)} of {len(items)} clips measured")
    corpus = np.array([r[0] for r in rows])
    speaker = np.array([f"{r[0]}:{r[1]}" for r in rows])
    y = np.array([r[2] for r in rows], dtype=int)
    X = np.array([r[3] for r in rows])
    mean, scale = X.mean(axis=0), X.std(axis=0)
    Z = (X - mean) / scale

    def report(name, train, test):
        """Fit on the `train` clips and print how well it does on the `test` clips."""
        model = LogisticRegression().fit(Z[train], y[train])
        p = model.predict_proba(Z[test])[:, 1]
        clip_acc = np.mean((p > 0.5) == y[test])
        spk = sorted(set(speaker[test]))
        spk_p = np.array([p[speaker[test] == s].mean() for s in spk])
        spk_y = np.array([y[test][speaker[test] == s][0] for s in spk])
        print(
            f"{name}: clips {clip_acc:.1%} right (AUC {roc_auc_score(y[test], p):.3f}); "
            f"speakers {np.mean((spk_p > 0.5) == spk_y):.0%} of {len(spk)}; "
            f"women's clips score {np.median(p[y[test] == 1]) * 100:.0f}, "
            f"men's {np.median(p[y[test] == 0]) * 100:.0f} (medians)"
        )

    # 2. Each corpus held out in turn.
    report("train RAVDESS+VCC2018, test PTDB-TUG", corpus != "ptdb", corpus == "ptdb")
    report("train RAVDESS, test VCC2018", corpus == "ravdess", corpus == "vcc2018")
    report("train VCC2018+PTDB-TUG, test RAVDESS", corpus != "ravdess", corpus == "ravdess")

    # 3. The model the app uses, fitted on everything.
    model = LogisticRegression().fit(Z, y)
    OUT.write_text(
        json.dumps(
            {
                "source": (
                    "logistic regression on pitch and resonance; RAVDESS, VCC2018, PTDB-TUG "
                    "(tools/train_gender.py)"
                ),
                "features": [
                    "median f0, semitones re 100 Hz",
                    "mean F2-F3 shift, semitones re 1500/2500 Hz",
                ],
                "mean": mean.round(4).tolist(),
                "scale": scale.round(4).tolist(),
                "weights": model.coef_[0].round(4).tolist(),
                "bias": round(float(model.intercept_[0]), 4),
            },
            indent=2,
        ),
        "utf-8",
    )
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
