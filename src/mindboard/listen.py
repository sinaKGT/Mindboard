"""Listening in a conversation: who is speaking, and when they have finished
(DESIGN.md 45).

The browser streams the microphone here - 16 kHz mono, already through its
echo cancellation - and this decides the turns, the way the speech-to-speech
pipeline (huggingface, Apache-2.0) behind CLARA does:

- **Silero VAD v5** (MIT, 2.3 MB ONNX) gives each 32 ms of sound a speech
  probability. Speech starts above 0.6 and lasts until it drops below 0.45
  (hysteresis), so a breath or a soft consonant does not end it.
- A sound only counts as you after **384 ms of speech** - a chime, a click,
  a cough, or what is left of the expert's own voice after echo
  cancellation, are shorter, and are dropped. Resuming within a pause needs
  only 192 ms.
- **250 ms** of quiet ends a stretch of speech; then **Smart Turn** reads the
  whole turn so far and says whether the thought is finished (p >= 0.55).
  Finished: the turn is written down and asked. Not finished: it waits - up
  to **2 s** of quiet - for you to go on, and if you do, the turn continues.
- The audio before speech begins (500 ms) is kept, so the first word is
  whole.

`Listener` only decides; it is fed audio and returns what happened, so it can
be tested with recorded sound. Writing the words down (whisper.cpp) is the
caller's, on its own thread, so listening never stops while it runs.
"""

from __future__ import annotations

import re
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .llm import Pinned

RATE = 16000
CHUNK = 512                                   # Silero's window at 16 kHz: 32 ms
CHUNK_MS = CHUNK * 1000 // RATE

VAD = Pinned("silero_vad_v5.onnx",
             "https://raw.githubusercontent.com/snakers4/silero-vad/v5.1.2/src/silero_vad/data/silero_vad.onnx",
             2327524, "2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f")


@dataclass
class Settings:
    """CLARA's tuning (configs/serve-gpu.json), which the speech-to-speech
    defaults were tuned towards."""
    thresh: float = 0.6
    min_speech_ms: int = 384
    min_continuation_ms: int = 192
    min_silence_ms: int = 250
    speech_pad_ms: int = 500
    smart_threshold: float = 0.55
    max_wait_ms: int = 2000
    max_turn_ms: int = 30000


# ------------------------------------------------------------------ Silero --

_vad_lock = threading.Lock()
_vad_session = None


def folder(home: Optional[Path] = None) -> Path:
    from . import voice
    return voice.folder(home)


def installed(home: Optional[Path] = None) -> bool:
    if not (folder(home) / VAD.name).exists():
        return False
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def _session(home: Optional[Path] = None):
    global _vad_session
    with _vad_lock:
        if _vad_session is None:
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.inter_op_num_threads = 1
            so.intra_op_num_threads = 1
            _vad_session = ort.InferenceSession(str(folder(home) / VAD.name), sess_options=so,
                                                providers=["CPUExecutionProvider"])
        return _vad_session


class Silero:
    """One stream's speech detector: the model is shared, its memory is not."""

    def __init__(self, session):
        self.session = session
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)
        self.sr = np.array(RATE, dtype=np.int64)

    def __call__(self, chunk: np.ndarray) -> float:
        x = np.concatenate([self.context, chunk.reshape(1, CHUNK).astype(np.float32)], axis=1)
        out, self.state = self.session.run(None, {"input": x, "state": self.state, "sr": self.sr})
        self.context = x[:, -64:]
        return float(out[0][0])


# ------------------------------------------------------------- the words --

_NOISE = re.compile(r"\([^)]*\)|\[[^\]]*\]|\*[^*]*\*|♪+")


def clean_heard(text: str) -> str:
    """What whisper wrote, without its notes on sounds - "(chiming)",
    "[BLANK_AUDIO]", "*music*" - and nothing at all if that was everything."""
    text = " ".join(_NOISE.sub(" ", text or "").split())
    return text if re.search(r"[A-Za-z0-9]", text) else ""


# --------------------------------------------------------------- turns --

