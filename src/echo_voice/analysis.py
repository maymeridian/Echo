'''
src/echo_voice/analysis.py
Author: maymeridian
Description: Turns a recording into the per-frame features the scorer compares.
             - Everything is sampled on one 10 ms frame grid, so pitch, formants and MFCCs can
               be indexed with the same frame numbers after time alignment.
             - Each recording picks its own formant ceiling (the one with the smoothest F1-F3
               tracks); Resonance reads both sides at one ceiling per speaker (steadied).
'''

from __future__ import annotations

import io
from dataclasses import dataclass, field, replace
from functools import cached_property
from pathlib import Path

import librosa
import numpy as np
import parselmouth
import soundfile as sf

from . import identity, line

SR = 16000  # Hz: every recording is analysed at this rate, the one the speech models take
HOP = 160  # 10 ms
N_FFT = 400  # 25 ms
MAX_SECONDS = 30.0  # the longest clip scored: the speech recogniser's window (line.sounds)
MIN_SECONDS = 0.3  # anything shorter is too little to score
MIN_VOICED_FRAMES = 20  # 0.2 s of voiced sound before pitch is trusted
STRAY = 0.15  # s: a sound shorter than this, on its own at either end, is not part of the line
APART = 0.4  # s of quiet between it and the rest

PITCH_FLOOR = 60.0  # Hz: pitch is looked for between these two,
PITCH_CEILING = 600.0  # wide enough for any speaking voice
# The formant ceiling has to suit the vocal tract: about 5000 Hz for men, more for
# women and children. Each recording gets the candidate whose F1-F3 tracks jump
# least from frame to frame (the idea behind Barreda's FastTrack, 2021): a wrong
# ceiling makes the tracker hop between resonances. Against Hillenbrand et al.
# (1995) this cuts F2/F3 errors over 15% from 5.6%/6.9% to 1.5%/2.8% compared
# with a fixed 5500 Hz (tools/validate.py formant-lines).
#
# That pick is steady enough for typical values, but it changes from one recording
# of a person to the next (on RAVDESS an actor gets 5.4 different ceilings over 60
# recordings), and each change moves the formants a little. Resonance compares
# formants frame by frame, so it uses one ceiling per speaker instead: the middle
# of what their recordings pick (speaker_ceiling, steadied). On RAVDESS that takes
# Resonance from AUC 0.825 to 0.848 at telling a retake from a stranger, with five
# recordings to go by (cache/experiments/ceiling, 2026-11).
FORMANT_CEILINGS = tuple(range(4500, 7001, 250))
FALLBACK_CEILING = 5500.0  # Hz, when there is too little continuous voicing to pick one


# --- THE ANALYSED RECORDING ---


class AnalysisError(ValueError):
    """The audio can't be scored; the message is safe to show the user."""


