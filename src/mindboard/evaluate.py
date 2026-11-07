"""Measuring retrieval: does a search find the cards a question is about?
(DESIGN.md 30)

A test set is the person's own: questions about one of their boards, each
with the cards a good search should return. It lives in their data folder
(`<data>/evals/`), never in the repository - it names their cards. This module
only measures; it writes nothing to any board.

For each question and each strategy it records where the right cards came:

- hit@5      a right card is in the first five
- recall@10  share of the right cards in the first ten
- MRR        1 / the rank of the first right card (1.0 when it comes first)
- sent       share of the right cards among the cards that would be sent to
             the model, within the token budget - what the answer can use
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import ask, retrieve
from .boards import conn_for, list_boards
from .ops import read_graph
from .paths import DataDir

# A strategy: (index, question, nodes, edges, cost, embedder) ->
# (ranked ids, the ids that would be sent)
Strategy = Callable[..., tuple[list[str], list[str]]]


@dataclass
class Score:
    question: str
    kind: str
    gold: list[str]
    ranked: list[str]
    sent: list[str]
    seconds: float

    @property
    def hit5(self) -> float:
        return 1.0 if set(self.ranked[:5]) & set(self.gold) else 0.0

    @property
    def recall10(self) -> float:
        return len(set(self.ranked[:10]) & set(self.gold)) / len(self.gold)

    @property
    def mrr(self) -> float:
        for rank, nid in enumerate(self.ranked, 1):
            if nid in self.gold:
                return 1.0 / rank
        return 0.0

    @property
    def sent_recall(self) -> float:
        return len(set(self.sent) & set(self.gold)) / len(self.gold)


@dataclass
class Report:
    strategy: str
    scores: list[Score] = field(default_factory=list)

    def mean(self, attr: str, kind: Optional[str] = None) -> float:
        picked = [s for s in self.scores if kind is None or s.kind == kind]
        return sum(getattr(s, attr) for s in picked) / len(picked) if picked else 0.0


def load_set(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    for q in data["questions"]:
        if not q.get("gold"):
            raise ValueError(f"no gold cards for: {q['question']}")
    return data


def default_set_path(dd: DataDir, board_title: str) -> Path:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in board_title).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return dd.root / "evals" / f"{slug}.json"


# ------------------------------------------------------------ strategies --
# Each one alone, then fused, then what is actually sent. The first baseline -
# SQLite FTS5 and SQLite-stored vectors, before this index - is kept as
# numbers in DESIGN.md 30, not as code.

def _keyword(index, q, nodes, edges, cost, embedder):
    ranked = index.keyword(q)
    return ranked, ranked


def _exact(index, q, nodes, edges, cost, embedder):
    ranked = index.exact(q)
    return ranked, ranked


def _meaning(index, q, nodes, edges, cost, embedder):
    ranked, _ = index.meaning(embedder.embed([f"{retrieve.QUERY_INSTRUCT}{q}"])[0])
    return ranked, ranked


def _fused(index, q, nodes, edges, cost, embedder):
    fused, _, _ = retrieve.candidates(index, q, embedder)
    ranked = sorted(fused, key=fused.get, reverse=True)
    return ranked, ranked


def _sent(index, q, nodes, edges, cost, embedder):
    found = retrieve.search(index, q, nodes, edges, cost, embedder)
    return found.ids, found.ids


STRATEGIES: dict[str, Strategy] = {
    "keyword (Tantivy BM25)": _keyword,
    "exact (quoted phrases only)": _exact,
    "meaning (exact cosine)": _meaning,
    "RRF of the three": _fused,
    "RRF + walk (sent)": _sent,
}


def run(dd: DataDir, test_set: dict, embedder, model: str,
        strategies: Optional[dict[str, Strategy]] = None) -> list[Report]:
    strategies = strategies or STRATEGIES
    board = next((b for b in list_boards(dd) if b["id"] == test_set["board"]), None)
    if board is None:
        raise ValueError("the test set's board is not in this data folder")
    conn = conn_for(dd, board["id"])
    graph = read_graph(conn, board["id"])
    nodes = {n["id"]: n for n in graph["nodes"]}
    cost = {i: ask.estimate(ask.card_text(n, nodes)) for i, n in nodes.items()}
    index = ask.board_index(dd, conn, board["id"], nodes, embedder, model)
    reports = []
    for name, strategy in strategies.items():
        report = Report(name)
        for q in test_set["questions"]:
            began = time.time()
            ranked, sent = strategy(index, q["question"], nodes, graph["edges"], cost, embedder)
            report.scores.append(Score(q["question"], q["kind"], q["gold"], ranked, sent,
                                       time.time() - began))
        reports.append(report)
    return reports


def table(reports: list[Report], kinds: list[str]) -> str:
    """The summary, one row per strategy."""
    head = f"{'strategy':28} {'hit@5':>6} {'rec@10':>7} {'MRR':>6} {'sent':>6}   " + \
           "  ".join(f"{k[:10]:>10}" for k in kinds) + "   ms/q"
    lines = [head, "-" * len(head)]
    for r in reports:
        per_kind = "  ".join(f"{r.mean('recall10', k):>10.2f}" for k in kinds)
        ms = 1000 * sum(s.seconds for s in r.scores) / max(len(r.scores), 1)
        lines.append(f"{r.strategy:28} {r.mean('hit5'):>6.2f} {r.mean('recall10'):>7.2f} "
                     f"{r.mean('mrr'):>6.2f} {r.mean('sent_recall'):>6.2f}   {per_kind}   {ms:5.0f}")
    lines.append(f"(per-kind columns: recall@10 for {', '.join(kinds)})")
    return "\n".join(lines)
