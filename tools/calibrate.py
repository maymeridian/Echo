'''
tools/calibrate.py
Author: maymeridian
Description: Calibrates the 0-100 curves against RAVDESS.
             - Usage:
                 python tools/calibrate.py cohort-mean               # writes speaker_mean.json
                 python tools/calibrate.py collect --ravdess cache/ravdess \
                     --vcc cache/vcc2018/vcc2018_evaluation --out cache/pairs.jsonl
                 python tools/calibrate.py fit cache/pairs.jsonl     # writes calibration.json
             - Download: https://zenodo.org/records/1188976 (Audio_Speech_Actors_01-24.zip).
             - RAVDESS (Livingstone & Russo, 2018; CC BY-NC-SA 4.0) has 24 actors, 12 male
               and 12 female, each saying the same two sentences in eight emotions, twice each.
               That gives what the scale needs:
                 retake      same actor, same line, same emotion, the other take
                 delivery    same actor, same line, a different emotion
                 words       same actor, the other sentence
                 stranger    another actor of the same sex, same line, any emotion
                 same_sex    another actor of the same sex, same line, same emotion
                 cross_sex   another actor of the other sex, same line, same emotion
             - Each curve scores 90 at the median retake and 20 at the median stranger. Each
               component's weight in the overall score is how much better than chance it tells
               a retake from a stranger (AUC - 0.5). The weighted mean then goes through one
               more curve with the same two anchors, so the headline means the same thing.
             - Articulation is the exception. It compares the sounds, as a speech recogniser
               hears them, and a stranger reading the same line makes nearly the same sounds:
               anchored at the stranger, the whole scale would sit inside the difference
               between two microphones. Its 20 is another line altogether:
                 other_line  the same speaker saying a different sentence (VCC2018, which has
                             35 sentences from each of 8 speakers; RAVDESS has two sentences
                             that share four of their six words)
               So Articulation reads how much of the line's sounds were matched: about 90 for
               the real person again, lower for a stranger reading it their own way, 20 for
               different words. Its weight is still how well it tells a retake from a stranger.
             - The target is always the clean studio file. The take goes through a simulated
               microphone and room (band limit, reverb, noise), because in real use the target
               comes from a clip and the take from your own mic.
             - Resonance reads both recordings of a pair at one formant ceiling: the middle of
               the two speakers' ceilings (analysis.shared_ceiling). The app has about five
               lines of a voice and a few takes of yours to choose each speaker's from, so here
               a recording's speaker ceiling is the middle of its own pick and the picks of four
               other recordings of the same actor through the same channel.
'''

from __future__ import annotations

import argparse
import itertools
import json
import random
import sys
import zlib
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from scipy.signal import butter, fftconvolve, sosfilt

from echo_voice.analysis import (
    FORMANT_CEILINGS,
    SR,
    AnalysisError,
    analyze,
    load,
    shared_ceiling,
    speaker_ceiling,
    steadied,
)
from echo_voice.scoring import component_scores, curve, measure, overall, weighted

# The calibration the app ships: `fit` rewrites it, and `cohort-mean` writes beside it.
CALIBRATION = Path(__file__).parents[1] / "src" / "echo_voice" / "calibration.json"