@dataclass
class Voice:
    """One analysed recording: the audio and its features on the 10 ms frame grid."""

    y: np.ndarray  # trimmed, peak-normalised mono at SR
    times: np.ndarray  # frame centres in seconds
    f0: np.ndarray  # Hz per frame, NaN where unvoiced
    formants: np.ndarray  # (3, frames) F1-F3 in Hz, NaN where unvoiced
    formant_ceiling: float  # Hz, the one this recording picks for itself (or was given)
    mfcc: np.ndarray  # (12, frames), mean-normalised, energy dropped
    steady: np.ndarray | None = None  # F1-F3 tracked at the ceiling Resonance reads at (steadied)
    steady_ceiling: float | None = None  # Hz, the ceiling `steady` was tracked at
    # F1-F3 at other ceilings (at), and other readings worked out once (word_drill's words)
    tracks: dict = field(default_factory=dict, repr=False, compare=False)

    def at(self, ceiling: float) -> np.ndarray:
        """F1-F3 tracked at `ceiling` on the voiced frames, worked out once."""
        ceiling = float(ceiling)
        if ceiling == self.formant_ceiling:
            return self.formants
        if ceiling not in self.tracks:
            snd = parselmouth.Sound(self.y.astype(np.float64), sampling_frequency=SR)
            self.tracks[ceiling] = _tracks_at(snd, self.times, np.flatnonzero(self.voiced), ceiling)
        return self.tracks[ceiling]

    @cached_property
    def sounds(self) -> np.ndarray:
        """What a speech recogniser hears, one frame every 20 ms (line.sounds):
        what recordings are lined up by, and what Articulation compares."""
        return line.sounds(self.y)

    @cached_property
    def sound(self) -> np.ndarray:
        """For each of those frames: is there sound, within 40 dB of the
        loudest? Pauses, a silent lead-in or tail, and a gated microphone's
        gaps are left out of what is compared."""
        hop = int(line.SOUND_STEP * SR)
        rms = librosa.feature.rms(y=self.y, frame_length=2 * hop, hop_length=hop)[0]
        db = 20 * np.log10(np.maximum(rms, 1e-10))
        on = db > db.max() - 40
        n = len(self.sounds)
        return on[:n] if len(on) >= n else np.concatenate([on, np.zeros(n - len(on), dtype=bool)])

    @property
    def duration(self) -> float:
        """Length in seconds."""
        return len(self.y) / SR

    @property
    def voiced(self) -> np.ndarray:
        """For each frame: was a pitch found?"""
        return np.isfinite(self.f0)

    @property
    def resonance_formants(self) -> np.ndarray:
        """The tracks Resonance compares: at the speaker's ceiling when that is
        known, so two recordings of one person are measured the same way."""
        return self.formants if self.steady is None else self.steady

    @cached_property
    def embedding(self) -> np.ndarray:
        """Speaker fingerprint; computed on first use because it loads a model."""
        return identity.embed(self.y, SR)

    @property
    def spoken(self) -> slice:
        """Frames from the first to the last voiced sound, plus 50 ms either side
        for unvoiced consonants. Leaves out breaths, sighs and clicks at the
        edges, which differ from take to take but aren't part of the line. A
        voiced blip on its own at either end (under 50 ms, with more than
        APART seconds between it and the rest) is left out too: a click or a
        breath can read as voiced for a frame or two."""
        idx = np.flatnonzero(self.voiced)
        if idx.size < MIN_VOICED_FRAMES:
            return slice(0, len(self.times))
        runs = np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1)
        apart = APART * SR / HOP
        while len(runs) > 1 and len(runs[-1]) < 5 and runs[-1][0] - runs[-2][-1] > apart:
            runs.pop()
        while len(runs) > 1 and len(runs[0]) < 5 and runs[1][0] - runs[0][-1] > apart:
            runs.pop(0)
        # 5 frames either side; the stop is one past the last frame.
        return slice(max(0, runs[0][0] - 5), min(len(self.times), runs[-1][-1] + 6))

    @property
    def spoken_duration(self) -> float:
        """Seconds from the first to the last voiced sound (spoken)."""
        span = self.spoken
        return (span.stop - span.start) * HOP / SR

    @property
    def median_formants(self) -> np.ndarray:
        """Typical F1-F3 in Hz over voiced frames; NaN with too little voicing."""
        if self.voiced.sum() < MIN_VOICED_FRAMES:
            return np.full(3, np.nan)
        return np.nanmedian(self.formants[:, self.voiced], axis=1)

    @property
    def median_f0(self) -> float | None:
        """Typical pitch in Hz, or None when there is too little voiced sound."""
        if self.voiced.sum() < MIN_VOICED_FRAMES:
            return None
        return float(np.nanmedian(self.f0))


# --- READING AND ANALYSING AUDIO ---


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


