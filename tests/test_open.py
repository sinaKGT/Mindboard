"""Opening a board's file in another app - a PDF in your own PDF reader.

The launcher itself is replaced in every test here: running the suite must never
open a window on the screen of whoever runs it. What these pin is everything
around the launch - what gets handed over, under what name, and what is refused.
"""

import hashlib
import os

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from mindboard import runtime
from mindboard.app import create_app
from mindboard.assets import safe_stem, store_bytes
from mindboard.boards import conn_for, create_board, delete_board
from mindboard.cli import app as cli_app
from mindboard.ids import ulid
from mindboard.ops import apply_envelopes

TOKEN = "test-token"
PDF = b"%PDF-1.4\n1 0 obj <<>> endobj\ntrailer <<>>\n%%EOF\n"
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)


def auth():
    return {"X-Mindboard-Token": TOKEN}


@pytest.fixture()
def launched(monkeypatch):
    """Every path the app tried to open, instead of opening it."""
    calls = []
    monkeypatch.setattr(runtime, "open_file", lambda path: calls.append(path))
    return calls


@pytest.fixture()
def client(dd):
    return TestClient(create_app(dd, TOKEN))


def store(dd, bid, data, name, mime="application/pdf"):
    return store_bytes(dd, conn_for(dd, bid), bid, data, declared_mime=mime,
                       orig_name=name)["sha256"]


def open_url(bid, sha):
    return f"/api/boards/{bid}/assets/{sha}/open"


def test_opens_a_read_only_copy_under_its_real_name(dd, board, client, launched):
    bid = board["id"]
    sha = store(dd, bid, PDF, "Siting notes.pdf")

    res = client.post(open_url(bid, sha), headers=auth())

    assert res.status_code == 200, res.text
    assert res.json() == {"opened": "Siting notes.pdf"}
    assert len(launched) == 1
    copy = launched[0]
    assert copy.name == "Siting notes.pdf"
    assert copy.read_bytes() == PDF
    assert not os.access(copy, os.W_OK), "the reader must not be able to save over it"
    # a copy, kept outside the board, and the board's own file untouched
    assert dd.opened in copy.parents
    assert dd.board_dir(bid) not in copy.parents
    own = dd.asset_path(bid, sha, ".pdf")
    assert hashlib.sha256(own.read_bytes()).hexdigest() == sha
    assert os.access(own, os.W_OK)


def test_opening_again_reuses_the_copy(dd, board, client, launched):
    bid = board["id"]
    sha = store(dd, bid, PDF, "notes.pdf")
    assert client.post(open_url(bid, sha), headers=auth()).status_code == 200
    # the first copy is read-only now; reopening must not trip over that
    assert client.post(open_url(bid, sha), headers=auth()).status_code == 200
    assert launched[0] == launched[1]


def test_two_files_with_one_name_stay_apart(dd, board, client, launched):
    bid = board["id"]
    a = store(dd, bid, PDF, "notes.pdf")
    b = store(dd, bid, PDF + b"% another\n", "notes.pdf")
    client.post(open_url(bid, a), headers=auth())
    client.post(open_url(bid, b), headers=auth())
    assert launched[0].name == launched[1].name == "notes.pdf"
    assert launched[0] != launched[1]
    assert launched[0].read_bytes() != launched[1].read_bytes()


def test_a_picture_is_not_handed_over(dd, board, client, launched):
    bid = board["id"]
    sha = store(dd, bid, PNG, "shot.png", "image/png")
    res = client.post(open_url(bid, sha), headers=auth())
    assert res.status_code == 415
    assert launched == []


def test_a_program_dressed_as_a_pdf_is_refused(dd, board, client, launched):
    """Unknown bytes fall back to the declared type at upload, so this is stored
    as a PDF. Its first bytes say otherwise, and those decide."""
    bid = board["id"]
    sha = store(dd, bid, b"MZ\x90\x00\x03 not a pdf at all", "invoice.pdf")
    row = conn_for(dd, bid).execute("SELECT mime FROM asset WHERE sha256=?", (sha,)).fetchone()
    assert row["mime"] == "application/pdf"     # the premise of this test

    res = client.post(open_url(bid, sha), headers=auth())

    assert res.status_code == 415
    assert "not really a PDF" in res.json()["detail"]
    assert launched == []
    assert not dd.opened.exists() or not any(dd.opened.rglob("*.pdf"))


def test_only_the_boards_own_files_with_the_key(dd, board, client, launched):
    bid = board["id"]
    sha = store(dd, bid, PDF, "notes.pdf")
    other = create_board(dd, "Other", "")["id"]

    assert client.post(open_url(bid, "0" * 64), headers=auth()).status_code == 404
    assert client.post(open_url(other, sha), headers=auth()).status_code == 404
    assert client.post(open_url("01NOSUCHBOARD0000000000000", sha),
                       headers=auth()).status_code == 404
    assert client.post(open_url(bid, sha)).status_code == 401
    assert launched == []


def test_says_so_when_nothing_opens_pdfs(dd, board, client, monkeypatch):
    def no_app(path):
        raise OSError("no application is associated with the specified file")
    monkeypatch.setattr(runtime, "open_file", no_app)
    bid = board["id"]
    sha = store(dd, bid, PDF, "notes.pdf")

    res = client.post(open_url(bid, sha), headers=auth())

    assert res.status_code == 409
    assert res.json()["detail"] == "no app on this computer opens PDF files"


@pytest.mark.parametrize("given, expected", [
    ("Siting notes.pdf", "Siting notes"),
    ("report.final.pdf", "report.final"),
    ("CON.pdf", "_CON"),
    ("lpt1.backup.pdf", "_lpt1.backup"),
    ('a<b>:c|d?.pdf', "a_b_c_d_"),
    ("", "document"),
    ("   .pdf", "document"),
])
def test_names_every_os_accepts(given, expected):
    assert safe_stem(given) == expected


def test_names_never_climb_out_of_their_folder():
    for given in ["../../etc/passwd", "..\\..\\Windows\\win.ini", "/abs/path.pdf"]:
        stem = safe_stem(given)
        assert "/" not in stem and "\\" not in stem and not stem.startswith(".")
    assert len(safe_stem("x" * 400 + ".pdf")) == 120


def test_gc_clears_copies_but_keeps_the_file(dd, board, client, launched):
    bid = board["id"]
    sha = store(dd, bid, PDF, "notes.pdf")
    node = {"id": ulid(), "type": "file", "pos": [0, 0], "title": "Notes",
            "content": {"sha256": sha, "filename": "notes.pdf"}}
    apply_envelopes(conn_for(dd, bid), bid,
                    [{"opId": ulid(), "op": {"kind": "node.create", "node": node}}])
    client.post(open_url(bid, sha), headers=auth())
    assert launched[0].exists()

    res = CliRunner().invoke(cli_app, ["gc", "--data-dir", str(dd.root)])

    assert res.exit_code == 0, res.output
    assert not launched[0].exists()
    assert dd.asset_path(bid, sha, ".pdf").exists()


def test_deleting_a_board_takes_its_copies(dd, board, client, launched):
    bid = board["id"]
    sha = store(dd, bid, PDF, "notes.pdf")
    client.post(open_url(bid, sha), headers=auth())
    assert dd.opened_dir(bid).exists()

    assert delete_board(dd, bid)

    assert not dd.opened_dir(bid).exists()