def low_priority() -> None:
    """Pool initializer: run workers below normal priority on Windows, so a long
    calibration doesn't get in the way of whatever else the machine is doing.
    0x4000 is BELOW_NORMAL_PRIORITY_CLASS."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)


# --- SIMULATED MICROPHONE AND ROOM ---


def channel(y: np.ndarray, seed: int) -> np.ndarray:
    """A recording as a microphone in a room would pick it up: band-limited, reverberant and
    noisy, with every setting drawn at random from `seed`."""
    # 1. The microphone's band: a low cut at 80-300 Hz, a high cut at 3.5-7.5 kHz.
    rng = np.random.default_rng(seed)
    low, high = rng.uniform(80, 300), rng.uniform(3500, 7500)
    y = sosfilt(butter(4, [low, high], btype="bandpass", fs=SR, output="sos"), y)

    # 2. A room heard from about arm's length: RT60 0.2-0.6 s, direct sound 3-12 dB louder
    # than the reverberant tail, which starts 3 ms after it.
    rt60 = rng.uniform(0.2, 0.6)
    n = int(rt60 * SR)
    t = np.arange(n) / SR
    tail = rng.standard_normal(n) * np.exp(-6.9 * t / rt60)  # -60 dB at rt60
    tail *= 10 ** (-rng.uniform(3, 12) / 20) / np.sqrt(np.sum(tail**2))
    rir = np.concatenate([[1.0], np.zeros(int(0.003 * SR)), tail])
    y = fftconvolve(y, rir)[: len(y)]

    # 3. Noise 15-30 dB under the speech.
    noise = np.cumsum(rng.standard_normal(len(y))) * 0.02  # brownish room hum
    noise += rng.standard_normal(len(y))
    noise -= noise.mean()
    snr_db = rng.uniform(15, 30)
    speech = np.sqrt(np.mean(y**2))
    y = y + noise * speech / np.sqrt(np.mean(noise**2)) / 10 ** (snr_db / 20)
    return y.astype(np.float32)


# --- COLLECT ---


def parse(path: Path) -> dict:
    """What a RAVDESS file name says. 03-01-EE-II-SS-RR-AA.wav: audio-only, speech, emotion,
    intensity, statement, repetition, actor (odd-numbered actors are men)."""
    _, _, emotion, intensity, statement, rep, actor = path.stem.split("-")
    return {
        "file": path.stem,
        "emotion": emotion,
        "intensity": intensity,
        "statement": statement,
        "rep": rep,
        "actor": int(actor),
        "sex": "m" if int(actor) % 2 else "f",
    }


def analyse_file(path: Path):
    """A RAVDESS file analysed clean and through a simulated microphone, with everything a pair
    needs worked out here, in the worker: (stem, {"clean": Voice, "mic": Voice}), or
    (stem, None) if either can't be analysed."""
    y = load(path)
    out = {}
    for kind, audio in (("clean", y), ("mic", channel(y, zlib.crc32(path.stem.encode())))):
        try:
            voice = analyze(audio)
        except AnalysisError:
            return path.stem, None
        # The models, and what the recogniser hears.
        _ = voice.embedding, voice.sound
        for ceiling in (4000.0, 4250.0, *FORMANT_CEILINGS):  # every ceiling a pair may be read at
            voice.at(ceiling)
        out[kind] = voice
    return path.stem, out


def heard_file(path: Path):
    """A recording as the recogniser hears it, clean and through a microphone: all
    Articulation needs."""
    y = load(path)
    out = {}
    for kind, audio in (("clean", y), ("mic", channel(y, zlib.crc32(str(path).encode())))):
        try:
            voice = analyze(audio)
        except AnalysisError:
            return str(path), None
        _ = voice.sound
        out[kind] = voice
    return str(path), out


