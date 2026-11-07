"""Finding the cards a question is about: GraphRAG for a mind map
(DESIGN.md 29-32).

A question about the whole board is always answered from a search of it -
never the board sent entire, which stops working long before a board reaches
the ten thousand cards it may hold. The search runs against the board's search
index (search_index.py), in three stages:

1. Candidates. BM25 keywords with stemming and meaning by embedding, fused by
   reciprocal rank; phrases the person quoted are searched exactly and join
   the fusion; a typo search joins when the keywords found little.
2. Reading. A cross-encoder reranker reads the question with each of the best
   candidates - and with what each card is linked to - and scores how well it
   answers. Candidates come from the fusion and from the graph (below), so a
   card the words missed but the links reach is read too.
3. The graph. Personalized PageRank spreads relevance from the best-read cards
   along the person's own typed relationships and group membership, and the
   two orders - read and spread - are fused.

The strongest cards that fit a token budget go to the model, with a short
outline of the board's groups so a broad question still sees its shape.
Without the reranker (not installed, or it failed) stage 2 is skipped and the
best candidates take one step along the links instead (walk).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from .search_index import QUERY_INSTRUCT, BoardIndex, card_fields, card_passage

BUDGET = 5000           # tokens of cards a search may send
SEEDS = 10              # the best cards, from which relevance spreads along the links
RRF_K = 60
# what the reranker reads of each pair; together they stay inside its
# 2,048-token context even in a script that takes a token per character
QUERY_CHARS = 500
DOC_CHARS = 1300


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class Reranker(Protocol):
    def rerank(self, query: str, documents: list[str]) -> list[float]: ...
    def capacity(self) -> int: ...


def ranked(scores: dict[str, float]) -> list[str]:
    return sorted(scores, key=scores.get, reverse=True)


def top(scores: dict[str, float], n: int) -> dict[str, float]:
    return {k: scores[k] for k in ranked(scores)[:n]}


@dataclass
class Found:
    ids: list[str]                                   # in the order they go to the model
    total: int                                       # cards on the board
    how: str                                         # what the search went by, in words
    scores: dict[str, float] = field(default_factory=dict)


def fuse(*rankings: list[str]) -> dict[str, float]:
    """Reciprocal rank fusion: a card near the top of any list rises; a card
    near the top of several rises most. No scores to calibrate between them."""
    score: dict[str, float] = {}
    for ranking in rankings:
        for rank, nid in enumerate(ranking):
            score[nid] = score.get(nid, 0.0) + 1.0 / (RRF_K + rank + 1)
    return score


def walk(seeds: dict[str, float], nodes: dict[str, dict], edges: list[dict],
         sims: dict[str, float]) -> dict[str, float]:
    """From the best cards, one step along the person's relationships - their
    own claims about what connects - and into and out of groups. A neighbour
    scores below the card that led to it, and higher if it also matches."""
    links: dict[str, list[str]] = {}
    for e in edges:
        links.setdefault(e["source"], []).append(e["target"])
        links.setdefault(e["target"], []).append(e["source"])
    children: dict[str, list[str]] = {}
    for n in nodes.values():
        if n.get("parent"):
            children.setdefault(n["parent"], []).append(n["id"])
    out = dict(seeds)
    for nid, s in seeds.items():
        near = list(links.get(nid, []))
        if nodes[nid]["type"] == "meta":
            near += children.get(nid, [])
        for other in near:
            if other not in nodes:
                continue
            bonus = max(sims.get(other, 0.0), 0.0) * 0.01
            out[other] = max(out.get(other, 0.0), s * 0.5 + bonus)
    return out


def graph_rank(seeds: dict[str, float], nodes: dict[str, dict], edges: list[dict],
               restart: float = 0.25, rounds: int = 40) -> dict[str, float]:
    """Personalized PageRank from the best matches over the person's own graph.

    Relevance starts on the seed cards, in proportion to how well they
    matched, and repeatedly flows along every relationship (either way) and
    every group membership, a quarter of it returning to the seeds each round.
    A card that several good matches lead to - one step away or three - ends up
    high; a card nothing relevant connects to stays at zero. This is how a
    question about "chunking that keeps context" reaches *Sliding Window*, a
    card whose whole text is its title, linked as an example of the card that
    matched. (The idea is HippoRAG's; the graph here is the person's, typed by
    them, not one a model extracted.)
    """
    import numpy as np

    ids = list(nodes)
    at = {nid: i for i, nid in enumerate(ids)}
    src: list[int] = []
    dst: list[int] = []
    for e in edges:
        a, b = at.get(e["source"]), at.get(e["target"])
        if a is not None and b is not None and a != b:
            src += [a, b]
            dst += [b, a]
    for n in nodes.values():
        p = at.get(n.get("parent") or "")
        if p is not None:
            src += [at[n["id"]], p]
            dst += [p, at[n["id"]]]
    start = np.zeros(len(ids))
    for nid, s in seeds.items():
        if nid in at:
            start[at[nid]] = max(s, 0.0)
    if start.sum() == 0 or not src:
        return {nid: float(start[at[nid]]) for nid in seeds if nid in at}
    start /= start.sum()
    s_arr, d_arr = np.array(src), np.array(dst)
    degree = np.bincount(s_arr, minlength=len(ids)).astype(float)
    rank = start.copy()
    for _ in range(rounds):
        flow = np.zeros(len(ids))
        np.add.at(flow, d_arr, rank[s_arr] / degree[s_arr])
        lost = rank[degree == 0].sum()                      # cards with no links
        rank = restart * start + (1 - restart) * (flow + lost * start)
    return {ids[i]: float(rank[i]) for i in np.nonzero(rank)[0]}


def relations(nodes: dict[str, dict], edges: list[dict]) -> dict[str, list[str]]:
    """Each card's relationships in words, from its own side: "is an example
    of Context-Preserving Chunking", "Sliding Window is an example of this"."""
    out: dict[str, list[str]] = {}
    for e in edges:
        a, b = nodes.get(e["source"]), nodes.get(e["target"])
        if a is None or b is None:
            continue
        label = (e.get("label") or "").strip() or "linked to"
        out.setdefault(a["id"], []).append(f"{label} {b.get('title') or 'a card'}")
        out.setdefault(b["id"], []).append(f"{a.get('title') or 'a card'} {label} this")
    return out


