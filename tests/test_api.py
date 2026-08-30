"""HTTP surface: auth, ops, media range requests, export/import."""

from fastapi.testclient import TestClient

from mindboard.app import create_app
from mindboard.ids import ulid

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)
TOKEN = "test-token"


def client(dd):
    return TestClient(create_app(dd, TOKEN))


def auth():
    return {"X-Mindboard-Token": TOKEN}


def test_api_requires_the_token(dd):
    c = client(dd)
    assert c.get("/api/boards").status_code == 401
    assert c.get("/api/boards", headers=auth()).status_code == 200
    assert c.get("/healthz").status_code == 200  # health check stays open


def test_board_lifecycle_and_ops(dd):
    c = client(dd)
    b = c.post("/api/boards", json={"title": "Ideas", "description": "d"}, headers=auth()).json()
    bid = b["id"]

    n = {"id": ulid(), "type": "text", "pos": [5, 5], "title": "seed",
         "content": {"text": "start here"}}
    r = c.post(f"/api/boards/{bid}/ops", headers=auth(),
               json={"envelopes": [{"opId": ulid(), "op": {"kind": "node.create", "node": n}}]})
    assert r.status_code == 200 and r.json()["rejected"] == []

    g = c.get(f"/api/boards/{bid}/graph", headers=auth()).json()
    assert len(g["nodes"]) == 1 and g["revision"] == 1

    assert c.get(f"/api/boards/{bid}/search?q=start", headers=auth()).json()["results"][0]["id"] == n["id"]

    listed = c.get("/api/boards", headers=auth()).json()["boards"]
    assert listed[0]["node_count"] == 1


def test_asset_upload_and_range(dd):
    c = client(dd)
    bid = c.post("/api/boards", json={"title": "M"}, headers=auth()).json()["id"]
    up = c.post(f"/api/boards/{bid}/assets", headers=auth(),
                files={"file": ("dot.png", PNG, "image/png")}).json()
    assert up["mime"] == "image/png" and up["width"] == 1

    full = c.get(f"/api/boards/{bid}/assets/{up['sha256']}", headers=auth())
    assert full.status_code == 200 and full.content == PNG
    assert full.headers["accept-ranges"] == "bytes"

    part = c.get(f"/api/boards/{bid}/assets/{up['sha256']}",
                 headers={**auth(), "Range": "bytes=0-9"})
    assert part.status_code == 206
    assert part.content == PNG[:10]
    assert part.headers["content-range"] == f"bytes 0-9/{len(PNG)}"


def test_export_then_import_over_http(dd):
    c = client(dd)
    bid = c.post("/api/boards", json={"title": "Round trip"}, headers=auth()).json()["id"]
    n = {"id": ulid(), "type": "text", "pos": [0, 0], "title": "keep me",
         "content": {"text": "x"}}
    c.post(f"/api/boards/{bid}/ops", headers=auth(),
           json={"envelopes": [{"opId": ulid(), "op": {"kind": "node.create", "node": n}}]})

    blob = c.get(f"/api/boards/{bid}/export", headers=auth())
    assert blob.status_code == 200 and blob.content[:2] == b"PK"

    imported = c.post("/api/boards/import", headers=auth(),
                      files={"file": ("b.mindboard", blob.content, "application/zip")}).json()
    assert imported["node_count"] == 1 and imported["id"] != bid
    assert len(c.get("/api/boards", headers=auth()).json()["boards"]) == 2
