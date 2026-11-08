"""Knowing when you have finished speaking (DESIGN.md 44): Smart Turn's input
made in numpy exactly as the reference makes it, its endpoint, and the rest of
hands-free talking - the voice's speed, and stopping an expert mid-answer."""

import io
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from mindboard import turn
from mindboard.app import create_app
from mindboard.models import SpeakRequest

H = {"X-Mindboard-Token": "t"}


def wav_bytes(pcm: np.ndarray, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes((np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def chirp() -> np.ndarray:
    t = np.arange(int(3.2 * 16000)) / 16000
    rng = np.random.default_rng(7)
    return (0.3 * np.sin(2 * np.pi * (200 + 300 * t) * t) + 0.02 * rng.standard_normal(len(t))).astype(np.float32)


def test_the_features_match_the_reference_extractor():
    # values from transformers 4.46.3 WhisperFeatureExtractor(chunk_length=8), called as
    # the reference inference.py calls it, on the same signal
    f = turn.features(chirp())
    assert f.shape == (1, 80, 800) and f.dtype == np.float32
    for mel, frame, want in [(0, 0, -0.22169), (5, 799, 0.74811), (20, 500, 0.76743), (40, 700, 0.65462),
                             (60, 650, 0.71773), (79, 799, 0.80576), (10, 300, -0.22169), (33, 600, 0.80014)]:
        assert f[0, mel, frame] == pytest.approx(want, abs=1e-4)
    assert float(f.mean()) == pytest.approx(0.16648, abs=1e-4)


def test_only_the_last_eight_seconds_count_and_silence_pads_the_start():
    short = np.ones(16000, np.float32)
    padded = turn.last_seconds(short)
    assert len(padded) == 128000 and padded[0] == 0 and padded[-1] == 1
    long = np.arange(200000, dtype=np.float32)
    assert turn.last_seconds(long)[-1] == 199999 and len(turn.last_seconds(long)) == 128000
    f = turn.mel_filters()
    assert f.shape == (201, 80) and (f >= 0).all()


def test_the_recording_is_read_as_sixteen_kilohertz_mono():
    pcm = turn.pcm_from_wav(wav_bytes(np.full(1600, 0.5, np.float32)))
    assert len(pcm) == 1600 and abs(pcm[0] - 0.5) < 1e-3
    with pytest.raises(ValueError):
        turn.pcm_from_wav(wav_bytes(np.zeros(100, np.float32), rate=44100))


def test_the_model_is_pinned():
    m = turn.MODEL
    assert m.size == 8679182 and len(m.sha256) == 64
    assert "/resolve/f766f81d" in m.url and "/main/" not in m.url, "a fixed revision, never floating"


def test_the_turn_endpoint(dd, monkeypatch):
    app = create_app(dd, "t")
    with TestClient(app) as c:
        monkeypatch.setattr(turn, "installed", lambda home=None: False)
        r = c.post("/api/voice/turn", content=wav_bytes(np.zeros(1600, np.float32)), headers=H)
        assert r.status_code == 400 and "mindboard ai voice" in r.json()["detail"]
        monkeypatch.setattr(turn, "installed", lambda home=None: True)
        monkeypatch.setattr(turn, "probability", lambda pcm, home=None: 0.83)
        r = c.post("/api/voice/turn", content=wav_bytes(np.zeros(1600, np.float32)), headers=H)
        assert r.json() == {"complete": True, "probability": 0.83}
        r = c.post("/api/voice/turn", content=b"not a wav", headers=H)
        assert r.status_code == 400


def test_stopping_an_expert_and_the_voices_speed(dd):
    with TestClient(create_app(dd, "t")) as c:
        assert c.post("/api/experts/ask/stop", headers=H).json() == {"stopped": False}
    assert SpeakRequest(text="hi").speed == 1.0
    assert SpeakRequest(text="hi", speed=0.8).speed == 0.8
    with pytest.raises(ValueError):
        SpeakRequest(text="hi", speed=3)


@pytest.mark.skipif(not __import__("mindboard.voice", fromlist=["x"]).complete(),
                    reason="the voice is not set up on this computer")
def test_a_finished_question_and_a_trailing_one():
    from mindboard import voice

    eng = voice._load()

    def said(text):
        s, r = eng.create(text, voice="af_heart", speed=1.0, lang="en-us")
        x = np.interp(np.linspace(0, len(s) - 1, int(len(s) * 16000 / r)), np.arange(len(s)), s)
        return np.concatenate([x, np.zeros(8000)]).astype(np.float32)

    assert turn.probability(said("How does reranking improve the answers I get from my board?")) > 0.5
    assert turn.probability(said("I would like to know how reranking, um, and")) < 0.5
