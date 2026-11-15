"""Check the measurements against data with known answers.

    python tools/validate.py pitch --ptdb cache/ptdb
        Pitch tracking against PTDB-TUG (Pirker et al., Interspeech 2011):
        laryngograph-derived reference pitch for 10 women and 10 men.
        Download: http://www2.spsc.tugraz.at/databases/PTDB-TUG/ (ODbL).

    python tools/validate.py formants --h95 cache/hillenbrand
        Formant tracking against Hillenbrand et al. (1995, JASA 97:3099):
        hand-checked F1-F3 for 1,668 /hVd/ words by men, women and children.
        Download: https://github.com/santiagobarreda/hillenbrand_et_al_1995

    python tools/validate.py vcc2018 --vcc cache/vcc2018 --out cache/vcc2018/scored.jsonl
        The whole scorer against human judgments: the Voice Conversion
        Challenge 2018 similarity test (Lorenzo-Trueba et al., Odyssey 2018),
        where listeners rated converted speech against the natural target or
        source speaker saying the same sentence, 1 (different, sure) to
        4 (same, sure). Download: https://doi.org/10.7488/ds/2337 and
        https://doi.org/10.7488/ds/2496 (CC BY 4.0).

    python tools/validate.py voxsim-fetch --voxsim cache/voxsim --out cache/voxceleb1
    python tools/validate.py voxsim --voxsim cache/voxsim --audio cache/voxceleb1
        The speaker-identity measures against VoxSim (Ahn et al., Interspeech
        2024, arXiv:2407.18505): human 1-6 similarity ratings of VoxCeleb1
        clip pairs, different sentences, recorded in the wild. Ratings from
        https://mm.kaist.ac.kr/projects/voxsim/ (CC BY 4.0). voxsim-fetch pulls
        only the needed clips out of the VoxCeleb1 zips on the Hugging Face
        mirror (ProgramComputer/voxceleb; accept its terms and log in first).

Each prints the current method next to the alternatives that were considered.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import parselmouth

from voicematch.analysis import SR, load

# ---------- pitch ----------


def _track(snd: parselmouth.Sound, floor: float, ceiling: float, method: str) -> parselmouth.Pitch:
    if method == "ac":
        return snd.to_pitch_ac(time_step=0.01, pitch_floor=floor, pitch_ceiling=ceiling)
    return snd.to_pitch_cc(time_step=0.01, pitch_floor=floor, pitch_ceiling=ceiling)


def _on_grid(pitch: parselmouth.Pitch, times: np.ndarray) -> np.ndarray:
    f0 = pitch.selected_array["frequency"]
    xs = pitch.xs()
    out = np.zeros(len(times))
    idx = np.round((times - xs[0]) / pitch.get_time_step()).astype(int)
    ok = (idx >= 0) & (idx < len(xs))
    out[ok] = f0[idx[ok]]
    return out


PITCH_METHODS = {
    # name: (algorithm, how the range is chosen)
    "ac 60-600 (current)": ("ac", "fixed"),
    "cc 60-600": ("cc", "fixed"),
    "ac two-pass, q35*0.72 .. q65*1.9 (De Looze & Hirst 2008)": ("ac", "dlh"),
    "ac two-pass, q25*0.75 .. q75*1.5 (Hirst 2011)": ("ac", "hirst"),
    "cc two-pass, q25*0.75 .. q75*1.5 (Hirst 2011)": ("cc", "hirst"),
}


def pitch_tracks(y: np.ndarray, times: np.ndarray) -> dict[str, np.ndarray]:
    snd = parselmouth.Sound(y.astype(np.float64), sampling_frequency=SR)
    out = {}
    for name, (method, rng) in PITCH_METHODS.items():
        floor, ceiling = 60.0, 600.0
        if rng != "fixed":
            first = _on_grid(_track(snd, 60.0, 700.0, method), times)
            v = first[first > 0]
            if v.size >= 20:
                if rng == "dlh":
                    floor, ceiling = np.quantile(v, 0.35) * 0.72, np.quantile(v, 0.65) * 1.9
                else:
                    floor, ceiling = np.quantile(v, 0.25) * 0.75, np.quantile(v, 0.75) * 1.5
                floor = max(40.0, floor)
        out[name] = _on_grid(_track(snd, floor, ceiling, method), times)
    return out


def _pitch_job(path: Path):
    ref = np.loadtxt(path.parent / f"ref_{path.stem[4:]}.f0")[:, 0]
    y = load(path)
    times = np.arange(int(len(y) / SR * 100)) / 100.0
    return path.stem, ref, pitch_tracks(y, times)


def validate_pitch(root: Path, workers: int) -> None:
    files = sorted(root.glob("mic_*.wav"))
    with ProcessPoolExecutor(workers) as pool:
        results = list(pool.map(_pitch_job, files))
    # The reference's first frame sits half a 32 ms window in; find the lag
    # (in 10 ms frames) that lines the current tracker up best and use it for all.
    def gpe_at(lag):
        errs = []
        for _, ref, tracks in results:
            est = tracks["ac 60-600 (current)"]
            r = ref[max(0, -lag):][: len(est)]
            e = est[max(0, lag):][: len(r)]
            r = r[: len(e)]
            both = (r > 0) & (e > 0)
            errs.append(np.mean(np.abs(e[both] / r[both] - 1) > 0.2))
        return float(np.mean(errs))

    lag = min(range(-4, 5), key=gpe_at)
    print(f"{len(results)} utterances, reference lag {lag * 10:+d} ms\n")
    rows = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for stem, ref, tracks in results:
        sex = "female" if stem.split("_")[1].startswith("F") else "male"
        for name, est in tracks.items():
            r = ref[max(0, -lag):]
            e = est[max(0, lag):]
            n = min(len(r), len(e))
            r, e = r[:n], e[:n]
            rv, ev = r > 0, e > 0
            both = rv & ev
            ratio = e[both] / r[both]
            gross = np.abs(ratio - 1) > 0.2
            for group in (sex, "all"):
                d = rows[name][group]
                d["frames"].append(n)
                d["gross"].append(gross.sum())
                d["both"].append(both.sum())
                d["vde"].append(np.sum(rv != ev))
                d["cents"].extend(np.abs(1200 * np.log2(ratio[~gross])))
                d["octave_down"].append(np.sum(ratio < 0.6))
                d["octave_up"].append(np.sum(ratio > 1.6))
    print(f"{'method':58s} {'group':7s} {'GPE':>6s} {'VDE':>6s} {'fine err':>9s} {'oct down/up':>12s}")
    for name, groups in rows.items():
        for group in ("female", "male", "all"):
            d = groups[group]
            gpe = sum(d["gross"]) / sum(d["both"])
            vde = sum(d["vde"]) / sum(d["frames"])
            fine = np.median(d["cents"])
            od = sum(d["octave_down"]) / sum(d["both"])
            ou = sum(d["octave_up"]) / sum(d["both"])
            print(f"{name:58s} {group:7s} {gpe:6.2%} {vde:6.2%} {fine:6.1f} c  {od:5.2%}/{ou:5.2%}")
        print()
    print("GPE: share of frames voiced in both with more than 20% error. VDE: share of")
    print("frames whose voiced/unvoiced decision differs. Fine error: median cents on the rest.")


# ---------- formants ----------

FIXED_CEILINGS = (5000, 5500)
CANDIDATES = np.arange(4500, 7501, 250)


def _formant_values(y: np.ndarray, ceiling: float, times: list[float]) -> np.ndarray:
    snd = parselmouth.Sound(y.astype(np.float64), sampling_frequency=SR)
    formant = snd.to_formant_burg(time_step=0.005, max_number_of_formants=5, maximum_formant=ceiling)
    return np.array([[formant.get_value_at_time(k, t) for t in times] for k in (1, 2, 3)])


def roughness(tracks: np.ndarray) -> float:
    """How far F1-F3 stray from a smooth (cubic) path, in semitones. Used to
    pick a ceiling per recording, after FastTrack (Barreda 2021): the wrong
    ceiling makes tracks jump between resonances."""
    total = 0.0
    x = np.linspace(-1, 1, tracks.shape[1])
    for f in tracks:
        if not np.all(np.isfinite(f)):
            return np.inf
        st = 12 * np.log2(f)
        fit = np.polyval(np.polyfit(x, st, 3), x)
        total += float(np.mean(np.abs(st - fit)))
    return total


def _formant_job(item):
    name, start, end, path = item
    y = load(path)
    probe = list(start + (end - start) * np.array([0.2, 0.5, 0.8]))
    dense = list(np.linspace(start, end, 21)[2:-2])  # inner 80% of the vowel
    out = {f"fixed {c}": _formant_values(y, c, probe) for c in FIXED_CEILINGS}
    group_ceiling = {"m": 5000, "w": 5500, "b": 6000, "g": 6000}[name[0]]
    out["by voice type (oracle)"] = _formant_values(y, group_ceiling, probe)
    scores = [(roughness(_formant_values(y, c, dense)), c) for c in CANDIDATES]
    best = min(scores)[1]
    out["smoothest ceiling per recording"] = _formant_values(y, best, probe)
    return name, out, best


def _h95_truth(root: Path):
    truth = {}
    for line in open(root / "vowdata.dat", encoding="latin-1"):
        parts = line.split()
        if len(parts) == 16 and parts[0][:1] in "mwbg" and parts[0][1:3].isdigit():
            v = [float(x) for x in parts[1:]]
            truth[parts[0]] = np.array([[v[6], v[9], v[12]], [v[7], v[10], v[13]], [v[8], v[11], v[14]]])
    times = {}
    for line in open(root / "timedata.dat", encoding="latin-1"):
        parts = line.split()
        if len(parts) >= 3 and parts[0] in truth:
            times[parts[0]] = (float(parts[1]) / 1000, float(parts[2]) / 1000)
    return truth, times


def validate_formants(root: Path, workers: int) -> None:
    truth, times = _h95_truth(root)
    items = [
        (n, *times[n], root / "audio" / f"{n}.wav")
        for n in sorted(truth)
        if n in times and (root / "audio" / f"{n}.wav").exists()
    ]
    with ProcessPoolExecutor(workers) as pool:
        results = list(pool.map(_formant_job, items, chunksize=8))

    group_name = {"m": "men", "w": "women", "b": "children", "g": "children"}
    err = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    chosen = defaultdict(list)
    for name, out, best in results:
        chosen[group_name[name[0]]].append(best)
        ref = truth[name]
        for method, est in out.items():
            for k in range(3):
                ok = (ref[k] > 0) & np.isfinite(est[k])
                rel = np.abs(est[k][ok] / ref[k][ok] - 1)
                for g in (group_name[name[0]], "all"):
                    err[method][g][k].extend(rel)
                missing = np.sum((ref[k] > 0) & ~np.isfinite(est[k]))
                for g in (group_name[name[0]], "all"):
                    err[method][g][f"missing{k}"].append(missing)
    print(f"{len(results)} vowels. Median |error| and share off by more than 15% (gross), per formant.\n")
    print(f"{'method':34s} {'group':9s}" + "".join(f"{'F' + str(k + 1) + ' med/gross':>17s}" for k in range(3)))
    for method, groups in err.items():
        for g in ("men", "women", "children", "all"):
            cells = []
            for k in range(3):
                a = np.array(groups[g][k])
                cells.append(f"{np.median(a):6.1%} / {np.mean(a > 0.15):5.1%}")
            print(f"{method:34s} {g:9s}" + "".join(f"{c:>17s}" for c in cells))
        print()
    for g, c in chosen.items():
        print(f"smoothest ceiling chosen for {g:9s}: median {np.median(c):.0f} Hz, IQR {np.percentile(c, 25):.0f}-{np.percentile(c, 75):.0f}")


# ----- whole lines: one ceiling per recording, chosen without vowel labels -----

LINE_CEILINGS = np.arange(4500, 7001, 250)


def _runs(voiced: np.ndarray) -> list[np.ndarray]:
    """Index arrays of consecutive voiced frames."""
    idx = np.flatnonzero(voiced)
    if idx.size == 0:
        return []
    return [r for r in np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1) if r.size >= 5]


def _line_tracks(snd: parselmouth.Sound, times: np.ndarray, ceiling: float) -> np.ndarray:
    formant = snd.to_formant_burg(time_step=0.01, max_number_of_formants=5, maximum_formant=ceiling)
    return np.array([[formant.get_value_at_time(k, float(t)) for t in times] for k in (1, 2, 3)])


def _rough_jumps(st: np.ndarray, runs) -> float:
    d = [np.abs(np.diff(st[:, r], axis=1)) for r in runs]
    return float(np.nanmean(np.concatenate(d, axis=1))) if d else np.inf


def _rough_median(st: np.ndarray, runs) -> float:
    from scipy.ndimage import median_filter

    res = []
    for r in runs:
        seg = st[:, r]
        res.append(np.abs(seg - median_filter(seg, size=(1, 5), mode="nearest")))
    return float(np.nanmean(np.concatenate(res, axis=1))) if res else np.inf


def _rough_chunks(st: np.ndarray, runs) -> float:
    res = []
    for r in runs:
        for chunk in np.array_split(r, max(1, len(r) // 10)):
            if len(chunk) < 5:
                continue
            x = np.linspace(-1, 1, len(chunk))
            seg = st[:, chunk]
            if not np.all(np.isfinite(seg)):
                res.append(np.full(3, 3.0))  # an undefined formant mid-vowel counts as a bad fit
                continue
            fit = np.array([np.polyval(np.polyfit(x, f, 2), x) for f in seg])
            res.append(np.abs(seg - fit).mean(axis=1))
    return float(np.mean(res)) if res else np.inf


CRITERIA = {"frame-to-frame jumps": _rough_jumps, "distance from running median": _rough_median,
            "quadratic fit per 100 ms": _rough_chunks}


def _line_job(item):
    speaker, parts = item
    gap = np.zeros(int(0.15 * SR), dtype=np.float32)
    pieces, probes, offset = [], [], 0.0
    for name, start, end, path in parts:
        y = load(path)
        pieces += [y, gap]
        probes.append((name, offset + start + (end - start) * np.array([0.2, 0.5, 0.8])))
        offset += (len(y) + len(gap)) / SR
    y = np.concatenate(pieces)
    y = 0.9 * y / np.max(np.abs(y))
    snd = parselmouth.Sound(y.astype(np.float64), sampling_frequency=SR)
    times = np.arange(int(len(y) / SR * 100)) / 100.0
    pitch = _on_grid(_track(snd, 60.0, 600.0, "ac"), times)
    runs = _runs(pitch > 0)
    st = {c: 12 * np.log2(_line_tracks(snd, times, c)) for c in LINE_CEILINGS}
    chosen = {"fixed 5500 (current)": 5500.0}
    for label, fn in CRITERIA.items():
        chosen[label] = float(min(LINE_CEILINGS, key=lambda c: fn(st[c], runs)))
    from voicematch.analysis import formant_tracks

    f0 = np.where(pitch > 0, pitch, np.nan)
    chosen["voicematch.analysis (production)"] = formant_tracks(snd, times, f0)[1]
    out = {}
    for label, ceiling in chosen.items():
        formant = snd.to_formant_burg(time_step=0.005, max_number_of_formants=5, maximum_formant=ceiling)
        out[label] = {name: np.array([[formant.get_value_at_time(k, float(t)) for t in ts] for k in (1, 2, 3)])
                      for name, ts in probes}
    return speaker, out, chosen


def validate_formant_lines(root: Path, workers: int) -> None:
    truth, times = _h95_truth(root)
    by_speaker = defaultdict(list)
    for n in sorted(truth):
        path = root / "audio" / f"{n}.wav"
        if n in times and path.exists():
            by_speaker[n[:3]].append((n, *times[n], path))
    with ProcessPoolExecutor(workers) as pool:
        results = list(pool.map(_line_job, by_speaker.items()))
    group_name = {"m": "men", "w": "women", "b": "children", "g": "children"}
    err = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    picks = defaultdict(lambda: defaultdict(list))
    for speaker, out, chosen in results:
        g = group_name[speaker[0]]
        for label, c in chosen.items():
            picks[label][g].append(c)
        for label, est in out.items():
            for name, values in est.items():
                ref = truth[name]
                for k in range(3):
                    ok = ref[k] > 0
                    rel = np.abs(values[k][ok] / ref[k][ok] - 1)
                    rel[~np.isfinite(rel)] = 1.0  # no value where the vowel has one: count as gross
                    for gg in (g, "all"):
                        err[label][gg][k].extend(rel)
    print(f"{len(results)} speakers, each speaker's 12 words joined into one recording;")
    print("one ceiling chosen per recording from its voiced frames, no vowel labels.\n")
    print(f"{'method':32s} {'group':9s}" + "".join(f"{'F' + str(k + 1) + ' med/gross':>17s}" for k in range(3))
          + "   ceiling chosen (median)")
    for label, groups in err.items():
        for g in ("men", "women", "children", "all"):
            cells = []
            for k in range(3):
                a = np.array(groups[g][k])
                cells.append(f"{np.median(a):6.1%} / {np.mean(a > 0.15):5.1%}")
            pick = np.median(picks[label][g]) if g != "all" else np.median(sum(picks[label].values(), []))
            print(f"{label:32s} {g:9s}" + "".join(f"{c:>17s}" for c in cells) + f"   {pick:.0f} Hz")
        print()


# ---------- human similarity judgments (VCC2018) ----------


def _vcc_path(root: Path, name: str) -> Path:
    system, target, source, sentence, _ = name[:-4].split("_")
    if system == "S00":
        return root / "vcc2018_evaluation" / source / f"{sentence}.wav"
    if system == "T00":
        return root / "vcc2018_reference" / target / f"{sentence}.wav"
    return root / "converted" / name


def vcc_pairs(root: Path) -> list[dict]:
    """Unique rated pairs with the mean listener score. The natural recording is
    the target; the other side is the take."""
    import csv

    ratings = defaultdict(list)
    path = root / "vcc2018_listening_test_scores" / "vcc2018_evaluation_sim_simple.txt"
    with open(path, encoding="utf-8") as f:
        for a, b, _, score in list(csv.reader(f))[1:]:
            if b.split("_")[0] in ("S00", "T00") and a.split("_")[0] not in ("S00", "T00"):
                a, b = b, a  # natural first
            ratings[(a, b)].append(int(score))
    pairs = []
    for (a, b), scores in ratings.items():
        pa, pb = _vcc_path(root, a), _vcc_path(root, b)
        if pa == pb or not pa.exists() or not pb.exists():
            continue
        system = b.split("_")[0]
        kind = {"S00": "natural", "T00": "natural"}.get(system, "converted")
        against = "source" if a.startswith("S00") else "target"
        pairs.append({
            "target": str(pa), "take": str(pb), "rating": float(np.mean(scores)), "n": len(scores),
            "system": system, "kind": kind, "against": against, "task": b[:-4].split("_")[-1],
        })
    return pairs


def _vcc_job(group):
    from voicematch.analysis import AnalysisError, analyze
    from voicematch.scoring import compare

    target_path, pairs = group
    try:
        target = analyze(load(target_path))
    except AnalysisError:
        return []
    rows = []
    for p in pairs:
        try:
            r = compare(target, analyze(load(p["take"])))
        except AnalysisError:
            continue
        p = dict(p, overall=r["overall"], identity=r["identity"])
        p.update({f"score_{c['key']}": c["score"] for c in r["components"]})
        rows.append(p)
    return rows


def score_vcc(root: Path, out: Path, workers: int) -> None:
    import json

    groups = defaultdict(list)
    for p in vcc_pairs(root):
        groups[p["target"]].append(p)
    print(f"scoring {sum(len(g) for g in groups.values())} pairs against {len(groups)} natural recordings...",
          file=sys.stderr)
    done = 0
    with ProcessPoolExecutor(workers) as pool, open(out, "w", encoding="utf-8") as f:
        for rows in pool.map(_vcc_job, groups.items()):
            for r in rows:
                f.write(json.dumps(r) + "\n")
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(groups)}", file=sys.stderr)
    report_vcc(out)


def report_vcc(path: Path, extra: dict[str, dict] | None = None) -> None:
    """Correlations with listeners, per pair and per system. `extra` maps a
    label to {(target, take): value} for alternative measures."""
    import json

    from scipy.stats import pearsonr, spearmanr

    rows = [json.loads(line) for line in open(path, encoding="utf-8")]
    measures = ["overall", "identity"] + sorted(k for k in rows[0] if k.startswith("score_"))
    values = {m: [r[m] for r in rows] for m in measures}
    for label, table in (extra or {}).items():
        measures.append(label)
        values[label] = [table.get((r["target"], r["take"])) for r in rows]

    def corr(idx, m):
        x = np.array([values[m][i] for i in idx], dtype=float)
        y = np.array([rows[i]["rating"] for i in idx])
        ok = np.isfinite(x)
        if ok.sum() < 5:
            return float("nan"), float("nan")
        return spearmanr(x[ok], y[ok])[0], pearsonr(x[ok], y[ok])[0]

    def system_corr(idx, m):
        groups = defaultdict(list)
        for i in idx:
            r = rows[i]
            groups[(r["system"], r["against"], r["task"])].append(i)
        xs, ys = [], []
        for g in groups.values():
            x = np.array([values[m][i] for i in g], dtype=float)
            if np.isfinite(x).sum() == 0:
                continue
            w = np.array([rows[i]["n"] for i in g], dtype=float)
            xs.append(np.nanmean(x))
            ys.append(np.average([rows[i]["rating"] for i in g], weights=w))
        return spearmanr(xs, ys)[0], pearsonr(xs, ys)[0], len(xs)

    conv = [i for i, r in enumerate(rows) if r["kind"] == "converted"]
    conv_t = [i for i in conv if rows[i]["against"] == "target"]
    print(f"{len(rows)} pairs ({len(conv)} converted, {len(rows) - len(conv)} natural).")
    print("Spearman / Pearson with the mean listener rating (1 = different, sure ... 4 = same, sure).\n")
    head = f"{'measure':26s}{'all pairs':>15s}{'converted':>15s}{'conv vs target':>16s}{'per system':>16s}"
    print(head)
    for m in measures:
        cells = [corr(range(len(rows)), m), corr(conv, m), corr(conv_t, m)]
        sr, pr, n = system_corr(conv, m)
        print(f"{m:26s}" + "".join(f"{a:7.3f}/{b:6.3f} " for a, b in cells) + f"  {sr:6.3f}/{pr:6.3f}")
    print(f"\nper system: {system_corr(conv, 'overall')[2]} groups (system x compared-with x task), "
          "means weighted by number of ratings")


# ----- other speaker models, and low-dimensional projections -----

SPEAKER_MODELS = {
    "resnet34-LM (current)": ("wespeaker_en_voxceleb_resnet34_LM.onnx", "wespeaker"),
    "resnet293-LM": ("wespeaker_en_voxceleb_resnet293_LM.onnx", "wespeaker"),
    "wespeaker CAM++-LM": ("wespeaker_en_voxceleb_CAM++_LM.onnx", "wespeaker"),
    "3d-speaker ERes2Net": ("3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx", "3dspeaker"),
    "3d-speaker CAM++": ("3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx", "3dspeaker"),
}
_sessions: dict = {}


def embed_with(model: str, y: np.ndarray) -> np.ndarray:
    """Embedding with each framework's own front end: WeSpeaker uses int16-scale
    samples and a Hamming window, 3D-Speaker [-1, 1] samples and Kaldi's default
    window; both subtract the utterance mean from 80-bin filterbanks."""
    import kaldi_native_fbank as knf
    import onnxruntime

    from voicematch.identity import cache_dir

    filename, framework = SPEAKER_MODELS[model]
    if model not in _sessions:
        opts = onnxruntime.SessionOptions()
        opts.intra_op_num_threads = 1
        _sessions[model] = onnxruntime.InferenceSession(str(cache_dir() / "models" / filename), opts,
                                                        providers=["CPUExecutionProvider"])
    sess = _sessions[model]
    o = knf.FbankOptions()
    o.frame_opts.samp_freq = SR
    o.frame_opts.dither = 0.0
    o.mel_opts.num_bins = 80
    if framework == "wespeaker":
        o.frame_opts.window_type = "hamming"
    samples = y.astype(np.float64) * 32768 + np.random.default_rng(0).standard_normal(len(y))
    if framework == "3dspeaker":
        samples /= 32768
    fb = knf.OnlineFbank(o)
    fb.accept_waveform(SR, samples.astype(np.float32).tolist())
    fb.input_finished()
    feats = np.array([fb.get_frame(i) for i in range(fb.num_frames_ready)], dtype=np.float32)
    feats -= feats.mean(axis=0)
    e = sess.run(None, {sess.get_inputs()[0].name: feats[None]})[0][0].astype(np.float64)
    return e / np.linalg.norm(e)


def _embed_job(path: str):
    from voicematch.analysis import AnalysisError, _prepare

    try:
        y = _prepare(load(path))
    except AnalysisError:
        return path, None
    return path, {m: embed_with(m, y).astype(np.float32) for m in SPEAKER_MODELS}


def _embed_files(paths: list[str], cache: Path, workers: int) -> dict[str, dict[str, np.ndarray]]:
    import pickle

    done = pickle.loads(cache.read_bytes()) if cache.exists() else {}
    todo = [p for p in paths if p not in done]
    if todo:
        print(f"embedding {len(todo)} files with {len(SPEAKER_MODELS)} models...", file=sys.stderr)
        with ProcessPoolExecutor(workers) as pool:
            for i, (path, e) in enumerate(pool.map(_embed_job, todo, chunksize=16), 1):
                done[path] = e
                if i % 2000 == 0:
                    print(f"  {i}/{len(todo)}", file=sys.stderr)
                    cache.write_bytes(pickle.dumps(done))
        cache.write_bytes(pickle.dumps(done))
    return done


def vcc_models(root: Path, scored: Path, workers: int) -> None:
    import json

    rows = [json.loads(line) for line in open(scored, encoding="utf-8")]
    files = sorted({r["target"] for r in rows} | {r["take"] for r in rows})
    from calibrate import cohort_files

    emb = _embed_files(files, root / "embeddings.pkl", workers)
    cohort = cohort_files(Path(__file__).parents[1] / "cache")
    cemb = _embed_files(sorted({f for fs in cohort.values() for f in fs}), root / "cohort_embeddings.pkl", workers)

    extra = {}
    for model in SPEAKER_MODELS:
        def cos(a, b, f=lambda v: v, model=model):
            ea, eb = emb.get(a), emb.get(b)
            if ea is None or eb is None:
                return None
            x, y = f(ea[model]), f(eb[model])
            return float(x @ y / np.linalg.norm(x) / np.linalg.norm(y))

        extra[f"cos {model}"] = {(r["target"], r["take"]): cos(r["target"], r["take"]) for r in rows}
        cents = np.array([np.mean([cemb[f][model] for f in fs if cemb.get(f) is not None], axis=0)
                          for fs in cohort.values()])
        mu = cents.mean(axis=0)
        _, _, vt = np.linalg.svd(cents - mu, full_matrices=False)
        extra[f"  mean-subtracted {model}"] = {
            (r["target"], r["take"]): cos(r["target"], r["take"], lambda v, mu=mu: v - mu) for r in rows}
        for k in (3, 10, 30):
            basis = vt[:k]
            extra[f"  top-{k} PCs {model}"] = {
                (r["target"], r["take"]): cos(r["target"], r["take"], lambda v, b=basis, mu=mu: b @ (v - mu)) for r in rows}
    print(f"cohort: {len(cohort)} speakers\n")
    report_vcc(scored, extra)


# ---------- VoxSim (human ratings of in-the-wild pairs) ----------

VOX_ZIPS = {
    "dev": "datasets/ProgramComputer/voxceleb/vox1/vox1_dev_wav.zip",
    "test": "datasets/ProgramComputer/voxceleb/vox1/vox1_test_wav.zip",
}


def voxsim_pairs(root: Path, split: str) -> list[dict]:
    import csv

    name = "voxsim_test_list.txt" if split == "test" else "voxsim_train_list_average.txt"
    with open(root / name, encoding="utf-8") as f:
        return [{"a": r[0], "b": r[1], "same": r[2] == "1", "rating": float(r[4])} for r in csv.reader(f)]


def _fetch_member(fs, zpath: str, info, out: Path) -> bool:
    import struct
    import zlib

    head = fs.cat_file(zpath, start=info.header_offset, end=info.header_offset + 30)
    name_len, extra_len = struct.unpack("<HH", head[26:30])
    start = info.header_offset + 30 + name_len + extra_len
    data = fs.cat_file(zpath, start=start, end=start + info.compress_size)
    if info.compress_type == 8:
        data = zlib.decompress(data, -15)
    elif info.compress_type != 0:
        raise ValueError(f"unsupported compression {info.compress_type}")
    if zlib.crc32(data) & 0xFFFFFFFF != info.CRC:
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return True


def voxsim_fetch(root: Path, out: Path, train_pairs: int, workers: int) -> None:
    import random
    import zipfile
    from concurrent.futures import ThreadPoolExecutor

    from huggingface_hub import HfFileSystem

    pairs = voxsim_pairs(root, "test")
    if train_pairs:
        pairs += random.Random(0).sample(voxsim_pairs(root, "train"), train_pairs)
    need = {c for p in pairs for c in (p["a"], p["b"])}
    todo = {c for c in need if not (out / c).exists()}
    print(f"{len(need)} clips needed, {len(todo)} to fetch", file=sys.stderr)
    fs = HfFileSystem()
    for split, zpath in VOX_ZIPS.items():
        with fs.open(zpath, "rb", block_size=16 * 2**20) as f:
            index = {i.filename.split("wav/", 1)[-1]: i for i in zipfile.ZipFile(f).infolist()}
        jobs = [(c, index[c]) for c in sorted(todo) if c in index]
        print(f"{split}: {len(index)} files in the zip, fetching {len(jobs)}", file=sys.stderr)
        bad = 0
        with ThreadPoolExecutor(workers) as pool:
            futures = [pool.submit(_fetch_member, fs, zpath, info, out / c) for c, info in jobs]
            for i, fut in enumerate(futures, 1):
                bad += not fut.result()
                if i % 1000 == 0:
                    print(f"  {i}/{len(jobs)}", file=sys.stderr)
        print(f"{split}: {len(jobs) - bad} written, {bad} failed the CRC check", file=sys.stderr)
    missing = [c for c in need if not (out / c).exists()]
    print(f"done; {len(missing)} clips still missing", file=sys.stderr)


def _voxsim_features(path: str):
    """Word-independent features of one clip: speaker embeddings, median pitch
    and median formants (as the app measures them)."""
    from voicematch import identity
    from voicematch.analysis import MAX_SECONDS, AnalysisError, _prepare, analyze

    y = load(path)[: int((MAX_SECONDS - 1) * SR)]
    try:
        v = analyze(y)
    except AnalysisError:
        return path, None
    yp = _prepare(y)
    formants = np.nanmedian(v.formants[:, v.voiced], axis=1) if v.voiced.sum() >= 20 else np.full(3, np.nan)
    return path, {
        "emb": v.embedding,
        "raw": identity.raw_embed(yp),
        "cam": embed_with("wespeaker CAM++-LM", yp),
        "eres": embed_with("3d-speaker ERes2Net", yp),
        "f0": v.median_f0,
        "formants": formants,
    }


def voxsim_eval(root: Path, audio: Path, workers: int) -> None:
    import pickle
    import random

    from scipy.stats import pearsonr, spearmanr

    from voicematch.scoring import CALIBRATION, curve

    sets = {"VoxSim test": voxsim_pairs(root, "test"),
            "VoxSim train sample": random.Random(0).sample(voxsim_pairs(root, "train"), 3000)}
    clips = sorted({c for ps in sets.values() for p in ps for c in (p["a"], p["b"]) if (audio / c).exists()})
    cache = audio / "features.pkl"
    feats = pickle.loads(cache.read_bytes()) if cache.exists() else {}
    todo = [str(audio / c) for c in clips if str(audio / c) not in feats]
    if todo:
        print(f"analysing {len(todo)} clips...", file=sys.stderr)
        with ProcessPoolExecutor(workers) as pool:
            for i, (path, f) in enumerate(pool.map(_voxsim_features, todo, chunksize=8), 1):
                feats[path] = f
                if i % 2000 == 0:
                    print(f"  {i}/{len(todo)}", file=sys.stderr)
                    cache.write_bytes(pickle.dumps(feats))
        cache.write_bytes(pickle.dumps(feats))

    def cosine(a, b):
        return float(a @ b / np.linalg.norm(a) / np.linalg.norm(b))

    curves = CALIBRATION["curves"]
    w = CALIBRATION["weights"]

    from types import SimpleNamespace

    from voicematch import similarity

    def measures(fa, fb):
        def ns(f):
            return SimpleNamespace(embedding=f["emb"], median_f0=f["f0"], median_formants=f["formants"])

        listener = similarity.predict(similarity.features(ns(fa), ns(fb)))
        out = {
            "Voice: listener model (current)": listener,
            "  embedding similarity alone": cosine(fa["emb"], fb["emb"]),
            "  ...without mean subtraction": cosine(fa["raw"], fb["raw"]),
            "  ...average of 3 models": (cosine(fa["emb"], fb["emb"]) + cosine(fa["cam"], fb["cam"])
                                         + cosine(fa["eres"], fb["eres"])) / 3,
        }
        pitch = None
        if fa["f0"] and fb["f0"]:
            pitch = abs(12 * np.log2(fa["f0"] / fb["f0"]))
            out["Pitch (median, semitones apart)"] = -pitch
        res = None
        if np.all(np.isfinite(fa["formants"])) and np.all(np.isfinite(fb["formants"])):
            res = float(np.mean(np.abs(12 * np.log2(fa["formants"] / fb["formants"]))))
            out["Resonance (median formants apart)"] = -res
        parts = {"identity": curve(6 - listener, **curves["identity"])}
        if pitch is not None:
            parts["pitch"] = curve(pitch, **curves["pitch"])
        if res is not None:
            parts["resonance"] = curve(res, **curves["resonance"])
        out["Voice + Pitch + Resonance (app weights)"] = (
            sum(w[k] * v for k, v in parts.items()) / sum(w[k] for k in parts))
        return out

    for label, pairs in sets.items():
        rows = []
        for p in pairs:
            fa, fb = feats.get(str(audio / p["a"])), feats.get(str(audio / p["b"]))
            if fa and fb:
                rows.append((p, measures(fa, fb)))
        print(f"\n{label}: {len(rows)} pairs ({sum(p['same'] for p, _ in rows)} same speaker), "
              "human ratings 1-6. Pearson / Spearman.")
        print(f"{'measure':42s}{'all pairs':>17s}{'different speakers':>20s}{'same speaker':>17s}")
        names = list(dict.fromkeys(k for _, m in rows for k in m))
        for name in names:
            cells = []
            for sel in (lambda p: True, lambda p: not p["same"], lambda p: p["same"]):
                xs = [(m[name], p["rating"]) for p, m in rows if sel(p) and name in m]
                x, y = np.array(xs).T
                cells.append(f"{pearsonr(x, y)[0]:6.3f} / {spearmanr(x, y)[0]:6.3f}")
            print(f"{name:42s}" + "".join(f"{c:>18s}" for c in cells))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("pitch")
    p.add_argument("--ptdb", type=Path, required=True)
    f = sub.add_parser("formants")
    f.add_argument("--h95", type=Path, required=True)
    fl = sub.add_parser("formant-lines")
    fl.add_argument("--h95", type=Path, required=True)
    v = sub.add_parser("vcc2018")
    v.add_argument("--vcc", type=Path, required=True)
    v.add_argument("--out", type=Path, required=True)
    v.add_argument("--report-only", action="store_true")
    v.add_argument("--models", action="store_true", help="compare speaker models and projections")
    vf = sub.add_parser("voxsim-fetch")
    vf.add_argument("--voxsim", type=Path, required=True)
    vf.add_argument("--out", type=Path, required=True)
    vf.add_argument("--train-pairs", type=int, default=0, help="also fetch this many random training pairs")
    ve = sub.add_parser("voxsim")
    ve.add_argument("--voxsim", type=Path, required=True)
    ve.add_argument("--audio", type=Path, required=True)
    for s in (p, f, fl, v, vf, ve):
        s.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    if args.command == "pitch":
        validate_pitch(args.ptdb, args.workers)
    elif args.command == "formants":
        validate_formants(args.h95, args.workers)
    elif args.command == "formant-lines":
        validate_formant_lines(args.h95, args.workers)
    elif args.command == "voxsim":
        voxsim_eval(args.voxsim, args.audio, args.workers)
    elif args.command == "voxsim-fetch":
        voxsim_fetch(args.voxsim, args.out, args.train_pairs, args.workers)
    elif args.models:
        vcc_models(args.vcc, args.out, args.workers)
    elif args.report_only:
        report_vcc(args.out)
    else:
        score_vcc(args.vcc, args.out, args.workers)


if __name__ == "__main__":
    sys.exit(main())
