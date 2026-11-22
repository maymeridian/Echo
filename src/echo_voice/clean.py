'''
src/echo_voice/clean.py
Author: maymeridian
Description: Separates the voice from music and background in a target clip.
             - Model: UVR-MDX-NET-Voc_FT (an MDX-Net vocal model from Ultimate Vocal Remover)
               in ONNX form through sherpa-onnx, on the CPU. It runs at 44.1 kHz stereo, so
               the 16 kHz mono clip is resampled up and back.
             - Checked with `tools/validate.py cleaning`: 48 clean sentences were mixed with
               music. Uncleaned, music as loud as the voice dropped a perfect match to a median
               of 51, with pitch 1.9 semitones off. After separation the median was back to
               100, with pitch 0.07 semitones off.
             - On clean speech it changes almost nothing (SI-SDR 37 dB), so every target is
               cleaned.
'''

from __future__ import annotations

import os
import threading

import librosa
import numpy as np

from .models import fetch

# The vocal separation model (UVR-MDX-NET Voc_FT, run by sherpa-onnx), where it is
# published, and its SHA-256, checked before it is used (models.fetch).
MODEL_NAME = "UVR-MDX-NET-Voc_FT.onnx"
MODEL_URL = (
    f"https://github.com/k2-fsa/sherpa-onnx/releases/download/source-separation-models/{MODEL_NAME}"
)
MODEL_SHA256 = "e411182ce2c53541fefdcc99a8f46f2fe03978eb22038b9497c1d9d95a00fad4"
MODEL_RATE = 44100  # Hz: the rate the model runs at

# The separator, loaded on first use. One clip goes through it at a time.
_lock = threading.Lock()
_separator = None


def _get_separator():
    """The separation model, downloaded the first time and loaded once."""
    global _separator
    with _lock:
        if _separator is None:
            import sherpa_onnx as so

            path = fetch(MODEL_NAME, MODEL_URL, MODEL_SHA256, "the vocal separation model, 67 MB")
            threads = max(1, min(4, (os.cpu_count() or 2) // 2))  # half the cores, 1 to 4
            config = so.OfflineSourceSeparationConfig(
                model=so.OfflineSourceSeparationModelConfig(
                    uvr=so.OfflineSourceSeparationUvrModelConfig(model=str(path)),
                    num_threads=threads,
                )
            )
            _separator = so.OfflineSourceSeparation(config)
        return _separator


def vocals(y: np.ndarray, sr: int = 16000) -> np.ndarray:
    """The voice from a mono recording, same rate and length."""
    # 1. Up to the model's rate, the same signal on both channels
    up = librosa.resample(np.asarray(y, dtype=np.float32), orig_sr=sr, target_sr=MODEL_RATE)
    separator = _get_separator()

    # 2. Separate; stem 0 is the vocals
    with _lock:
        out = separator.process(sample_rate=MODEL_RATE, samples=np.stack([up, up]))
    voice = np.asarray(out.stems[0].data).mean(axis=0)

    # 3. Back to the clip's rate and length
    voice = librosa.resample(voice, orig_sr=out.sample_rate, target_sr=sr)
    voice = np.pad(voice, (0, max(0, len(y) - len(voice))))[: len(y)]
    return voice.astype(np.float32)