def analyze(y: np.ndarray, ceiling: float | None = None, trim: bool = True) -> Voice:
    """`ceiling`: the formant ceiling to use, when the speaker's is already
    known. Without it the recording picks its own (formant_tracks), which is
    only dependable on a whole line: a single word picked the same ceiling
    as its line 23% of the time, and its formants then read a median 5.9% off.

    `trim`: cut the lead-in and tail that are only background. Audio the app
    saved (a line's target.wav, a take, a trainer attempt) was trimmed when
    it came in, and trimming is not idempotent: done again on a separated
    clip it moved the start by up to 10 ms and took 10-60 ms off the end, and
    on a clip that starts in digital silence it moved the start 160 ms. The
    line's word timings were measured on the audio as saved, so it is read
    as saved (trim=False)."""
    # 1. Check, trim and normalise the audio
    y = _prepare(y, trim)

    # 2. MFCCs, which set the frame grid. Coefficient 0, the energy, is dropped.
    #    Removing each coefficient's mean takes out most of the microphone and room
    #    colouring, which would otherwise swamp a clip-vs-live comparison.
    mfcc = librosa.feature.mfcc(y=y, sr=SR, n_mfcc=13, n_fft=N_FFT, hop_length=HOP, n_mels=40)[1:]
    mfcc = mfcc - mfcc.mean(axis=1, keepdims=True)
    times = librosa.frames_to_time(np.arange(mfcc.shape[1]), sr=SR, hop_length=HOP)

    # 3. Pitch
    snd = parselmouth.Sound(y.astype(np.float64), sampling_frequency=SR)
    f0 = _pitch(snd, times)

    # 4. Formants, at the ceiling given or at the one the recording picks
    if ceiling is None:
        formants, ceiling = formant_tracks(snd, times, f0)
    else:
        formants = _tracks_at(snd, times, np.flatnonzero(np.isfinite(f0)), float(ceiling))
    return Voice(
        y=y,
        times=times,
        f0=f0,
        formants=formants,
        formant_ceiling=float(ceiling),
        mfcc=mfcc,
    )


