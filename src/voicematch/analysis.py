"""Turn a recording into the per-frame features the scorer compares.

Everything is sampled on one 10 ms frame grid so pitch, formants and MFCCs can
be indexed with the same frame numbers after time alignment.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import librosa
import numpy as np
import parselmouth
import soundfile as sf

from . import identity

SR = 16000
HOP = 160  # 10 ms
N_FFT = 400  # 25 ms
MAX_SECONDS = 30.0
MIN_SECONDS = 0.3
MIN_VOICED_FRAMES = 20  # 0.2 s of voiced sound before pitch is trusted

PITCH_FLOOR = 60.0
PITCH_CEILING = 600.0
# One ceiling for every voice so a target and a take are always measured the
# same way; 5500 Hz is Praat's setting for five formants in a shorter vocal tract.
FORMANT_CEILING = 5500.0


class AnalysisError(ValueError):
    """The audio can't be scored; the message is safe to show the user."""


@dataclass
class Voice:
    y: np.ndarray  # trimmed, peak-normalised mono at SR
    times: np.ndarray  # frame centres in seconds
    f0: np.ndarray  # Hz per frame, NaN where unvoiced
    formants: np.ndarray  # (3, frames) F1-F3 in Hz, NaN where unvoiced
    mfcc: np.ndarray  # (12, frames), mean-normalised, energy dropped

    @property
    def duration(self) -> float:
        return len(self.y) / SR

    @property
    def voiced(self) -> np.ndarray:
        return np.isfinite(self.f0)

    @cached_property
    def embedding(self) -> np.ndarray:
        """Speaker fingerprint; computed on first use because it loads a model."""
        return identity.embed(self.y, SR)

    @property
    def spoken(self) -> slice:
        """Frames from the first to the last voiced sound, plus 50 ms either side
        for unvoiced consonants. Leaves out breaths, sighs and clicks at the
        edges, which differ from take to take but aren't part of the line."""
        idx = np.flatnonzero(self.voiced)
        if idx.size < MIN_VOICED_FRAMES:
            return slice(0, len(self.times))
        return slice(max(0, idx[0] - 5), min(len(self.times), idx[-1] + 6))

    @property
    def spoken_duration(self) -> float:
        span = self.spoken
        return (span.stop - span.start) * HOP / SR

    @property
    def median_f0(self) -> float | None:
        """Typical pitch in Hz, or None when there is too little voiced sound."""
        if self.voiced.sum() < MIN_VOICED_FRAMES:
            return None
        return float(np.nanmedian(self.f0))


def load(path: str | Path) -> np.ndarray:
    """Read any audio file libsndfile understands as mono at SR."""
    try:
        y, _ = librosa.load(str(path), sr=SR, mono=True)
    except Exception as exc:  # librosa surfaces several backend error types
        raise AnalysisError(f"Couldn't read {path}: {exc}") from exc
    return y


def decode(data: bytes) -> np.ndarray:
    """Decode an in-memory audio file (the browser sends WAV) to mono at SR."""
    try:
        y, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except Exception as exc:
        raise AnalysisError("Couldn't read that audio.") from exc
    y = y.mean(axis=1)
    if sr != SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    return y


def analyze(y: np.ndarray) -> Voice:
    y = _prepare(y)
    mfcc = librosa.feature.mfcc(
        y=y, sr=SR, n_mfcc=13, n_fft=N_FFT, hop_length=HOP, n_mels=40
    )[1:]
    # Removing each coefficient's mean takes out most of the microphone and
    # room colouring, which would otherwise swamp a clip-vs-live comparison.
    mfcc = mfcc - mfcc.mean(axis=1, keepdims=True)
    times = librosa.frames_to_time(np.arange(mfcc.shape[1]), sr=SR, hop_length=HOP)

    snd = parselmouth.Sound(y.astype(np.float64), sampling_frequency=SR)
    f0 = _pitch(snd, times)
    return Voice(y=y, times=times, f0=f0, formants=_formants(snd, times, f0), mfcc=mfcc)


def _prepare(y: np.ndarray) -> np.ndarray:
    y = np.asarray(y, dtype=np.float32)
    if y.size == 0 or float(np.max(np.abs(y))) < 1e-4:
        raise AnalysisError("That recording is silent — check the microphone.")
    y = _trim(y)
    seconds = len(y) / SR
    if seconds < MIN_SECONDS:
        raise AnalysisError("That recording is too short to score.")
    if seconds > MAX_SECONDS:
        raise AnalysisError(
            f"That clip is {seconds:.0f} s long — keep it under {MAX_SECONDS:.0f} s."
        )
    return 0.9 * y / float(np.max(np.abs(y)))


def _trim(y: np.ndarray, pad: float = 0.05) -> np.ndarray:
    """Cut the lead-in and tail that are only background.

    A fixed "40 dB below the peak" rule never fires in a room with audible
    noise, and then a second of hiss at each end skews timing, alignment and
    the speaker embedding. So the cut-off sits above the measured noise floor.
    """
    rms = librosa.feature.rms(y=y, frame_length=N_FFT, hop_length=HOP)[0]
    db = 20 * np.log10(np.maximum(rms, 1e-10))
    peak, floor = float(db.max()), float(np.percentile(db, 10))
    threshold = max(peak - 40, floor + min(15.0, max(6.0, 0.3 * (peak - floor))))
    loud = np.flatnonzero(db > threshold)
    if loud.size == 0:
        return y
    margin = int(pad * SR)
    start = max(0, loud[0] * HOP - margin)
    end = min(len(y), (loud[-1] + 1) * HOP + N_FFT // 2 + margin)
    return y[start:end]


def _pitch(snd: parselmouth.Sound, times: np.ndarray) -> np.ndarray:
    pitch = snd.to_pitch_ac(
        time_step=HOP / SR, pitch_floor=PITCH_FLOOR, pitch_ceiling=PITCH_CEILING
    )
    track = pitch.selected_array["frequency"]
    xs = pitch.xs()
    f0 = np.full(len(times), np.nan)
    if len(xs) == 0:
        return f0
    step = pitch.get_time_step()
    idx = np.round((times - xs[0]) / step).astype(int)
    # Praat has no pitch frames in the half-window at each end of the sound.
    inside = (idx >= 0) & (idx < len(xs))
    f0[inside] = track[idx[inside]]
    f0[f0 <= 0] = np.nan
    return f0


def _formants(snd: parselmouth.Sound, times: np.ndarray, f0: np.ndarray) -> np.ndarray:
    formant = snd.to_formant_burg(
        time_step=HOP / SR, max_number_of_formants=5, maximum_formant=FORMANT_CEILING
    )
    out = np.full((3, len(times)), np.nan)
    for i in np.flatnonzero(np.isfinite(f0)):  # formants only mean much when voiced
        for k in range(3):
            out[k, i] = formant.get_value_at_time(k + 1, float(times[i]))
    return out