def linked_passage(base: str, said: list[str], limit: int = 8) -> str:
    """A card as the reranker reads it: what it says, then what it is linked
    to. A card that is only a title - "Sliding Window" - means little alone,
    and a reranker scores it near zero for a question about chunking that
    keeps context; with "Linked: is an example of Context-Preserving Chunking"
    it scores near the top (measured, DESIGN.md 31)."""
    return base + ("\nLinked: " + "; ".join(said[:limit]) if said else "")


def rerank_and_spread(question: str, fused: dict[str, float], nodes: dict[str, dict],
                      edges: list[dict], reranker: Reranker) -> dict[str, float]:
    """Stages 2 and 3: read the candidates, spread from the best read.

    Measured against the alternatives on a test set (DESIGN.md 31): reranking
    the fused list alone, reranking and then fusing with spreading from the
    fused seeds, spreading without reading. Letting the reranker choose where
    the spreading starts did best - recall@10 0.94 against 0.76 for the walk."""
    n = reranker.capacity()
    spread = graph_rank(top(fused, SEEDS), nodes, edges)
    pool = list(dict.fromkeys(ranked(fused)[:n] + ranked(spread)[:n]))
    said = relations(nodes, edges)
    docs = [linked_passage(card_passage(card_fields(nodes[i], nodes)), said.get(i, []))[:DOC_CHARS]
            for i in pool]
    read = dict(zip(pool, reranker.rerank(question.strip()[-QUERY_CHARS:], docs)))
    return fuse(ranked(read), ranked(graph_rank(top(read, SEEDS), nodes, edges)))


