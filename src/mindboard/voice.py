"""A voice for the experts: Kokoro, on this computer (DESIGN.md 41).

Kokoro-82M (Apache-2.0) through kokoro-onnx 0.6.1 (MIT), on the processor
with onnxruntime - so it never takes graphics memory from the 9B, and speaks
faster than real time. Its pronunciation step uses espeak-ng and phonemizer,
which are GPL-3.0: fine to run, but not something Mindboard (MIT) should
ship, so the voice is an optional extra, installed only by `mindboard ai
voice`, never a core dependency. Like every model, its files are pinned -
exact address, size and SHA-256 - and nothing is sent anywhere: speech is
made here and played here.
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import sys
import threading
import wave
from pathlib import Path
from typing import Optional

from .llm import Pinned, Progress, download, llm_home

_KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
MODEL = Pinned("kokoro-v1.0.onnx", _KOKORO + "kokoro-v1.0.onnx", 325532387,
               "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5")
VOICES = Pinned("voices-v1.0.bin", _KOKORO + "voices-v1.0.bin", 28214398,
                "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d")
PACKAGE = "kokoro-onnx==0.6.1"
FOLDER = "kokoro"
# voices that suit an expert, warm to deep; each expert gets one by turn
CAST = ["af_heart", "am_michael", "bf_emma", "bm_george", "af_bella", "am_fenrir", "bf_isabella", "bm_lewis"]

_lock = threading.Lock()
_engine = None


class VoiceError(RuntimeError):
    """Said to the person as it is."""


def folder(home: Optional[Path] = None) -> Path:
    return (home or llm_home()) / "models" / FOLDER


def installed(home: Optional[Path] = None) -> bool:
    f = folder(home)
    if not all((f / p.name).exists() for p in (MODEL, VOICES)):
        return False
    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        return False
    return True


def install(home: Optional[Path] = None, progress: Optional[Progress] = None) -> Path:
    """The package into this environment, and the pinned files into .llm."""
    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        uv = shutil.which("uv")
        cmd = ([uv, "pip", "install", "--quiet", "--python", sys.executable, PACKAGE] if uv
               else [sys.executable, "-m", "pip", "install", "--quiet", PACKAGE])
        done = subprocess.run(cmd, capture_output=True, text=True)
        if done.returncode != 0:
            raise VoiceError("could not install the voice package:\n" + (done.stderr or done.stdout)[-600:])
    f = folder(home)
    for pin in (MODEL, VOICES):
        download(pin, f, progress)
    return f


def _load(home: Optional[Path] = None):
    global _engine
    with _lock:
        if _engine is None:
            if not installed(home):
                raise VoiceError("the voice is not set up - run: mindboard ai voice")
            from kokoro_onnx import Kokoro
            f = folder(home)
            _engine = Kokoro(str(f / MODEL.name), str(f / VOICES.name))
        return _engine


def speakable(text: str) -> str:
    """What is read aloud: the words, without citations, markup or the
    NEXT line - and without saying "Beyond your board"."""
    text = re.sub(r"\[\[[0-9A-Za-z]{26}\]\]", "", text)
    text = re.sub(r"(?m)^[ \t]*NEXT:.*$", "", text)
    text = re.sub(r"[*_#>`]+", "", text).replace("Beyond your board:", "")
    return " ".join(text.split())


def speak(text: str, voice: str = CAST[0], speed: float = 1.0, home: Optional[Path] = None) -> bytes:
    """One stretch of speech as a WAV file (24 kHz, mono, 16-bit)."""
    words = speakable(text)[:1200]
    if not words:
        raise VoiceError("nothing to say")
    engine = _load(home)
    import numpy as np
    with _lock:                                  # one sentence at a time through the engine
        samples, rate = engine.create(words, voice=voice if voice in CAST else CAST[0],
                                      speed=speed, lang="en-us")
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
