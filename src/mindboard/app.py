"""FastAPI application. Binds 127.0.0.1 only; localhost is not a trust boundary,
so every /api route requires the per-install token (see DESIGN.md 6)."""

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from . import __version__
from .assets import (
    copy_asset, fetch_bytes, fetch_url, link_asset, parse_range, resolve, store_bytes,
    store_stream, stream_file, thumbnail,
)
from .boards import board_meta, conn_for, create_board, delete_board, list_boards
from .bundle import (
    BundleError, backup_filename, create_backup, export_board, import_bundle, is_backup,
    restore_backup,
)
from .ids import ulid
from .models import BoardCreate, BoardPatch, OpsRequest
from .ops import apply_envelopes, read_graph
from .paths import DataDir

WEB_DIR = Path(__file__).parent / "web"
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def create_app(dd: DataDir, token: str, dev: bool = False) -> FastAPI:
    app = FastAPI(title="Mindboard", version=__version__, docs_url=None, redoc_url=None)
    app.state.dd = dd
    app.state.token = token

    # ---------------------------------------------------------------- auth --
    @app.middleware("http")
    async def token_gate(request: Request, call_next):
        path = request.url.path
        supplied = (
            request.query_params.get("t")
            or request.headers.get("x-mindboard-token")
            or request.cookies.get("mb_token")
        )
        if path.startswith("/api/") and supplied != token:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        response = await call_next(request)
        if request.query_params.get("t") == token:
            response.set_cookie(
                "mb_token", token, httponly=True, samesite="strict", max_age=60 * 60 * 24 * 365
            )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def board_or_404(bid: str) -> dict[str, Any]:
        meta = board_meta(dd, bid)
        if meta is None:
            raise HTTPException(404, "no such board")
        return meta

    # -------------------------------------------------------------- boards --
    @app.get("/healthz")
    def healthz():
        return {"ok": True, "version": __version__, "data_dir": str(dd.root)}

    @app.get("/api/boards")
    def api_list_boards():
        return {"boards": list_boards(dd)}

    @app.post("/api/boards")
    def api_create_board(body: BoardCreate):
        return create_board(dd, body.title, body.description)

    @app.get("/api/boards/{bid}")
    def api_get_board(bid: str):
        return board_or_404(bid)

    @app.patch("/api/boards/{bid}")
    def api_patch_board(bid: str, body: BoardPatch):
        board_or_404(bid)
        patch = {k: v for k, v in body.model_dump().items() if v is not None}
        if patch:
            apply_envelopes(conn_for(dd, bid), bid,
                            [{"opId": ulid(), "op": {"kind": "board.update", "patch": patch}}])
        return board_or_404(bid)

    @app.delete("/api/boards/{bid}")
    def api_delete_board(bid: str):
        board_or_404(bid)
        return {"deleted": delete_board(dd, bid)}

    # --------------------------------------------------------------- graph --
    @app.get("/api/boards/{bid}/graph")
    def api_graph(bid: str):
        meta = board_or_404(bid)
        graph = read_graph(conn_for(dd, bid), bid)
        return {"board": meta, "revision": meta["revision"], **graph}

    @app.post("/api/boards/{bid}/ops")
    def api_ops(bid: str, body: OpsRequest):
        board_or_404(bid)
        try:
            return apply_envelopes(conn_for(dd, bid), bid, body.envelopes)
        except Exception as exc:
            raise HTTPException(400, str(exc))

    @app.get("/api/boards/{bid}/search")
    def api_search(bid: str, q: str = "", limit: int = 30):
        board_or_404(bid)
        q = q.strip()
        if not q:
            return {"results": []}
        c = conn_for(dd, bid)
        term = " ".join(f'"{w}"*' for w in re.findall(r"\w+", q))
        if not term:
            return {"results": []}
        try:
            rows = c.execute(
                """SELECT f.node_id, n.type, n.title, n.parent_id,
                          snippet(node_fts, 2, '', '', '...', 12) AS snip
                   FROM node_fts f JOIN node n ON n.id = f.node_id
                   WHERE node_fts MATCH ? LIMIT ?""", (term, limit)).fetchall()
        except Exception:
            return {"results": []}
        return {"results": [
            {"id": r["node_id"], "type": r["type"], "title": r["title"],
             "parent": r["parent_id"], "snippet": r["snip"]} for r in rows]}

    # -------------------------------------------------------------- assets --
    @app.post("/api/boards/{bid}/assets")
    def api_upload_asset(
        bid: str,
        file: UploadFile = File(...),
        node_id: Optional[str] = Form(None),
        role: str = Form("primary"),
        width: Optional[int] = Form(None),
        height: Optional[int] = Form(None),
        duration_ms: Optional[int] = Form(None),
    ):
        """Take a copy of the file. The board owns it from here on.

        Streamed rather than read whole: an hour of screen recording is several
        gigabytes, and this endpoint should not need that much memory to accept
        one. A plain `def` on purpose - it runs on a worker thread, so a long
        upload does not stall the rest of the app.
        """
        board_or_404(bid)
        c = conn_for(dd, bid)
        try:
            info = store_stream(
                dd, c, bid, file.file,
                declared_mime=file.content_type or "",
                orig_name=file.filename or "",
                width=width, height=height, duration_ms=duration_ms,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if node_id:
            link_asset(c, node_id, info["sha256"], role)
        return info

    @app.post("/api/boards/{bid}/assets/copy")
    def api_copy_asset(bid: str, payload: dict):
        """Copy one file from another board - what a node pasted across boards needs."""
        board_or_404(bid)
        src = (payload or {}).get("from_board", "")
        sha = (payload or {}).get("sha256", "")
        board_or_404(src)
        info = copy_asset(dd, src, conn_for(dd, src), bid, conn_for(dd, bid), sha)
        if info is None:
            raise HTTPException(404, "no such asset")
        return info

    @app.post("/api/boards/{bid}/assets/from-url")
    def api_asset_from_url(bid: str, payload: dict):
        board_or_404(bid)
        url = (payload or {}).get("url", "").strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "expected an http(s) URL")
        try:
            got = fetch_url(url)
        except Exception as exc:
            raise HTTPException(400, f"could not fetch that URL: {exc}")
        if got["kind"] == "link":
            # Store the preview picture locally, like any other media. A board
            # should still show what a link is when the network is not there.
            conn = conn_for(dd, bid)
            candidates = list((got.get("video") or {}).get("thumb_urls") or [])
            if got.get("image"):
                candidates.append(got["image"])
            thumb = None
            for candidate in candidates:
                fetched = fetch_bytes(candidate)
                if fetched is None:
                    continue
                payload, ctype = fetched
                try:
                    info = store_bytes(dd, conn, bid, payload, declared_mime=ctype,
                                       orig_name="preview", source_url=candidate)
                except Exception:
                    continue
                if info["mime"].startswith("image/") and info["bytes"] > 1024:
                    thumb = info["sha256"]
                    break
            return {**got, "thumb": thumb}
        info = store_bytes(
            dd, conn_for(dd, bid), bid, got["data"],
            declared_mime=got["mime"], orig_name=got["orig_name"], source_url=url,
        )
        return {"kind": "media", **info, "source_url": url}

    @app.get("/api/boards/{bid}/assets/{sha}")
    def api_get_asset(bid: str, sha: str, request: Request):
        board_or_404(bid)
        c = conn_for(dd, bid)
        path = resolve(dd, c, bid, sha)
        if path is None:
            raise HTTPException(404, "no such asset")
        row = c.execute("SELECT mime, orig_name FROM asset WHERE sha256=?", (sha,)).fetchone()
        mime = row["mime"]
        size = path.stat().st_size
        inline = mime.startswith(("image/", "video/", "audio/")) or mime == "application/pdf"
        name = SAFE_NAME.sub("_", row["orig_name"] or f"{sha[:12]}")
        base = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, max-age=31536000, immutable",
            "Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
        }
        rng = parse_range(request.headers.get("range", ""), size)
        if rng is None:
            return FileResponse(path, media_type=mime, headers=base)
        start, end = rng
        headers = {**base, "Content-Range": f"bytes {start}-{end}/{size}",
                   "Content-Length": str(end - start + 1)}
        return StreamingResponse(stream_file(path, start, end), status_code=206,
                                 media_type=mime, headers=headers)

    @app.get("/api/boards/{bid}/assets/{sha}/thumb")
    def api_get_thumb(bid: str, sha: str, size: int = 480):
        board_or_404(bid)
        p = thumbnail(dd, conn_for(dd, bid), bid, sha, size)
        if p is None:
            raise HTTPException(404, "no thumbnail")
        return FileResponse(p, media_type="image/webp",
                            headers={"Cache-Control": "private, max-age=31536000, immutable"})

    # ------------------------------------------------------- import/export --
    @app.get("/api/boards/{bid}/export")
    def api_export(bid: str):
        meta = board_or_404(bid)
        tmp = Path(tempfile.mkdtemp()) / f"{SAFE_NAME.sub('_', meta['title']) or 'board'}.mindboard"
        export_board(dd, bid, tmp)
        return FileResponse(
            tmp, media_type="application/zip", filename=tmp.name,
            background=BackgroundTask(lambda: shutil.rmtree(tmp.parent, ignore_errors=True)),
        )

    @app.get("/api/backup")
    def api_backup():
        """Every board, media included, as one ordinary zip."""
        tmp = Path(tempfile.mkdtemp()) / backup_filename()
        create_backup(dd, tmp)
        return FileResponse(
            tmp, media_type="application/zip", filename=tmp.name,
            background=BackgroundTask(lambda: shutil.rmtree(tmp.parent, ignore_errors=True)),
        )

    @app.post("/api/boards/import")
    async def api_import(file: UploadFile = File(...), mode: str = Form("skip")):
        """Takes either one .mindboard bundle or a whole backup zip."""
        data = await file.read()
        tmp = Path(tempfile.mkdtemp()) / "incoming.zip"
        tmp.write_bytes(data)
        try:
            if is_backup(tmp):
                result = restore_backup(dd, tmp, mode=mode)
                return {"kind": "backup", **result}
            return {"kind": "board", **import_bundle(dd, tmp)}
        except BundleError as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:
            raise HTTPException(400, f"could not read that file: {exc}")
        finally:
            shutil.rmtree(tmp.parent, ignore_errors=True)

    # -------------------------------------------------------------- static --
    index = WEB_DIR / "index.html"
    if index.exists():
        assets_dir = WEB_DIR / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="static-assets")

        @app.get("/{full_path:path}")
        def spa(full_path: str):
            candidate = WEB_DIR / full_path
            if full_path and candidate.is_file() and WEB_DIR in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(index, media_type="text/html")
    else:
        @app.get("/{full_path:path}")
        def no_ui(full_path: str):
            return Response(
                "<h1 style='font:600 16px system-ui;padding:2rem'>Mindboard backend is running, "
                "but the interface was not built.<br><small style='font-weight:400'>Run "
                "<code>cd web &amp;&amp; npm install &amp;&amp; npm run build</code></small></h1>",
                media_type="text/html", status_code=200,
            )

    return app