def candidates(index: BoardIndex, question: str, embedder: Optional[Embedder]
               ) -> tuple[dict[str, float], dict[str, float], list[str]]:
    """Fused candidates, the meaning similarities, and which searches ran."""
    used = []
    exact = index.exact(question)         # only quoted phrases (search_index.phrases)
    keyword = index.keyword(question)
    lists = [exact, keyword] if exact else [keyword]
    used += ["exact phrases"] if exact else []
    used += ["keywords"]
    if len(keyword) < 5:
        lists.append(index.typos(question))
        used.append("near-spellings")
    sims: dict[str, float] = {}
    if embedder is not None:
        try:
            qvec = embedder.embed([f"{QUERY_INSTRUCT}{question.strip()}"[:2000]])[0]
            meaning, sims = index.meaning(qvec)
            lists.append(meaning)
            done, total = index.progress()
            # while the background indexer is still at it, say how far it got
            used.append("meaning" if done >= total
                        else f"meaning (learned for {done:,} of {total:,} cards so far)")
        except Exception:
            pass
    return fuse(*lists), sims, used


def search(index: BoardIndex, question: str, nodes: dict[str, dict], edges: list[dict],
           cost: dict[str, int], embedder: Optional[Embedder] = None,
           budget: Optional[int] = None, reranker: Optional[Reranker] = None) -> Found:
    """The cards to send for this question, strongest first, within budget.
    `index` must be synced to the board; `cost` is each card's size in tokens
    as it would be sent."""
    budget = BUDGET if budget is None else budget      # read now, not when imported
    fused, sims, used = candidates(index, question, embedder)
    fused = {k: v for k, v in fused.items() if k in nodes}
    if not fused:                      # nothing matched at all: the board's shape is all there is
        fused = {nid: 0.0 for nid in list(nodes)[:SEEDS]}
    scored: Optional[dict[str, float]] = None
    if reranker is not None:
        try:
            scored = rerank_and_spread(question, fused, nodes, edges, reranker)
            used.append("a close reading along your links")
        except Exception:              # the reranker failed: the walk still answers
            scored = None
    if scored is None:
        scored = walk(top(fused, SEEDS), nodes, edges, sims)
        for nid, s in fused.items():   # matches beyond the seeds still count, below them
            scored.setdefault(nid, s * 0.5)

    chosen: list[str] = []
    spent = 0
    for nid in sorted(scored, key=scored.get, reverse=True):
        c = cost.get(nid, 50)
        if spent + c > budget:
            continue
        chosen.append(nid)
        spent += c
    how = ", ".join(used[:-1]) + f" and {used[-1]}" if len(used) > 1 else used[0]
    return Found(chosen, len(nodes), how, {n: scored[n] for n in chosen})


def outline(nodes: dict[str, dict], limit: int = 1500) -> str:
    """The board's groups as a short indented list, for broad questions."""
    kids: dict[Optional[str], list[dict]] = {}
    for n in nodes.values():
        kids.setdefault(n.get("parent"), []).append(n)
    lines: list[str] = []

    def count(nid: str) -> int:
        return sum(1 + count(k["id"]) for k in kids.get(nid, []))

    def visit(parent: Optional[str], depth: int) -> None:
        for n in kids.get(parent, []):
            if n["type"] != "meta":
                continue
            lines.append(f"{'  ' * depth}- {n.get('title') or 'Group'} ({count(n['id'])} cards)")
            visit(n["id"], depth + 1)

    visit(None, 0)
    loose = sum(1 for n in kids.get(None, []) if n["type"] != "meta")
    if loose:
        lines.append(f"- {loose} cards not in any group")
    text = "\n".join(lines)
    return text[:limit] + ("\n…" if len(text) > limit else "")
