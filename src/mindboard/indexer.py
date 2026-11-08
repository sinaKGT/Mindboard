"""Embedding a board's cards in the background, so a question never waits for
it (DESIGN.md 33).

Embedding every card of a big board is the slow part of search - 92 s for ten
thousand cards on a graphics card, far longer on a processor - and it used to
happen at the board's first question, which waited for all of it. Now a board
is indexed while the person works: when it is opened, and again shortly after
each change. A question searches with the vectors made so far; a card without
one yet is still found by its words, its links and the reranker.

One thread for the whole app, one board at a time, a few cards per request so
a question's own embedding is never stuck behind a long batch. It reads boards
and writes only the search index - never a board.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from .boards import conn_for
from .ops import read_graph
from .paths import DataDir
from .search_index import BoardIndex, FileNotes, PictureNotes, card_notes, file_of, picture_of

log = logging.getLogger("mindboard.indexer")

QUIET = 2.0         # seconds after the last change before a board is indexed
BATCH = 16          # cards per request to the embedding server


class Indexer:
    """`want(board)` whenever a board is opened or changed; the rest happens on
    the indexer's own thread. `embedder()` gives the embedding model, or None
    when it is not installed - then there is nothing to do."""

    def __init__(self, dd: DataDir, embedder: Callable[[], Optional[object]],
                 quiet: float = QUIET, batch: int = BATCH,
                 describer: Optional[Callable[[], Optional[object]]] = None):
        self.dd = dd
        self.embedder = embedder
        # the chat model, to describe pictures - only while it is loaded anyway
        self.describer = describer
        self.quiet = quiet
        self.batch = batch
        self._wanted: dict[str, float] = {}       # board -> when it was last asked for
        self._cv = threading.Condition()
        self._thread: Optional[threading.Thread] = None
        self._stopping = False
        self.busy_with: Optional[str] = None

    def want(self, board_id: str) -> None:
        with self._cv:
            if self._stopping:
                return
            self._wanted[board_id] = time.time()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="mindboard-indexer",
                                                daemon=True)
                self._thread.start()
            self._cv.notify()

    def stop(self) -> None:
        with self._cv:
            self._stopping = True
            self._cv.notify()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def idle(self, timeout: float = 30.0) -> bool:
        """Wait until nothing is wanted or being indexed - for tests."""
        end = time.time() + timeout
        while time.time() < end:
            with self._cv:
                if not self._wanted and self.busy_with is None:
                    return True
            time.sleep(0.05)
        return False

    # ----------------------------------------------------------- thread --
    def _next(self) -> Optional[str]:
        """The board whose last change is at least `quiet` seconds old - so a
        burst of edits is indexed once, after it."""
        with self._cv:
            while not self._stopping:
                if self._wanted:
                    bid, at = min(self._wanted.items(), key=lambda kv: kv[1])
                    wait = at + self.quiet - time.time()
                    if wait <= 0:
                        del self._wanted[bid]
                        self.busy_with = bid
                        return bid
                    self._cv.wait(wait)
                else:
                    self._cv.wait()
            return None

    def _run(self) -> None:
        while True:
            bid = self._next()
            if bid is None:
                return
            try:
                self._index(bid)
            except Exception:
                # a board deleted meanwhile, a model that would not start: the
                # next change or question asks again
                log.exception("indexing board %s", bid)
            finally:
                with self._cv:
                    self.busy_with = None

    def _changed_again(self, bid: str) -> bool:
        with self._cv:
            return bid in self._wanted or self._stopping

    def _index(self, bid: str) -> None:
        model = self.embedder()
        if model is None:
            return
        conn = conn_for(self.dd, bid)
        row = conn.execute("SELECT revision FROM board WHERE id=?", (bid,)).fetchone()
        if row is None:
            return                                  # the board is gone
        nodes = {n["id"]: n for n in read_graph(conn, bid)["nodes"]}
        index = BoardIndex.get(self.dd, bid)
        notes = PictureNotes.get(self.dd)
        self._read_files(bid, conn, nodes)
        index.sync_text(nodes, row["revision"], model.inst.embedder.name, card_notes(self.dd))
        try:
            # stop between batches if the board changed again: it is back in
            # the queue, and its next round starts from the new cards
            while index.pending() and not self._changed_again(bid):
                index.embed_some(model, self.batch)
            if self._describe(bid, conn, nodes, notes):
                index.sync_text(nodes, row["revision"], model.inst.embedder.name, card_notes(self.dd))
                while index.pending() and not self._changed_again(bid):
                    index.embed_some(model, self.batch)
        finally:
            index.flush()

    def _read_files(self, bid: str, conn, nodes: dict) -> None:
        """Read the text of the board's PDFs not yet read (DESIGN.md 48). No
        model is needed - a few tenths of a second a file - so it is done first,
        and the PDFs are searchable by what they cover from this round on."""
        from .assets import pdf_text, resolve

        files = FileNotes.get(self.dd)
        for sha in dict.fromkeys(s for n in nodes.values() if (s := file_of(n)) and s not in files.notes):
            if self._changed_again(bid):
                return
            path = resolve(self.dd, conn, bid, sha)
            if path is not None:
                files.put(sha, pdf_text(path))

    def _describe(self, bid: str, conn, nodes: dict, notes: PictureNotes) -> bool:
        """Describe the board's pictures not yet described (DESIGN.md 38) -
        only while the chat model is loaded anyway, so opening a board never
        loads it, and never while it answers a question: then it stops, and
        the next round carries on. True if any were described."""
        from .ask import describe_picture

        chat = self.describer() if self.describer else None
        if chat is None or not chat.running():
            return False
        todo = list(dict.fromkeys(s for n in nodes.values()
                                  if (s := picture_of(n)) and s not in notes.notes))
        done = False
        for sha in todo:
            if self._changed_again(bid) or not chat._asking.acquire(blocking=False):
                break
            try:
                text = describe_picture(chat, self.dd, conn, bid, sha)
            finally:
                chat._asking.release()
            if text:
                notes.put(sha, text)
                done = True
        return done
