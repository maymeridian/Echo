'''
src/echo_voice/identity.py
Author: maymeridian
Description: Speaker embeddings: a fixed-length fingerprint of who is speaking.
             - Model: WeSpeaker's ResNet34-LM (trained on VoxCeleb) in ONNX form on the CPU,
               downloaded on first use and checked against a pinned hash.
             - The features follow WeSpeaker's own inference: 80-bin Kaldi filterbanks on
               int16-scaled samples, then the per-utterance mean subtracted. That last step
               matters: without it (sherpa-onnx 2.0.1 skips it) the embedding shifts with
               recording level and strangers score nearly as close as the same person.
'''

from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np

from .models import fetch

# --- THE MODEL ---

MODEL_NAME = "wespeaker_en_voxceleb_resnet34_LM.onnx"
MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    f"speaker-recongition-models/{MODEL_NAME}"
)
MODEL_SHA256 = "e9848563da86f263117134dfd7ad63c92355b37de492b55e325400c9d9c39012"

# The session, loaded on first use.
_lock = threading.Lock()
_session = None


def model_path() -> Path:
    """Path to the model, downloading it the first time."""
    return fetch(MODEL_NAME, MODEL_URL, MODEL_SHA256, "the speaker model, 26 MB")


def _get_session():
    """The model's ONNX session on the CPU (two threads), loaded once."""
    global _session
    with _lock:
        if _session is None:
            import onnxruntime

            options = onnxruntime.SessionOptions()
            options.intra_op_num_threads = 2
            _session = onnxruntime.InferenceSession(
                str(model_path()),
                options,
                providers=["CPUExecutionProvider"],
            )
        return _session


# --- EMBEDDINGS ---


def _fbank(y: np.ndarray, sr: int) -> np.ndarray:
    """The model's input: 80-bin Kaldi filterbanks (Hamming window, no dither of
    their own), less their mean over the recording."""
    import kaldi_native_fbank as knf

    # 1. The filterbank settings
    opts = knf.FbankOptions()
    opts.frame_opts.samp_freq = sr
    opts.frame_opts.dither = 0.0
    opts.frame_opts.window_type = "hamming"
    opts.mel_opts.num_bins = 80
    fbank = knf.OnlineFbank(opts)

    # 2. The samples on the int16 scale, with one int16 step of dither, as in
    #    WeSpeaker's training. Without it, stretches of exact digital silence (noise
    #    gates, edits, TTS) sit at the log floor and drag the mean subtraction. Seeded
    #    so the same audio always embeds the same.
    samples = np.asarray(y, dtype=np.float64) * 32768
    samples += np.random.default_rng(0).standard_normal(len(samples))
    fbank.accept_waveform(sr, samples.astype(np.float32).tolist())
    fbank.input_finished()

    # 3. The frames, less their mean
    feats = np.array([fbank.get_frame(i) for i in range(fbank.num_frames_ready)], dtype=np.float32)
    return feats - feats.mean(axis=0)


def raw_embed(y: np.ndarray, sr: int = 16000) -> np.ndarray:
    """Unit-length model output, before the cohort mean is taken off."""
    e = _get_session().run(None, {"feats": _fbank(y, sr)[None]})[0][0].astype(np.float64)
    return e / np.linalg.norm(e)


def _cohort_mean() -> np.ndarray | None:
    """The mean embedding of the cohort (speaker_mean.json), or None when the file
    is missing or was made with another model."""
    path = Path(__file__).parent / "speaker_mean.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text("utf-8"))
    if data.get("model") != MODEL_NAME:
        return None
    return np.asarray(data["mean"], dtype=np.float64)


_MEAN = _cohort_mean()


def embed(y: np.ndarray, sr: int = 16000) -> np.ndarray:
    """Unit-length speaker embedding of a mono recording.

    The mean embedding of 137 unrelated speakers is subtracted first, as in
    WeSpeaker's own scoring; what every voice has in common then stops
    counting as similarity. Against the VCC2018 listening tests this raised
    the rank correlation with listeners by 0.009 (bootstrap 95% CI 0.007 to
    0.010; tools/validate.py vcc2018 --models).
    """
    e = raw_embed(y, sr)
    if _MEAN is not None:
        e = e - _MEAN
    return e / np.linalg.norm(e)


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two unit-length embeddings, -1..1."""
    return float(a @ b)