@dataclass
class Listener:
    """Decides the turns in one stream of sound. `feed` takes samples (float,
    16 kHz) and returns events:

    - {"type": "speech"}            you began speaking: a new turn
    - {"type": "pause", "p": ...}   you paused; Smart Turn thinks you are not done
    - {"type": "end", "audio": ...} your turn seems over: start writing `audio` down
    - {"type": "reopen"}            ...but you went on within the reopen window:
                                    the same turn continues, forget that ending
    - {"type": "commit"}            the turn is over for good: ask it

    The reopen window (900 ms after a turn ends) is the speech-to-speech
    pipeline's answer to a pause Smart Turn misjudged: carrying on within it
    continues the turn instead of starting a new one. Writing the words down
    runs during the window, so it costs no time.
    """
    vad: Callable[[np.ndarray], float]
    smart: Optional[Callable[[np.ndarray], float]] = None
    settings: Settings = field(default_factory=Settings)
    reopen_ms: int = 900

    def __post_init__(self):
        s = self.settings
        self._rest = np.zeros(0, dtype=np.float32)
        self._pre: deque[np.ndarray] = deque(maxlen=max(1, s.speech_pad_ms // CHUNK_MS))
        self._seg: list[np.ndarray] = []       # the stretch of sound being heard
        self._turn: list[np.ndarray] = []      # the turn: its stretches so far
        self._in_seg = False
        self._active = 0                       # ms of speech in this stretch
        self._quiet = 0                        # ms of quiet at its end
        self._counted = False                  # this stretch counts as speech
        self._open = False                     # a turn is under way
        self._waiting = 0                      # ms of quiet since the turn paused
        self._ended: Optional[np.ndarray] = None   # a turn that ended, still reopenable
        self._since_end = 0

    @property
    def open(self) -> bool:
        return self._open

    def turn_audio(self) -> np.ndarray:
        parts = self._turn + (self._seg if self._counted else [])
        return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)

    def feed(self, samples: np.ndarray) -> list[dict]:
        events: list[dict] = []
        data = np.concatenate([self._rest, np.asarray(samples, dtype=np.float32)])
        n = len(data) // CHUNK
        for i in range(n):
            events += self._chunk(data[i * CHUNK:(i + 1) * CHUNK])
        self._rest = data[n * CHUNK:]
        return events

    def _chunk(self, c: np.ndarray) -> list[dict]:
        s = self.settings
        p = self.vad(c)
        out: list[dict] = []
        if self._ended is not None:
            self._since_end += CHUNK_MS

        if not self._in_seg:
            if p >= s.thresh:                                   # a stretch of sound begins
                self._in_seg, self._seg = True, [*self._pre, c]
                self._pre.clear()
                self._active, self._quiet, self._counted = CHUNK_MS, 0, False
                return out
            self._pre.append(c)
            if self._ended is not None and self._since_end >= self.reopen_ms:
                out.append(self._commit())
            elif self._open:
                self._waiting += CHUNK_MS
                if self._waiting >= s.max_wait_ms:              # quiet long enough: done
                    out += self._end(final=True)
            return out

        self._seg.append(c)
        if p >= s.thresh - 0.15:
            self._active += CHUNK_MS
            self._quiet = 0
        else:
            self._quiet += CHUNK_MS
        if self._open and not self._counted:                    # a sound in the pause, not yet you
            self._waiting += CHUNK_MS
        continuing = self._open or self._ended is not None
        need = s.min_continuation_ms if continuing else s.min_speech_ms
        if not self._counted and self._active >= need:
            self._counted = True
            if self._ended is not None:                         # you went on: the same turn
                self._turn, self._ended, self._open, self._waiting = [self._ended], None, True, 0
                out.append({"type": "reopen"})
            elif not self._open:
                self._open = True
                out.append({"type": "speech"})
        if self._counted and sum(len(x) for x in self._turn + self._seg) * 1000 // RATE >= s.max_turn_ms:
            self._close_seg()
            return out + self._end(final=True)
        if self._quiet >= s.min_silence_ms:                     # the stretch has ended
            counted, quiet = self._counted, self._quiet
            self._close_seg()
            if not counted:                                     # too short to be speech
                if self._ended is not None and self._since_end >= self.reopen_ms:
                    out.append(self._commit())
                elif self._open and self._waiting >= s.max_wait_ms:
                    out += self._end(final=True)
                return out
            # without Smart Turn, only the long quiet ends a turn
            p_done = self.smart(self.turn_audio()) if self.smart else 0.0
            if p_done >= s.smart_threshold:
                out += self._end(final=False)
            else:
                self._waiting = quiet
                out.append({"type": "pause", "p": round(p_done, 3)})
        elif self._open and not self._counted and self._waiting >= s.max_wait_ms:
            self._close_seg()
            out += self._end(final=True)
        return out

    def _close_seg(self):
        if self._counted:
            self._turn += self._seg
        self._seg, self._in_seg, self._active, self._quiet, self._counted = [], False, 0, 0, False

    def _end(self, final: bool) -> list[dict]:
        """The turn is over - for good after the long quiet, or reopenable
        after Smart Turn's word."""
        audio = self.turn_audio()
        self._turn, self._open, self._waiting = [], False, 0
        if final:
            self._ended = None
            return [{"type": "end", "audio": audio}, {"type": "commit"}]
        self._ended, self._since_end = audio, 0
        return [{"type": "end", "audio": audio}]

    def _commit(self) -> dict:
        self._ended = None
        return {"type": "commit"}


def listener(home: Optional[Path] = None, settings: Optional[Settings] = None) -> Listener:
    """A listener for one conversation, with Smart Turn if it is installed
    (without it, 2 s of quiet ends a turn)."""
    from . import turn
    smart = (lambda a: turn.probability(a, home)) if turn.installed(home) else None
    return Listener(Silero(_session(home)), smart, settings or Settings())
