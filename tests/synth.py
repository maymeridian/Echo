'''
tests/synth.py
Author: maymeridian
Description: A crude synthetic voice: glottal pulses through five formant resonators.
             - Good enough for Praat to find the pitch and formants we put in, which lets the
               tests change one property at a time and check the scorer notices.
             - compilation() strings real CMU ARCTIC clips together instead, like a video of a
               character's voice lines.
'''

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.signal import lfilter

SR = 16000
VOWELS = [(700, 1200, 2600), (300, 2300, 3000), (350, 800, 2400), (500, 1500, 2500)]
ARCTIC = Path(__file__).parent / "data" / "arctic"


def voice(
    f0: float = 120.0,
    formant_scale: float = 1.0,
    vowel_seconds: float = 0.4,
    glide_semitones: float = 4.0,
) -> np.ndarray:
    """Four vowels, each gliding up `glide_semitones`, with short gaps between."""
    n = int(vowel_seconds * SR)
    envelope = np.minimum(1.0, np.minimum(np.arange(n), np.arange(n)[::-1]) / (0.02 * SR))
    parts = []
    for formants in VOWELS:
        hz = f0 * 2 ** (np.linspace(-0.5, 0.5, n) * glide_semitones / 12)
        phase = np.cumsum(hz / SR)
        y = np.diff(np.floor(phase), prepend=0.0)  # one impulse per glottal cycle
        # F4 and F5 are fixed per voice; without them Praat's five-formant fit
        # parks a spare pole near 2 kHz and reports it as F3.
        centres = (*formants, 3500, 4500)
        bandwidths = (80, 100, 140, 200, 250)
        for centre, bandwidth in zip(centres, bandwidths, strict=True):
            r = np.exp(-np.pi * bandwidth / SR)
            theta = 2 * np.pi * centre * formant_scale / SR
            y = lfilter([1.0], [1.0, -2 * r * np.cos(theta), r * r], y)
        parts += [envelope * y / np.max(np.abs(y)), np.zeros(int(0.06 * SR))]
    return np.concatenate(parts[:-1]).astype(np.float32)


def compilation(
    clips=("bdl_arctic_a0001", "bdl_arctic_a0002") * 2 + ("slt_arctic_a0001",),
    gap=1.2,
    tail=0.0,
):
    """Real speech in a row, like a video of a character's voice lines: CMU
    ARCTIC clips with `gap` s of quiet between them and `tail` s at the end.
    The default is four lines by bdl (a man) and one by slt (a woman)."""
    import soundfile as sf

    rng = np.random.default_rng(0)

    def quiet(seconds):
        """`seconds` of faint noise: the quiet between and after the clips."""
        return 1e-4 * rng.standard_normal(int(seconds * SR))

    parts = []
    for name in clips:
        y, sr = sf.read(ARCTIC / f"{name}.wav", dtype="float32")
        assert sr == SR
        parts += [0.5 * y / np.max(np.abs(y)), quiet(gap)]
    parts.append(quiet(tail))
    return np.concatenate(parts).astype(np.float32)
