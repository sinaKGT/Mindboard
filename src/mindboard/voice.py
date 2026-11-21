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
import os
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


# On a Mac (DESIGN.md 50): whisper.cpp publishes no program for macOS - only a
# framework for apps - but Homebrew builds it for Apple silicon, with Metal.
# So a Mac listens through Homebrew's whisper-cpp: setting up the voice
# installs it (`brew install whisper-cpp`), and it is found where Homebrew puts
# it even when a double-clicked launcher has a short PATH. The model it reads
# is the same pinned, checked file as everywhere else. Homebrew checks its own
# downloads; the program is not pinned here, so its flags are the only thing
# relied on, and they are whisper.cpp's long-standing ones.
BREW_FORMULA = "whisper-cpp"
BREW_PREFIXES = ("/opt/homebrew/bin", "/usr/local/bin")
# the program's name since whisper.cpp 1.7; before that Homebrew called it whisper-cpp
MAC_NAMES = ("whisper-cli", "whisper-cpp")


def _on_path(name: str) -> Optional[Path]:
    """A program on PATH, or in Homebrew's folders - which a double-clicked
    launcher's PATH may lack."""
    found = shutil.which(name)
    if found:
        return Path(found)
    for prefix in BREW_PREFIXES:
        candidate = Path(prefix) / name
        if candidate.is_file():
            return candidate
    return None


def brew() -> Optional[Path]:
    return _on_path("brew") if sys.platform == "darwin" else None


def whisper_cli(home: Optional[Path] = None) -> Optional[Path]:
    name = "whisper-cli.exe" if sys.platform == "win32" else "whisper-cli"
    found = sorted(_whisper_dir(home).rglob(name)) if _whisper_dir(home).exists() else []
    if found:
        return found[0]
    if sys.platform == "darwin":
        for n in MAC_NAMES:
            cli = _on_path(n)
            if cli is not None:
                return cli
    return None


def setup_note() -> str:
    """What setting up the voice will do here beyond downloading, said first."""
    if sys.platform == "darwin" and whisper_cli() is None:
        return ("On a Mac, listening is installed with Homebrew (brew install whisper-cpp)."
                if brew() else "On a Mac, listening needs Homebrew (brew.sh) - speaking works without it.")
    return ""


def _install_with_brew(progress: Optional[Progress]) -> None:
    """whisper.cpp on a Mac, through Homebrew - a minute or two, once."""
    b = brew()
    if b is None:
        raise VoiceError("speaking to experts on a Mac uses Homebrew's whisper-cpp. Install Homebrew "
                         "(brew.sh), then choose Set up the AI again - or run: brew install whisper-cpp")
    if progress:
        progress(f"{BREW_FORMULA} (Homebrew)", 0, 1)
    try:
        done = subprocess.run([str(b), "install", BREW_FORMULA], capture_output=True, text=True,
                              timeout=1800, env={**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VoiceError(f"Homebrew could not install whisper-cpp ({exc.__class__.__name__}) - "
                         "run: brew install whisper-cpp") from exc
    if done.returncode != 0 or whisper_cli() is None:
        said = (done.stderr or done.stdout).strip().splitlines()[-1:] or [""]
        raise VoiceError(f"Homebrew could not install whisper-cpp: {said[0][:200]} - "
                         "run: brew install whisper-cpp")
    if progress:
        progress(f"{BREW_FORMULA} (Homebrew)", 1, 1)


def can_listen(home: Optional[Path] = None) -> bool:
    return whisper_cli(home) is not None and (folder(home) / WHISPER_MODEL.name).exists()


def complete(home: Optional[Path] = None) -> bool:
    """Everything the voice needs: speaking, listening, and knowing when you
    have finished (Smart Turn)."""
    from . import listen, turn
    return installed(home) and can_listen(home) and turn.installed(home) and listen.installed(home)


def install_listening(home: Optional[Path] = None, progress: Optional[Progress] = None) -> None:
    """whisper.cpp's program and its English model, Silero VAD - who is
    speaking - and Smart Turn - when they have finished (DESIGN.md 44, 45) -
    pinned and checked."""
    import tarfile
    import zipfile

    plat = ("windows" if sys.platform == "win32" else "linux" if sys.platform.startswith("linux")
            else "macos" if sys.platform == "darwin" else "")
    if not plat:
        raise VoiceError("speaking to experts is not available on this kind of computer yet")
    if plat == "macos":
        if whisper_cli(home) is None:
            _install_with_brew(progress)
    elif whisper_cli(home) is None:
        pin, target = WHISPER_BIN[plat], _whisper_dir(home)
        archive = download(pin, target.parent / "downloads", progress)
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
    from . import listen, turn
    download(turn.MODEL, folder(home), progress)
    download(listen.VAD, folder(home), progress)


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
