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
import time
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


def picture_of(node: dict) -> Optional[str]:
    """The picture a card shows - an image, or a video's cover - by its hash."""
    c = node.get("content") or {}
    return c.get("sha256") if node.get("type") == "image" else (
        c.get("poster") if node.get("type") == "video" else None)


def file_of(node: dict) -> Optional[str]:
    """The PDF a file card holds, by its hash - the files whose text is read
    (DESIGN.md 48). Other files are found by their name and note only."""
    c = node.get("content") or {}
    name = str(c.get("filename") or c.get("alt") or node.get("title") or "")
    if node.get("type") == "file" and c.get("sha256") and (
            c.get("mime") == "application/pdf" or name.lower().endswith(".pdf")):
        return c["sha256"]
    return None


def noted(node: dict) -> Optional[str]:
    """The hash under which a card's derived words are kept: its picture's, or
    its PDF's."""
    return picture_of(node) or file_of(node)


class PictureNotes:
    """What each picture shows, in words, by the picture's hash (DESIGN.md 38):
    written once by the local model (indexer.py), so a picture is found by what
    is in it, not only by its title. Derived like the vectors - kept beside the
    search indexes in `<data>/search/`, never in a board, and regenerated if
    deleted.

    DESIGN.md 51: descriptions now write out all the text a picture holds, kept
    in `pictures-2.json`. The short ones of §38 (`pictures.json`) are still used
    until each picture is described again, so nothing becomes harder to find
    meanwhile; `done(sha)` says whether a picture has the new kind."""

    _all: dict[str, "PictureNotes"] = {}
    FILE = "pictures-2.json"
    OLD: Optional[str] = "pictures.json"

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._new: dict[str, str] = self._read(path)
        old = self._read(path.with_name(self.OLD)) if self.OLD else {}
        self.notes: dict[str, str] = {**old, **self._new}

    @staticmethod
    def _read(path: Path) -> dict[str, str]:
        try:
            got = json.loads(path.read_text(encoding="utf-8"))
            return got if isinstance(got, dict) else {}
        except (OSError, ValueError):
            return {}

    def done(self, sha: str) -> bool:
        """Described the current way - not only by an older, shorter description."""
        return sha in self._new

    @classmethod
    def get(cls, dd: DataDir) -> "PictureNotes":
        key = str(dd.root / "search" / cls.FILE)
        with _registry:
            if key not in cls._all:
                cls._all[key] = cls(Path(key))
            return cls._all[key]

    def put(self, sha: str, text: str) -> None:
        with self._lock:
            self._new = {**self._new, sha: text}
            self.notes = {**self.notes, sha: text}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._new), encoding="utf-8")
            tmp.replace(self.path)


class FileNotes(PictureNotes):
    """The text of each PDF on the boards, by the file's hash (DESIGN.md 48):
    read once by the background indexer, so a PDF is found by what it covers,
    not only by its name. Kept like the pictures' descriptions, in
    `<data>/search/files.json`; "" marks a PDF with no text to read (a scan)."""

    _all: dict[str, "PictureNotes"] = {}
    FILE = "files.json"
    OLD = None


def card_notes(dd: DataDir) -> dict[str, str]:
    """Every card's derived words, by hash: pictures' descriptions and PDFs' text."""
    return {**PictureNotes.get(dd).notes, **FileNotes.get(dd).notes}


