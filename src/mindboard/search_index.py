"""One board's search index: Tantivy for exact and keyword search, an exact
vector matrix for search by meaning (DESIGN.md 30).

The board's SQLite stays the record - every change still goes through the
op-log. This index is derived from it: rebuilt when the board's revision moves
on, never exported, never written by a feature. It lives outside the board's
folder (`<data>/search/<board>/`), so an index held open can never stop a board
from being deleted - the reason `opened/` lives there too (§22).

Tantivy brings what a SQLite FTS table did badly: English stemming, so
"evaluating" finds "evaluation"; phrase queries for exact matches; per-field
boosts, so a title counts double; and typo-tolerant terms. Vectors are searched
exactly with numpy: at mind-map scale an approximate index would trade accuracy
for speed nobody needs.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
from pathlib import Path
from typing import Optional

import numpy as np
import tantivy

from .paths import DataDir

FIELDS = ["title", "body", "path"]
BOOSTS = {"title": 2.0, "body": 1.0, "path": 0.5}
LIMIT = 40                     # candidates from each search
QUERY_INSTRUCT = ("Instruct: Given a question about a person's mind map, retrieve the "
                  "cards that help answer it\nQuery:")
STOP = set("""a an and are as at be but by can do does for from has have how i in is it its
me my of on or our so that the their them then there these they this to was we what when
where which who why will with you your about into over than also any all more most know
tell show give explain describe""".split())

_locks: dict[str, threading.Lock] = {}
_open: dict[str, "BoardIndex"] = {}
_registry = threading.Lock()


def index_dir(dd: DataDir, board_id: str) -> Path:
    return dd.root / "search" / board_id


def terms(question: str) -> list[str]:
    """The words worth searching for: lowercase, no short or common words.
    Built into queries by hand - a person's text is never handed to the query
    language, where a stray quote or colon is syntax."""
    words = re.findall(r"[0-9a-z]+", question.lower())
    return [w for w in words if len(w) > 2 and w not in STOP][:16]


def phrases(question: str) -> list[list[str]]:
    """Exact phrases: what the person put in quotes.

    Every pair of neighbouring words was tried as a phrase too, and measured
    (DESIGN.md 30): pairs like "rag system" match many generic cards, and fused
    at any weight they made the ranking worse. Quoting is a clear signal that
    the exact words matter; a guessed pair is not."""
    out = [terms(q) for q in re.findall(r'"([^"]+)"', question)]
    seen: set[tuple[str, ...]] = set()
    unique = []
    for p in out:
        if len(p) >= 2 and tuple(p) not in seen:
            seen.add(tuple(p))
            unique.append(p)
    return unique


def card_fields(node: dict, nodes: dict[str, dict]) -> dict[str, str]:
    c = node.get("content") or {}
    trail, cur = [], nodes.get(node.get("parent") or "")
    while cur:
        trail.insert(0, cur.get("title") or "")
        cur = nodes.get(cur.get("parent") or "")
    body = [str(c.get(k) or "") for k in ("text", "summary", "excerpt", "alt", "filename", "url")]
    return {"title": node.get("title") or "", "body": "\n".join(b for b in body if b),
            "path": " / ".join(t for t in trail if t)}


def card_passage(f: dict[str, str]) -> str:
    """What a card says, as one passage to embed."""
    head = f["title"] + (f"\nin {f['path']}" if f["path"] else "")
    return (head + ("\n" + f["body"] if f["body"] else ""))[:1500]


def _schema() -> tantivy.Schema:
    sb = tantivy.SchemaBuilder()
    sb.add_text_field("id", stored=True, tokenizer_name="raw")
    for name in FIELDS:
        sb.add_text_field(name, tokenizer_name="en_stem")
    return sb.build()


class BoardIndex:
    """Open with `BoardIndex.get(dd, board_id)`; call `sync` before searching."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.meta_path = folder / "meta.json"
        self.vec_path = folder / "vectors.npz"
        self.text_dir = folder / "text"
        self.revision = -1
        self.model = ""
        self.ids: list[str] = []
        self.matrix = np.zeros((0, 0), dtype=np.float32)
        self.hashes: dict[str, str] = {}
        self.index: Optional[tantivy.Index] = None
        self._load()

    @classmethod
    def get(cls, dd: DataDir, board_id: str) -> "BoardIndex":
        key = str(index_dir(dd, board_id))
        with _registry:
            if key not in _open:
                _open[key] = cls(Path(key))
                _locks[key] = threading.Lock()
            return _open[key]

    @staticmethod
    def drop(dd: DataDir, board_id: str) -> None:
        """Forget and delete a board's index - with the board, or to rebuild."""
        key = str(index_dir(dd, board_id))
        with _registry:
            _open.pop(key, None)
            _locks.pop(key, None)
        import gc

        gc.collect()                        # let go of Tantivy's open files first
        shutil.rmtree(key, ignore_errors=True)

    # ---------------------------------------------------------- storage --
    def _load(self) -> None:
        try:
            meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            data = np.load(self.vec_path, allow_pickle=False)
            self.revision, self.model = meta["revision"], meta["model"]
            self.hashes = meta["hashes"]
            self.ids = [str(i) for i in data["ids"]]
            self.matrix = data["matrix"].astype(np.float32)
            self.index = tantivy.Index(_schema(), path=str(self.text_dir), reuse=True)
        except (OSError, ValueError, KeyError):
            self.revision = -1

    def _save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.vec_path.with_suffix(".tmp.npz")
        np.savez(tmp, ids=np.array(self.ids, dtype=str), matrix=self.matrix)
        tmp.replace(self.vec_path)
        self.meta_path.write_text(json.dumps({"revision": self.revision, "model": self.model,
                                              "hashes": self.hashes}), encoding="utf-8")

    # ------------------------------------------------------------- sync --
    def sync(self, nodes: dict[str, dict], revision: int, embedder, model: str) -> None:
        """Bring the index up to the board's revision. Text is re-indexed whole
        - milliseconds at mind-map scale; vectors only for cards whose passage
        changed, or all of them if the embedding model did."""
        lock = _locks.get(str(self.folder)) or threading.Lock()
        with lock:
            if revision == self.revision and model == self.model and self.index is not None:
                return
            fields = {nid: card_fields(n, nodes) for nid, n in nodes.items()}

            self.text_dir.mkdir(parents=True, exist_ok=True)
            index = tantivy.Index(_schema(), path=str(self.text_dir), reuse=True)
            writer = index.writer(heap_size=30_000_000, num_threads=1)
            writer.delete_all_documents()
            for nid, f in fields.items():
                writer.add_document(tantivy.Document(id=nid, **f))
            writer.commit()
            writer.wait_merging_threads()
            index.reload()
            self.index = index

            if embedder is not None:
                passages = {nid: card_passage(f) for nid, f in fields.items()}
                hashes = {nid: hashlib.sha1(p.encode("utf-8")).hexdigest()
                          for nid, p in passages.items()}
                old = {nid: i for i, nid in enumerate(self.ids)} if model == self.model else {}
                todo = [nid for nid in passages if nid not in old or self.hashes.get(nid) != hashes[nid]]
                fresh = dict(zip(todo, embedder.embed([passages[n] for n in todo]))) if todo else {}
                ids = list(passages)
                dim = len(next(iter(fresh.values()))) if fresh else self.matrix.shape[1]
                matrix = np.zeros((len(ids), dim), dtype=np.float32)
                for row, nid in enumerate(ids):
                    matrix[row] = fresh[nid] if nid in fresh else self.matrix[old[nid]]
                self.ids, self.matrix, self.hashes, self.model = ids, matrix, hashes, model
            self.revision = revision
            self._save()

    # ----------------------------------------------------------- search --
    def _run(self, query, limit: int = LIMIT) -> list[tuple[str, float]]:
        if self.index is None:
            return []
        searcher = self.index.searcher()
        return [(searcher.doc(addr)["id"][0], score)
                for score, addr in searcher.search(query, limit).hits]

    def keyword(self, question: str, limit: int = LIMIT) -> list[str]:
        """BM25 over title (x2), body and group path, with English stemming."""
        words = terms(question)
        if not words or self.index is None:
            return []
        query = self.index.parse_query(" ".join(words), FIELDS, field_boosts=BOOSTS)
        return [nid for nid, _ in self._run(query, limit)]

    # Phrases and typos go through Tantivy's query parser, not its hand-built
    # phrase and fuzzy queries: those take words as they are, while the index
    # holds them stemmed ("retrieval" is stored as "retriev"), so they never
    # matched. The parser stems the query exactly as the index was stemmed.
    # The words were cleaned to letters and digits by terms(), so quoting them
    # is safe.

    def exact(self, question: str, limit: int = LIMIT) -> list[str]:
        """Cards containing the question's phrases word for word."""
        found = phrases(question)
        if not found or self.index is None:
            return []
        query = self.index.parse_query(" ".join(f'"{" ".join(p)}"' for p in found), FIELDS,
                                       field_boosts=BOOSTS)
        return [nid for nid, _ in self._run(query, limit)]

    def typos(self, question: str, limit: int = LIMIT) -> list[str]:
        """A fallback for misspellings: terms within one edit. Tantivy scores
        every such hit alike, so it only joins the fusion when the keyword
        search found little."""
        long_words = [w for w in terms(question) if len(w) >= 5]
        if not long_words or self.index is None:
            return []
        query = self.index.parse_query(" ".join(long_words), ["title", "body"],
                                       fuzzy_fields={"title": (False, 1, True),
                                                     "body": (False, 1, True)})
        return [nid for nid, _ in self._run(query, limit)]

    def meaning(self, qvec: list[float], limit: int = LIMIT) -> tuple[list[str], dict[str, float]]:
        """Exact cosine against every card - the vectors are unit length."""
        if not self.ids or self.matrix.size == 0:
            return [], {}
        sims = self.matrix @ np.asarray(qvec, dtype=np.float32)
        order = np.argsort(-sims)[:limit]
        return ([self.ids[i] for i in order],
                {self.ids[i]: float(sims[i]) for i in range(len(self.ids))})
