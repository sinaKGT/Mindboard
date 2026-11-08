"""Experts (DESIGN.md 40): kept in the library, searched across their boards,
answered in their voice, chosen by the guide, carried in backups."""

import json

import pytest
from fastapi.testclient import TestClient

from mindboard import ask, experts
from mindboard.app import create_app
from mindboard.boards import conn_for, create_board
from mindboard.bundle import create_backup, restore_backup
from mindboard.ids import ulid
from mindboard.ops import apply_envelopes
from mindboard.search_index import BoardIndex, terms


def node(title, text, parent=None):
    return {"id": ulid(), "type": "text", "pos": [0, 0], "title": title, "content": {"text": text}, "parent": parent}


@pytest.fixture()
def library(dd):
    """Two boards on different subjects, and one no expert uses."""
    made = {}
    for title, cards in [
        ("RAG notes", [("Re-ranking", "a cross-encoder reads the question with each document"),
                       ("Chunking", "split documents into overlapping windows")]),
        ("Garden", [("Tomatoes", "water tomatoes deeply twice a week"),
                    ("Compost", "turn the compost heap every month")]),
        ("Unused", [("Nothing", "nothing here")]),
    ]:
        b = create_board(dd, title, "")
        ns = [node(t, x) for t, x in cards]
        assert not apply_envelopes(conn_for(dd, b["id"]), b["id"],
                                   [{"opId": ulid(), "op": {"kind": "node.create", "node": n}} for n in ns])["rejected"]
        made[title] = {"board": b["id"], **{t: n["id"] for (t, _), n in zip(cards, ns)}}
    yield made
    for m in made.values():
        BoardIndex.drop(dd, m["board"])


class WordReranker:
    def capacity(self):
        return 30

    def rerank(self, query, documents):
        words = set(terms(query))
        return [len(words & set(terms(d))) / max(len(words), 1) for d in documents]


def test_an_expert_is_a_name_a_brief_and_its_boards(dd, library):
    e = experts.create(dd, {"name": "Retrieval Engineer", "brief": "Knows search.",
                            "boards": [library["RAG notes"]["board"], "0" * 26]})
    assert e["boards"] == [library["RAG notes"]["board"]], "a board that is not here is dropped"
    assert e["shape"] in experts.SHAPES and e["colour"]
    listed = experts.listing(dd)
    assert listed[0]["cards"] == 2 and listed[0]["boards"][0]["title"] == "RAG notes"
    experts.update(dd, e["id"], {"shape": "ankh", "name": "Search Engineer"})
    assert experts.get(dd, e["id"])["shape"] == "ankh" and experts.get(dd, e["id"])["brief"] == "Knows search."
    second = experts.create(dd, {"name": "Gardener", "boards": [library["Garden"]["board"]]})
    assert second["shape"] != e["shape"] or second["colour"] != e["colour"], "each new one looks different"
    assert experts.delete(dd, e["id"]) and len(experts.load(dd)) == 1


def test_an_expert_needs_a_name_and_a_board(dd, library):
    with pytest.raises(experts.ExpertError, match="name"):
        experts.create(dd, {"name": " ", "boards": [library["Garden"]["board"]]})
    with pytest.raises(experts.ExpertError, match="board"):
        experts.create(dd, {"name": "Lost", "boards": ["0" * 26]})


def test_an_expert_searches_all_its_boards_and_speaks_as_itself(dd, library):
    e = experts.create(dd, {"name": "Generalist", "brief": "Patient and plain.",
                            "boards": [library["RAG notes"]["board"], library["Garden"]["board"]]})
    ctx, lib = experts.context(dd, e, "how often should I water tomatoes?")
    assert library["Garden"]["Tomatoes"] in ctx.cards
    assert ctx.where[library["Garden"]["Tomatoes"]] == {"board": library["Garden"]["board"], "title": "Tomatoes"}
    assert "on the board 'Garden'" in ctx.text
    assert library["Unused"]["Nothing"] not in lib.nodes, "only its own boards"
    system = ask.messages(ctx, "q", [])[0]["content"]
    assert "You are Generalist. Patient and plain." in system
    ctx, _ = experts.context(dd, e, "what does a cross-encoder do?", reranker=WordReranker())
    assert ctx.cards[0] == library["RAG notes"]["Re-ranking"]


