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


# ---------------------------------------------------------------- listening --
#
# Speaking *to* an expert: whisper.cpp (MIT) turns a short recording into
# words, on this computer - the browser's own speech recognition would send
# the audio to a server. The official processor build (release b5454, beside
# v1.9.5) and the English base model, both pinned. A spoken question is a few
# seconds long, which the processor transcribes in about a second.

WHISPER_BUILD = "b5454"
_WHISPER = f"https://github.com/ggml-org/whisper.cpp/releases/download/{WHISPER_BUILD}/"
WHISPER_BIN = {
    "windows": Pinned("whisper-bin-x64.zip", _WHISPER + "whisper-bin-x64.zip", 8928640,
                      "6ba69e3482d7826214f90a6a9c84ca07782aec1e1d0c6a7c30c994fd5d816ccb"),
    "linux": Pinned("whisper-bin-ubuntu-x64.tar.gz", _WHISPER + "whisper-bin-ubuntu-x64.tar.gz", 10364195,
                    "a72becf15d7917f990f6313867a52638b82b7f9ef237fb0c980dac56a135781c"),
}
WHISPER_MODEL = Pinned("ggml-base.en.bin",
                       "https://huggingface.co/ggerganov/whisper.cpp/resolve/"
                       "5359861c739e955e79d9a303bcbc70fb988958b1/ggml-base.en.bin", 147964211,
                       "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002")


def _whisper_dir(home: Optional[Path] = None) -> Path:
    return (home or llm_home()) / "whisper" / WHISPER_BUILD


def whisper_cli(home: Optional[Path] = None) -> Optional[Path]:
    name = "whisper-cli.exe" if sys.platform == "win32" else "whisper-cli"
    found = sorted(_whisper_dir(home).rglob(name)) if _whisper_dir(home).exists() else []
    return found[0] if found else None


def can_listen(home: Optional[Path] = None) -> bool:
    return whisper_cli(home) is not None and (folder(home) / WHISPER_MODEL.name).exists()


def install_listening(home: Optional[Path] = None, progress: Optional[Progress] = None) -> None:
    """whisper.cpp's program and its English model, pinned and checked."""
    import tarfile
    import zipfile

    plat = "windows" if sys.platform == "win32" else "linux" if sys.platform.startswith("linux") else ""
    if not plat:
        raise VoiceError("speaking to experts is not available on this kind of computer yet")
    pin, target = WHISPER_BIN[plat], _whisper_dir(home)
    archive = download(pin, target.parent / "downloads", progress)
    if whisper_cli(home) is None:
        target.mkdir(parents=True, exist_ok=True)
        root = target.resolve()
        if pin.name.endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                for m in z.namelist():                    # never write outside its folder
                    if not (target / m).resolve().is_relative_to(root):
                        raise VoiceError("the whisper archive is not what was expected")
                z.extractall(target)
        else:
            with tarfile.open(archive) as t:
                t.extractall(target, filter="data")
        cli = whisper_cli(home)
        if cli is not None and plat == "linux":
            cli.chmod(0o755)
    download(WHISPER_MODEL, folder(home), progress)


def transcribe(wav: bytes, home: Optional[Path] = None) -> str:
    """The words in a short recording (16 kHz mono WAV), in English."""
    import tempfile

    cli = whisper_cli(home)
    if cli is None or not can_listen(home):
        raise VoiceError("listening is not set up - run: mindboard ai voice")
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "said.wav"
        f.write_bytes(wav)
        done = subprocess.run([str(cli), "-m", str(folder(home) / WHISPER_MODEL.name), "-f", str(f),
                               "-nt", "-np", "-l", "en", "-t", "4"],
                              capture_output=True, text=True, timeout=120, cwd=str(cli.parent),
                              creationflags=0x08000000 if sys.platform == "win32" else 0)
    if done.returncode != 0:
        raise VoiceError("could not make out the words")
    text = " ".join(done.stdout.split())
    return re.sub(r"\[(BLANK_AUDIO|MUSIC|NOISE)[^\]]*\]", "", text).strip()
