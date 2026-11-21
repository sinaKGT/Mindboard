"""Double check: an answer's numbers looked up again (DESIGN.md 82)."""

import json
from types import SimpleNamespace

from mindboard import doublecheck
from mindboard.filechat import Chunk

PAGE = ("Table 1. Group results (GBP million)\n2022 2023 2024 2025\nRevenue 2.64 2.91 3.48 3.84\n"
        "Staff (year end) 112 121 139 148")


class Model:
    """Plays the model: what it copies from a page, and the calculation it
    asks for - and remembers what it was shown."""

    def __init__(self, checks=None, calcs=None):
        self.checks, self.calcs, self.asked = checks or [], calcs or [], []

    def stream(self, messages, **kw):
        system = messages[0]["content"]
        self.asked.append(messages)
        reply = ({"checks": self.checks} if system.startswith("You look numbers up")
                 else {"calculations": self.calcs})
        yield {"type": "text", "text": json.dumps(reply)}
        yield {"type": "done"}

    def cancelled(self):
        return False


def session(pages=None):
    chunks = [Chunk(where, text, page=int(where.split()[-1])) for where, text in (pages or {"p. 2": PAGE}).items()]
    return SimpleNamespace(index=SimpleNamespace(chunks=chunks), kind="text", name="r.pdf", path=None)


def run(model, answer, question="What was revenue in 2024?", s=None):
    evs = list(doublecheck.check(model, s or session(), question, answer, []))
    return evs[-1]


def test_the_numbers_worth_checking():
    answer = ("1. Revenue in 2024 was £3.48m (p. 2) [[01ABCDEFGHJKMNPQRSTVWXYZ00]].\n"
              "2. Staff were 139, and Q3 was quiet; S-07 had $x^2$ errors.")
    got = [c["text"] for c in doublecheck.claims_in(answer, "revenue and staff in 2024?")]
    assert got == ["3.48", "139"], "no page, id, list number, asked year, Q3, S-07 or formula"
    first = doublecheck.claims_in(answer, "")[0]
    assert first["pages"] == ["p. 2"]


def test_numbers_that_name_things_are_not_checked():
    """Measured: the years of a list were "corrected" into the values beside
    them, and "Table 2" into 6,840."""
    answer = ("In Table 4.1, band D was 2,076.18 in 2024 (p. 3).\n*   2022: 1.12 (p. 2)\n"
              "Q2 had 3,093 (GBP 000), sample 3 had 7.08 and Section 2 says 45.")
    got = [c["text"] for c in doublecheck.claims_in(answer, "what was it?")]
    assert got == ["2,076.18", "1.12", "3,093", "7.08", "45"]


def test_a_copy_from_another_row_than_it_names_confirms_nothing():
    page = ("Table 4.1 Budget and actual\nService Budget Actual Variance\n"
            "Adult social care 48,210 49,875 (1,665)\nChildren’s services 31,460 32,918 (1,458)")
    s = session({"p. 6": page})
    liar = Model(checks=[{"n": 1, "found": "49,875", "row": "Children's services", "column": "Actual"}])
    got = run(liar, "Children's services spent 49,875 (p. 6).", "What did children's services spend?", s)
    assert got["answer"] == "Children's services spent 32,918 (p. 6).", "49,875 is the adult row's: the cell is read"
    nocol = Model(checks=[{"n": 1, "found": "49,875", "row": "Children's services", "column": ""}])
    got = run(nocol, "Children's services spent 49,875 (p. 6).", "What did children's services spend?", s)
    assert got["marks"][0]["status"] == "unchecked", "no column to read, and not on its row's line"
    right = Model(checks=[{"n": 1, "found": "32,918", "row": "Children's services", "column": "Actual"}])
    got = run(right, "Children's services spent 49,875 (p. 6).", "What did children's services spend?", s)
    assert got["marks"][0]["status"] == "fixed" and got["answer"] == "Children's services spent 32,918 (p. 6)."


MONTHS = ("Table 3.1 Monthly spending\nService Jan Feb Mar Apr\nWaste 412 465 148 233\n"
          "Operating costs 1.12 1.27 1.49 1.71\nOperating profit 0.54 0.58 0.68 0.69")


