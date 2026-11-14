"""A crude synthetic voice: glottal pulses through five formant resonators.

Good enough for Praat to find the pitch and formants we put in, which lets the
tests change one property at a time and check the scorer notices.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter

SR = 16000
VOWELS = [(700, 1200, 2600), (300, 2300, 3000), (350, 800, 2400), (500, 1500, 2500)]


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
        for centre, bandwidth in zip((*formants, 3500, 4500), (80, 100, 140, 200, 250), strict=True):
            r = np.exp(-np.pi * bandwidth / SR)
            theta = 2 * np.pi * centre * formant_scale / SR
            y = lfilter([1.0], [1.0, -2 * r * np.cos(theta), r * r], y)
        parts += [envelope * y / np.max(np.abs(y)), np.zeros(int(0.06 * SR))]
    return np.concatenate(parts[:-1]).astype(np.float32)
