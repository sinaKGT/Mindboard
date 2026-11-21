"""Asking one file (DESIGN.md 64).

A file the person wants to ask about - a PDF, Word document, Excel workbook,
CSV or text file, from a board or added straight into the chat - is never put
into the model's context whole. It is *prepared* first: read into passages
(chunks), its figures described by the model, a keyword index (BM25, Tantivy,
in memory) and a vector per passage made. Then each question is searched
inside that file only, the way a board is searched - exact phrases, keywords
and meaning fused by reciprocal rank, then reranked - and only the passages
that match best go to the model, each with where it is (p. 12; Sales, rows
2-21; a heading).

Multimodal, smartly: a PDF page that is mostly a figure, a table, formulas or
a scan goes to the model as a picture of the page when one of its passages is
among the best - text-only pages go as text. Figures are described once while
preparing, so a question can find them by what they show.

Temporary: a prepared file lives in memory and under `<data>/tmp/filechat`,
and is gone when its conversation is closed, after hours unused, or when
Mindboard starts again. Nothing here touches a board or its search.
"""
from __future__ import annotations

import base64
import csv
import io
import logging
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import tantivy

from . import retrieve
from .paths import DataDir
from .search_index import phrases, terms

log = logging.getLogger(__name__)

KINDS = {".pdf": "pdf", ".docx": "docx", ".xlsx": "xlsx", ".xlsm": "xlsx", ".csv": "csv",
         ".txt": "text", ".md": "text"}
READABLE = "PDF, Word (.docx), Excel (.xlsx), CSV and text files"

CHUNK_CHARS = 1100             # a passage: a paragraph or two
OVERLAP = 150                  # carried into the next, so a sentence is not cut in two
MAX_CHUNKS = 6000              # a very long file: the rest is left out, and said so
MAX_ROWS = 20000               # rows of a sheet read
FIGURES_MAX = 24               # figures described while preparing
BUDGET = 5000                  # tokens of passages sent with a question
PASSAGES_MAX = 12
PAGE_PICTURES = 2              # pages sent as pictures with one question
CANDIDATES = 60                # from each search
RERANK = 30                    # of the fused candidates, reranked
EMBED_BATCH = 32
IDLE_HOURS = 6.0
MAX_BYTES = 200 * 1024 * 1024
PICTURE_SIDE = 1024            # as a card's picture (ask.MAX_SIDE)

FILE_INSTRUCT = ("Instruct: Given a question about a document, retrieve the passages of the "
                 "document that help answer it\nQuery:")
FIGURE_PROMPT = ("This is {where} of a document. Describe its figures, charts, diagrams and "
                 "tables: what each shows, its title, labels, axis names, key numbers and "
                 "trends. Write formulas in LaTeX between dollar signs. If the page is a scan "
                 "of text, write out the text. If it holds nothing but ordinary text, answer "
                 "only: TEXT ONLY. No preamble.")
FIGURE_TOKENS = 450
FIGURE_SECONDS = 120.0         # one figure's description; past this it is skipped
OVERVIEW = re.compile(r"\b(summar\w*|overview|about|main (points|ideas|idea)|gist|tl;?dr|"
                      r"outline|key (points|findings|takeaways)|what is (this|it))\b", re.I)

PDFIUM = threading.Lock()      # pdfium is not thread-safe: one call at a time


class FileError(Exception):
    """Said to the person: what is wrong with the file."""


@dataclass
class Chunk:
    where: str                         # "p. 12", "Sales, rows 2-21", a heading
    text: str
    page: Optional[int] = None         # a PDF page, from 1
    visual: bool = False               # its page is better seen than read
    figure: bool = False               # a figure's description, not the file's words
    image: Optional[bytes] = None      # a Word document's picture, to show the model


# --------------------------------------------------------------- reading --