def test_the_cell_a_row_and_column_name_is_read_by_the_code():
    assert doublecheck.cell("Waste", "Mar", MONTHS) == "148"
    assert doublecheck.cell("Operating profit", "Feb", MONTHS) == "0.58", "not the costs row"
    assert doublecheck.cell("Waste", "Dec", MONTHS) is None, "no such column"
    assert doublecheck.cell("Parks", "Mar", MONTHS) is None, "no such row"
    assert doublecheck.cell("Revenue", "2024", PAGE) == "3.48", "a header with no first cell"


def test_a_misread_cell_is_read_again_by_the_code():
    """Told "Waste, Mar", the model copied 465 - the row's, another month's."""
    model = Model(checks=[{"n": 1, "found": "465", "row": "Waste", "column": "Mar"}])
    got = run(model, "Waste cost £148 in March (p. 5).", "How much was spent on Waste in March?",
              session({"p. 5": MONTHS}))
    assert got["marks"][0]["status"] == "ok" and got["answer"] == "Waste cost £148 in March (p. 5)."
    wrong = run(Model(checks=[{"n": 1, "found": "£465", "row": "Waste", "column": "Feb"}]),
                "Waste cost £148 in February (p. 5).", "Waste in February?", session({"p. 5": MONTHS}))
    assert wrong["answer"] == "Waste cost £465 in February (p. 5).", "corrected, the answer's own £ kept"


def test_a_wrong_cell_is_corrected_from_the_page():
    model = Model(checks=[{"n": 1, "found": "3.48", "row": "Revenue", "column": "2024"},
                          {"n": 2, "found": "139", "row": "Staff (year end)", "column": "2024"}])
    got = run(model, "Revenue in 2024 was £3.84m (p. 2), with 139 staff (p. 2).")
    assert got["answer"] == "Revenue in 2024 was £3.48m (p. 2), with 139 staff (p. 2)."
    assert got["marks"][0] == {"text": "3.48", "status": "fixed", "was": "3.84", "note": "p. 2, Revenue, 2024"}
    assert got["marks"][1]["status"] == "ok"
    assert got["summary"] == "2 numbers: 1 checked, 1 corrected"
    assert got["marked"] == ("Revenue in 2024 was £⟦fixed|3.48|3.84|p. 2, Revenue, 2024⟧m (p. 2), with "
                             "⟦ok|139||p. 2, Staff (year end), 2024⟧ staff (p. 2).")
    shown = model.asked[0][1]["content"]
    assert "Revenue 2.64 2.91 3.48 3.84" in shown[0]["text"], "the page's rows, as text"
    assert "3.84" in shown[-1]["text"] and "QUESTION ASKED" in shown[-1]["text"]


def test_a_copy_that_is_not_on_the_page_proves_nothing():
    model = Model(checks=[{"n": 1, "found": "3.58", "row": "Revenue", "column": "2024"}])
    got = run(model, "Revenue in 2024 was 3.84 (p. 2).")
    assert got["answer"] == "Revenue in 2024 was 3.84 (p. 2)."
    assert got["marks"][0]["status"] == "unchecked" and "not confirmed" in got["marks"][0]["note"]


def test_a_number_on_no_page_is_worked_out_again_by_the_calculator():
    model = Model(checks=[{"n": 1, "found": "", "row": "", "column": ""}],
                  calcs=[{"label": "growth", "op": "difference",
                          "values": [{"value": "3.84", "where": "p. 2"}, {"value": "3.48", "where": "p. 2"}]}])
    got = run(model, "Revenue grew by 0.40 (p. 2) (calculated).", "By how much did revenue grow from 2024 to 2025?")
    assert got["answer"] == "Revenue grew by 0.36 (p. 2) (calculated)."
    assert got["marks"][0]["status"] == "fixed" and got["marks"][0]["note"].startswith("calculator:")
    ok = run(Model(calcs=model.calcs), "Revenue grew by 0.36 (p. 2).", "By how much did revenue grow?")
    assert ok["marks"][0]["status"] == "ok"


def test_a_page_the_file_does_not_have_falls_back_to_a_search():
    model = Model(checks=[{"n": 1, "found": "112", "row": "Staff", "column": "2022"}])
    s = session()
    evs = list(doublecheck.check(model, s, "How many staff in 2022?", "There were 148 staff (p. 12).", [],
                                 find=lambda q: s.index.chunks))
    assert evs[0]["pages"] == ["p. 2"] and evs[-1]["marks"][0]["status"] == "fixed"
    assert evs[-1]["answer"] == "There were 112 staff (p. 12)."


def test_nothing_to_check():
    got = run(Model(), "The report does not say.")
    assert got["marks"] == [] and got["answer"] == "The report does not say."
