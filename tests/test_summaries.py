"""Board tile summaries (DESIGN.md 54): written by the local model while it is
loaded and free - never loaded for this - kept, and written again only when
the board has changed a lot."""

import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient

from mindboard import summaries
from mindboard.app import create_app
from mindboard.boards import conn_for, create_board
from mindboard.ids import ulid
from mindboard.ops import apply_envelopes


def board_with(dd, n=6, title="RAG notes"):
    b = create_board(dd, title, "what I learn about retrieval")
    group = {"id": ulid(), "type": "meta", "pos": [0, 0], "title": "Chunking", "content": {}}
    cards = [{"id": ulid(), "type": "text", "pos": [i * 50, 0], "title": f"Card {i}",
              "parent": group["id"] if i < 2 else None, "content": {"text": f"note {i}"}} for i in range(n)]
    env = [{"opId": ulid(), "op": {"kind": "node.create", "node": x}} for x in [group, *cards]]
    env.append({"opId": ulid(), "op": {"kind": "edge.create", "edge": {
        "id": ulid(), "source": cards[3]["id"], "target": cards[4]["id"], "kind": "relates", "label": ""}}})
    assert not apply_envelopes(conn_for(dd, b["id"]), b["id"], env)["rejected"]
    return b, cards


class Writer:
    def __init__(self, text="Retrieval for lectures: chunking, overlap and reranking.", running=True):
        self.text, self._running, self.asked = text, running, []
        self._asking = threading.Lock()
        self.inst = SimpleNamespace(model="m")

    def running(self):
        return self._running

    def stop(self):
        pass

    def chat(self, messages, **kw):
        self.asked.append(messages)
        return {"text": self.text}


def test_a_summary_stays_true_until_the_board_changes_a_lot():
    kept = {"text": "x", "title": "RAG", "count": 100}
    assert summaries.fresh(kept, "RAG", 104), "a few cards more: still true"
    assert summaries.fresh(kept, "RAG", 114), "within 15%"
    assert not summaries.fresh(kept, "RAG", 120), "a fifth bigger: written again"
    assert not summaries.fresh(kept, "Retrieval", 100), "renamed: written again"
    assert summaries.fresh({"text": "x", "title": "T", "count": 3}, "T", 7), "small boards move by 5 first"
    assert not summaries.fresh({"text": "x", "title": "T", "count": 3}, "T", 8)
    assert not summaries.fresh(None, "T", 3) and not summaries.fresh({"title": "T", "count": 3}, "T", 3)


def test_the_model_reads_the_title_groups_and_most_connected_cards(dd):
    b, cards = board_with(dd)
    from mindboard.ops import read_graph
    g = read_graph(conn_for(dd, b["id"]), b["id"])
    text = summaries.brief(b, g["nodes"], g["edges"])
    assert "TITLE\nRAG notes" in text and "what I learn about retrieval" in text
    assert "- Chunking (2 cards)" in text
    listed = text.split("MOST CONNECTED CARDS")[1]
    assert listed.index("Card 3") < listed.index("Card 0"), "the linked cards first"


def test_the_tile_asks_and_never_loads_the_model(dd):
    b, _ = board_with(dd)
    h = {"X-Mindboard-Token": "t"}
    app = create_app(dd, "t")
    with TestClient(app) as c:
        asleep = Writer(running=False)
        app.state.ask["model"] = asleep
        got = c.get(f"/api/boards/{b['id']}/summary", headers=h).json()
        assert got["pending"] and got["summary"] == "" and asleep.asked == [], "not loaded for a hover"

        busy = Writer()
        busy._asking.acquire()
        app.state.ask["model"] = busy
        assert c.get(f"/api/boards/{b['id']}/summary", headers=h).json()["pending"], "a question goes first"

        free = Writer()
        app.state.ask["model"] = free
        got = c.get(f"/api/boards/{b['id']}/summary", headers=h).json()
        assert got == {"summary": "Retrieval for lectures: chunking, overlap and reranking."}
        assert "never with \"This board\"" in free.asked[0][0]["content"]
        again = c.get(f"/api/boards/{b['id']}/summary", headers=h).json()
        assert again["summary"].startswith("Retrieval") and len(free.asked) == 1, "kept, not written twice"

        empty = create_board(dd, "Empty", "")
        assert c.get(f"/api/boards/{empty['id']}/summary", headers=h).json()["empty"]


def test_the_indexer_summarises_while_the_model_is_loaded_anyway(dd):
    from mindboard.indexer import Indexer
    from mindboard.ops import read_graph

    b, _ = board_with(dd)
    g = read_graph(conn_for(dd, b["id"]), b["id"])
    asleep = Writer(running=False)
    Indexer(dd, lambda: None, describer=lambda: asleep)._summarise(b["id"], g)
    assert asleep.asked == [] and b["id"] not in summaries.load(dd)
    loaded = Writer()
    Indexer(dd, lambda: None, describer=lambda: loaded)._summarise(b["id"], g)
    assert summaries.load(dd)[b["id"]]["text"].startswith("Retrieval")
    Indexer(dd, lambda: None, describer=lambda: loaded)._summarise(b["id"], g)
    assert len(loaded.asked) == 1, "still true: not written again"
