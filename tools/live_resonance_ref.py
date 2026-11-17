"""Reference measurements for tools/check_live_resonance.mjs.

The page's live resonance meter tracks formants in the browser
(static/formants.js). This writes what the app itself measures on the same
recordings, for that tracker to be checked against: 12 speakers of VCC2018
(Lorenzo-Trueba et al., 2018; CC BY 4.0) reading the same 12 sentences.

For every recording: its speaker's formant ceiling (the middle of what the
speaker's recordings pick, as the app chooses it) and the typical F1-F3 Praat
finds at that ceiling. For every pair of speakers on one sentence: how much
brighter or darker one is than the other, both as the trainer measures it (the
gap between typical formants) and as a take's Resonance does (along the
alignment).

    python tools/live_resonance_ref.py --vcc cache/vcc2018 --out cache/live_resonance_ref.json
    node tools/check_live_resonance.mjs cache/live_resonance_ref.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import librosa
import numpy as np

from echo_voice.analysis import AnalysisError, analyze, load, speaker_ceiling, steadied

SENTENCES = range(30001, 30013)


def low_priority() -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)


def job(path: str):
    try:
        voice = analyze(load(path))
    except AnalysisError:
        return path, None
    return path, voice


def aligned_shift(a, b) -> float | None:
    """scoring.measure's signed Resonance, without the parts that need a speaker model."""
    _, path = librosa.sequence.dtw(X=a.mfcc, Y=b.mfcc, metric="cosine")
    ti, ki = path[::-1][:, 0], path[::-1][:, 1]
    fa, fb = a.resonance_formants[:, ti], b.resonance_formants[:, ki]
    ok = np.isfinite(fa).all(axis=0) & np.isfinite(fb).all(axis=0)
    if ok.sum() < 20:
        return None
    return float(np.median(12 * np.log2(fb[:, ok] / fa[:, ok]), axis=1).mean())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vcc", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    folders = sorted((args.vcc / "vcc2018_evaluation").glob("VCC2S*")) + sorted((args.vcc / "vcc2018_reference").glob("VCC2T*"))
    paths = [str(folder / f"{n}.wav") for folder in folders for n in SENTENCES]
    voices = {}
    with ProcessPoolExecutor(args.workers, initializer=low_priority) as pool:
        for path, voice in pool.map(job, paths, chunksize=4):
            if voice is not None:
                voices[path] = voice

    files, steady = [], {}
    for folder in folders:
        mine = [p for p in voices if Path(p).parent == folder]
        ceiling = speaker_ceiling(voices[p].formant_ceiling for p in mine)
        for p in mine:
            v = steadied(voices[p], ceiling)
            steady[p] = v
            typical = np.nanmedian(v.resonance_formants[:, v.voiced], axis=1)
            files.append({
                "path": p,
                "speaker": folder.name,
                "sentence": int(Path(p).stem),
                "ceiling": ceiling,
                "f0": v.median_f0,
                "formants": [round(float(f), 1) for f in typical],
            })
    index = {f["path"]: i for i, f in enumerate(files)}

    pairs = []
    for n in SENTENCES:
        said = [f for f in files if f["sentence"] == n]
        for a, b in itertools.combinations(said, 2):
            va, vb = steady[a["path"]], steady[b["path"]]
            typical = float(np.mean(12 * np.log2(np.array(b["formants"]) / np.array(a["formants"]))))
            pairs.append({
                "target": index[a["path"]],
                "take": index[b["path"]],
                "typical": round(typical, 3),  # semitones, the take against the target, from typical formants
                "aligned": aligned_shift(va, vb),  # the same along the alignment, as a take is scored
            })
    args.out.write_text(json.dumps({"files": files, "pairs": pairs}, indent=1), "utf-8")
    print(f"{len(files)} recordings of {len(folders)} speakers, {len(pairs)} pairs -> {args.out}")


if __name__ == "__main__":
    main()
