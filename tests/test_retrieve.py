"""Finding the cards a question is about: the board's search index (Tantivy and
exact vectors), the fusion, the walk along relationships, the budget, how ask
uses it, and the evaluator.

The embedder is a stand-in that turns words into vectors, so "meaning" here is
shared words - predictable, which is the point. The real models are measured
against a real board with `mindboard ai eval` (DESIGN.md 30).
"""

import hashlib
import json

import pytest

from mindboard import ask, evaluate, retrieve
from mindboard.boards import conn_for, delete_board
from mindboard.ids import ulid
from mindboard.ops import apply_envelopes, read_graph
from mindboard.search_index import BoardIndex, index_dir, phrases, terms


class WordEmbedder:
    """Bag of words, hashed into 64 dimensions, unit length. Counts its calls."""

    def __init__(self):
        self.texts: list[str] = []

    def embed(self, texts):
        self.texts += texts
        out = []
        for t in texts:
            v = [0.0] * 64
            for w in terms(t.split("Query:")[-1]):
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
                      content={"url": "https://ragas.io", "excerpt": "an assessment framework"})
    n["judge"] = node(title="LLM judges", content={"text": "a model grades another model"})
    n["chain"] = node(title="Prompt chaining", content={"text": "steps feeding the next prompt"})
    n["bodyonly"] = node(title="Misc", content={"text": "chaining is mentioned here in passing"})
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


def revision(dd, board):
    return conn_for(dd, board["id"]).execute(
        "SELECT revision FROM board WHERE id=?", (board["id"],)).fetchone()["revision"]


@pytest.fixture()
def index(dd, board, rag):
    nodes, _ = graph(dd, board)
    ix = BoardIndex.get(dd, board["id"])
    ix.sync(nodes, revision(dd, board), WordEmbedder(), "m")
    yield ix
    BoardIndex.drop(dd, board["id"])


def costs(nodes):
    return {nid: ask.estimate(ask.card_text(n, nodes)) for nid, n in nodes.items()}


# ----------------------------------------------------------------- words --

def test_questions_become_clean_words():
    assert terms('What\'s "the" best: way? (evaluating) RAG-systems!') == \
        ["best", "way", "evaluating", "rag", "systems"]
    assert phrases('find "Retrieval Quality" and "open source" cards') == \
        [["retrieval", "quality"], ["open", "source"]]
    assert phrases("no quotes, so no guessed pairs") == []


# ----------------------------------------------------------------- index --

def test_stemming_finds_other_forms_of_a_word(index, rag):
    assert index.keyword("evaluating")[0] == rag["eval"], '"evaluating" finds "Evaluating RAG"'
    assert rag["eval"] in index.keyword("evaluation")


def test_a_title_counts_more_than_a_passing_mention(index, rag):
    hits = index.keyword("chaining")
    assert hits.index(rag["chain"]) < hits.index(rag["bodyonly"])


def test_a_group_path_is_searchable(index, rag):
    """Retrieval quality's own words never say "evaluating"; its group does."""
    assert rag["retrieval"] in index.keyword("evaluating")


def test_quoted_phrases_match_word_for_word(index, rag):
    assert index.exact('"retrieved chunks"') == [rag["retrieval"]]
    assert index.exact('"chunks retrieved"') == []


def test_misspellings_still_find_the_card(index, rag):
    assert rag["retrieval"] in index.typos("retreived chunks")


def test_meaning_is_searched_exactly(index, rag):
    vec = WordEmbedder().embed(["Query:faithfulness hallucination answer"])[0]
    ranked, sims = index.meaning(vec)
    assert ranked[0] == rag["generation"]
    assert sims[ranked[0]] == max(sims.values())


def test_only_new_or_changed_cards_are_embedded(dd, board, rag):
    nodes, _ = graph(dd, board)
    ix = BoardIndex.get(dd, board["id"])
    e = WordEmbedder()
    ix.sync(nodes, revision(dd, board), e, "m")
    assert len(e.texts) == len(nodes)
    e.texts.clear()
    ix.sync(nodes, revision(dd, board), e, "m")
    assert e.texts == [], "same revision: nothing to do"

    c = conn_for(dd, board["id"])
    apply_envelopes(c, board["id"], [{"opId": ulid(), "op": {
        "kind": "node.update", "id": rag["chain"], "patch": {"title": "Prompt chaining, edited"}}}])
    nodes, _ = graph(dd, board)
    ix.sync(nodes, revision(dd, board), e, "m")
    assert len(e.texts) == 1 and "edited" in e.texts[0]
    assert ix.keyword("edited") == [rag["chain"]], "the text index follows the board"

    e.texts.clear()
    ix.sync(nodes, revision(dd, board), e, "another-model")
    assert len(e.texts) == len(nodes), "a different model means new vectors"
    BoardIndex.drop(dd, board["id"])


def test_the_index_survives_a_restart(dd, board, rag):
    nodes, _ = graph(dd, board)
    BoardIndex.get(dd, board["id"]).sync(nodes, revision(dd, board), WordEmbedder(), "m")
    reopened = BoardIndex(index_dir(dd, board["id"]))
    assert reopened.revision == revision(dd, board)
    assert reopened.keyword("faithfulness") == [rag["generation"]]
    assert reopened.matrix.shape == (len(nodes), 64)
    BoardIndex.drop(dd, board["id"])