def _prepare(y: np.ndarray, trim: bool = True) -> np.ndarray:
    """The recording ready to analyse: checked it can be scored, trimmed (with `trim`)
    and scaled to a peak of 0.9. Raises AnalysisError when it can't be scored."""
    y = np.asarray(y, dtype=np.float32)
    if y.size == 0 or float(np.max(np.abs(y))) < 1e-4:
        raise AnalysisError("That recording is silent — check the microphone.")
    if trim:
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

    A short sound on its own at either end is cut too: the click on Stop lands
    a second after the last word, and with it in, the quiet before it was
    lined up against the end of the target (on the saved takes, appending a
    second of silence and a click moved Rhythm by 49 points for one take in
    ten). It goes when it lasts under STRAY seconds and more than APART
    seconds of quiet lie between it and the rest.

    `pad`: seconds kept either side of the sound.
    """
    # 1. Loudness, and the cut-off: 30% of the way from the noise floor (the 10th
    #    percentile) to the peak, kept 6-15 dB over the floor and at least the peak less 40 dB
    rms = librosa.feature.rms(y=y, frame_length=N_FFT, hop_length=HOP)[0]
    db = 20 * np.log10(np.maximum(rms, 1e-10))
    peak, floor = float(db.max()), float(np.percentile(db, 10))
    threshold = max(peak - 40, floor + min(15.0, max(6.0, 0.3 * (peak - floor))))
    loud = np.flatnonzero(db > threshold)
    if loud.size == 0:
        return y

    # 2. Drop a short sound on its own at either end
    runs = np.split(loud, np.flatnonzero(np.diff(loud) > 1) + 1)
    seconds = HOP / SR
    while (
        len(runs) > 1
        and len(runs[-1]) * seconds < STRAY
        and (runs[-1][0] - runs[-2][-1]) * seconds > APART
    ):
        runs.pop()
    while (
        len(runs) > 1
        and len(runs[0]) * seconds < STRAY
        and (runs[1][0] - runs[0][-1]) * seconds > APART
    ):
        runs.pop(0)

    # 3. Cut, keeping `pad` either side (and the last frame's half-window)
    margin = int(pad * SR)
    start = max(0, runs[0][0] * HOP - margin)
    end = min(len(y), (runs[-1][-1] + 1) * HOP + N_FFT // 2 + margin)
    return y[start:end]


def _pitch(snd: parselmouth.Sound, times: np.ndarray) -> np.ndarray:
    """Pitch in Hz at each of `times` (Praat's autocorrelation tracker), NaN where unvoiced."""
    pitch = snd.to_pitch_ac(
        time_step=HOP / SR,
        pitch_floor=PITCH_FLOOR,
        pitch_ceiling=PITCH_CEILING,
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


# --- FORMANTS AND CEILINGS ---


def formant_tracks(
    snd: parselmouth.Sound,
    times: np.ndarray,
    f0: np.ndarray,
) -> tuple[np.ndarray, float]:
    """F1-F3 on voiced frames, with the ceiling that gives the smoothest tracks."""
    voiced = np.flatnonzero(np.isfinite(f0))  # formants only mean much when voiced
    # Smoothness is judged inside runs of at least 5 voiced frames (50 ms).
    runs = [r for r in np.split(voiced, np.flatnonzero(np.diff(voiced) > 1) + 1) if r.size >= 5]
    if not runs:  # too little continuous voicing to judge smoothness
        return _tracks_at(snd, times, voiced, FALLBACK_CEILING), FALLBACK_CEILING
    best = (np.inf, FALLBACK_CEILING, None)  # roughness, ceiling, tracks
    for ceiling in FORMANT_CEILINGS:
        tracks = _tracks_at(snd, times, voiced, ceiling)
        semitones = 12 * np.log2(tracks)
        jumps = np.concatenate([np.abs(np.diff(semitones[:, r], axis=1)) for r in runs], axis=1)
        roughness = float(np.nanmean(jumps)) if np.isfinite(jumps).any() else np.inf
        if roughness < best[0]:
            best = (roughness, float(ceiling), tracks)
    if best[2] is None:  # no candidate produced usable tracks
        return _tracks_at(snd, times, voiced, FALLBACK_CEILING), FALLBACK_CEILING
    return best[2], best[1]


def _tracks_at(
    snd: parselmouth.Sound,
    times: np.ndarray,
    frames: np.ndarray,
    ceiling: float,
) -> np.ndarray:
    """F1-F3 in Hz at `ceiling` on the given frames (Praat's Burg tracker), NaN elsewhere."""
    # Five formants under the ceiling, Praat's standard setting; the first three are kept.
    formant = snd.to_formant_burg(
        time_step=HOP / SR,
        max_number_of_formants=5,
        maximum_formant=float(ceiling),
    )
    out = np.full((3, len(times)), np.nan)
    for i in frames:
        for k in range(3):
            out[k, i] = formant.get_value_at_time(k + 1, float(times[i]))
    return out


def speaker_ceiling(picks) -> float | None:
    """One formant ceiling for a speaker: the middle of the ceilings their
    recordings pick for themselves, on the grid. None with nothing to go by."""
    picks = [float(p) for p in picks if p]
    if not picks:
        return None
    middle = float(np.median(picks))
    return float(min(FORMANT_CEILINGS, key=lambda c: abs(c - middle)))


def shared_ceiling(a: float | None, b: float | None) -> float | None:
    """One formant ceiling for a pair of recordings: the middle of the two
    speakers' ceilings, on the grid. Read at different ceilings, the same audio
    is not level with itself (a copy of a line read at 4750 against 5250 Hz
    scored 3 to 24 on four of five lines), so both sides are read at one."""
    picks = [float(c) for c in (a, b) if c]
    if not picks:
        return None
    return float(min(FORMANT_CEILINGS, key=lambda c: (abs(c - sum(picks) / len(picks)), c)))


def steadied(voice: Voice, ceiling: float | None) -> Voice:
    """The recording with F1-F3 also tracked at the ceiling Resonance reads
    it at. Everything else (typical formants, the listener model, gender
    read) keeps the tracks the recording picked for itself, as those were fitted."""
    if ceiling is None or ceiling == voice.steady_ceiling:
        return voice
    out = replace(voice, steady=voice.at(ceiling), steady_ceiling=float(ceiling))
    # Already worked out; no need to run the models again.
    for name in ("embedding", "sounds", "sound"):
        if name in voice.__dict__:
            out.__dict__[name] = voice.__dict__[name]
    return out
