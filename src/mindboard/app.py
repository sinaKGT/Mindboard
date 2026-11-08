"""FastAPI application. Binds 127.0.0.1 only; localhost is not a trust boundary,
so every /api route requires the per-install token (see DESIGN.md 6)."""

import json
import queue
import re
import threading
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from . import __version__, runtime
from .assets import (
    NotOpenable, copy_asset, fetch_bytes, fetch_url, link_asset, parse_range, resolve,
    store_bytes, store_stream, stream_file, thumbnail, working_copy,
)
from .boards import board_meta, conn_for, create_board, delete_board, list_boards
from .bundle import (
    BundleError, backup_filename, create_backup, export_board, import_bundle, is_backup,
    restore_backup,
)
from .ids import ulid
from .models import AskRequest, BoardCreate, BoardPatch, DraftRequest, OpsRequest
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
        indexer.want(bid)              # opened: get its search index ready meanwhile
        return {"board": meta, "revision": meta["revision"], **graph}

    @app.post("/api/boards/{bid}/ops")
    def api_ops(bid: str, body: OpsRequest):
        board_or_404(bid)
        try:
            result = apply_envelopes(conn_for(dd, bid), bid, body.envelopes)
        except Exception as exc:
            raise HTTPException(400, str(exc))
        indexer.want(bid)              # changed: its new cards get their vectors
        return result

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

    @app.post("/api/boards/{bid}/assets/{sha}/open")
    def api_open_asset(bid: str, sha: str):
        """Open one of the board's files in this computer's own app for it - a
        PDF in your PDF reader.

        A browser cannot start an app; this server can, because it runs on the
        same machine as the person using it. What it opens is a read-only copy
        under the file's real name (assets.working_copy), found by hash in this
        board's own records - never a path from the request. Not an op: opening
        a file changes nothing on the board.
        """
        board_or_404(bid)
        try:
            path = working_copy(dd, conn_for(dd, bid), bid, sha)
        except FileNotFoundError:
            raise HTTPException(404, "no such file on this board")
        except NotOpenable as exc:
            raise HTTPException(415, str(exc))
        try:
            runtime.open_file(path)
        except OSError:
            raise HTTPException(
                409, f"no app on this computer opens {path.suffix.lstrip('.').upper()} files")
        return {"opened": path.name}

    @app.get("/api/boards/{bid}/assets/{sha}/thumb")
    def api_get_thumb(bid: str, sha: str, size: int = 480):
        board_or_404(bid)
        p = thumbnail(dd, conn_for(dd, bid), bid, sha, size)
        if p is None:
            raise HTTPException(404, "no thumbnail")
        return FileResponse(p, media_type="image/webp",
                            headers={"Cache-Control": "private, max-age=31536000, immutable"})

    # ----------------------------------------------------------------- ask --
    held: dict[str, Any] = {}
    app.state.ask = held

    def stop_model() -> None:
        indexer.stop()
        for key in ("model", "embed", "rerank"):
            if held.get(key) is not None:
                held[key].stop()

    app.router.on_shutdown.append(stop_model)

    def local_model():
        """One model for the whole app, made on first use. None until set up."""
        from . import llm

        inst = llm.find_install()
        if inst is None:
            return None
        current = held.get("model")
        if current is None or current.inst.model != inst.model:
            if current is not None:
                current.stop()
            current = held["model"] = llm.LocalModel(inst)
        return current

    def search_model(key: str):
        """A search model - "embed" for finding cards by meaning, "rerank" for
        reading candidates closely - made on first use. None when it is not
        installed: search then goes without it. Where each runs is settled
        between the models themselves (llm.make_room)."""
        from . import llm

        inst = llm.find_install()
        path = None if inst is None else inst.embedder if key == "embed" else inst.reranker
        if path is None:
            return None
        current = held.get(key)
        if current is None or (
                (current.inst.embedder if key == "embed" else current.inst.reranker) != path):
            if current is not None:
                current.stop()
            kind = llm.EmbedModel if key == "embed" else llm.RerankModel
            current = held[key] = kind(inst)
        return current

    # Cards are embedded in the background - when a board is opened and after
    # each change - so a question never waits for it (indexer.py)
    from .indexer import Indexer

    # it describes pictures with the chat model, but only while that is loaded
    indexer = Indexer(dd, lambda: search_model("embed"), describer=lambda: held.get("model"))
    app.state.indexer = indexer

    @app.post("/api/boards/{bid}/ask/draft")
    def api_ask_draft(bid: str, body: DraftRequest):
        """A card drafted from an answer, for the person to check. Writes
        nothing: the card is added by the interface, through the op-log,
        only when the person presses Add."""
        from .ask import draft_card

        board_or_404(bid)
        model = local_model()
        if model is None:
            raise HTTPException(503, "Ask your board is not set up yet")
        if not model._asking.acquire(blocking=False):
            raise HTTPException(409, "still answering the last question")
        try:
            nodes = {n["id"]: n for n in read_graph(conn_for(dd, bid), bid)["nodes"]}
            return draft_card(model, nodes, body.question, body.answer, body.cited)
        finally:
            model._asking.release()

    @app.post("/api/boards/{bid}/ask")
    async def api_ask(bid: str, body: AskRequest, request: Request):
        """Ask the local model about exactly these cards. Streams server-sent
        events: context, thinking, text..., then done with the checked answer.
        Reads the board only - nothing is written."""
        from starlette.concurrency import run_in_threadpool

        from .ask import AskError, answer_events, build, check_fits

        history = [t.model_dump() for t in body.history]

        def prepare():
            board_or_404(bid)
            model = local_model()
            if model is None:
                raise HTTPException(503, "Ask your board is not set up yet - choose 8 in the "
                                         "Mindboard menu, or run: mindboard ai install")
            if model._asking.locked():
                raise HTTPException(409, "still answering the last question")
            try:
                emb = search_model("embed")
                ctx = build(dd, conn_for(dd, bid), bid, body.cards, body.question, history,
                            emb, emb.inst.embedder.name if emb else "", search_model("rerank"),
                            background=True, keep=body.keep, explain=body.explain)
            except AskError as exc:
                raise HTTPException(400, str(exc))
            finally:
                indexer.want(bid)      # whatever the question did not embed, the indexer will
            try:
                check_fits(ctx, body.question, history, body.think)
            except AskError as exc:
                raise HTTPException(413, str(exc))
            return model, ctx, model.memory_note()

        model, ctx, warning = await run_in_threadpool(prepare)
        loading = not model.running()

        # The answer is made on its own thread and handed over through a queue.
        # It used to be pulled piece by piece by the response itself - and when
        # a browser hung up, this server never heard: its send to the closed
        # connection waited forever, nothing pulled the next piece, and the
        # model sat mid-answer holding the one slot, so every later question
        # was told "still answering". Now the response can stall without
        # stalling the model, and Stop reaches the model directly (ask/stop).
        pieces: "queue.Queue[Optional[dict]]" = queue.Queue()

        def produce() -> None:
            try:
                for item in answer_events(model, ctx, body.question, history, body.think):
                    pieces.put(item)
            except Exception as exc:     # ModelError, ModelBusy: said, not thrown
                pieces.put({"event": "error", "message": str(exc)})
            finally:
                pieces.put(None)
                # the model is loaded and free now: a moment to describe pictures
                indexer.want(bid)

        threading.Thread(target=produce, daemon=True, name="mindboard-ask").start()

        async def events():
            try:
                while True:
                    # where the server does learn of a hang-up, stop at once
                    if await request.is_disconnected():
                        break
                    item = await run_in_threadpool(pieces.get)
                    if item is None:
                        break
                    name = item.pop("event")
                    if name == "context":
                        # another program holding the card makes every answer
                        # slow; say so before the person waits for one
                        item["warning"] = warning
                        item["loading"] = loading
                    yield f"event: {name}\ndata: {json.dumps(item)}\n\n"
                    if name == "error":
                        break
            finally:
                model.cancel()           # no-op if the answer already ended

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @app.post("/api/boards/{bid}/ask/stop")
    def api_ask_stop(bid: str):
        """Stop the answer being written. The browser's Stop calls this as well
        as hanging up, because a hang-up alone is not always heard."""
        board_or_404(bid)
        model = held.get("model")
        if model is not None:
            model.cancel()
        return {"stopped": model is not None}

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
