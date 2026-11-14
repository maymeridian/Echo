"""Speaker embeddings: a fixed-length fingerprint of who is speaking.

Uses WeSpeaker's ResNet34-LM (trained on VoxCeleb) in ONNX form on the CPU.
The model is downloaded on first use and checked against a pinned hash.

The features follow WeSpeaker's own inference: 80-bin Kaldi filterbanks on
int16-scaled samples, then the per-utterance mean subtracted. That last step
matters: without it (sherpa-onnx 2.0.1 skips it) the embedding shifts with
recording level and strangers score nearly as close as the same person.
"""

from __future__ import annotations

import hashlib
import os
import sys
import threading
import urllib.request
from pathlib import Path

import numpy as np

MODEL_NAME = "wespeaker_en_voxceleb_resnet34_LM.onnx"
MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    f"speaker-recongition-models/{MODEL_NAME}"
)
MODEL_SHA256 = "e9848563da86f263117134dfd7ad63c92355b37de492b55e325400c9d9c39012"

_lock = threading.Lock()
_session = None


def cache_dir() -> Path:
    return Path(os.environ.get("VOICEMATCH_CACHE") or Path.home() / ".cache" / "voicematch")


def model_path() -> Path:
    """Path to the model, downloading it the first time."""
    path = cache_dir() / "models" / MODEL_NAME
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    print(f"Downloading the speaker model ({MODEL_NAME}, 26 MB)...", file=sys.stderr, flush=True)
    digest = hashlib.sha256()
    with urllib.request.urlopen(MODEL_URL, timeout=60) as response, open(partial, "wb") as out:
        while chunk := response.read(1 << 20):
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest() != MODEL_SHA256:
        partial.unlink()
        raise RuntimeError(f"The downloaded {MODEL_NAME} doesn't match its expected hash.")
    partial.replace(path)
    return path


def _get_session():
    global _session
    with _lock:
        if _session is None:
            import onnxruntime

            options = onnxruntime.SessionOptions()
            options.intra_op_num_threads = 2
            _session = onnxruntime.InferenceSession(
                str(model_path()), options, providers=["CPUExecutionProvider"]
            )
        return _session


def _fbank(y: np.ndarray, sr: int) -> np.ndarray:
    import kaldi_native_fbank as knf

    opts = knf.FbankOptions()
    opts.frame_opts.samp_freq = sr
    opts.frame_opts.dither = 0.0
    opts.frame_opts.window_type = "hamming"
    opts.mel_opts.num_bins = 80
    fbank = knf.OnlineFbank(opts)
    # One int16 step of dither, as in WeSpeaker's training. Without it, stretches
    # of exact digital silence (noise gates, edits, TTS) sit at the log floor
    # and drag the mean subtraction. Seeded so the same audio always embeds the same.
    samples = np.asarray(y, dtype=np.float64) * 32768
    samples += np.random.default_rng(0).standard_normal(len(samples))
    fbank.accept_waveform(sr, samples.astype(np.float32).tolist())
    fbank.input_finished()
    feats = np.array([fbank.get_frame(i) for i in range(fbank.num_frames_ready)], dtype=np.float32)
    return feats - feats.mean(axis=0)


def embed(y: np.ndarray, sr: int = 16000) -> np.ndarray:
    """Unit-length speaker embedding of a mono recording."""
    e = _get_session().run(None, {"feats": _fbank(y, sr)[None]})[0][0].astype(np.float64)
    return e / np.linalg.norm(e)


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(a @ b)