def collect(ravdess: Path, out: Path, workers: int, seed: int, vcc: Path | None = None) -> None:
    """Measure every kind of pair on RAVDESS (and other_line pairs on VCC2018, given `vcc`)
    and write one JSON line per pair to `out`."""
    # 1. Every file, clean and through a mic.
    files = sorted(ravdess.glob("Actor_*/03-01-*.wav"))
    meta = {p.stem: parse(p) for p in files}
    print(f"analysing {len(files)} files twice (clean and through a mic)…", file=sys.stderr)
    voices = {}
    with ProcessPoolExecutor(workers, initializer=low_priority) as pool:
        for i, (stem, v) in enumerate(pool.map(analyse_file, files, chunksize=8), 1):
            if v is not None:
                voices[stem] = v
            if i % 200 == 0:
                print(f"  {i}/{len(files)}", file=sys.stderr)

    # 2. One formant ceiling per speaker for Resonance, chosen from five recordings.
    by_actor = defaultdict(list)
    for stem in sorted(voices):
        by_actor[meta[stem]["actor"]].append(stem)
    ceiling_rng = random.Random(seed + 1)  # its own stream, so the pairs drawn below stay the same
    ceilings = {}
    for stems in by_actor.values():
        for kind in ("clean", "mic"):
            for stem in stems:
                others = ceiling_rng.sample([s for s in stems if s != stem], 4)
                ceilings[(stem, kind)] = speaker_ceiling(
                    voices[s][kind].formant_ceiling for s in (stem, *others)
                )

    # 3. The pairs, by (actor, statement, emotion, intensity, repetition): retakes, the other
    # sentence and other deliveries by the same actor. Neutral (01) has only normal intensity.
    by_key = {}
    for stem, m in meta.items():
        by_key[(m["actor"], m["statement"], m["emotion"], m["intensity"], m["rep"])] = stem
    pairs = []
    deliveries = [(e, "01") for e in ("01", "02", "03", "04", "05", "06", "07", "08")]
    deliveries += [(e, "02") for e in ("02", "03", "04", "05", "06", "07", "08")]
    actors = range(1, 25)
    for a, s, (e, i) in itertools.product(actors, ("01", "02"), deliveries):
        pairs.append(("retake", (a, s, e, i, "01"), (a, s, e, i, "02")))
        pairs.append(("retake_clean", (a, s, e, i, "01"), (a, s, e, i, "02")))
        if s == "01":
            pairs.append(("words", (a, "01", e, i, "01"), (a, "02", e, i, "01")))
        if (e, i) != ("01", "01") and i == "01":
            pairs.append(("delivery", (a, s, "01", "01", "01"), (a, s, e, i, "01")))

    # 4. Other actors, same delivery: a sample of each sex pairing.
    rng = random.Random(seed)
    others = defaultdict(list)
    for a, b in itertools.permutations(actors, 2):
        kind = "same_sex" if a % 2 == b % 2 else "cross_sex"
        for s, (e, i) in itertools.product(("01", "02"), deliveries):
            others[kind].append((kind, (a, s, e, i, "01"), (b, s, e, i, "02")))
    pairs += rng.sample(others["same_sex"], 2000) + rng.sample(others["cross_sex"], 1000)
    pairs += [("same_sex_clean", t, k) for _, t, k in rng.sample(others["same_sex"], 500)]

    # 5. Someone saying the line their own way: emotion chosen independently.
    for _ in range(2000):
        a, b = rng.sample(actors, 2)
        while a % 2 != b % 2:
            a, b = rng.sample(actors, 2)
        s = rng.choice(("01", "02"))
        (e1, i1), (e2, i2) = rng.choice(deliveries), rng.choice(deliveries)
        pairs.append(("stranger", (a, s, e1, i1, "01"), (b, s, e2, i2, "02")))

    # 6. Measure each pair at its shared ceiling, the target clean and the take through a mic
    # (or clean, for the _clean kinds), then the other_line pairs.
    with open(out, "w", encoding="utf-8") as f:
        for kind, tkey, kkey in pairs:
            t, k = by_key.get(tkey), by_key.get(kkey)
            if t not in voices or k not in voices:
                continue
            take_kind = "clean" if kind.endswith("_clean") else "mic"
            ceiling = shared_ceiling(ceilings[(t, "clean")], ceilings[(k, take_kind)])
            target = steadied(voices[t]["clean"], ceiling)
            take = steadied(voices[k][take_kind], ceiling)
            m = measure(target, take)
            row = {"kind": kind, "target": t, "take": k, **m.distances()}
            row.update(
                pitch_signed=m.pitch,
                resonance_signed=m.resonance_shift,
                tempo_signed=m.tempo,
            )
            row.update(cosine=m.identity, listener=m.listener)
            f.write(json.dumps(row) + "\n")
        far = other_lines(vcc, workers, seed) if vcc else []
        for row in far:
            f.write(json.dumps(row) + "\n")
    print(f"wrote {len(pairs)} pairs and {len(far)} other-line pairs to {out}", file=sys.stderr)


