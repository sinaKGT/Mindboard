"""Listening in a conversation (DESIGN.md 45): Silero VAD decides who is
speaking, Smart Turn when they have finished, a reopen window forgives a
misjudged pause - and the stream that carries it all."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from mindboard import listen, voice
from mindboard.app import create_app
from mindboard.listen import CHUNK, CHUNK_MS, Listener, clean_heard


class Script:
    """A speech detector that says what it is told, chunk by chunk."""

    def __init__(self, plan):
        self.probs = [p for ms, p in plan for _ in range(max(1, ms // CHUNK_MS))]

    def __call__(self, chunk):
        return self.probs.pop(0) if self.probs else 0.0


def run(plan, smart=None):
    vad = Script(plan)
    lis = Listener(vad, smart)
    events = []
    while vad.probs:
        events += [e["type"] for e in lis.feed(np.zeros(CHUNK, np.float32))]
    for _ in range(100):                                # and some quiet after
        events += [e["type"] for e in lis.feed(np.zeros(CHUNK, np.float32))]
    return events


def test_whispers_notes_on_sounds_are_not_words():
    assert clean_heard("(chiming)") == ""
    assert clean_heard("[BLANK_AUDIO]") == ""
    assert clean_heard(" *music* ") == ""
    assert clean_heard("So (laughs) what is reranking?") == "So what is reranking?"
    assert clean_heard("...") == ""


def test_a_chime_or_a_cough_is_not_you():
    assert run([(500, 0), (300, 0.95), (1000, 0)], smart=lambda a: 1.0) == []


def test_a_finished_question_ends_the_turn_after_the_reopen_window():
    ev = run([(300, 0), (1500, 0.9), (2000, 0)], smart=lambda a: 0.9)
    assert ev == ["speech", "end", "commit"]


def test_going_on_within_the_window_continues_the_same_turn():
    ev = run([(300, 0), (1200, 0.9), (500, 0), (1200, 0.9), (2000, 0)], smart=lambda a: 0.9)
    assert ev == ["speech", "end", "reopen", "end", "commit"]


def test_an_unfinished_thought_waits_for_you():
    # Smart Turn says not done: a pause, and the turn goes on when you do
    ev = run([(300, 0), (1200, 0.9), (1200, 0), (1000, 0.9), (3000, 0)], smart=lambda a: 0.1)
    assert ev == ["speech", "pause", "pause", "end", "commit"]


def test_without_smart_turn_the_long_quiet_ends_it():
    ev = run([(300, 0), (1200, 0.9), (3000, 0)], smart=None)
    assert ev == ["speech", "pause", "end", "commit"]


def test_the_vad_is_pinned():
    v = listen.VAD
    assert v.size == 2327524 and len(v.sha256) == 64 and "/v5.1.2/" in v.url


def test_the_stream_needs_the_token(dd):
    from starlette.websockets import WebSocketDisconnect

    with TestClient(create_app(dd, "t")) as c:
        with pytest.raises(WebSocketDisconnect) as no:
            with c.websocket_connect("/api/voice/stream") as ws:
                ws.receive_json()
        assert no.value.code == 4401
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/api/voice/stream?t=t", headers={"origin": "https://evil.example"}) as ws:
                ws.receive_json()


def test_a_spoken_turn_through_the_stream(dd, monkeypatch):
    plan = [(300, 0), (1500, 0.9), (2500, 0)]
    monkeypatch.setattr(listen, "installed", lambda home=None: True)
    monkeypatch.setattr(voice, "can_listen", lambda home=None: True)
    monkeypatch.setattr(listen, "listener", lambda home=None, settings=None: Listener(Script(plan), lambda a: 0.9))
    monkeypatch.setattr(voice, "transcribe", lambda wav, home=None: "What is reranking? (chiming)")
    with TestClient(create_app(dd, "t")) as c:
        with c.websocket_connect("/api/voice/stream?t=t") as ws:
            got = []
            silence = np.zeros(2048, "<i2").tobytes()
            for _ in range(40):
                ws.send_bytes(silence)
            while "turn" not in [g["type"] for g in got]:
                got.append(ws.receive_json())
            kinds = [g["type"] for g in got]
            assert kinds[0] == "speech" and "writing" in kinds
            assert got[-1] == {"type": "turn", "text": "What is reranking?"}


@pytest.mark.skipif(not voice.complete(), reason="the voice is not set up on this computer")
def test_real_speech_with_a_pause_in_the_middle_is_one_turn():
    eng = voice._load()

    def said(text):
        s, r = eng.create(text, voice="am_michael", speed=1.0, lang="en-us")
        return np.interp(np.linspace(0, len(s) - 1, int(len(s) * 16000 / r)), np.arange(len(s)), s).astype(np.float32)

    rng = np.random.default_rng(1)
    quiet = lambda sec: (rng.standard_normal(int(sec * 16000)) * 0.002).astype(np.float32)
    t = np.arange(int(0.4 * 16000)) / 16000
    chime = (0.3 * np.sin(2 * np.pi * 1320 * t) * np.exp(-t * 6)).astype(np.float32)
    audio = np.concatenate([quiet(1), chime, quiet(1), said("I would like to know how reranking"), quiet(0.7),
                            said("actually works on my board."), quiet(3)])
    lis = listen.listener()
    kinds = []
    for i in range(0, len(audio), 2048):
        kinds += [e["type"] for e in lis.feed(audio[i:i + 2048])]
    assert kinds.count("speech") == 1, "the chime is not a turn, and the pause does not start a new one"
    assert kinds.count("commit") == 1 and kinds[-1] == "commit"