def test_the_guide_says_who_can_help(dd, library):
    rag = experts.create(dd, {"name": "Retrieval Engineer", "boards": [library["RAG notes"]["board"]]})
    garden = experts.create(dd, {"name": "Gardener", "boards": [library["Garden"]["board"]]})
    said = experts.guide(dd, "how often should I water the tomatoes?", reranker=WordReranker())
    assert said["picked"][0]["id"] == garden["id"]
    assert rag["id"] not in [p["id"] for p in said["picked"]], "the other one knows nothing of it"
    assert "Gardener" in said["why"] and "Tomatoes" in said["picked"][0]["cards"]
    assert experts.guide(dd.__class__(dd.root / "empty").ensure(), "anything")["picked"] == []


def test_experts_travel_in_a_backup(dd, library, tmp_path):
    e = experts.create(dd, {"name": "Gardener", "boards": [library["Garden"]["board"]]})
    out = create_backup(dd, tmp_path / "backup.zip")
    experts.delete(dd, e["id"])
    restore_backup(dd, out["path"])
    assert [x["name"] for x in experts.load(dd)] == ["Gardener"]
    restore_backup(dd, out["path"])
    assert len(experts.load(dd)) == 1, "restoring twice does not double them"


def test_the_experts_api(dd, library):
    app = create_app(dd, "t")
    h = {"X-Mindboard-Token": "t"}
    with TestClient(app) as c:
        r = c.post("/api/experts", headers=h, json={"name": "Gardener", "boards": [library["Garden"]["board"]],
                                                     "shape": "ankh"})
        assert r.status_code == 200 and r.json()["shape"] == "ankh"
        eid = r.json()["id"]
        got = c.get("/api/experts", headers=h).json()
        assert got["experts"][0]["cards"] == 2
        assert [x["shape"] for x in got["shapes"] if x["unlocked"]] == ["ankh"]
        assert c.patch(f"/api/experts/{eid}", headers=h, json={"shape": "pyramid"}).status_code == 400
        assert c.patch(f"/api/experts/{eid}", headers=h, json={"brief": "Calm."}).json()["brief"] == "Calm."
        assert c.post("/api/experts", headers=h, json={"name": "", "boards": []}).status_code == 400
        guide = c.post("/api/experts/guide", headers=h, json={"question": "water tomatoes"}).json()
        assert guide["picked"][0]["id"] == eid
        assert c.delete(f"/api/experts/{eid}", headers=h).json() == {"deleted": True}
        assert c.post(f"/api/experts/{eid}/ask", headers=h, json={"question": "q"}).status_code == 404


def test_monuments_are_earned_as_the_library_grows(dd, library):
    """Only the ankh at first; the obelisk once two boards hold 20 cards."""
    assert [x["shape"] for x in experts.shapes(dd) if x["unlocked"]] == ["ankh"]
    e = experts.create(dd, {"name": "Gardener", "boards": [library["Garden"]["board"]]})
    assert e["shape"] == "ankh"
    with pytest.raises(experts.ExpertError, match="still locked.*2 boards with 20"):
        experts.update(dd, e["id"], {"shape": "obelisk"})
    for title in ("RAG notes", "Garden"):
        bid = library[title]["board"]
        apply_envelopes(conn_for(dd, bid), bid, [{"opId": ulid(), "op": {"kind": "node.create",
                        "node": node(f"note {i}", "more")}} for i in range(20)])
    obelisk = next(x for x in experts.shapes(dd) if x["shape"] == "obelisk")
    assert obelisk["unlocked"] and obelisk["have"] == 2
    assert experts.update(dd, e["id"], {"shape": "obelisk"})["shape"] == "obelisk"


# ----------------------------------------------------------------- voice --

def test_only_the_words_are_read_aloud():
    from mindboard import voice

    said = voice.speakable("**Overlap** keeps context [[" + "A" * 26 + "]].\nBeyond your board:\nAlso this.\nNEXT: and then?")
    assert said == "Overlap keeps context . Also this." or said == "Overlap keeps context. Also this."


def test_each_expert_gets_a_voice_of_its_own(dd, library):
    from mindboard import voice

    a = experts.create(dd, {"name": "A", "boards": [library["Garden"]["board"]]})
    b = experts.create(dd, {"name": "B", "boards": [library["Garden"]["board"]]})
    assert a["voice"] in voice.CAST and b["voice"] in voice.CAST and a["voice"] != b["voice"]
    assert experts.update(dd, a["id"], {"name": "A2"})["voice"] == a["voice"], "kept on an edit"


def test_the_voice_says_whether_it_is_set_up(dd):
    app = create_app(dd, "t")
    with TestClient(app) as c:
        got = c.get("/api/voice", headers={"X-Mindboard-Token": "t"}).json()
        assert set(got) == {"installed", "cast", "listen", "turn", "talk"} and "af_heart" in got["cast"]


@pytest.mark.skipif(not __import__("mindboard.voice", fromlist=["installed"]).installed(),
                    reason="the voice is not set up on this computer")
