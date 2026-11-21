"""The calculator for questions about a file (DESIGN.md 82)."""

from decimal import Decimal

import pytest

from mindboard import calc

PAGES = {"p. 3": "Table 2. Visitors by site and quarter, 2025\nSite Q1 Q2 Q3 Q4\n"
                 "Riverside 3,965 4,421 5,876 4,120\nMeadows 2,187 2,318 2,871 2,049",
         "p. 6": "Children's services 31,460 32,918 (1,458)\nRoom occupancy (%) 78.4 81.2"}


def v(value, where="p. 3", what="x"):
    return {"value": value, "where": where, "what": what}


@pytest.mark.parametrize("text, want", [
    ("3,965", Decimal("3965")), ("£1.25", Decimal("1.25")), ("(1,458)", Decimal("-1458")),
    ("12.5%", Decimal("12.5")), ("−4", Decimal("-4")), ("abc", None), ("", None), ("nan", None),
])
def test_numbers_as_documents_write_them(text, want):
    assert calc.number(text) == want


def test_a_number_counts_only_where_it_is_written():
    assert calc.written_on("4,421", "p. 3", PAGES) and calc.written_on("4421", "P. 3", PAGES)
    assert not calc.written_on("4,421", "p. 6", PAGES), "on another page"
    assert not calc.written_on("421", "p. 3", PAGES), "part of a longer number is not it"
    assert calc.written_on("1,458", "p. 6", PAGES) and calc.written_on("78.4", "p. 6", PAGES)
    assert not calc.written_on("3,966", "p. 3", PAGES)


def test_a_number_is_found_on_its_page_or_the_one_page_that_has_it():
    """The model often names the table, not the page."""
    assert calc.find_page("4,421", "p. 3", PAGES) == "p. 3"
    assert calc.find_page("4,421", "Table 2, p. 3", PAGES) == "p. 3"
    assert calc.find_page("4,421", "Table 2", PAGES) == "p. 3", "the one page with it"
    assert calc.find_page("4,412", "p. 3", PAGES) is None
    both = {**PAGES, "p. 13": "Riverside 4,421"}
    assert calc.find_page("4,421", "Table 2", both) is None, "on two pages, none cited: cannot tell"
    assert calc.find_page("4,421", "p. 13", both) == "p. 13"


def test_a_total_is_worked_out_exactly_from_checked_numbers():
    got = calc.run({"label": "Riverside 2025", "op": "sum",
                    "values": [v("3,965"), v("4,421"), v("5,876"), v("4,120")]}, PAGES)
    assert got["error"] == "" and got["result"] == "18,382"
    assert "Riverside 2025 = 18,382" in got["said"] and "4,421 (x, p. 3)" in got["said"]


@pytest.mark.parametrize("op, values, want", [
    ("difference", [v("4,120"), v("2,049")], "2,071"),
    ("average", [v("78.4", "p. 6"), v("81.2", "p. 6")], "79.80"),
    ("percent_change", [v("78.4", "p. 6"), v("81.2", "p. 6")], "3.6%"),
    ("percent_of", [v("2,049"), v("4,120")], "49.7%"),
    ("ratio", [v("4,120"), v("2,049")], "2.01"),
    ("max", [v("3,965"), v("4,421")], "4,421"),
    ("difference", [v("31,460", "p. 6"), v("(1,458)", "p. 6")], "32,918"),
])
def test_each_operation(op, values, want):
    got = calc.run({"label": "it", "op": op, "values": values}, PAGES)
    assert got["error"] == "" and got["result"] == want, got


def test_a_number_not_on_its_page_is_not_used():
    """Facts only: the calculation is refused and said, never worked out from
    a number the model may have misread or made up."""
    got = calc.run({"label": "gap", "op": "difference", "values": [v("4,412"), v("2,049")]}, PAGES)
    assert got["error"] and "not found in the passages" in got["said"] and "4,412" in got["said"]
    assert "result" not in got


@pytest.mark.parametrize("bad", [
    {"label": "x", "op": "eval", "values": [v("3,965")]},
    {"label": "x", "op": "difference", "values": [v("3,965")]},
    {"label": "x", "op": "sum", "values": []},
    {"label": "x", "op": "ratio", "values": [v("3,965"), {"value": "0", "where": "p. 3"}]},
])
def test_what_cannot_be_done_is_said(bad):
    got = calc.run(bad, {"p. 3": PAGES["p. 3"] + "\n0"})
    assert got["error"] and "result" not in got


def test_which_questions_need_working_out():
    for q in ("How many visitors in total?", "By how much did revenue grow?", "What was the average pH?",
              "What percentage were resolved?", "How many times higher is it?", "over budget by how much?"):
        assert calc.needs(q), q
    for q in ("What was revenue in 2024?", "Who visited in June?", "What is the pH of S-07?"):
        assert not calc.needs(q), q


def test_what_the_answer_reads():
    ok = calc.run({"label": "t", "op": "sum", "values": [v("3,965"), v("4,421")]}, PAGES)
    bad = calc.run({"label": "u", "op": "sum", "values": [v("1")]}, PAGES)
    text = calc.calculated_text([ok, bad], "")
    assert text.startswith("CALCULATED") and "t = 8,386" in text
    assert "NOT CALCULATED: u:" in text and "Do not work these out yourself" in text
    assert calc.calculated_text([], "") == ""