def test_deleting_a_board_deletes_its_index(dd, board, rag):
    nodes, _ = graph(dd, board)
    BoardIndex.get(dd, board["id"]).sync(nodes, revision(dd, board), WordEmbedder(), "m")
    assert index_dir(dd, board["id"]).exists()
    delete_board(dd, board["id"])
    assert not index_dir(dd, board["id"]).exists()


# ---------------------------------------------------------------- search --

def test_finds_the_cards_the_question_is_about(dd, board, rag, index):
    nodes, edges = graph(dd, board)
    found = retrieve.search(index, "how do I check recall and precision of retrieval?", nodes,
                            edges, costs(nodes), WordEmbedder())
    assert found.ids[0] == rag["retrieval"]
    assert "keywords" in found.how and "meaning" in found.how


def test_walks_the_persons_own_links(dd, board, rag, index):
    """Ragas shares no words with the question; it comes in because the person
    linked it to the card that matched - and above cards that match nothing."""
    nodes, edges = graph(dd, board)
    found = retrieve.search(index, "recall precision retrieved chunks", nodes, edges,
                            costs(nodes), WordEmbedder(), budget=400)
    assert rag["ragas"] in found.ids
    fillers = [rag[f"filler{i}"] for i in range(30)]
    assert all(found.ids.index(rag["ragas"]) < found.ids.index(f) for f in fillers if f in found.ids)


def test_a_matching_group_brings_its_cards(dd, board, rag, index):
    nodes, edges = graph(dd, board)
    found = retrieve.search(index, "how to measure it", nodes, edges, costs(nodes), WordEmbedder())
    assert {rag["eval"], rag["retrieval"], rag["generation"]} <= set(found.ids)


def test_stays_within_budget(dd, board, rag, index):
    nodes, edges = graph(dd, board)
    cost = costs(nodes)
    found = retrieve.search(index, "anything about evaluation", nodes, edges, cost,
                            WordEmbedder(), budget=120)
    assert sum(cost[i] for i in found.ids) <= 120


def test_without_an_embedder_it_searches_by_wording(dd, board, rag, index):
    nodes, edges = graph(dd, board)
    for embedder in (None, Broken()):
        found = retrieve.search(index, "faithfulness hallucination", nodes, edges, costs(nodes),
                                embedder)
        assert "meaning" not in found.how
        assert found.ids[0] == rag["generation"]


def test_quoted_phrases_join_the_fusion(dd, board, rag, index):
    _, _, used = retrieve.candidates(index, 'what about "retrieved chunks"?', WordEmbedder())
    assert used[0] == "exact phrases"
    _, _, used = retrieve.candidates(index, "what about retrieved chunks?", WordEmbedder())
    assert "exact phrases" not in used


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
    assert ctx.mode == "search" and "meaning" in ctx.how
    assert rag["retrieval"] in ctx.cards and len(ctx.cards) < ctx.total
    assert ctx.text.startswith("BOARD OUTLINE") and "Evaluating RAG" in ctx.text
    BoardIndex.drop(dd, board["id"])


def test_a_follow_up_is_searched_with_what_led_up_to_it(dd, board, rag, monkeypatch):
    monkeypatch.setattr(retrieve, "WHOLE_UNDER", 100)
    seen = []
    real = retrieve.search
    monkeypatch.setattr(retrieve, "search", lambda ix, q, *a, **k: seen.append(q) or real(ix, q, *a, **k))
    ask.build(dd, conn_for(dd, board["id"]), board["id"], [], "which are open source?",
              history=[{"role": "user", "text": "what tools evaluate RAG?"},
                       {"role": "assistant", "text": "Ragas and others"}],
              embedder=WordEmbedder(), embed_model="m")
    assert seen == ["what tools evaluate RAG? which are open source?"]
    BoardIndex.drop(dd, board["id"])


def test_cards_chosen_by_hand_are_never_searched(dd, board, rag, monkeypatch):
    monkeypatch.setattr(retrieve, "WHOLE_UNDER", 1)
    monkeypatch.setattr(retrieve, "search", lambda *a, **k: pytest.fail("searched chosen cards"))
    ctx = ask.build(dd, conn_for(dd, board["id"]), board["id"], [rag["chain"]], "anything",
                    embedder=WordEmbedder(), embed_model="m")
    assert ctx.mode == "chosen" and ctx.cards == [rag["chain"]]


# ------------------------------------------------------------- evaluator --

def test_the_evaluator_scores_what_it_finds(dd, board, rag, tmp_path):
    test_set = {"board": board["id"], "questions": [
        {"kind": "simple", "question": "faithfulness and hallucination", "gold": [rag["generation"]]},
        {"kind": "multi-hop", "question": "gardening weather", "gold": [rag["ragas"]]},
    ]}
    path = tmp_path / "set.json"
    path.write_text(json.dumps(test_set))
    reports = evaluate.run(dd, evaluate.load_set(path), WordEmbedder(), "m")
    keyword = next(r for r in reports if r.strategy.startswith("keyword"))
    assert keyword.scores[0].mrr == 1.0 and keyword.scores[0].hit5 == 1.0
    assert keyword.scores[1].recall10 == 0.0
    assert "hit@5" in evaluate.table(reports, ["simple", "multi-hop"])
    BoardIndex.drop(dd, board["id"])


def test_a_test_set_needs_its_right_answers(tmp_path):
    path = tmp_path / "set.json"
    path.write_text(json.dumps({"board": "x", "questions": [{"kind": "simple", "question": "q"}]}))
    with pytest.raises(ValueError, match="no gold"):
        evaluate.load_set(path)
