'''
tools/live_resonance_ref.py
Author: maymeridian
Description: Writes reference measurements for tools/check_live_resonance.mjs.
             - Usage, the reference first and then the check:
                 python tools/live_resonance_ref.py --vcc cache/vcc2018 \
                     --out cache/live_resonance_ref.json
                 node tools/check_live_resonance.mjs cache/live_resonance_ref.json
             - The page's live resonance meter tracks formants in the browser
               (static/formants.js). This writes what the app itself measures on the same
               recordings, for that tracker to be checked against: 12 speakers of VCC2018
               (Lorenzo-Trueba et al., 2018; CC BY 4.0) reading the same 12 sentences.
             - For every recording: its speaker's formant ceiling (the middle of what the
               speaker's recordings pick, as the app chooses it) and the typical F1-F3 Praat
               finds at that ceiling.
             - For every pair of speakers on one sentence: the one ceiling the app reads both at
               (the middle of the two speakers'), and how much brighter or darker one is than
               the other there, from F2 and F3: both as the trainer measures it (the gap between
               typical formants) and as a take's Resonance does (along the alignment).
'''

from __future__ import annotations

import argparse
import itertools
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from echo_voice.analysis import (
    AnalysisError,
    analyze,
    load,
    shared_ceiling,
    speaker_ceiling,
    steadied,
)
from echo_voice.scoring import align, on_grid, read_resonance

# --- THE RECORDINGS ---

SENTENCES = range(30001, 30013)  # the 12 sentences every VCC2018 speaker reads


# --- MEASURING ---


def low_priority() -> None:
    """Pool initializer: run workers below normal priority on Windows (0x4000 is
    BELOW_NORMAL_PRIORITY_CLASS), so the machine stays usable."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)


def job(path: str):
    """One recording analysed, as (path, Voice), or (path, None) if it can't be."""
    try:
        voice = analyze(load(path))
    except AnalysisError:
        return path, None
    _ = voice.sound  # what the recogniser hears, for the alignment: worked out here, in the worker
    return path, voice


def aligned_shift(a, b) -> float | None:
    """scoring.measure's signed Resonance, without the parts that need a speaker model."""
    si, sj, _ = align(a, b)
    return read_resonance(a, b, *on_grid(a, b, si, sj))[2]


# --- COMMAND LINE ---

# The --help text.
HELP = """Reference measurements for tools/check_live_resonance.mjs.

The page's live resonance meter tracks formants in the browser
(static/formants.js). This writes what the app itself measures on the same
recordings, for that tracker to be checked against: 12 speakers of VCC2018
(Lorenzo-Trueba et al., 2018; CC BY 4.0) reading the same 12 sentences.

For every recording: its speaker's formant ceiling (the middle of what the
speaker's recordings pick, as the app chooses it) and the typical F1-F3 Praat
finds at that ceiling. For every pair of speakers on one sentence: the one
ceiling the app reads both at (the middle of the two speakers'), and how much
brighter or darker one is than the other there, from F2 and F3: both as the
trainer measures it (the gap between typical formants) and as a take's
Resonance does (along the alignment).

    python tools/live_resonance_ref.py --vcc cache/vcc2018 --out cache/live_resonance_ref.json
    node tools/check_live_resonance.mjs cache/live_resonance_ref.json
"""


def main() -> None:
    """Analyse every recording, then write each one's measurements and each pair's gaps."""
    parser = argparse.ArgumentParser(
        description=HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--vcc", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    # 1. Every speaker's recordings of the 12 sentences, analysed.
    sources = sorted((args.vcc / "vcc2018_evaluation").glob("VCC2S*"))
    targets = sorted((args.vcc / "vcc2018_reference").glob("VCC2T*"))
    folders = sources + targets
    paths = [str(folder / f"{n}.wav") for folder in folders for n in SENTENCES]
    voices = {}
    with ProcessPoolExecutor(args.workers, initializer=low_priority) as pool:
        for path, voice in pool.map(job, paths, chunksize=4):
            if voice is not None:
                voices[path] = voice

    # 2. Each recording at its speaker's ceiling: the typical F1-F3 there.
    files = []
    for folder in folders:
        mine = [p for p in voices if Path(p).parent == folder]
        ceiling = speaker_ceiling(voices[p].formant_ceiling for p in mine)
        for p in mine:
            v = steadied(voices[p], ceiling)
            typical = np.nanmedian(v.resonance_formants[:, v.voiced], axis=1)
            files.append(
                {
                    "path": p,
                    "speaker": folder.name,
                    "sentence": int(Path(p).stem),
                    "ceiling": ceiling,
                    "f0": v.median_f0,
                    "formants": [round(float(f), 1) for f in typical],
                }
            )
    index = {f["path"]: i for i, f in enumerate(files)}

    # 3. Each pair of speakers on one sentence, both read at the pair's ceiling.
    pairs = []
    for n in SENTENCES:
        said = [f for f in files if f["sentence"] == n]
        for a, b in itertools.combinations(said, 2):
            ceiling = shared_ceiling(a["ceiling"], b["ceiling"])
            va, vb = steadied(voices[a["path"]], ceiling), steadied(voices[b["path"]], ceiling)
            fa, fb = (np.nanmedian(v.resonance_formants[1:, v.voiced], axis=1) for v in (va, vb))
            typical = float(np.mean(12 * np.log2(fb / fa)))
            pairs.append(
                {
                    "target": index[a["path"]],
                    "take": index[b["path"]],
                    # The one ceiling both are read at.
                    "ceiling": ceiling,
                    # Semitones, the take against the target, from typical F2 and F3.
                    "typical": round(typical, 3),
                    # The same along the alignment, as a take is scored.
                    "aligned": aligned_shift(va, vb),
                }
            )
    args.out.write_text(json.dumps({"files": files, "pairs": pairs}, indent=1), "utf-8")
    print(f"{len(files)} recordings of {len(folders)} speakers, {len(pairs)} pairs -> {args.out}")


if __name__ == "__main__":
    main()