def split(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP) -> list[str]:
    """Passages of about `size` characters, cut after a sentence where one ends
    near the cut, each starting a little before the last one ended."""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = max(text.rfind(". ", start + size // 2, end), text.rfind("\n", start + size // 2, end))
            if cut > 0:
                end = cut + 1
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return out


def _tableish(text: str) -> bool:
    """Mostly numbers: a table, whose columns text extraction jumbles."""
    if len(text) < 80:
        return False
    digits = sum(ch.isdigit() for ch in text)
    return digits / max(len(text), 1) > 0.22


MATH = re.compile(r"[∑∫∂√≤≥≈≠±×÷∞∏αβγδθλμσφωΔΣΩ]|\b[a-z]\^|\\frac|\\sum")


def _mathish(text: str) -> bool:
    return len(MATH.findall(text)) >= 4


def read_pdf(path: Path) -> tuple[list[Chunk], list[int], int]:
    """A PDF's passages, the pages worth describing (figures, scans), and its
    page count. Read with pdfium: faster than pypdf, and it can count the
    pictures and drawn lines on a page - a chart drawn as lines has no picture
    in it, but hundreds of paths."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    chunks: list[Chunk] = []
    figures: list[int] = []
    with PDFIUM:
        try:
            pdf = pdfium.PdfDocument(str(path))
        except Exception as exc:
            raise FileError("this PDF could not be opened - it may be damaged or locked "
                            "with a password") from exc
        try:
            pages = len(pdf)
            for i in range(pages):
                page = pdf[i]
                try:
                    tp = page.get_textpage()
                    text = " ".join((tp.get_text_range() or "").split())
                    tp.close()
                    images = paths = 0
                    for seen, obj in enumerate(page.get_objects(max_depth=2)):
                        if obj.type == raw.FPDF_PAGEOBJ_IMAGE:
                            images += 1
                        elif obj.type == raw.FPDF_PAGEOBJ_PATH:
                            paths += 1
                        if seen > 1500:
                            break
                finally:
                    page.close()
                drawn = images > 0 or paths > 60
                visual = drawn or len(text) < 200 or _tableish(text) or _mathish(text)
                for piece in split(text):
                    chunks.append(Chunk(f"p. {i + 1}", piece, page=i + 1, visual=visual))
                if drawn or len(text) < 200:
                    figures.append(i + 1)
                if len(chunks) >= MAX_CHUNKS:
                    break
        finally:
            pdf.close()
    return chunks, figures, pages


def render_page(path: Path, page: int, side: int = PICTURE_SIDE) -> bytes:
    """One PDF page as a JPEG, at most `side` pixels a side."""
    import pypdfium2 as pdfium

    with PDFIUM:
        pdf = pdfium.PdfDocument(str(path))
        try:
            pg = pdf[page - 1]
            try:
                w, h = pg.get_size()
                image = pg.render(scale=side / max(w, h, 1)).to_pil().convert("RGB")
            finally:
                pg.close()
        finally:
            pdf.close()
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def read_docx(path: Path) -> tuple[list[Chunk], list[tuple[str, bytes]]]:
    """A Word document's passages, under the heading each falls in, its tables
    row by row, and its pictures, to describe."""
    try:
        import docx
        doc = docx.Document(str(path))
    except Exception as exc:
        raise FileError("this Word document could not be opened - only .docx is read, "
                        "not the older .doc") from exc
    chunks: list[Chunk] = []
    heading, buf = "Start", []

    def flush():
        for piece in split("\n".join(buf)):
            chunks.append(Chunk(heading, piece))
        buf.clear()

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name if para.style is not None else "") or ""
        if style.lower().startswith(("heading", "title")):
            flush()
            heading = text[:80]
        buf.append(text)
    flush()
    for t, table in enumerate(doc.tables, 1):
        rows = []
        for row in table.rows:
            cells = []
            for cell in row.cells:
                value = " ".join(cell.text.split())
                if not cells or cells[-1] != value:      # merged cells repeat themselves
                    cells.append(value)
            rows.append(" | ".join(cells))
        for piece in split("\n".join(rows)):
            chunks.append(Chunk(f"table {t}", piece))
    pictures = []
    for rel in doc.part.rels.values():
        if "image" in rel.reltype:
            try:
                pictures.append((f"picture {len(pictures) + 1}", rel.target_part.blob))
            except Exception:
                pass
    return chunks[:MAX_CHUNKS], pictures


def _rows_to_chunks(sheet: str, rows) -> list[Chunk]:
    """A table's rows as passages, each row with its header's names, so a
    passage reads "Region: North; Sales: 1200" wherever it was cut."""
    chunks: list[Chunk] = []
    header: Optional[list[str]] = None
    buf, first, last, size = [], 0, 0, 0

    def flush():
        nonlocal buf, size
        if buf:
            where = f"{sheet}, row {first}" if first == last else f"{sheet}, rows {first}-{last}"
            chunks.append(Chunk(where, "\n".join(buf)))
        buf, size = [], 0

    for r, row in enumerate(rows, 1):
        if r > MAX_ROWS:
            break
        values = ["" if v is None else str(v).strip() for v in row]
        if not any(values):
            continue
        if header is None:
            header = [v or f"column {i + 1}" for i, v in enumerate(values)]
            continue
        line = "; ".join(f"{header[i] if i < len(header) else f'column {i + 1}'}: {v}"
                         for i, v in enumerate(values) if v)
        if not buf:
            first = r
        buf.append(line)
        last, size = r, size + len(line)
        if size >= CHUNK_CHARS:
            flush()
    flush()
    return chunks


def read_xlsx(path: Path) -> list[Chunk]:
    try:
        import openpyxl
        book = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    except Exception as exc:
        raise FileError("this Excel workbook could not be opened") from exc
    chunks: list[Chunk] = []
    try:
        for ws in book.worksheets:
            chunks += _rows_to_chunks(ws.title, ws.iter_rows(values_only=True))
            if len(chunks) >= MAX_CHUNKS:
                break
    finally:
        book.close()
    return chunks[:MAX_CHUNKS]


def _read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def read_csv(path: Path) -> list[Chunk]:
    text = _read_text_file(path)
    try:
        dialect = csv.Sniffer().sniff(text[:4000])
    except csv.Error:
        dialect = csv.excel
    return _rows_to_chunks(path.stem, csv.reader(io.StringIO(text), dialect))[:MAX_CHUNKS]


def read_text(path: Path) -> list[Chunk]:
    parts = split(_read_text_file(path))
    return [Chunk(f"part {i + 1}", p) for i, p in enumerate(parts[:MAX_CHUNKS])]


def load_book(s: "FileSession"):
    """A spreadsheet's sheets as tables, every row (up to sheets.MAX_ROWS), for
    the tools that calculate over a whole sheet (DESIGN.md 65)."""
    from .sheets import Workbook

    book = Workbook()
    try:
        if s.kind == "xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(str(s.path), read_only=True, data_only=True)
            try:
                for ws in wb.worksheets:
                    book.add(ws.title, ws.iter_rows(values_only=True))
            finally:
                wb.close()
        else:
            text = _read_text_file(s.path)
            try:
                dialect = csv.Sniffer().sniff(text[:4000])
            except csv.Error:
                dialect = csv.excel
            book.add(s.path.stem if s.board else Path(s.name).stem, csv.reader(io.StringIO(text), dialect))
    except Exception:
        log.exception("loading the sheets of %s", s.name)
        return None
    return book if book.sheets else None


# ---------------------------------------------------------------- index --

class FileIndex:
    """A file's passages, searchable: keywords and phrases (Tantivy, in
    memory, English stemming) and meaning (a unit vector per passage)."""

    def __init__(self, chunks: list[Chunk], vectors: Optional[np.ndarray] = None):
        self.chunks = chunks
        self.vectors = vectors
        sb = tantivy.SchemaBuilder()
        sb.add_text_field("id", stored=True, tokenizer_name="raw")
        sb.add_text_field("body", tokenizer_name="en_stem")
        sb.add_text_field("where", tokenizer_name="en_stem")
        self.index = tantivy.Index(sb.build())
        writer = self.index.writer(heap_size=30_000_000, num_threads=1)
        for i, c in enumerate(chunks):
            writer.add_document(tantivy.Document(id=str(i), body=c.text, where=c.where))
        writer.commit()
        writer.wait_merging_threads()
        self.index.reload()

    def _run(self, query) -> list[str]:
        searcher = self.index.searcher()
        return [searcher.doc(addr)["id"][0] for _, addr in searcher.search(query, CANDIDATES).hits]

    def keyword(self, question: str) -> list[str]:
        words = terms(question)
        if not words:
            return []
        return self._run(self.index.parse_query(" ".join(words), ["body", "where"],
                                                field_boosts={"body": 1.0, "where": 0.5}))

    def exact(self, question: str) -> list[str]:
        found = phrases(question)
        if not found:
            return []
        return self._run(self.index.parse_query(" ".join(f'"{" ".join(p)}"' for p in found), ["body"]))

    def meaning(self, qvec: list[float]) -> list[str]:
        if self.vectors is None or not len(self.vectors):
            return []
        q = np.asarray(qvec, dtype=np.float32)
        q /= max(float(np.linalg.norm(q)), 1e-9)
        sims = self.vectors @ q
        return [str(i) for i in np.argsort(-sims)[:CANDIDATES]]


def unit(vectors: list[list[float]]) -> np.ndarray:
    m = np.asarray(vectors, dtype=np.float32)
    return m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)


# -------------------------------------------------------------- session --

@dataclass
class FileSession:
    id: str
    name: str
    kind: str
    path: Path
    folder: Path
    board: str = ""
    sha: str = ""
    status: str = "preparing"          # preparing, ready, failed
    stage: str = "reading"             # reading, pictures, meaning, ready
    done: int = 0
    total: int = 0
    error: str = ""
    pages: int = 0
    note: str = ""
    index: Optional[FileIndex] = None
    # a spreadsheet's sheets as tables, for the tools (sheets.py, DESIGN.md 65)
    book: Optional[object] = None
    used: float = field(default_factory=time.time)
    closed: bool = False

    def info(self) -> dict:
        n = len(self.index.chunks) if self.index else 0
        return {"id": self.id, "name": self.name, "kind": self.kind, "status": self.status,
                "stage": self.stage, "done": self.done, "total": self.total, "error": self.error,
                "pages": self.pages, "passages": n, "note": self.note,
                "sheets": [{"name": sh.name, "rows": sh.rows, "columns": len(sh.columns)}
                           for sh in self.book.sheets.values()] if self.book else [],
                "board": self.board, "sha": self.sha}


def describe(model, image: bytes, where: str) -> str:
    """What a page's or a picture's figures show, in words; "" for plain text."""
    url = "data:image/jpeg;base64," + base64.b64encode(_jpeg(image)).decode("ascii")
    reply = model.chat([{"role": "user", "content": [
        {"type": "text", "text": FIGURE_PROMPT.format(where=where)},
        {"type": "image_url", "image_url": {"url": url}}]}],
        max_tokens=FIGURE_TOKENS, think=False, temperature=0.2, timeout=FIGURE_SECONDS)
    text = " ".join(str(reply.get("text") or "").split())
    return "" if not text or text.upper().startswith("TEXT ONLY") else text[:2400]


def _jpeg(image: bytes, side: int = PICTURE_SIDE) -> bytes:
    """Any picture as a JPEG the model takes, shrunk."""
    from PIL import Image

    with Image.open(io.BytesIO(image)) as im:
        im = im.convert("RGB")
        im.thumbnail((side, side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return buf.getvalue()


class FileChats:
    """The files being asked about, by id. Temporary by design: closing one,
    leaving it unused for hours, or starting Mindboard again removes it."""

    def __init__(self, dd: DataDir):
        self.dd = dd
        self.root = dd.root / "tmp" / "filechat"
        shutil.rmtree(self.root, ignore_errors=True)       # nothing survives a restart
        self.sessions: dict[str, FileSession] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ open --
    def _new(self, name: str, path: Optional[Path], **kw) -> FileSession:
        kind = KINDS.get(Path(name).suffix.lower())
        if kind is None:
            raise FileError(f"Mindboard can ask {READABLE} - not {Path(name).suffix or 'this file'}")
        self.sweep()
        fid = uuid.uuid4().hex[:16]
        folder = self.root / fid
        folder.mkdir(parents=True, exist_ok=True)
        s = FileSession(fid, name, kind, path or folder / ("file" + Path(name).suffix.lower()), folder, **kw)
        with self._lock:
            self.sessions[fid] = s
        return s

    def from_upload(self, stream, name: str) -> FileSession:
        """A file added straight into the chat: kept in its own folder until closed."""
        name = Path(name or "file").name
        s = self._new(name, None)
        size = 0
        with open(s.path, "wb") as out:
            while chunk := stream.read(1 << 20):
                size += len(chunk)
                if size > MAX_BYTES:
                    out.close()
                    self.close(s.id)
                    raise FileError("that file is too large to ask about (200 MB at most)")
                out.write(chunk)
        return s

    def from_board(self, path: Path, name: str, board: str, sha: str) -> FileSession:
        """A file on a board, read where it is - the board's copy is never changed."""
        return self._new(Path(name or path.name).name, path, board=board, sha=sha)

    def get(self, fid: str) -> Optional[FileSession]:
        with self._lock:
            s = self.sessions.get(fid)
        if s is not None:
            s.used = time.time()
        return s

    def close(self, fid: str) -> bool:
        with self._lock:
            s = self.sessions.pop(fid, None)
        if s is None:
            return False
        s.closed = True
        shutil.rmtree(s.folder, ignore_errors=True)        # the upload, page pictures
        return True

    def sweep(self) -> None:
        cutoff = time.time() - IDLE_HOURS * 3600
        with self._lock:
            old = [fid for fid, s in self.sessions.items() if s.used < cutoff]
        for fid in old:
            self.close(fid)

    # --------------------------------------------------------- prepare --
    def prepare(self, s: FileSession, chat: Callable[[], object], embed: Callable[[], object],
                take_turn: Callable[[object], bool]) -> None:
        """Read, describe and index the file, on its own thread."""
        threading.Thread(target=self._prepare, args=(s, chat, embed, take_turn), daemon=True,
                         name=f"mindboard-file-{s.id}").start()

    def _prepare(self, s: FileSession, chat, embed, take_turn) -> None:
        try:
            chunks, figures = self._read(s)
            if s.closed:
                return
            chunks += self._figures(s, figures, chat, take_turn)
            if not chunks:
                raise FileError("nothing in this file could be read - a scanned PDF needs the AI "
                                "set up to read its pages" if s.kind == "pdf"
                                else "this file has no text in it")
            if s.closed:
                return
            if s.kind in ("xlsx", "csv"):
                s.book = load_book(s)
            vectors = self._embed(s, chunks, embed)
            s.index = FileIndex(chunks, vectors)
            s.stage, s.status = "ready", "ready"
        except FileError as exc:
            s.status, s.error = "failed", str(exc)
        except Exception as exc:                            # said, not hidden
            log.exception("preparing %s", s.name)
            s.status, s.error = "failed", f"this file could not be prepared: {exc}"

    def _read(self, s: FileSession) -> tuple[list[Chunk], list]:
        s.stage = "reading"
        if s.kind == "pdf":
            chunks, pages, s.pages = read_pdf(s.path)
            return chunks, [("page", p) for p in pages]
        if s.kind == "docx":
            chunks, pictures = read_docx(s.path)
            return chunks, [("picture", p) for p in pictures]
        if s.kind == "xlsx":
            return read_xlsx(s.path), []
        if s.kind == "csv":
            return read_csv(s.path), []
        return read_text(s.path), []

    def _figures(self, s: FileSession, figures: list, chat, take_turn) -> list[Chunk]:
        """Figures, charts and scanned pages, described by the model once - so a
        question finds them by what they show. One at a time, each taking the
        model's slot like a question (DESIGN.md 60)."""
        figures = figures[:FIGURES_MAX]
        model = chat() if figures else None
        if model is None:
            if figures:
                s.note = "Figures were not described: the AI is not set up."
            return []
        s.stage, s.done, s.total = "pictures", 0, len(figures)
        out: list[Chunk] = []
        missed = 0
        for kind, what in figures:
            if s.closed:
                break
            try:
                if kind == "page":
                    image, where = render_page(s.path, what), f"page {what}"
                else:
                    (label, image), where = what, what[0]
                if not take_turn(model):
                    break
                try:
                    said = describe(model, image, where)
                finally:
                    model._asking.release()
            except Exception:
                # a figure that fails or takes too long is skipped, not waited on
                log.exception("describing %s of %s", kind, s.name)
                said, missed = "", missed + 1
            if said:
                if kind == "page":
                    out.append(Chunk(f"p. {what}", f"Figure: {said}", page=what, visual=True, figure=True))
                else:
                    out.append(Chunk(what[0], f"Figure: {said}", figure=True, visual=True, image=what[1]))
            s.done += 1
        if missed:
            s.note = (s.note + " " if s.note else "") + (
                f"{missed} figure{'s' if missed > 1 else ''} could not be described, so "
                f"{'they are' if missed > 1 else 'it is'} found by text only.")
        return out

    def _embed(self, s: FileSession, chunks: list[Chunk], embed) -> Optional[np.ndarray]:
        model = embed()
        if model is None:
            s.note = (s.note + " " if s.note else "") + "Searched by words only: the meaning model is not set up."
            return None
        s.stage, s.done, s.total = "meaning", 0, len(chunks)
        vectors: list[list[float]] = []
        try:
            for i in range(0, len(chunks), EMBED_BATCH):
                if s.closed:
                    return None
                batch = chunks[i:i + EMBED_BATCH]
                vectors += model.embed([f"{c.where}\n{c.text}"[:1500] for c in batch])
                s.done = min(len(chunks), i + len(batch))
        except Exception:
            log.exception("embedding %s", s.name)
            s.note = (s.note + " " if s.note else "") + "Searched by words only: the meaning model failed."
            return None
        return unit(vectors)

    # ------------------------------------------------------------ find --
    def find(self, s: FileSession, question: str, embedder=None, reranker=None) -> tuple[list[Chunk], str]:
        """The passages to send for this question, best first, within budget -
        and what the search went by."""
        idx = s.index
        assert idx is not None
        lists, used = [], []
        exact = idx.exact(question)
        if exact:
            lists.append(exact)
            used.append("exact phrases")
        lists.append(idx.keyword(question))
        used.append("keywords")
        if embedder is not None and idx.vectors is not None:
            try:
                qvec = embedder.embed([f"{FILE_INSTRUCT}{question.strip()}"[:2000]])[0]
                lists.append(idx.meaning(qvec))
                used.append("meaning")
            except Exception:
                log.exception("embedding a question about %s", s.name)
        order = [int(i) for i in retrieve.ranked(retrieve.fuse(*lists))]
        if reranker is not None and len(order) > 1:
            try:
                head = order[:RERANK]
                scores = reranker.rerank(question, [f"{idx.chunks[i].where}\n{idx.chunks[i].text}"[:1300]
                                                    for i in head])
                order = [i for _, i in sorted(zip(scores, head), key=lambda p: -p[0])] + order[RERANK:]
                used.append("a close reading")
            except Exception:
                log.exception("reranking passages of %s", s.name)
        if OVERVIEW.search(question) or not order:
            # "what is this file about?" - its start says, whatever matched
            order = [0] + [i for i in order if i != 0] if idx.chunks else order
        from .ask import estimate

        picked, spent = [], 0
        for i in order:
            cost = estimate(idx.chunks[i].text) + 8
            if picked and spent + cost > BUDGET:
                continue
            picked.append(idx.chunks[i])
            spent += cost
            if len(picked) >= PASSAGES_MAX or spent >= BUDGET:
                break
        return picked, ", ".join(used)

    def context(self, s: FileSession, picked: list[Chunk], how: str):
        """The passages as the model reads them, in the file's order, and the
        pages better seen than read as pictures."""
        from .ask import Context, Picture

        idx = s.index
        assert idx is not None
        place = {id(c): i for i, c in enumerate(idx.chunks)}
        in_order = sorted(picked, key=lambda c: place.get(id(c), 0))
        pages = f", {s.pages} pages" if s.pages else ""
        lines = [f'PASSAGES from the file "{s.name}"{pages} - the parts that best match the '
                 "question, not the whole file. Each starts with where it is."]
        lines += [f"[{c.where}] {c.text}" for c in in_order]
        pictures, shown = [], set()
        for c in picked:                                    # best first
            if len(pictures) >= PAGE_PICTURES or not c.visual:
                continue
            key = c.page if c.page is not None else c.where
            if key in shown:
                continue
            try:
                image = render_page(s.path, c.page) if c.page is not None and s.kind == "pdf" \
                    else _jpeg(c.image) if c.image else None
            except Exception:
                image = None
            if image is None:
                continue
            shown.add(key)
            from PIL import Image
            with Image.open(io.BytesIO(image)) as im:
                w, h = im.size
            pictures.append(Picture(f"Page {c.page}" if c.page is not None else c.where.capitalize(),
                                    "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii"),
                                    (w * h) // (32 * 32) + 16))
        passages = [{"where": c.where, "page": c.page, "figure": c.figure,
                     "pictured": (c.page if c.page is not None else c.where) in shown} for c in in_order]
        return Context([], "\n\n".join(lines), pictures, mode="file", how=how,
                       total=len(idx.chunks), passages=passages)

    def thumb(self, s: FileSession, page: int, side: int = 360) -> Path:
        """A PDF page, small, for the answer's 'pages used'; kept with the file."""
        out = s.folder / f"page-{page}-{side}.jpg"
        if not out.exists():
            out.write_bytes(render_page(s.path, page, side))
        return out
