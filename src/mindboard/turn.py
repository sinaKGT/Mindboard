"""Knowing when you have finished speaking: Smart Turn v3.2 (DESIGN.md 44).

Talking to an expert hands-free needs two things a pause alone cannot tell:
whether you have finished your thought, or are only taking a breath. Smart
Turn (pipecat-ai, BSD-2-Clause) answers exactly that from the sound of your
voice - intonation and rhythm, not words - in about 10-60 ms on the
processor. It is a Whisper Tiny encoder with a classifier, 8.7 MB as int8
ONNX, and runs on the onnxruntime the voice already brings.

It reads the last 8 seconds of your turn as a Whisper log-mel spectrogram.
The reference code computes that with the `transformers` library; here it is
the same arithmetic in numpy, so nothing large is installed for it - and a
test checks it against the reference numbers.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import numpy as np

from .llm import Pinned

RATE = 16000
SECONDS = 8
N_FFT, HOP, N_MELS = 400, 160, 80
THRESHOLD = 0.5

_REV = "f766f81d3cfdf7737ac64aad813d91bbfd56bf93"
MODEL = Pinned("smart-turn-v3.2-cpu.onnx",
               f"https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/{_REV}/smart-turn-v3.2-cpu.onnx",
               8679182, "2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f")

_lock = threading.Lock()
_session = None
_filters: Optional[np.ndarray] = None


# ---------------------------------------------------- the Whisper features --

def _hz_to_mel(hz: np.ndarray) -> np.ndarray:
    """Slaney's mel scale: linear below 1 kHz, logarithmic above."""
    hz = np.asarray(hz, dtype=np.float64)
    mel = 3.0 * hz / 200.0
    log = hz >= 1000.0
    mel[log] = 15.0 + np.log(hz[log] / 1000.0) * (27.0 / np.log(6.4))
    return mel


def _mel_to_hz(mel: np.ndarray) -> np.ndarray:
    mel = np.asarray(mel, dtype=np.float64)
    hz = 200.0 * mel / 3.0
    log = mel >= 15.0
    hz[log] = 1000.0 * np.exp(np.log(6.4) * (mel[log] - 15.0) / 27.0)
    return hz


def mel_filters() -> np.ndarray:
    """Whisper's 80 triangular filters over 201 frequency bins, area-normalised
    (slaney) - shape (201, 80)."""
    global _filters
    if _filters is None:
        fft_freqs = np.linspace(0, RATE // 2, 1 + N_FFT // 2)
        mels = np.linspace(_hz_to_mel(np.array([0.0]))[0], _hz_to_mel(np.array([8000.0]))[0], N_MELS + 2)
        freqs = _mel_to_hz(mels)
        diff = np.diff(freqs)
        slopes = freqs[None, :] - fft_freqs[:, None]
        down = -slopes[:, :-2] / diff[:-1]
        up = slopes[:, 2:] / diff[1:]
        f = np.maximum(0.0, np.minimum(down, up))
        f *= (2.0 / (freqs[2:N_MELS + 2] - freqs[:N_MELS]))[None, :]
        _filters = f
    return _filters


def last_seconds(audio: np.ndarray) -> np.ndarray:
    """The last 8 seconds, padded with silence at the start if shorter."""
    n = RATE * SECONDS
    audio = np.asarray(audio, dtype=np.float32)
    if len(audio) >= n:
        return audio[-n:]
    return np.pad(audio, (n - len(audio), 0))


def features(audio: np.ndarray) -> np.ndarray:
    """The model's input for one turn: (1, 80, 800) float32."""
    x = last_seconds(audio).astype(np.float64)
    x = (x - x.mean()) / np.sqrt(x.var() + 1e-7)                 # do_normalize
    padded = np.pad(x, (N_FFT // 2, N_FFT // 2), mode="reflect")  # centred frames
    frames = 1 + (len(padded) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(frames)[:, None]
    window = np.hanning(N_FFT + 1)[:-1]                           # periodic Hann
    power = np.abs(np.fft.rfft(padded[idx] * window, n=N_FFT)) ** 2
    mel = np.maximum(1e-10, power @ mel_filters()).T              # (80, frames)
    log = np.log10(mel)[:, :-1]
    log = np.maximum(log, log.max() - 8.0)
    return ((log + 4.0) / 4.0).astype(np.float32)[None, :, :]


# ------------------------------------------------------------ the model --

def folder(home: Optional[Path] = None) -> Path:
    from . import voice
    return voice.folder(home)


def installed(home: Optional[Path] = None) -> bool:
    if not (folder(home) / MODEL.name).exists():
        return False
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def _load(home: Optional[Path] = None):
    global _session
    with _lock:
        if _session is None:
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            so.inter_op_num_threads = 1
            so.intra_op_num_threads = 2
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            _session = ort.InferenceSession(str(folder(home) / MODEL.name), sess_options=so,
                                            providers=["CPUExecutionProvider"])
        return _session


def probability(audio: np.ndarray, home: Optional[Path] = None) -> float:
    """How likely it is that this turn is finished, 0..1."""
    session = _load(home)
    with _lock:
        out = session.run(None, {"input_features": features(audio)})
    return float(np.asarray(out[0]).reshape(-1)[0])


def pcm_from_wav(wav: bytes) -> np.ndarray:
    """16 kHz mono 16-bit WAV bytes as float samples in [-1, 1]."""
    import io
    import wave

    with wave.open(io.BytesIO(wav)) as w:
        if w.getframerate() != RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("expected 16 kHz mono 16-bit audio")
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