def test_the_voice_speaks_on_this_computer():
    import io
    import wave

    from mindboard import voice

    wav = voice.speak("Hello from your expert.", "am_michael")
    with wave.open(io.BytesIO(wav)) as w:
        assert w.getframerate() == 24000 and w.getnchannels() == 1
        assert w.getnframes() / w.getframerate() > 0.5, "some speech came out"
    with pytest.raises(voice.VoiceError, match="nothing to say"):
        voice.speak("[[" + "A" * 26 + "]]")


def test_listening_needs_its_setup(tmp_path):
    from mindboard import voice

    assert not voice.can_listen(tmp_path)
    with pytest.raises(voice.VoiceError, match="mindboard ai voice"):
        voice.transcribe(b"RIFF....", home=tmp_path)
    for pin in [voice.WHISPER_MODEL, *voice.WHISPER_BIN.values()]:
        assert pin.size > 0 and len(pin.sha256) == 64 and pin.url.startswith("https://")
        assert "/main/" not in pin.url and "latest" not in pin.url, "pinned, never floating"


@pytest.mark.skipif(not (__import__("mindboard.voice", fromlist=["x"]).can_listen()
                         and __import__("mindboard.voice", fromlist=["x"]).installed()),
                    reason="the voice is not set up on this computer")
def test_what_the_voice_says_can_be_heard_back(dd):
    import io
    import wave

    import numpy as np

    from mindboard import voice

    said = voice.speak("Which experts know about chunking?", "am_michael")
    with wave.open(io.BytesIO(said)) as w:
        pcm, rate = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16), w.getframerate()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(pcm[np.arange(0, len(pcm), rate / 16000).astype(int)].tobytes())
    app = create_app(dd, "t")
    with TestClient(app) as c:
        heard = c.post("/api/voice/listen", headers={"X-Mindboard-Token": "t", "Content-Type": "audio/wav"},
                       content=out.getvalue()).json()["text"].lower()
    assert "experts" in heard and "chunking" in heard


# -------------------------------------------------------------- pictures --

def test_an_expert_answer_can_show_the_pictures_it_cites(dd, monkeypatch):
    """DESIGN.md 48: each cited card says which picture it shows, so the
    answer can show it; a picture left out of the question still goes in as
    words, as it does for a board (39)."""
    from io import BytesIO

    from PIL import Image

    from mindboard.assets import store_bytes
    from mindboard.search_index import PictureNotes

    b = create_board(dd, "Diagrams", "")
    c = conn_for(dd, b["id"])
    buf = BytesIO()
    Image.new("RGB", (64, 32), "#335577").save(buf, "PNG")
    pic = store_bytes(dd, c, b["id"], buf.getvalue(), declared_mime="image/png", orig_name="pipeline.png")
    img = {"id": ulid(), "type": "image", "pos": [0, 0], "title": "RAG pipeline",
           "content": {"sha256": pic["sha256"], "text": "the pipeline"}}
    txt = node("Chunking", "split documents into windows")
    assert not apply_envelopes(c, b["id"], [{"opId": ulid(), "op": {"kind": "node.create", "node": n}}
                                            for n in (img, txt)])["rejected"]
    PictureNotes.get(dd).put(pic["sha256"], "a diagram of a retrieval pipeline")
    e = experts.create(dd, {"name": "Drawer", "boards": [b["id"]]})
    try:
        ctx, lib = experts.context(dd, e, "show me the RAG pipeline diagram")
        assert ctx.where[img["id"]] == {"board": b["id"], "title": "RAG pipeline", "pic": pic["sha256"]}
        assert experts.where(lib, txt["id"]) == {"board": b["id"], "title": "Chunking"}, \
            "a card without a picture has none"
        assert len(ctx.pictures) == 1, "the model sees the picture itself"

        monkeypatch.setattr(experts, "SEARCH_PICTURE_TOKENS", 0)
        ctx, _ = experts.context(dd, e, "show me the RAG pipeline diagram")
        assert ctx.pictures == [] and ctx.pictures_left_out == 1
        assert "picture shows: a diagram of a retrieval pipeline" in ctx.text, "left out, it goes in as words"
        assert "cite the cards whose pictures show it" in ask.SYSTEM, "the model knows it can show them"
    finally:
        BoardIndex.drop(dd, b["id"])


def test_a_video_shows_its_cover():
    assert experts.picture_of({"type": "video", "content": {"sha256": "v", "poster": "p"}}) == "p"
    assert experts.picture_of({"type": "image", "content": {"sha256": "i"}}) == "i"
    assert experts.picture_of({"type": "text", "content": {"text": "x"}}) is None
