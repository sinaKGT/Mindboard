"""Finding the cards a question is about: GraphRAG for a mind map
(DESIGN.md 29).

A board too big to send whole is searched instead. Two searches - by meaning
(embeddings) and by wording (the full-text index the app already keeps) - are
fused; the best cards seed a walk along the person's own typed relationships
and into groups; the strongest cards that fit a token budget go to the model,
with a short outline of the board's groups so a broad question still sees its
shape.

Card vectors are a derived index, like the full-text one: regenerable, never
exported, never an op. Each is kept with a hash of the text it was made from,
so a card is embedded again only when what it says has changed.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from array import array
from dataclasses import dataclass, field
from typing import Optional, Protocol

QUERY_INSTRUCT = ("Instruct: Given a question about a person's mind map, retrieve the "
                  "cards that help answer it\nQuery:")
WHOLE_UNDER = 4000      # tokens of card text under which a board is sent whole (pictures come on top)
BUDGET = 5000           # tokens of cards a search may send
CANDIDATES = 24         # from each search
SEEDS = 10              # the best of them, from which the walk starts
RRF_K = 60
STOP = set("""a an and are as at be but by can do does for from has have how i in is it its
me my of on or our so that the their them then there these they this to was we what when
where which who why will with you your about into over than also any all more most""".split())


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass
class Found:
    ids: list[str]                                   # in the order they go to the model
    total: int                                       # cards on the board
    how: str                                         # "meaning and wording" | "wording"
    scores: dict[str, float] = field(default_factory=dict)


# ----------------------------------------------------------------- index --

def card_doc(node: dict, nodes: dict[str, dict]) -> str:
    """What a card says, as one passage to embed: title, where it sits, its words."""
    c = node.get("content") or {}
    parts = [node.get("title") or ""]
    trail, cur = [], nodes.get(node.get("parent") or "")
    while cur:
        trail.insert(0, cur.get("title") or "")
        cur = nodes.get(cur.get("parent") or "")
    if trail:
        parts.append("in " + " / ".join(t for t in trail if t))
    for key in ("text", "summary", "excerpt", "alt", "filename", "url"):
        value = str(c.get(key) or "").strip()
        if value and value not in parts:
            parts.append(value)
    return "\n".join(p for p in parts if p)[:1500]


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _pack(vec: list[float]) -> bytes:
    return array("f", vec).tobytes()


def _unpack(blob: bytes) -> list[float]:
    a = array("f")
    a.frombytes(blob)
    return a.tolist()


def refresh(conn: sqlite3.Connection, nodes: dict[str, dict], embedder: Embedder,
            model: str) -> dict[str, list[float]]:
    """Every card's vector, embedding only the cards that are new or changed."""
    have = {r["node_id"]: r for r in conn.execute(
        "SELECT node_id, model, text_hash, vec FROM node_embedding")}
    docs = {nid: card_doc(n, nodes) for nid, n in nodes.items()}
    stale = [nid for nid, doc in docs.items()
             if nid not in have or have[nid]["model"] != model
             or have[nid]["text_hash"] != _hash(doc)]
    vectors = {nid: _unpack(have[nid]["vec"]) for nid in docs if nid not in stale}
    if stale:
        fresh = embedder.embed([docs[nid] for nid in stale])
        for nid, vec in zip(stale, fresh):
            vectors[nid] = vec
            conn.execute(
                "INSERT OR REPLACE INTO node_embedding (node_id, model, vec, text_hash) "
                "VALUES (?,?,?,?)", (nid, model, _pack(vec), _hash(docs[nid])))
    return vectors


# ---------------------------------------------------------------- search --

def _terms(question: str) -> list[str]:
    words = re.findall(r"\w+", question.lower())
    return [w for w in words if len(w) > 2 and w not in STOP][:12]


def by_wording(conn: sqlite3.Connection, question: str, scope: set[str]) -> list[str]:
    terms = _terms(question)
    if not terms:
        return []
    match = " OR ".join(f'"{t}"*' for t in terms)
    try:
        rows = conn.execute("SELECT node_id FROM node_fts WHERE node_fts MATCH ? "
                            "ORDER BY bm25(node_fts) LIMIT ?", (match, CANDIDATES * 3)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [r["node_id"] for r in rows if r["node_id"] in scope][:CANDIDATES]


def by_meaning(qvec: list[float], vectors: dict[str, list[float]],
               scope: set[str]) -> tuple[list[str], dict[str, float]]:
    sims = {nid: sum(a * b for a, b in zip(qvec, v)) for nid, v in vectors.items() if nid in scope}
    ranked = sorted(sims, key=sims.get, reverse=True)[:CANDIDATES]
    return ranked, sims


def fuse(*rankings: list[str]) -> dict[str, float]:
    """Reciprocal rank fusion: a card near the top of either list rises; a card
    near the top of both rises most. No scores to calibrate between searches."""
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


def search(conn: sqlite3.Connection, question: str, nodes: dict[str, dict], edges: list[dict],
           cost: dict[str, int], embedder: Optional[Embedder] = None,
           model: str = "", budget: int = BUDGET) -> Found:
    """The cards to send for this question, strongest first, within budget.

    `cost` is each card's size in tokens as it would be sent. Without an
    embedder - not installed, or not answering - it searches by wording alone
    rather than fail.
    """
    scope = set(nodes)
    sims: dict[str, float] = {}
    meaning: list[str] = []
    how = "wording"
    if embedder is not None:
        try:
            vectors = refresh(conn, nodes, embedder, model)
            qvec = embedder.embed([f"{QUERY_INSTRUCT}{question.strip()}"[:2000]])[0]
            meaning, sims = by_meaning(qvec, vectors, scope)
            how = "meaning and wording"
        except Exception:
            meaning, sims, how = [], {}, "wording"
    fused = fuse(meaning, by_wording(conn, question, scope))
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
