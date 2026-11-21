"""Asking one file (DESIGN.md 64): prepared - read, figures described,
indexed - then searched inside itself only, and temporary."""
import io
import time


import pytest
from fastapi.testclient import TestClient

from mindboard import filechat
from mindboard.app import create_app
from mindboard.filechat import FileChats, FileIndex, read_csv, read_docx, read_pdf, read_xlsx, split
from mindboard.paths import DataDir

from test_ask import TOKEN, events, model  # noqa: F401  (the stand-in llama-server)


def text_pdf(pages: list[str]) -> bytes:
    """A small PDF with one line of Helvetica text a page - by hand, as no
    PDF writer is a dependency."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 40 760 Td ({text}) Tj ET".encode()
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream")
        content = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content} 0 R >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{o}\nendobj\n".encode())
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return out.getvalue()


def picture_pdf() -> bytes:
    """A page that is only a picture - a scan, or a chart."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), (200, 40, 40)).save(buf, "PDF")
    return buf.getvalue()


LONG = ("The turbine blade fatigue test ran for nine hundred hours at the Derby rig. " * 3).strip()


def test_split_cuts_after_sentences_with_some_overlap():
    text = " ".join(f"Sentence number {i} says something useful." for i in range(80))
    parts = split(text, size=300, overlap=60)
    assert len(parts) > 5 and all(len(p) <= 300 for p in parts)
    assert all(p.endswith(".") for p in parts[:-1]), "cut after a sentence"
    assert parts[1][:20] in parts[0], "each starts a little before the last ended"
    assert split("short") == ["short"] and split("   ") == []