def other_lines(vcc: Path, workers: int, seed: int, per_speaker: int = 40) -> list[dict]:
    """The far anchor for Articulation: one speaker saying two different
    sentences, the second through a microphone."""
    files = sorted(vcc.glob("VCC2S*/3*.wav"))
    print(f"hearing {len(files)} VCC2018 files twice…", file=sys.stderr)
    heard = {}
    with ProcessPoolExecutor(workers, initializer=low_priority) as pool:
        for name, v in pool.map(heard_file, files, chunksize=8):
            if v is not None:
                heard[name] = v
    rng = random.Random(seed + 2)
    by_speaker = defaultdict(list)
    for f in files:
        if str(f) in heard:
            by_speaker[f.parent.name].append(str(f))
    rows = []
    for speaker, names in sorted(by_speaker.items()):
        for _ in range(per_speaker):
            a, b = rng.sample(names, 2)
            m = measure(heard[a]["clean"], heard[b]["mic"])
            rows.append(
                {
                    "kind": "other_line",
                    "target": f"{speaker}/{Path(a).stem}",
                    "take": f"{speaker}/{Path(b).stem}",
                    "articulation": m.articulation,
                }
            )
    return rows


# --- COHORT MEAN FOR SPEAKER EMBEDDINGS ---


def cohort_files(cache: Path) -> dict[str, list[str]]:
    """Speakers unrelated to the VCC2018 validation: RAVDESS actors (neutral and
    calm takes), PTDB-TUG talkers and the Hillenbrand adults."""
    spk = defaultdict(list)
    for f in sorted((cache / "ravdess").glob("Actor_*/03-01-0[12]-01-*.wav")):
        spk["ravdess" + f.parent.name].append(str(f))
    for f in sorted((cache / "ptdb").glob("mic_*.wav")):
        spk["ptdb" + f.stem.split("_")[1]].append(str(f))
    for f in sorted((cache / "hillenbrand" / "audio").glob("[mw]*.wav")):
        spk["h95" + f.stem[:3]].append(str(f))
    return spk


def _raw_embedding(path: str):
    """A file's speaker embedding before the cohort mean is taken out: (path, embedding), or
    (path, None) if it can't be analysed."""
    from echo_voice import identity
    from echo_voice.analysis import _prepare

    try:
        return path, identity.raw_embed(_prepare(load(path)))
    except AnalysisError:
        return path, None


def cohort_mean(cache: Path, workers: int) -> None:
    """Mean of the speakers' average embeddings, so no one speaker (or corpus
    with many takes) dominates. Writes src/echo_voice/speaker_mean.json."""
    from echo_voice import identity

    cohort = cohort_files(cache)
    files = sorted({f for fs in cohort.values() for f in fs})
    with ProcessPoolExecutor(workers, initializer=low_priority) as pool:
        emb = dict(pool.map(_raw_embedding, files, chunksize=16))
    cents = [np.mean([emb[f] for f in fs if emb[f] is not None], axis=0) for fs in cohort.values()]
    mean = np.mean(cents, axis=0)
    out = CALIBRATION.parent / "speaker_mean.json"
    out.write_text(
        json.dumps(
            {
                "model": identity.MODEL_NAME,
                "source": (
                    f"{len(cents)} speakers: RAVDESS, PTDB-TUG, Hillenbrand et al. 1995 adults "
                    "(tools/calibrate.py)"
                ),
                "mean": [round(float(v), 6) for v in mean],
            }
        )
        + "\n",
        "utf-8",
    )
    print(f"wrote {out} from {len(cents)} speakers, |mean| = {np.linalg.norm(mean):.3f}")


# --- FIT ---

# The distances a curve is fitted for: each part, with Rhythm as its two halves.
KEYS = ["identity", "pitch", "melody", "resonance", "articulation", "tempo", "timing"]


def auc(good: np.ndarray, bad: np.ndarray) -> float:
    """Chance that a random same-person pair is closer than a random other-person pair."""
    good, bad = np.sort(good), np.sort(bad)
    return float(np.mean(np.searchsorted(good, bad, side="left")) / len(good))


