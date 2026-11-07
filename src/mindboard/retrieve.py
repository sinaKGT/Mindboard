"""Finding the cards a question is about: GraphRAG for a mind map
(DESIGN.md 29, 30).

A board too big to send whole is searched instead, against the board's search
index (search_index.py): BM25 keywords with stemming and meaning by embedding,
fused by reciprocal rank; phrases the person quoted are searched exactly and
join the fusion; a typo search joins when the keywords found little. The best cards seed a walk along
the person's own typed relationships and into groups; the strongest cards that
fit a token budget go to the model, with a short outline of the board's groups
so a broad question still sees its shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from .search_index import QUERY_INSTRUCT, BoardIndex

WHOLE_UNDER = 4000      # tokens of card text under which a board is sent whole (pictures come on top)
BUDGET = 5000           # tokens of cards a search may send
SEEDS = 10              # the best fused cards, from which the walk starts
RRF_K = 60


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


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
            used.append("meaning")
        except Exception:
            pass
    return fuse(*lists), sims, used


def search(index: BoardIndex, question: str, nodes: dict[str, dict], edges: list[dict],
           cost: dict[str, int], embedder: Optional[Embedder] = None,
           budget: Optional[int] = None) -> Found:
    """The cards to send for this question, strongest first, within budget.
    `index` must be synced to the board; `cost` is each card's size in tokens
    as it would be sent."""
    budget = BUDGET if budget is None else budget      # read now, not when imported
    fused, sims, used = candidates(index, question, embedder)
    fused = {k: v for k, v in fused.items() if k in nodes}
    if not fused:                      # nothing matched at all: the board's shape is all there is
        fused = {nid: 0.0 for nid in list(nodes)[:SEEDS]}
    seeds = dict(sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:SEEDS])
    scored = walk(seeds, nodes, edges, sims)
    for nid, s in fused.items():       # matches beyond the seeds still count, below them
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