def test_a_pdf_is_read_by_page_and_a_picture_page_is_marked_to_describe(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(text_pdf(["Hydrogen buses start in Nottingham in 2027.", LONG]))
    chunks, figures, pages = read_pdf(p)
    assert pages == 2 and [c.where for c in chunks] == ["p. 1", "p. 2"]
    assert chunks[0].text.startswith("Hydrogen buses") and chunks[0].page == 1
    assert figures == [1] and chunks[0].visual and not chunks[1].visual, "little text on a page: seen, not read"
    scan = tmp_path / "scan.pdf"
    scan.write_bytes(picture_pdf())
    chunks, figures, pages = read_pdf(scan)
    assert chunks == [] and figures == [1] and pages == 1
    assert filechat.render_page(scan, 1, 200)[:2] == b"\xff\xd8", "a page drawn as a JPEG"
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(filechat.FileError):
        read_pdf(bad)


def test_word_excel_and_csv_are_read_with_where_each_passage_is(tmp_path):
    import docx
    import openpyxl
    from PIL import Image

    d = docx.Document()
    d.add_heading("Funding", level=1)
    d.add_paragraph("The Innovate UK grant covers the digital twin until March 2027.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text, t.cell(1, 0).text, t.cell(1, 1).text = "Partner", "Share", "NTU", "60%"
    pic = io.BytesIO()
    Image.new("RGB", (40, 40), (0, 0, 255)).save(pic, "PNG")
    pic.seek(0)
    d.add_picture(pic)
    d.save(tmp_path / "a.docx")
    chunks, pictures = read_docx(tmp_path / "a.docx")
    assert ("Funding", True) in [(c.where, "Innovate UK" in c.text) for c in chunks]
    assert any(c.where == "table 1" and "NTU | 60%" in c.text for c in chunks)
    assert len(pictures) == 1 and pictures[0][0] == "picture 1"

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales"
    for row in [("Region", "Sales"), ("North", 1200), ("South", 900)]:
        ws.append(row)
    wb.save(tmp_path / "a.xlsx")
    rows = read_xlsx(tmp_path / "a.xlsx")
    assert rows[0].where == "Sales, rows 2-3" and "Region: North; Sales: 1200" in rows[0].text

    (tmp_path / "visits.csv").write_text("name,date\nDaniel King,11 Nov 2026\n", encoding="utf-8")
    got = read_csv(tmp_path / "visits.csv")
    assert got[0].where == "visits, row 2" and got[0].text == "name: Daniel King; date: 11 Nov 2026"


class Words:
    """Meaning, played by shared words: enough to rank a passage by a question."""
    VOCAB = ["turbine", "fatigue", "hydrogen", "bus", "grant", "funding", "chart", "red"]

    def embed(self, texts):
        return [[float(w in t.lower()) + 0.01 for w in self.VOCAB] for t in texts]


def test_a_file_is_searched_inside_itself_within_a_budget(tmp_path):
    from mindboard.filechat import Chunk
    chunks = [Chunk("p. 1", "Introduction to the project and its partners.", page=1),
              Chunk("p. 2", "Hydrogen bus trials in Nottingham.", page=2),
              Chunk("p. 3", "Turbine fatigue results after nine hundred hours.", page=3),
              Chunk("p. 4", "Figure: a red bar chart of funding by year.", page=4, visual=True, figure=True)]
    idx = FileIndex(chunks, filechat.unit(Words().embed([c.text for c in chunks])))
    assert idx.keyword("turbine fatigue") == ["2"]
    assert idx.exact('"hydrogen bus"') == ["1"]
    assert idx.meaning(Words().embed(["red chart"])[0])[0] == "3"
    chats = FileChats(DataDir(tmp_path))
    s = chats._new("r.pdf", tmp_path / "r.pdf")
    s.index, s.status = idx, "ready"
    picked, how = chats.find(s, "what did the fatigue test find?", Words())
    assert picked[0].where == "p. 3" and how == "keywords, meaning"
    picked, _ = chats.find(s, "summarize this file", Words())
    assert picked[0].where == "p. 1", "an overview starts at the start"
    ctx = chats.context(s, picked, how)
    assert ctx.mode == "file" and ctx.text.startswith('PASSAGES from the file "r.pdf"')
    assert ctx.text.index("[p. 1]") < ctx.text.index("[p. 2]"), "in the file's order"
    assert ctx.cards == [] and ctx.total == 4


def wait_ready(client, fid, h, seconds=30):
    deadline = time.time() + seconds
    while time.time() < deadline:
        info = client.get(f"/api/files/{fid}", headers=h).json()
        if info["status"] != "preparing":
            return info
        time.sleep(0.1)
    raise AssertionError("never prepared")


def test_asking_a_file_prepares_it_then_answers_from_its_passages(tmp_path, model):  # noqa: F811
    dd = DataDir(tmp_path / "data")
    app = create_app(dd, TOKEN)
    h = {"X-Mindboard-Token": TOKEN}
    with TestClient(app) as client:
        pdf = text_pdf(["Hydrogen buses start in Nottingham in 2027.", LONG, "Funding comes from Innovate UK."])
        res = client.post("/api/files", headers=h, files={"file": ("plan.pdf", pdf, "application/pdf")})
        assert res.status_code == 200, res.text
        fid = res.json()["id"]
        info = wait_ready(client, fid, h)
        assert info["status"] == "ready" and info["pages"] == 3 and info["passages"] >= 3, info
        assert info["kind"] == "pdf" and info["board"] == ""
        ask = client.post(f"/api/files/{fid}/ask", headers=h,
                          json={"question": "when do the hydrogen buses start?", "cards": []})
        assert ask.status_code == 200, ask.text
        evs = events(ask)
        ctx = evs[0]
        assert ctx[0] == "context" and ctx[1]["mode"] == "file" and ctx[1]["cards"] == []
        assert ctx[1]["passages"] and ctx[1]["passages"][0]["where"].startswith("p. ")
        assert evs[-1][0] == "done"
        assert client.get(f"/api/files/{fid}/page/1", headers=h).headers["content-type"] == "image/jpeg"
        assert client.get(f"/api/files/{fid}/raw", headers=h).content == pdf
        folder = dd.root / "tmp" / "filechat" / fid
        assert folder.exists()
        assert client.delete(f"/api/files/{fid}", headers=h).json() == {"closed": True}
        assert not folder.exists(), "temporary: gone when closed"
        assert client.get(f"/api/files/{fid}", headers=h).status_code == 404
        bad = client.post("/api/files", headers=h, files={"file": ("a.exe", b"MZ", "application/octet-stream")})
        assert bad.status_code == 400 and "PDF, Word" in bad.json()["detail"]
    if app.state.ask.get("model"):
        app.state.ask["model"].stop()


def test_a_pdf_keeps_a_table_row_on_its_line(tmp_path):
    """DESIGN.md 82: the page's lines are kept, so a row's numbers stay with
    its label rather than running into the next row's."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import number_eval

    p = tmp_path / "r.pdf"
    p.write_bytes(number_eval.report_pdf())
    chunks, _, _ = read_pdf(p)
    page2 = "\n".join(c.text for c in chunks if c.page == 2)
    assert "\nRevenue 2.64 2.91 3.48 3.84\n" in page2
    assert "\nCost of sales 1.12 1.27 1.49 1.71\n" in page2


def test_a_sum_about_a_file_is_worked_out_by_the_calculator(tmp_path, model):  # noqa: F811
    """DESIGN.md 82: the model picks the numbers, the calculator adds them, and
    a number not written on its page is refused, not used."""
    dd = DataDir(tmp_path / "data")
    app = create_app(dd, TOKEN)
    h = {"X-Mindboard-Token": TOKEN}
    with TestClient(app) as client:
        pdf = text_pdf(["Riverside visitors 3,965 and 4,421 in the first two quarters."])
        fid = client.post("/api/files", headers=h, files={"file": ("v.pdf", pdf, "application/pdf")}).json()["id"]
        wait_ready(client, fid, h)
        res = client.post(f"/api/files/{fid}/ask", headers=h, json={"question": "what is the total?"})
        assert res.status_code == 200, res.text
        tools = [d for e, d in events(res) if e == "tools"]
        assert tools, "the calculator ran"
        calls = tools[0]["calls"]
        assert calls[0]["tool"] == "calculate" and "the total = 8,386" in calls[0]["said"] and not calls[0]["error"]
        assert calls[1]["error"] and "987654" in calls[1]["said"], "a number on no page is refused"
        plain = client.post(f"/api/files/{fid}/ask", headers=h, json={"question": "who are the visitors?"})
        assert not [e for e, _ in events(plain) if e == "tools"], "no sum asked, no calculator"


def test_nothing_prepared_survives_a_restart(tmp_path):
    dd = DataDir(tmp_path)
    left = dd.root / "tmp" / "filechat" / "abc"
    left.mkdir(parents=True)
    (left / "file.pdf").write_bytes(b"x")
    FileChats(dd)
    assert not left.exists()




# ------------------------------------------------- spreadsheet tools (65) --

def sales_book():
    from mindboard.sheets import Workbook
    book = Workbook()
    rows = [("Region", "Rep", "Sales", "Date")]
    rows += [("North", "Ana", 1200, "2026-01-05"), ("South", "Bo", 900, "2026-02-11"),
             ("North", "Cy", "1,800", "2026-03-02"), ("East", "Di", None, "2026-03-20"),
             ("South", "Ed", 300, "2026-04-01")]
    book.add("Sales", rows)
    return book


def test_sheet_tools_calculate_over_every_row():
    book = sales_book()
    sheet = book.sheets["sales"]
    assert sheet.rows == 5 and [c.numeric for c in sheet.columns] == [False, False, True, False]
    assert "SHEET \"Sales\" - 5 rows" in book.describe() and '"Sales" (number)' in book.describe()
    total = book.run({"tool": "calculate", "sheet": "Sales", "op": "sum", "column": "Sales"})
    assert total["result"] == 4200, "\"1,800\" read as a number; the empty cell left out"
    north = book.run({"tool": "calculate", "op": "average", "column": "sales",
                      "where": [{"column": "Region", "op": "=", "value": "north"}]})
    assert north["result"] == 1500 and north["rows"] == 2, "one sheet need not be named; case ignored"
    per = book.run({"tool": "calculate", "sheet": "Sales", "op": "sum", "column": "Sales", "group_by": "Region"})
    assert per["result"][0] == ("North", 3000) and "per \"Region\"" in per["said"]
    assert book.run({"tool": "calculate", "op": "count"})["result"] == 5
    assert book.run({"tool": "calculate", "op": "median", "column": "Sales"})["result"] == 1050
    since = book.run({"tool": "rows", "where": [{"column": "Date", "op": ">=", "value": "2026-03-01"}],
                      "order_by": "Date", "order": "asc", "columns": ["Rep", "Date"]})
    assert since["rows"] == 3 and since["said"].index("Cy") < since["said"].index("Ed")
    assert "North (2 rows)" in book.run({"tool": "distinct", "column": "Region"})["said"]


def test_a_wrong_tool_call_is_said_never_guessed_or_run_as_sql():
    book = sales_book()
    for call, words in [({"tool": "calculate", "op": "sum", "column": "Profit"}, "no column"),
                        ({"tool": "calculate", "op": "sum", "column": "Rep"}, "not a number column"),
                        ({"tool": "drop", "sheet": "Sales"}, "no tool"),
                        ({"tool": "calculate", "op": "sum", "column": "Sales",
                          "where": [{"column": "Region", "op": "; DROP TABLE t1", "value": 1}]}, "cannot compare"),
                        ({"tool": "rows", "where": [{"column": "Rep", "op": "contains", "value": "%' OR 1=1 --"}]}, "")]:
        got = book.run(call)
        assert (words in got.get("error", "")) if words else got["rows"] == 0, (call, got)
    assert book.run({"tool": "calculate", "op": "count"})["result"] == 5, "the data is untouched"
    from mindboard.sheets import calculated_text
    text = calculated_text([book.run({"tool": "calculate", "op": "sum", "column": "Sales"}),
                            book.run({"tool": "calculate", "op": "sum", "column": "Profit"})])
    assert text.startswith("CALCULATED") and "NOT CALCULATED" in text and "Do not work these figures out" in text


class Choosing:
    """The model choosing tools: says what it was given to say, as a stream."""

    def __init__(self, reply, slow=0.0):
        self.reply, self.slow = reply, slow

    def stream(self, messages, **kw):
        for piece in [self.reply[:5], self.reply[5:]]:
            time.sleep(self.slow)
            yield {"type": "text", "text": piece}
        yield {"type": "done", "stopped": False}


def test_the_model_decides_on_tools_and_a_slow_or_garbled_choice_is_no_choice(monkeypatch):
    from mindboard import sheets
    book = sales_book()
    calls, why, trouble = sheets.decide(Choosing(
        '{"why": "a total", "calls": [{"tool": "calculate", "op": "sum", "column": "Sales"}]}'), book, "total sales?")
    assert calls == [{"tool": "calculate", "op": "sum", "column": "Sales"}] and why == "a total" and trouble == ""
    assert sheets.decide(Choosing('{"why": "one row", "calls": []}'), book, "what did Ana sell?")[0] == []
    assert sheets.decide(Choosing("I think you should sum it"), book, "q")[2].endswith("could not be read")
    monkeypatch.setattr(sheets, "DECIDE_SECONDS", 0.05)
    assert sheets.decide(Choosing('{"calls": []}', slow=0.1), book, "q")[2] == "choosing a calculation took too long"


def test_asking_a_spreadsheet_runs_the_tools_the_model_chose(tmp_path, model, monkeypatch):  # noqa: F811
    import openpyxl
    from mindboard import sheets
    monkeypatch.setattr(sheets, "decide", lambda m, b, q, h=None: (
        [{"tool": "calculate", "op": "sum", "column": "Sales"}, {"tool": "calculate", "op": "sum", "column": "Nope"}],
        "a total", ""))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales"
    for row in [("Region", "Sales"), ("North", 1200), ("South", 900)]:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    dd = DataDir(tmp_path / "data")
    app = create_app(dd, TOKEN)
    h = {"X-Mindboard-Token": TOKEN}
    with TestClient(app) as client:
        fid = client.post("/api/files", headers=h, files={"file": ("sales.xlsx", buf.getvalue(), "application/octet-stream")}).json()["id"]
        info = wait_ready(client, fid, h)
        assert info["status"] == "ready" and info["sheets"] == [{"name": "Sales", "rows": 2, "columns": 2}]
        evs = events(client.post(f"/api/files/{fid}/ask", headers=h, json={"question": "total sales?", "cards": []}))
        tools = evs[0]
        assert tools[0] == "tools" and tools[1]["why"] == "a total"
        assert tools[1]["calls"][0]["said"].startswith('sum of "Sales" in "Sales" = 2100')
        assert tools[1]["calls"][1]["error"].startswith("no column")
        assert evs[1][0] == "context" and evs[-1][0] == "done"
    if app.state.ask.get("model"):
        app.state.ask["model"].stop()
