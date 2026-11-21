"""Numbers in file answers, measured (DESIGN.md 82).

Makes two PDFs with tables - cells placed where a real report places them,
ruled lines, numbers that differ by a swapped digit (3.48 beside 3.84) - asks
a running Mindboard about them, and scores each answer: is the right number
in it? The truth is worked out here from the tables themselves.

Usage:
  python scripts/number_eval.py --url http://127.0.0.1:7798 --token-file <data>/token
  python scripts/number_eval.py --write-pdfs <folder>       # only the PDFs
Options: --only 1,5,7 (question numbers), --check (also press Double check), --out results.json
"""

import argparse
import io
import json
import re
import sys
import time
import urllib.request
import uuid
from pathlib import Path

# ------------------------------------------------------------- the PDFs --

def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list) -> bytes:
    """A PDF by hand (no PDF writer is a dependency). Each page is a list of
    ("t", x, y, size, text, bold) and ("l", x1, y1, x2, y2) items - or a JPEG
    (bytes) filling the page, as a scan."""
    objs: list = ["<< /Type /Catalog /Pages 2 0 R >>", None,
                  "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
                  "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"]
    kids = []
    for items in pages:
        xobj = ""
        if isinstance(items, bytes):
            from PIL import Image
            with Image.open(io.BytesIO(items)) as im:
                w, h = im.size
            objs.append(f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceGray "
                        f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(items)} >>\nstream\n".encode()
                        + items + b"\nendstream")
            xobj = f"/XObject << /Im1 {len(objs)} 0 R >>"
            stream = "q 595 0 0 842 0 0 cm /Im1 Do Q"
        else:
            ops = ["0.5 w 0.6 G"]
            for it in items:
                if it[0] == "l":
                    _, x1, y1, x2, y2 = it
                    ops.append(f"{x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l S")
                else:
                    _, x, y, size, text, bold = it
                    ops.append(f"BT /{'F2' if bold else 'F1'} {size} Tf 1 0 0 1 {x:.1f} {y:.1f} Tm ({_esc(text)}) Tj ET")
            stream = "\n".join(ops)
        objs.append(f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream")
        content = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                    f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> {xobj} >> /Contents {content} 0 R >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        body = o if isinstance(o, bytes) else o.encode("latin-1")
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return out.getvalue()


def prose(lines: list[str], top: float = 760, size: int = 11) -> list[tuple]:
    return [("t", 56, top - i * (size + 5), size, line, False) for i, line in enumerate(lines)]


def table(title: str, head: list[str], rows: list[list[str]], top: float, widths: list[float],
          note: str = "") -> list[tuple]:
    """A ruled table: the first column left-aligned, numbers right-aligned."""
    items: list[tuple] = [("t", 56, top, 11, title, True)]
    y = top - 26
    x0, x_end = 56, 56 + sum(widths)
    items.append(("l", x0, y + 15, x_end, y + 15))
    for r, cells in enumerate([head] + rows):
        x = x0
        for c, cell in enumerate(cells):
            w = widths[c]
            if c == 0:
                items.append(("t", x + 4, y, 9.5, cell, r == 0))
            else:
                items.append(("t", x + w - 6 - len(cell) * 5.0, y, 9.5, cell, r == 0))
            x += w
        items.append(("l", x0, y - 6, x_end, y - 6))
        y -= 21
    x = x0
    for w in widths + [0]:
        items.append(("l", x, top - 11, x, y + 15))
        x += w
    if note:
        items.append(("t", 56, y - 4, 8.5, note, False))
    return items


YEARS = ["2022", "2023", "2024", "2025"]
GROUP = {
    "Revenue": [2.64, 2.91, 3.48, 3.84],
    "Cost of sales": [1.12, 1.27, 1.49, 1.71],
    "Gross profit": [1.52, 1.64, 1.99, 2.13],
    "Operating costs": [0.98, 1.06, 1.31, 1.44],
    "Operating profit": [0.54, 0.58, 0.68, 0.69],
}
STAFF = [112, 121, 139, 148]
SITES = {
    "Northgate": [4812, 5207, 6130, 4398],
    "Riverside": [3965, 4421, 5876, 4120],
    "Castle Hill": [6204, 6840, 8312, 5933],
    "Meadows": [2187, 2318, 2871, 2049],
    "Eastwood": [3390, 3093, 4417, 3309],
}
KPI = {  # 2024, 2025
    "Room occupancy (%)": [78.4, 81.2],
    "Visitor satisfaction (out of 5)": [4.3, 4.6],
    "Volunteers": [264, 246],
    "Energy use (MWh)": [412.8, 398.5],
}
SAMPLES = {  # pH, nitrogen mg/kg, phosphorus mg/kg, moisture %
    "S-01": [6.42, 18.6, 34.8, 21.4],
    "S-02": [6.24, 21.3, 43.8, 19.8],
    "S-03": [7.08, 16.8, 38.4, 24.1],
    "S-04": [5.91, 24.7, 48.3, 17.6],
    "S-05": [6.87, 19.2, 29.6, 22.9],
    "S-06": [7.31, 15.4, 31.2, 26.3],
    "S-07": [6.78, 20.1, 36.9, 20.7],
    "S-08": [6.09, 22.8, 41.5, 18.2],
    "S-09": [7.13, 17.5, 24.6, 25.0],
    "S-10": [6.55, 23.6, 45.1, 16.9],
}


def report_pdf() -> bytes:
    p1 = prose([
        "Riverside Heritage Trust - Annual report 2025",
        "",
        "The Trust looks after five heritage sites across the county and welcomes",
        "visitors throughout the year. This report sets out the group's results for",
        "the last four years, visitor numbers by site for 2025, and the indicators the",
        "board follows. All money figures are in millions of pounds unless stated.",
        "",
        "Income grew again this year, helped by the reopening of the Castle Hill",
        "galleries in the summer and a longer season at Riverside. Costs rose with",
        "the larger programme and the move to the new collections store.",
    ])
    p2 = prose(["Section 2 - Financial results", "", "The table below gives the group's results by year."]) + \
        table("Table 1. Group results (GBP million)", ["", *YEARS],
              [[k, *[f"{v:.2f}" for v in vals]] for k, vals in GROUP.items()]
              + [["Staff (year end)", *[str(v) for v in STAFF]]],
              top=690, widths=[170, 75, 75, 75, 75],
              note="Staff are counted as people employed on 31 December, not full-time equivalents.")
    p3 = prose(["Section 3 - Visitors", "", "Visitors to each site in 2025, by quarter."]) + \
        table("Table 2. Visitors by site and quarter, 2025", ["Site", "Q1", "Q2", "Q3", "Q4"],
              [[k, *[f"{v:,}" for v in vals]] for k, vals in SITES.items()],
              top=690, widths=[150, 80, 80, 80, 80])
    p4 = prose(["Section 4 - Key indicators", "", "The indicators the board follows, for the last two years."]) + \
        table("Table 3. Key indicators", ["Indicator", "2024", "2025"],
              [[k, *[(f"{v:g}" if isinstance(v, int) else f"{v:.1f}") for v in vals]] for k, vals in KPI.items()],
              top=690, widths=[230, 90, 90])
    return make_pdf([p1, p2, p3, p4])


def soil_pdf() -> bytes:
    p1 = prose([
        "Field trial 14 - soil sample results",
        "",
        "Ten samples were taken from the north field on 3 September and tested at the",
        "county laboratory. Results are on the next page.",
    ])
    p2 = table("Table A. Laboratory results by sample",
               ["Sample", "pH", "Nitrogen (mg/kg)", "Phosphorus (mg/kg)", "Moisture (%)"],
               [[k, f"{v[0]:.2f}", f"{v[1]:.1f}", f"{v[2]:.1f}", f"{v[3]:.1f}"] for k, v in SAMPLES.items()],
               top=780, widths=[80, 70, 110, 120, 100])
    return make_pdf([p1, p2])


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SERVICES = ["Highways", "Libraries", "Parks", "Waste", "Leisure", "Housing", "Planning", "Transport",
            "Culture", "Trading standards", "Coroners", "Registrars", "Archives", "Cemeteries",
            "Markets", "Car parks"]


def _monthly() -> dict:
    import random
    rnd = random.Random(14)
    return {s: [rnd.randint(120, 980) for _ in MONTHS] for s in SERVICES}


MONTHLY = _monthly()
BUDGET = {  # budget, actual (GBP thousand)
    "Adult social care": [48210, 49875],
    "Children's services": [31460, 32918],
    "Highways": [12730, 12084],
    "Housing": [8945, 9312],
    "Leisure": [4180, 4225],
    "Waste": [9870, 9561],
    "Libraries": [3215, 3109],
    "Corporate": [6430, 6612],
}
BANDS = "ABCDEFGH"
TAX_2425 = [1384.12, 1614.81, 1845.49, 2076.18, 2537.55, 2998.93, 3460.30, 4152.36]
TAX_2526 = [round(v * 1.0499, 2) for v in TAX_2425]
STAFF_BY = {"Finance": [46, 41.6], "Planning": [31, 28.4], "Legal": [18, 17.0], "IT": [27, 26.2],
            "HR": [22, 19.8], "Customer services": [58, 49.3]}
COMPLAINTS = {"Complaints": [412, 389, 457, 401], "Compliments": [206, 231, 198, 244],
              "Resolved within 10 days (%)": [82.5, 86.1, 79.4, 88.0]}


def _money(v: int) -> str:
    return f"{v:,}"


def _brackets(v: int) -> str:
    return f"({abs(v):,})" if v < 0 else f"{v:,}"


def columnwise_table(title: str, head: list[str], rows: list[list[str]], top: float,
                     widths: list[float]) -> list[tuple]:
    """As `table`, but its text written column by column, as some programs
    write a PDF: read in order, each column comes out whole, then the next."""
    items = [it for it in table(title, head, rows, top, widths) if it[0] == "l" or it[4] == title]
    x = 56
    for c, w in enumerate(widths):
        y = top - 26
        for r, cells in enumerate([head] + rows):
            cell = cells[c]
            cx = x + 4 if c == 0 else x + w - 6 - len(cell) * 5.0
            items.append(("t", cx, y, 9.5, cell, r == 0))
            y -= 21
        x += w
    return items


def scanned_table() -> bytes:
    """A table as a scanned page: a greyscale picture, a little soft."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    im = Image.new("L", (1240, 1754), 250)
    d = ImageDraw.Draw(im)
    try:
        font, bold = ImageFont.truetype("arial.ttf", 26), ImageFont.truetype("arialbd.ttf", 28)
    except OSError:
        font = bold = ImageFont.load_default()
    d.text((110, 140), "Appendix C. Complaints and compliments by quarter, 2025/26", font=bold, fill=20)
    cols = [110, 640, 790, 940, 1090]
    y = 230
    for r, (label, vals) in enumerate([("", ["Q1", "Q2", "Q3", "Q4"])] + list(COMPLAINTS.items())):
        d.text((cols[0], y), label, font=bold if r == 0 else font, fill=25)
        for c, v in enumerate(vals):
            s = v if isinstance(v, str) else (f"{v:.1f}" if isinstance(v, float) else str(v))
            d.text((cols[c + 1], y), s, font=bold if r == 0 else font, fill=25)
        d.line((100, y + 42, 1180, y + 42), fill=120, width=2)
        y += 58
    im = im.rotate(0.6, fillcolor=250).filter(ImageFilter.GaussianBlur(0.8))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=72)
    return buf.getvalue()


def budget_pdf() -> bytes:
    filler = [
        "The council sets its budget each February for the year from April. This",
        "document reports spending for the year so far, compares it with the budget,",
        "and sets out council tax for the coming year. Figures are in thousands of",
        "pounds (GBP 000) unless stated; brackets show an overspend.",
    ]
    tax_rows = lambda vals: [[f"Band {b}", f"{v:,.2f}"] for b, v in zip(BANDS, vals)]
    p1 = prose(["Northbrook District Council", "Budget monitoring and council tax report 2025/26", ""] + filler)
    p2 = prose(["1. Summary", ""] + filler * 3)
    p3 = prose(["2. Council tax last year", "", "The charges for 2024/25 were as follows."]) + \
        table("Table 2.1 Council tax by band, 2024/25 (GBP a year)", ["Band", "Charge"], tax_rows(TAX_2425),
              top=690, widths=[150, 110])
    p4 = prose(["3. Spending by service", ""] + filler * 2)
    widths = [92] + [37.5] * 12
    p5 = [("t", 15, 800, 10, "Table 3.1 Monthly spending by service, 2025/26 (GBP 000)", True)]
    y, x0 = 775, 15
    for r, cells in enumerate([["Service", *MONTHS]] + [[s, *[str(v) for v in MONTHLY[s]]] for s in SERVICES]):
        x = x0
        for c, cell in enumerate(cells):
            cx = x + 2 if c == 0 else x + widths[c] - 4 - len(cell) * 3.9
            p5.append(("t", cx, y, 7.5, cell, r == 0))
            x += widths[c]
        p5.append(("l", x0, y - 5, x0 + sum(widths), y - 5))
        y -= 17
    p6 = prose(["4. Budget and actual spending", "", "Brackets show an overspend."]) + \
        table("Table 4.1 Budget and actual, 2025/26 (GBP 000)", ["Service", "Budget", "Actual", "Variance"],
              [[k, _money(b), _money(a), _brackets(b - a)] for k, (b, a) in BUDGET.items()],
              top=690, widths=[170, 90, 90, 90])
    # the header's second line, as a two-line header
    p6 += [("t", 56 + 170 + 90 - 6 - 7 * 5.0, 690 - 26 - 11, 8.5, "2025/26", True),
           ("t", 56 + 170 + 180 - 6 - 7 * 5.0, 690 - 26 - 11, 8.5, "2025/26", True)]
    p7 = prose(["5. Council tax for 2025/26", "", "Charges rise by 4.99 per cent in every band."]) + \
        table("Table 5.1 Council tax by band, 2025/26 (GBP a year)", ["Band", "Charge"], tax_rows(TAX_2526),
              top=690, widths=[150, 110])
    p8 = prose(["6. Staff", "", "People employed on 31 March, and full-time equivalents."]) + \
        columnwise_table("Table 6.1 Staff by department", ["Department", "Staff", "FTE"],
                         [[k, str(v[0]), f"{v[1]:.1f}"] for k, v in STAFF_BY.items()],
                         top=690, widths=[170, 80, 80])
    p10 = prose(["8. Next steps", ""] + filler * 2)
    return make_pdf([p1, p2, p3, p4, p5, p6, p7, p8, scanned_table(), p10])


# --------------------------------------------------------- the questions --

def _col(i):
    return [v[i] for v in SAMPLES.values()]


QUESTIONS = [
    # (file, question, expected: number or name, decimals that count)
    ("report", "What was revenue in 2024?", 3.48, 2),
    ("report", "What was revenue in 2025?", 3.84, 2),
    ("report", "What was the operating profit in 2023?", 0.58, 2),
    ("report", "How many staff did the trust have at the end of 2022?", 112, 0),
    ("report", "What were operating costs in 2025?", 1.44, 2),
    ("report", "By how much did revenue grow from 2024 to 2025?", 3.84 - 3.48, 2),
    ("report", "What was the percentage increase in revenue from 2022 to 2025?", (3.84 - 2.64) / 2.64 * 100, 1),
    ("report", "What was the gross margin in 2025, as gross profit over revenue?", 2.13 / 3.84 * 100, 1),
    ("report", "What was the average cost of sales over the four years?", sum(GROUP["Cost of sales"]) / 4, 2),
    ("report", "How many visitors did Castle Hill have in Q3 2025?", 8312, 0),
    ("report", "How many visitors did Riverside have in 2025 in total?", sum(SITES["Riverside"]), 0),
    ("report", "Which site had the most visitors in Q2?", "Castle Hill", None),
    ("report", "How many more visitors did Northgate have than Meadows in Q4?", 4398 - 2049, 0),
    ("report", "How many visitors did Eastwood have in Q2?", 3093, 0),
    ("report", "What was room occupancy in 2025?", 81.2, 1),
    ("report", "How many volunteers were there in 2024?", 264, 0),
    ("report", "By how much did energy use fall from 2024 to 2025?", 412.8 - 398.5, 1),
    ("soil", "What was the pH of sample S-07?", 6.78, 2),
    ("soil", "What was the nitrogen level of sample S-03?", 16.8, 1),
    ("soil", "What was the phosphorus level of sample S-02?", 43.8, 1),
    ("soil", "Which sample had the highest phosphorus?", "S-04", None),
    ("soil", "What was the moisture of sample S-10?", 16.9, 1),
    ("soil", "What is the difference in nitrogen between S-02 and S-05?", 21.3 - 19.2, 1),
    ("soil", "What was the average pH of the ten samples?", sum(_col(0)) / 10, 2),
    ("soil", "What was the total phosphorus of samples S-01, S-02 and S-03?", 34.8 + 43.8 + 38.4, 1),
    ("soil", "How many times higher is phosphorus in S-04 than in S-09?", 48.3 / 24.6, 2),
    ("budget", "How much was spent on Libraries in August?", MONTHLY["Libraries"][7], 0),
    ("budget", "How much was spent on Waste in November?", MONTHLY["Waste"][10], 0),
    ("budget", "What was total Highways spending from January to March?", sum(MONTHLY["Highways"][:3]), 0),
    ("budget", "Which month had the highest spending on Parks?",
     MONTHS[MONTHLY["Parks"].index(max(MONTHLY["Parks"]))], None),
    ("budget", "How much was spent on Registrars in June?", MONTHLY["Registrars"][5], 0),
    ("budget", "How much was spent on Car parks in October?", MONTHLY["Car parks"][9], 0),
    ("budget", "What was the actual spend on Adult social care in 2025/26?", 49875, 0),
    ("budget", "What was the variance for Leisure?", 4225 - 4180, 0),
    ("budget", "By how much was Children's services over budget?", 32918 - 31460, 0),
    ("budget", "What was the 2025/26 budget for Housing?", 8945, 0),
    ("budget", "What was the band D council tax in 2024/25?", TAX_2425[3], 2),
    ("budget", "What is the band D council tax in 2025/26?", TAX_2526[3], 2),
    ("budget", "By how much did band B council tax rise from 2024/25 to 2025/26?", TAX_2526[1] - TAX_2425[1], 2),
    ("budget", "How many staff work in Planning?", 31, 0),
    ("budget", "What is the full-time equivalent in Customer services?", 49.3, 1),
    ("budget", "How many complaints were there in Q3?", 457, 0),
    ("budget", "What percentage of complaints were resolved within 10 days in Q2?", 86.1, 1),
]

NUMBER = re.compile(r"(?<![\w.])[-−]?\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> list[float]:
    out = []
    for m in NUMBER.findall(text):
        try:
            out.append(float(m.replace(",", "").replace("−", "-")))
        except ValueError:
            pass
    return out


def right(answer: str, expected, decimals) -> bool:
    if isinstance(expected, str):
        return expected.lower() in answer.lower()
    want = round(expected, decimals)
    for n in numbers_in(answer):
        for w in (want, want * 1000):        # "GBP 000" tables: £148,000 is 148
            if abs(n - w) <= 0.5 * 10 ** -decimals + 1e-9 or (w and abs(n - w) / abs(w) < 0.002):
                return True
    return False


# ------------------------------------------------------------- the runner --

class Server:
    def __init__(self, url: str, token: str):
        self.url, self.token = url.rstrip("/"), token

    def _req(self, method, path, body=None, headers=None, timeout=900):
        h = {"x-mindboard-token": self.token, **(headers or {})}
        req = urllib.request.Request(self.url + path, body, h, method=method)
        return urllib.request.urlopen(req, timeout=timeout)

    def upload(self, name: str, data: bytes) -> str:
        b = uuid.uuid4().hex
        body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
                f"Content-Type: application/pdf\r\n\r\n").encode() + data + f"\r\n--{b}--\r\n".encode()
        with self._req("POST", "/api/files", body, {"content-type": f"multipart/form-data; boundary={b}"}) as r:
            fid = json.load(r)["id"]
        for _ in range(900):
            with self._req("GET", f"/api/files/{fid}") as r:
                info = json.load(r)
            if info["status"] in ("ready", "failed"):
                if info["status"] != "ready":
                    raise SystemExit(f"{name} could not be prepared: {info}")
                return fid
            time.sleep(1)
        raise SystemExit(f"{name} took too long to prepare")

    def close(self, fid: str):
        try:
            self._req("DELETE", f"/api/files/{fid}").close()
        except Exception:
            pass

    def stream(self, path: str, body: dict) -> list[tuple[str, dict]]:
        out, ev = [], None
        with self._req("POST", path, json.dumps(body).encode(), {"content-type": "application/json"}) as r:
            for raw in r:
                line = raw.decode("utf-8").rstrip("\n")
                if line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:") and ev not in ("text", "thinking", "progress"):
                    out.append((ev, json.loads(line[5:].strip())))
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:7788")
    ap.add_argument("--token-file")
    ap.add_argument("--write-pdfs")
    ap.add_argument("--only", default="")
    ap.add_argument("--check", action="store_true", help="also press Double check on each answer")
    ap.add_argument("--out")
    a = ap.parse_args()
    pdfs = {"report": ("Riverside annual report 2025.pdf", report_pdf()),
            "soil": ("Field trial 14 soil results.pdf", soil_pdf()),
            "budget": ("Northbrook budget report 2025-26.pdf", budget_pdf())}
    if a.write_pdfs:
        folder = Path(a.write_pdfs)
        folder.mkdir(parents=True, exist_ok=True)
        for name, data in pdfs.values():
            (folder / name).write_bytes(data)
        print("written to", folder)
        return
    srv = Server(a.url, Path(a.token_file).read_text().strip())
    only = {int(x) for x in a.only.split(",") if x.strip()}
    fids = {k: srv.upload(name, data) for k, (name, data) in pdfs.items()}
    results = []
    try:
        for i, (file, q, expected, decimals) in enumerate(QUESTIONS, 1):
            if only and i not in only:
                continue
            t0 = time.time()
            evs = srv.stream(f"/api/files/{fids[file]}/ask", {"question": q})
            done = next((d for e, d in evs if e == "done"), {})
            answer = done.get("board") or done.get("answer", "")
            row = {"i": i, "question": q, "expected": expected if isinstance(expected, str) else round(expected, decimals or 0),
                   "right": right(answer, expected, decimals), "seconds": round(time.time() - t0, 1),
                   "answer": done.get("answer", ""),
                   "tools": [d for e, d in evs if e in ("tools", "calc")],
                   "error": next((d for e, d in evs if e == "error"), None)}
            if a.check and done.get("answer"):
                t1 = time.time()
                chk = srv.stream(f"/api/files/{fids[file]}/check",
                                 {"question": q, "answer": done["answer"]})
                got = next((d for e, d in chk if e == "checked"), {})
                row["checked"] = got
                fixed = got.get("answer", answer)
                row["right_after_check"] = right(fixed, expected, decimals)
                row["check_seconds"] = round(time.time() - t1, 1)
            results.append(row)
            mark = "ok " if row["right"] else "XX "
            extra = ""
            if "right_after_check" in row:
                extra = f"  after check: {'ok' if row['right_after_check'] else 'XX'} ({row['check_seconds']}s)"
            print(f"{mark}{i:2d} {row['seconds']:5.1f}s  want {row['expected']}  | {q}{extra}")
            if not row["right"]:
                print("      " + " ".join(answer.split())[:300])
    finally:
        for fid in fids.values():
            srv.close(fid)
    n = len(results)
    good = sum(r["right"] for r in results)
    print(f"\n{good} of {n} right")
    if a.check:
        print(f"{sum(r.get('right_after_check', False) for r in results)} of {n} right after Double check")
    if a.out:
        Path(a.out).write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
