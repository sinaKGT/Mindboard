"""Finding the cards a question is about (retrieve.py): the index, the two
searches, the walk along relationships, the budget, and how ask uses it.

The embedder is a stand-in that turns words into vectors, so "meaning" here is
shared words - predictable, which is the point. The real model was checked
against a real board (DESIGN.md 29).
"""

import hashlib

import pytest

from mindboard import ask, retrieve
from mindboard.boards import conn_for
from mindboard.ids import ulid
from mindboard.ops import apply_envelopes, read_graph


class WordEmbedder:
    """Bag of words, hashed into 64 dimensions, unit length. Counts its calls."""

    def __init__(self):
        self.texts: list[str] = []

    def embed(self, texts):
        self.texts += texts
        out = []
        for t in texts:
            v = [0.0] * 64
            for w in retrieve._terms(t.split("Query:")[-1]):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1.0
            n = sum(x * x for x in v) ** 0.5 or 1.0
            out.append([x / n for x in v])
        return out


class Broken:
    def embed(self, texts):
        raise RuntimeError("not answering")


def node(**kw):
    base = {"id": ulid(), "type": "text", "pos": [0, 0], "title": "", "content": {}, "parent": None}
    base.update(kw)
    return base


@pytest.fixture()
def rag(dd, board):
    """A board about RAG: an evaluation group, tools, unrelated cards, links."""
    bid = board["id"]
    n = {}
    n["eval"] = node(type="meta", title="Evaluating RAG", content={"text": "how to measure it"})
    n["retrieval"] = node(title="Retrieval quality", parent=n["eval"]["id"],
                          content={"text": "recall at k and precision of the retrieved chunks"})
    n["generation"] = node(title="Generation quality", parent=n["eval"]["id"],
                           content={"text": "faithfulness and hallucination of the answer"})
    n["ragas"] = node(type="link", title="Ragas",
                      content={"url": "https://ragas.io", "excerpt": "evaluation framework"})
    n["judge"] = node(title="LLM judges", content={"text": "a model grades another model"})
    n["chain"] = node(title="Prompt chaining", content={"text": "steps feeding the next prompt"})
    for i in range(30):
        n[f"filler{i}"] = node(title=f"Note {i}", content={"text": f"unrelated thought number {i} "
                                                                    "about gardening and weather"})
    edges = [{"id": ulid(), "source": n["ragas"]["id"], "target": n["retrieval"]["id"],
              "kind": "supports", "label": "measures"},
             {"id": ulid(), "source": n["judge"]["id"], "target": n["generation"]["id"],
              "kind": "part-of", "label": ""}]
    c = conn_for(dd, bid)
    env = [{"opId": ulid(), "op": {"kind": "node.create", "node": v}} for v in n.values()]
    env += [{"opId": ulid(), "op": {"kind": "edge.create", "edge": e}} for e in edges]
    assert not apply_envelopes(c, bid, env)["rejected"]
    return {k: v["id"] for k, v in n.items()}


def graph(dd, board):
    g = read_graph(conn_for(dd, board["id"]), board["id"])
    return {n["id"]: n for n in g["nodes"]}, g["edges"]


def costs(nodes):
    return {nid: ask.estimate(ask.card_text(n, nodes)) for nid, n in nodes.items()}


# ----------------------------------------------------------------- index --

def test_a_card_is_embedded_by_what_it_says_and_where_it_sits(dd, board, rag):
    nodes, _ = graph(dd, board)
    doc = retrieve.card_doc(nodes[rag["retrieval"]], nodes)
    assert doc.startswith("Retrieval quality") and "in Evaluating RAG" in doc and "recall" in doc
    assert "evaluation framework" in retrieve.card_doc(nodes[rag["ragas"]], nodes)


def test_only_new_or_changed_cards_are_embedded(dd, board, rag):
    c = conn_for(dd, board["id"])
    nodes, _ = graph(dd, board)
    e = WordEmbedder()
    retrieve.refresh(c, nodes, e, "m")
    assert len(e.texts) == len(nodes)
    e.texts.clear()
    retrieve.refresh(c, nodes, e, "m")
    assert e.texts == [], "nothing changed: nothing embedded"

    apply_envelopes(c, board["id"], [{"opId": ulid(), "op": {
        "kind": "node.update", "id": rag["chain"], "patch": {"title": "Prompt chaining, edited"}}}])
    nodes, _ = graph(dd, board)
    retrieve.refresh(c, nodes, e, "m")
    assert len(e.texts) == 1 and "edited" in e.texts[0]

    e.texts.clear()
    retrieve.refresh(c, nodes, e, "another-model")
    assert len(e.texts) == len(nodes), "a different model means new vectors"


def test_a_deleted_card_takes_its_vector_with_it(dd, board, rag):
    c = conn_for(dd, board["id"])
    nodes, _ = graph(dd, board)
    retrieve.refresh(c, nodes, WordEmbedder(), "m")
    apply_envelopes(c, board["id"], [{"opId": ulid(), "op": {
        "kind": "node.delete", "ids": [rag["chain"]]}}])
    left = c.execute("SELECT 1 FROM node_embedding WHERE node_id=?", (rag["chain"],)).fetchone()
    assert left is None


