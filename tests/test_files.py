"""PDFs found by what they cover, and offered with an answer (DESIGN.md 48):
read once by the background indexer, searched like any card's text, excerpted
for the model, and named with the card for the page to offer."""

import io

import pytest

from mindboard import ask, experts
from mindboard.assets import pdf_text, store_bytes
from mindboard.boards import conn_for, create_board
from mindboard.ids import ulid
from mindboard.indexer import Indexer
from mindboard.ops import apply_envelopes, read_graph
from mindboard.search_index import BoardIndex, FileNotes, PictureNotes, card_fields, card_notes, file_of


def make_pdf(*pages: str) -> bytes:
    """A real PDF with a text layer, one line a page - small enough to build by hand."""
    objs = [b"<</Type/Catalog/Pages 2 0 R>>",
            b"<</Type/Pages/Kids[%s]/Count %d>>" % (b" ".join(b"%d 0 R" % (4 + 2 * i) for i in range(len(pages))),
                                                    len(pages)),
            b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    for i, text in enumerate(pages):
        stream = f"BT /F1 14 Tf 20 100 Td ({text}) Tj ET".encode()
        objs.append(b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 500 144]/Contents %d 0 R"
                    b"/Resources<</Font<</F1 3 0 R>>>>>>" % (5 + 2 * i))
        objs.append(b"<</Length %d>>stream\n" % len(stream) + stream + b"\nendstream")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for o in offsets:
        out.write(b"%010d 00000 n \n" % o)
    out.write(b"trailer <</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


@pytest.fixture()
def shelf(dd):
    """A board with a PDF whose title says nothing of what it covers, and a note."""
    b = create_board(dd, "Reading", "")
    c = conn_for(dd, b["id"])
    stored = store_bytes(dd, c, b["id"], make_pdf("Sliding window chunking keeps context across boundaries",
                                                 "Overlap of fifteen percent works well"),
                         declared_mime="application/pdf", orig_name="paper-2024.pdf")
    pdf = {"id": ulid(), "type": "file", "pos": [0, 0], "title": "paper-2024.pdf",
           "content": {"sha256": stored["sha256"], "filename": "paper-2024.pdf", "mime": "application/pdf"}}
    note = {"id": ulid(), "type": "text", "pos": [0, 0], "title": "Garden", "content": {"text": "water the tomatoes"}}
    assert not apply_envelopes(c, b["id"], [{"opId": ulid(), "op": {"kind": "node.create", "node": n}}
                                            for n in (pdf, note)])["rejected"]
    yield {"board": b["id"], "pdf": pdf, "note": note, "sha": stored["sha256"]}
    BoardIndex.drop(dd, b["id"])


def test_a_pdf_is_read_from_its_first_pages(tmp_path):
    p = tmp_path / "x.pdf"
    p.write_bytes(make_pdf("first page words", "second page words", "third page words"))
    assert pdf_text(p) == "(3 pages) first page words second page words third page words"
    assert pdf_text(p, pages=1) == "(3 pages) first page words", "only its first pages"
    assert pdf_text(p, chars=12) == "(3 pages) fi", "and only so much"
    (tmp_path / "scan.pdf").write_bytes(b"%PDF-1.4 not really")
    assert pdf_text(tmp_path / "scan.pdf") == "", "a PDF with nothing to read gives nothing"


def test_only_pdfs_have_text_read():
    assert file_of({"type": "file", "content": {"sha256": "a", "filename": "Notes.PDF"}}) == "a"
    assert file_of({"type": "file", "content": {"sha256": "b", "mime": "application/pdf", "filename": "x"}}) == "b"
    assert file_of({"type": "file", "content": {"sha256": "c", "filename": "data.zip"}}) is None
    assert file_of({"type": "image", "content": {"sha256": "d", "filename": "x.pdf"}}) is None


def test_the_indexer_reads_each_pdf_once_and_search_finds_it_by_what_it_covers(dd, shelf):
    conn = conn_for(dd, shelf["board"])
    nodes = {n["id"]: n for n in read_graph(conn, shelf["board"])["nodes"]}
    indexer = Indexer(dd, lambda: None)
    indexer._read_files(shelf["board"], conn, nodes)
    text = FileNotes.get(dd).notes[shelf["sha"]]
    assert text.startswith("(2 pages) Sliding window chunking")
    assert shelf["sha"] not in PictureNotes.get(dd).notes, "kept apart from the pictures' words"
    assert card_notes(dd)[shelf["sha"]] == text
    assert "File holds: (2 pages) Sliding window" in card_fields(shelf["pdf"], nodes, card_notes(dd))["body"]

    FileNotes.get(dd).put(shelf["sha"], "changed")
    indexer._read_files(shelf["board"], conn, nodes)
    assert FileNotes.get(dd).notes[shelf["sha"]] == "changed", "read once, not at every round"

    FileNotes.get(dd).put(shelf["sha"], text)
    ctx = ask.build(dd, conn, shelf["board"], [], "how much overlap between chunks?")
    assert ctx.cards[0] == shelf["pdf"]["id"], "found by its text, though its title says nothing"
    assert "file holds: (2 pages) Sliding window chunking" in ctx.text, "and the model reads what it covers"
    assert "when a file covers what they ask, cite it" in ask.SYSTEM


def test_an_expert_names_the_pdf_so_the_answer_can_offer_it(dd, shelf):
    FileNotes.get(dd).put(shelf["sha"], "(2 pages) Sliding window chunking keeps context")
    e = experts.create(dd, {"name": "Reader", "boards": [shelf["board"]]})
    ctx, lib = experts.context(dd, e, "sliding window chunking")
    assert ctx.where[shelf["pdf"]["id"]] == {"board": shelf["board"], "title": "paper-2024.pdf",
                                             "pdf": shelf["sha"], "file": "paper-2024.pdf"}
    assert experts.where(lib, shelf["note"]["id"]) == {"board": shelf["board"], "title": "Garden"}
    assert "file holds: (2 pages) Sliding window" in ctx.text


def test_a_planned_answer_still_offers_the_pdf_that_matches_the_question(dd, shelf):
    """The steps' cards go first in a planned answer, and once pushed a
    matching PDF out of the cards an answer offers; it is kept in and named."""
    from test_plan import Scripted, plan_reply

    FileNotes.get(dd).put(shelf["sha"], "(2 pages) Sliding window chunking keeps context; overlap of fifteen percent")
    e = experts.create(dd, {"name": "Reader", "boards": [shelf["board"]]})
    lib = experts.library(dd, e)
    model = Scripted(plan_reply([{"find": "tomatoes", "work_out": "a"}, {"find": "garden", "work_out": "b"}]),
                     f"Water them [[{shelf['note']['id']}]].", "Twice a week.",
                     f"Use fifteen percent [[{shelf['note']['id']}]].\nNEXT: a\nNEXT: b\nNEXT: c")
    evs = list(experts.answer(model, dd, e, lib, "how much overlap for sliding window chunking?", []))
    context = next(ev for ev in evs if ev["event"] == "context")
    assert context["offer"] == [shelf["pdf"]["id"]], "offered, though the answer cited only the note"
    assert context["where"][shelf["pdf"]["id"]]["pdf"] == shelf["sha"]