def card_fields(node: dict, nodes: dict[str, dict],
                notes: Optional[dict[str, str]] = None) -> dict[str, str]:
    c = node.get("content") or {}
    trail, cur = [], nodes.get(node.get("parent") or "")
    while cur:
        trail.insert(0, cur.get("title") or "")
        cur = nodes.get(cur.get("parent") or "")
    body = [str(c.get(k) or "") for k in ("text", "summary", "excerpt", "alt", "filename", "url")]
    seen = (notes or {}).get(noted(node) or "")
    if seen:
        body.append(f"Picture shows: {seen}" if picture_of(node) else f"File holds: {seen}")
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
    """Open with `BoardIndex.get(dd, board_id)`.

    Two halves, kept up to date differently (DESIGN.md 33):

    - the **text** index, rebuilt in full whenever the board's revision moves
      on - 0.2 s at ten thousand cards - by `sync_text`, at every question;
    - the **vectors**, one per card, made from the card's passage. Embedding is
      the slow part - 92 s for ten thousand cards on a graphics card, far
      longer on a processor - so it is done a batch at a time (`embed_some`),
      in the background (indexer.py), and a search uses the vectors made so
      far. A card whose passage changed keeps its old vector until the new one
      is made; a card with none yet is still found by its words.

    `sync` does both at once, for the terminal, the evaluator and tests.
    Thread-safe: searches, the indexer and syncs share one instance.
    """

    SAVE_EVERY = 20.0          # seconds between writes of the vectors while embedding

    def __init__(self, folder: Path):
        self.folder = folder
        self.meta_path = folder / "meta.json"
        self.vec_path = folder / "vectors.npz"
        self.text_dir = folder / "text"
        self.revision = -1
        self.model = ""                                   # the vectors' embedding model
        self.index: Optional[tantivy.Index] = None
        self._vectors: dict[str, np.ndarray] = {}         # card -> its vector
        self.hashes: dict[str, str] = {}                  # card -> passage its vector was made from
        # the board as of the last sync_text: each card's passage, and its hash
        self._passages: Optional[dict[str, str]] = None
        self._wanted: dict[str, str] = {}
        self._snapshot: Optional[tuple[list[str], np.ndarray]] = None
        self.notes: dict[str, str] = {}          # pictures' descriptions (PictureNotes)
        self._notes_key = ""
        self._lock = threading.RLock()
        self._dirty = False
        self._saved_at = 0.0
        # each card's size in tokens as it would be sent, kept by ask.board_index
        self.costs: dict[str, int] = {}
        self.costs_revision = -1
        self._load()

    @classmethod
    def get(cls, dd: DataDir, board_id: str) -> "BoardIndex":
        key = str(index_dir(dd, board_id))
        with _registry:
            if key not in _open:
                _open[key] = cls(Path(key))
            return _open[key]

    @staticmethod
    def drop(dd: DataDir, board_id: str) -> None:
        """Forget and delete a board's index - with the board, or to rebuild."""
        key = str(index_dir(dd, board_id))
        with _registry:
            _open.pop(key, None)
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
            matrix = data["matrix"].astype(np.float32)
            self._vectors = {str(i): matrix[row] for row, i in enumerate(data["ids"])}
            self.index = tantivy.Index(_schema(), path=str(self.text_dir), reuse=True)
        except (OSError, ValueError, KeyError):
            self.revision = -1

    def _save(self) -> None:
        ids, matrix = self._matrix()
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.vec_path.with_suffix(".tmp.npz")
        np.savez(tmp, ids=np.array(ids, dtype=str), matrix=matrix)
        tmp.replace(self.vec_path)
        self.meta_path.write_text(json.dumps({
            "revision": self.revision, "model": self.model,
            "hashes": {i: self.hashes[i] for i in ids if i in self.hashes}}), encoding="utf-8")
        self._dirty, self._saved_at = False, time.time()

    def flush(self) -> None:
        """Write the vectors made so far, if any are unsaved."""
        with self._lock:
            if self._dirty:
                self._save()

    def _matrix(self) -> tuple[list[str], np.ndarray]:
        """The vectors as one matrix for searching - rebuilt only after a change."""
        if self._snapshot is None:
            ids = list(self._vectors)
            matrix = (np.stack([self._vectors[i] for i in ids]) if ids
                      else np.zeros((0, 0), dtype=np.float32))
            self._snapshot = (ids, matrix)
        return self._snapshot

    @property
    def ids(self) -> list[str]:
        return self._matrix()[0]

    @property
    def matrix(self) -> np.ndarray:
        return self._matrix()[1]

    # ------------------------------------------------------------- sync --
    def sync_text(self, nodes: dict[str, dict], revision: int, model: Optional[str] = None,
                  notes: Optional[dict[str, str]] = None) -> None:
        """Bring the text index up to the board's revision and note which cards
        need a vector. Fast enough for every question. `model` is the embedding
        model in use: a different one makes every vector stale, as vectors from
        two models cannot be compared; None leaves the vectors as they are.
        `notes` are the pictures' descriptions (PictureNotes): a new one
        re-indexes the text, and its card gets a new vector."""
        with self._lock:
            if model is not None and model != self.model:
                self._vectors, self.hashes, self.model = {}, {}, model
                self._snapshot, self._dirty = None, True
            if notes is not None:
                self.notes = notes
            shown = sorted((s, self.notes[s]) for n in nodes.values()
                           if (s := noted(n)) and s in self.notes)
            notes_key = hashlib.sha1(json.dumps(shown).encode("utf-8")).hexdigest()
            if (revision == self.revision and self.index is not None
                    and self._passages is not None and notes_key == self._notes_key):
                return
            fields = {nid: card_fields(n, nodes, self.notes) for nid, n in nodes.items()}
            if (revision != self.revision or self.index is None
                    or notes_key != self._notes_key):
                self._notes_key = notes_key
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
            self._passages = {nid: card_passage(f) for nid, f in fields.items()}
            self._wanted = {nid: hashlib.sha1(p.encode("utf-8")).hexdigest()
                            for nid, p in self._passages.items()}
            gone = [nid for nid in self._vectors if nid not in self._passages]
            for nid in gone:                       # cards no longer on the board
                del self._vectors[nid]
                self.hashes.pop(nid, None)
            if gone:
                self._snapshot = None
            changed = revision != self.revision or bool(gone)
            self.revision = revision
            if changed or self._dirty:
                self._save()

    def pending(self) -> list[str]:
        """Cards whose vector is missing or was made from an older passage."""
        with self._lock:
            return [nid for nid, h in self._wanted.items() if self.hashes.get(nid) != h]

    def progress(self) -> tuple[int, int]:
        """(cards with a current vector, cards on the board)."""
        with self._lock:
            done = sum(1 for nid, h in self._wanted.items() if self.hashes.get(nid) == h)
            return done, len(self._wanted)

    def embed_some(self, embedder, limit: int = 16, cards: Optional[list[str]] = None) -> int:
        """Embed up to `limit` of the cards still waiting - or of `cards` - and
        return how many still wait. The embedding runs outside the lock, so
        searches go on meanwhile; a card that changed again while it was being
        embedded keeps waiting."""
        with self._lock:
            todo = [n for n in (cards if cards is not None else self.pending())
                    if self.hashes.get(n) != self._wanted.get(n, self.hashes.get(n))][:limit]
            texts = [self._passages[n] for n in todo] if self._passages else []
            made_from = [self._wanted[n] for n in todo]
            model = self.model
        if todo:
            vectors = embedder.embed(texts)
            with self._lock:
                if model == self.model:
                    for nid, h, v in zip(todo, made_from, vectors):
                        if self._wanted.get(nid) == h:
                            self._vectors[nid] = np.asarray(v, dtype=np.float32)
                            self.hashes[nid] = h
                    self._snapshot, self._dirty = None, True
                    if time.time() - self._saved_at > self.SAVE_EVERY:
                        self._save()
        return len(self.pending())

    def sync(self, nodes: dict[str, dict], revision: int, embedder, model: str,
             notes: Optional[dict[str, str]] = None) -> None:
        """Text and every vector, now - for the terminal, the evaluator, tests."""
        self.sync_text(nodes, revision, model if embedder is not None else None, notes)
        if embedder is not None:
            while self.embed_some(embedder, 64):
                pass
        self.flush()

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
        """Exact cosine against every card that has a vector so far - the
        vectors are unit length."""
        with self._lock:
            ids, matrix = self._matrix()
        if not ids or matrix.size == 0:
            return [], {}
        sims = matrix @ np.asarray(qvec, dtype=np.float32)
        order = np.argsort(-sims)[:limit]
        return [ids[i] for i in order], {ids[i]: float(sims[i]) for i in range(len(ids))}