# ---------------------------------------------------------------- search --

def test_finds_the_cards_the_question_is_about(dd, board, rag):
    c = conn_for(dd, board["id"])
    nodes, edges = graph(dd, board)
    found = retrieve.search(c, "how do I check recall and precision of retrieval?", nodes, edges,
                            costs(nodes), WordEmbedder(), "m")
    assert found.how == "meaning and wording"
    assert found.ids[0] == rag["retrieval"], "the best match first"
    assert not any(found.ids.index(rag[f"filler{i}"]) < found.ids.index(rag["retrieval"])
                   for i in range(30) if rag[f"filler{i}"] in found.ids)


def test_walks_the_persons_own_links(dd, board, rag):
    """Ragas shares no words with the question; it comes in because the person
    linked it to the card that matched - and above cards that match nothing."""
    c = conn_for(dd, board["id"])
    nodes, edges = graph(dd, board)
    found = retrieve.search(c, "recall precision retrieved chunks", nodes, edges, costs(nodes),
                            WordEmbedder(), "m", budget=400)
    assert rag["ragas"] in found.ids
    fillers = [rag[f"filler{i}"] for i in range(30)]
    assert all(found.ids.index(rag["ragas"]) < found.ids.index(f) for f in fillers if f in found.ids)


def test_a_matching_group_brings_its_cards(dd, board, rag):
    c = conn_for(dd, board["id"])
    nodes, edges = graph(dd, board)
    found = retrieve.search(c, "how to measure it", nodes, edges, costs(nodes), WordEmbedder(), "m")
    assert {rag["eval"], rag["retrieval"], rag["generation"]} <= set(found.ids)


def test_stays_within_budget(dd, board, rag):
    c = conn_for(dd, board["id"])
    nodes, edges = graph(dd, board)
    cost = costs(nodes)
    found = retrieve.search(c, "anything about evaluation", nodes, edges, cost, WordEmbedder(),
                            "m", budget=120)
    assert sum(cost[i] for i in found.ids) <= 120


def test_without_an_embedder_it_searches_by_wording(dd, board, rag):
    c = conn_for(dd, board["id"])
    nodes, edges = graph(dd, board)
    for embedder in (None, Broken()):
        found = retrieve.search(c, "faithfulness hallucination", nodes, edges, costs(nodes),
                                embedder, "m")
        assert found.how == "wording"
        assert found.ids[0] == rag["generation"]


def test_the_outline_shows_the_boards_groups(dd, board, rag):
    nodes, _ = graph(dd, board)
    text = retrieve.outline(nodes)
    assert "- Evaluating RAG (2 cards)" in text and "cards not in any group" in text


# ------------------------------------------------------- how ask uses it --

def test_a_small_board_is_still_sent_whole(dd, board, rag):
    ctx = ask.build(dd, conn_for(dd, board["id"]), board["id"], [], "recall?",
                    embedder=WordEmbedder(), embed_model="m")
    assert ctx.mode == "whole" and len(ctx.cards) == ctx.total


def test_a_big_board_is_searched(dd, board, rag, monkeypatch):
    monkeypatch.setattr(retrieve, "WHOLE_UNDER", 100)
    monkeypatch.setattr(retrieve, "BUDGET", 150)
    ctx = ask.build(dd, conn_for(dd, board["id"]), board["id"], [], "recall and precision?",
                    embedder=WordEmbedder(), embed_model="m")
    assert ctx.mode == "search" and ctx.how == "meaning and wording"
    assert rag["retrieval"] in ctx.cards and len(ctx.cards) < ctx.total
    assert ctx.text.startswith("BOARD OUTLINE") and "Evaluating RAG" in ctx.text


def test_a_follow_up_is_searched_with_what_led_up_to_it(dd, board, rag, monkeypatch):
    monkeypatch.setattr(retrieve, "WHOLE_UNDER", 100)
    seen = []
    real = retrieve.search
    monkeypatch.setattr(retrieve, "search", lambda conn, q, *a, **k: seen.append(q) or real(conn, q, *a, **k))
    ask.build(dd, conn_for(dd, board["id"]), board["id"], [], "which are open source?",
              history=[{"role": "user", "text": "what tools evaluate RAG?"},
                       {"role": "assistant", "text": "Ragas and others"}],
              embedder=WordEmbedder(), embed_model="m")
    assert seen == ["what tools evaluate RAG? which are open source?"]


def test_cards_chosen_by_hand_are_never_searched(dd, board, rag, monkeypatch):
    monkeypatch.setattr(retrieve, "WHOLE_UNDER", 1)
    monkeypatch.setattr(retrieve, "search", lambda *a, **k: pytest.fail("searched chosen cards"))
    ctx = ask.build(dd, conn_for(dd, board["id"]), board["id"], [rag["chain"]], "anything",
                    embedder=WordEmbedder(), embed_model="m")
    assert ctx.mode == "chosen" and ctx.cards == [rag["chain"]]