def fit(pairs_path: Path, write: bool) -> None:
    """Fit the curves, weights and speaker check to the collected pairs, print how each kind
    of pair scores, and write them into calibration.json when `write` is set."""
    # 1. The pairs, with the other_line ones kept apart.
    with open(pairs_path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    far = [r["articulation"] for r in rows if r["kind"] == "other_line"]
    if not far:
        sys.exit(
            "No other_line pairs: Articulation needs them for its far anchor. Collect with --vcc."
        )
    rows = [r for r in rows if r["kind"] != "other_line"]
    by_kind = defaultdict(lambda: defaultdict(list))
    for r in rows:
        for k in KEYS:
            if r[k] is not None:
                by_kind[r["kind"]][k].append(r[k])

    # 2. Each component's curve, from the median retake and the median stranger.
    kinds = ["retake_clean", "retake", "delivery", "words", "stranger", "same_sex", "cross_sex"]
    print(f"{'median distance':14s}" + "".join(f"{k:>13s}" for k in kinds) + "   AUC")
    curves, aucs = {}, {}
    med_art = {}
    for key in KEYS:
        med = {kind: float(np.median(by_kind[kind][key])) for kind in kinds}
        if key == "articulation":
            med_art = med
        aucs[key] = auc(np.array(by_kind["retake"][key]), np.array(by_kind["stranger"][key]))
        print(f"{key:14s}" + "".join(f"{med[k]:13.3f}" for k in kinds) + f"   {aucs[key]:.2f}")
        curves[key] = {"at90": round(med["retake"], 4), "at20": round(med["stranger"], 4)}

    # 3. Articulation: 20 is another line altogether (see the top of this file).
    curves["articulation"]["at20"] = round(float(np.median(far)), 4)
    print(
        f"articulation, another line by the same speaker ({len(far)} pairs): {np.median(far):.3f}"
    )
    for kind in kinds:
        score = curve(med_art[kind], **curves["articulation"])
        print(f"  articulation scores a median {kind} pair {score:.0f}")

    # 4. The weights: each component's AUC - 0.5 (at least 0.02), with tempo and timing
    # together as rhythm, as shares of the total.
    raw = {k: max(0.02, aucs[k] - 0.5) for k in KEYS}
    # Rhythm is tempo and timing, each counted by how well it tells a retake from a stranger.
    rhythm = {k: round(raw[k] / (raw["tempo"] + raw["timing"]), 3) for k in ("tempo", "timing")}
    raw["rhythm"] = raw.pop("tempo") + raw.pop("timing")
    weights = {k: round(v / sum(raw.values()), 3) for k, v in raw.items()}
    print("\nweights", weights, "| rhythm is", rhythm)

    # 5. The headline's own curve, with the same two anchors.
    cal = {"curves": curves, "weights": weights, "rhythm": rhythm}
    means = defaultdict(list)
    for r in rows:
        means[r["kind"]].append(weighted(component_scores({k: r[k] for k in KEYS}, cal), weights))
    cal["overall"] = {
        "at90": round(100 - float(np.median(means["retake"])), 3),
        "at20": round(100 - float(np.median(means["stranger"])), 3),
    }
    print(f"\n{'overall p10/50/90':14s}" + "".join(f"{k:>13s}" for k in kinds))
    for label, table in (("weighted mean", means), ("headline", None)):
        if table is None:
            table = defaultdict(list)
            for r in rows:
                headline = overall(component_scores({k: r[k] for k in KEYS}, cal), cal)
                table[r["kind"]].append(headline)
        cells = ["/".join(f"{v:.0f}" for v in np.percentile(table[k], [10, 50, 90])) for k in kinds]
        print(f"{label:14s}" + "".join(f"{c:>13s}" for c in cells))

    # 6. The speaker check.
    def cos(kind):
        """The raw embedding similarity of every pair of a kind: what the speaker check uses."""
        return np.array([r["cosine"] for r in rows if r["kind"] == kind])

    accept = float(np.quantile(cos("same_sex"), 0.99))  # 1% of same-sex strangers get through
    same = np.concatenate([cos("retake"), cos("delivery"), cos("words")])
    print(
        f"\nspeaker check at cosine {accept:.3f}: "
        f"passes {np.mean(same >= accept):.0%} of same-person "
        f"pairs through a mic, 1% of emotion-matched same-sex strangers, "
        f"{np.mean(cos('stranger') >= accept):.1%} of strangers, "
        f"{np.mean(cos('cross_sex') >= accept):.1%} of cross-sex"
    )

    # 7. Into calibration.json, keeping whatever else it holds.
    if write:
        data = json.loads(CALIBRATION.read_text("utf-8"))
        data.update(
            source=(
                "RAVDESS speech, 24 actors, takes through a simulated mic and room "
                "(tools/calibrate.py)"
            ),
            curves=curves,
            weights=weights,
            rhythm=rhythm,
            overall=cal["overall"],
            identity_accept=round(accept, 4),
            auc={k: round(v, 3) for k, v in aucs.items()},
        )
        CALIBRATION.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
        print(f"wrote {CALIBRATION}")


# --- COMMAND LINE ---

# The --help text. The collect line is longer than a line of code may be, so it is split in
# two literals; the text is unchanged.
HELP = (
    """Calibrate the 0-100 curves against RAVDESS.

RAVDESS (Livingstone & Russo, 2018; CC BY-NC-SA 4.0) has 24 actors, 12 male
and 12 female, each saying the same two sentences in eight emotions, twice
each. That gives what the scale needs:

  retake      same actor, same line, same emotion, the other take
  delivery    same actor, same line, a different emotion
  words       same actor, the other sentence
  stranger    another actor of the same sex, same line, any emotion
  same_sex    another actor of the same sex, same line, same emotion
  cross_sex   another actor of the other sex, same line, same emotion

Each curve scores 90 at the median retake and 20 at the median stranger. Each
component's weight in the overall score is how much better than chance it tells
a retake from a stranger (AUC - 0.5). The weighted mean then goes through one
more curve with the same two anchors, so the headline means the same thing.

Articulation is the exception. It compares the sounds, as a speech recogniser
hears them, and a stranger reading the same line makes nearly the same sounds:
anchored at the stranger, the whole scale would sit inside the difference
between two microphones. Its 20 is another line altogether:

  other_line  the same speaker saying a different sentence (VCC2018, which has
              35 sentences from each of 8 speakers; RAVDESS has two sentences
              that share four of their six words)

So Articulation reads how much of the line's sounds were matched: about 90 for
the real person again, lower for a stranger reading it their own way, 20 for
different words. Its weight is still how well it tells a retake from a stranger.

The target is always the clean studio file. The take goes through a simulated
microphone and room (band limit, reverb, noise), because in real use the
target comes from a clip and the take from your own mic.

Resonance reads both recordings of a pair at one formant ceiling: the middle
of the two speakers' ceilings (analysis.shared_ceiling). The app has about
five lines of a voice and a few takes of yours to choose each speaker's from,
so here a recording's speaker ceiling is the middle of its own pick and the
picks of four other recordings of the same actor through the same channel.

    python tools/calibrate.py cohort-mean               # writes speaker_mean.json
    python tools/calibrate.py collect --ravdess cache/ravdess """
    """--vcc cache/vcc2018/vcc2018_evaluation --out cache/pairs.jsonl
    python tools/calibrate.py fit cache/pairs.jsonl     # writes calibration.json

Download: https://zenodo.org/records/1188976 (Audio_Speech_Actors_01-24.zip).
"""
)


def main() -> None:
    """Run the command given on the command line."""
    parser = argparse.ArgumentParser(
        description=HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--ravdess", type=Path, required=True)
    c.add_argument(
        "--vcc",
        type=Path,
        help="VCC2018 evaluation folder, for Articulation's far anchor",
    )
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--workers", type=int, default=8)
    c.add_argument("--seed", type=int, default=0)
    cm = sub.add_parser("cohort-mean", help="write speaker_mean.json from unrelated speakers")
    cm.add_argument("--cache", type=Path, default=Path("cache"))
    cm.add_argument("--workers", type=int, default=8)
    f = sub.add_parser("fit")
    f.add_argument("pairs", type=Path)
    f.add_argument("--dry-run", action="store_true", help="print, don't write calibration.json")
    args = parser.parse_args()
    if args.command == "collect":
        collect(args.ravdess, args.out, args.workers, args.seed, args.vcc)
    elif args.command == "cohort-mean":
        cohort_mean(args.cache, args.workers)
    else:
        fit(args.pairs, not args.dry_run)


if __name__ == "__main__":
    main()
