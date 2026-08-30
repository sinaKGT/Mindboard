"""Media: content-addressed storage, URL fetching, thumbnails, range streaming.

Everything a board displays is stored locally, so a board stays watchable
offline and every export is complete.
"""

import hashlib
import io
import mimetypes
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterator, Optional

import httpx

from urllib.parse import urljoin

from .db import now
from .paths import DataDir

MAX_FETCH_BYTES = 512 * 1024 * 1024  # 512 MB ceiling on a pasted URL
INLINE_MIME_PREFIXES = ("image/", "video/", "audio/")

EXT_BY_MIME = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
    "image/webp": ".webp", "image/svg+xml": ".svg", "image/avif": ".avif",
    "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov",
    "video/x-matroska": ".mkv", "audio/mpeg": ".mp3", "audio/wav": ".wav",
    "application/pdf": ".pdf",
}


def ext_for(mime: str, filename: Optional[str] = None) -> str:
    if mime in EXT_BY_MIME:
        return EXT_BY_MIME[mime]
    if filename and "." in filename:
        return "." + filename.rsplit(".", 1)[1].lower()[:8]
    return mimetypes.guess_extension(mime or "") or ".bin"


def sniff_mime(data: bytes, declared: str = "", filename: str = "") -> str:
    """Trust the bytes over the extension."""
    sigs = [
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"%PDF-", "application/pdf"),
        (b"\x1a\x45\xdf\xa3", "video/webm"),
    ]
    for sig, mime in sigs:
        if data.startswith(sig):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp":
        brand = data[8:12]
        return "video/quicktime" if brand[:3] == b"qt " else "video/mp4"
    if declared and declared != "application/octet-stream":
        return declared.split(";")[0].strip()
    return mimetypes.guess_type(filename or "")[0] or "application/octet-stream"


def store_bytes(
    dd: DataDir,
    conn: sqlite3.Connection,
    board_id: str,
    data: bytes,
    *,
    declared_mime: str = "",
    orig_name: str = "",
    source_url: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    duration_ms: Optional[int] = None,
) -> dict[str, Any]:
    mime = sniff_mime(data, declared_mime, orig_name)
    sha = hashlib.sha256(data).hexdigest()
    ext = ext_for(mime, orig_name)

    if mime.startswith("image/") and (width is None or height is None):
        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as im:
                width, height = im.size
        except Exception:
            pass

    path = dd.asset_path(board_id, sha, ext)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    existing = conn.execute("SELECT * FROM asset WHERE sha256=?", (sha,)).fetchone()
    if existing is None:
        conn.execute(
            """INSERT INTO asset (sha256, mime, ext, bytes, orig_name, width, height,
                                  duration_ms, source_url, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (sha, mime, ext, len(data), orig_name or None, width, height,
             duration_ms, source_url, now()),
        )
    return {
        "sha256": sha, "mime": mime, "ext": ext, "bytes": len(data),
        "width": width, "height": height, "duration_ms": duration_ms,
        "orig_name": orig_name or None,
    }


def link_asset(conn: sqlite3.Connection, node_id: str, sha: str, role: str = "primary") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO node_asset (node_id, sha256, role) VALUES (?,?,?)",
        (node_id, sha, role),
    )


def resolve(dd: DataDir, conn: sqlite3.Connection, board_id: str, sha: str) -> Optional[Path]:
    row = conn.execute("SELECT ext FROM asset WHERE sha256=?", (sha,)).fetchone()
    if row is None:
        return None
    p = dd.asset_path(board_id, sha, row["ext"])
    return p if p.exists() else None


def thumbnail(dd: DataDir, conn: sqlite3.Connection, board_id: str, sha: str,
              size: int = 480) -> Optional[Path]:
    out = dd.thumbs_dir(board_id) / f"{sha}_{size}.webp"
    if out.exists():
        return out
    src = resolve(dd, conn, board_id, sha)
    if src is None:
        return None
    row = conn.execute("SELECT mime FROM asset WHERE sha256=?", (sha,)).fetchone()
    if not row or not row["mime"].startswith("image/"):
        return None
    try:
        from PIL import Image

        out.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((size, size))
            im.save(out, "WEBP", quality=82, method=4)
        return out
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# pasted URLs
# --------------------------------------------------------------------------- #

# Platforms whose player we can embed. The URL is enough to build both the
# player address and, for YouTube, the thumbnail - no API key, no scraping.
_YOUTUBE_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|embed/|shorts/|live/|v/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{6,})")
_VIMEO_RE = re.compile(r"vimeo\.com/(?:video/)?(\d+)")

MAX_THUMB_BYTES = 8 * 1024 * 1024


def video_embed(url: str) -> Optional[dict[str, Any]]:
    """Recognise a video the page can play in place."""
    m = _YOUTUBE_RE.search(url)
    if m:
        vid = m.group(1)
        return {
            "provider": "youtube",
            "video_id": vid,
            # -nocookie so watching inside a board does not seed tracking cookies
            "embed": f"https://www.youtube-nocookie.com/embed/{vid}?autoplay=1&rel=0",
            "thumb_urls": [
                f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg",
                f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            ],
        }
    m = _VIMEO_RE.search(url)
    if m:
        vid = m.group(1)
        return {"provider": "vimeo", "video_id": vid,
                "embed": f"https://player.vimeo.com/video/{vid}?autoplay=1",
                "thumb_urls": []}
    return None


def fetch_bytes(url: str, limit: int = MAX_THUMB_BYTES) -> Optional[tuple[bytes, str]]:
    """Grab a small resource - a preview image - or give up quietly."""
    try:
        headers = {"User-Agent": "Mindboard/0.1 (+local)"}
        with httpx.Client(follow_redirects=True, timeout=15.0, headers=headers) as client:
            with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    return None
                ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                chunks, total = [], 0
                for chunk in resp.iter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > limit:
                        return None
                return b"".join(chunks), ctype
    except Exception:
        return None


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_DESC_RE = re.compile(
    r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+'
    r'content=["\'](.*?)["\']', re.I | re.S)
_OGIMG_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](.*?)["\']', re.I | re.S)


def _host_of(url: str) -> str:
    return re.sub(r"^https?://", "", url).split("/")[0]


PROVIDER_NAMES = {"youtube": "YouTube", "vimeo": "Vimeo"}


def _link_from_url_alone(url: str, embed: Optional[dict[str, Any]]) -> dict[str, Any]:
    """A link we could not open is still a link worth keeping. A recognised
    video needs nothing from the page anyway - the address carries both the
    player and the thumbnail."""
    host = _host_of(url)
    title = f"{PROVIDER_NAMES.get(embed['provider'], embed['provider'])} video" if embed else host
    return {"kind": "link", "url": url, "title": title, "excerpt": "", "image": None,
            "host": host, "video": embed, "unreachable": True}


def fetch_url(url: str) -> dict[str, Any]:
    """Fetch a pasted URL.

    Media -> bytes we can store locally. A page -> metadata for a link node.
    """
    embed = video_embed(url)
    headers = {"User-Agent": "Mindboard/0.1 (+local)"}
    is_media = False
    try:
        with httpx.Client(follow_redirects=True, timeout=30.0, headers=headers) as client:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                is_media = ctype.startswith(INLINE_MIME_PREFIXES) or ctype == "application/pdf"
                chunks, total = [], 0
                limit = MAX_FETCH_BYTES if is_media else 512 * 1024
                for chunk in resp.iter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > limit:
                        if is_media:
                            raise ValueError("file exceeds the 512 MB limit")
                        break
                data = b"".join(chunks)
    except ValueError:
        raise
    except Exception:
        # The page would not open. Refusing the paste helps nobody - keep the
        # link, and keep the player if we recognised one.
        return _link_from_url_alone(url, embed)

    if is_media:
        name = url.rsplit("/", 1)[-1].split("?")[0]
        return {"kind": "media", "data": data, "mime": ctype, "orig_name": name}

    html = data.decode("utf-8", "replace")
    title = _TITLE_RE.search(html)
    desc = _DESC_RE.search(html)
    img = _OGIMG_RE.search(html)
    host = _host_of(url)
    image = urljoin(url, img.group(1).strip()) if img else None
    return {
        "kind": "link",
        "url": url,
        "title": (title.group(1).strip()[:200] if title else host),
        "excerpt": (desc.group(1).strip()[:400] if desc else ""),
        "image": image,
        "host": host,
        "video": embed,
    }

# --------------------------------------------------------------------------- #
# range streaming - without this, <video> seeking silently fails in Chrome
# --------------------------------------------------------------------------- #

def parse_range(header: str, size: int) -> Optional[tuple[int, int]]:
    m = re.match(r"bytes=(\d*)-(\d*)$", (header or "").strip())
    if not m:
        return None
    start_s, end_s = m.groups()
    if start_s == "" and end_s == "":
        return None
    if start_s == "":
        length = min(int(end_s), size)
        return (size - length, size - 1)
    start = int(start_s)
    end = int(end_s) if end_s else size - 1
    if start >= size:
        return None
    return (start, min(end, size - 1))


def stream_file(path: Path, start: int, end: int, chunk: int = 512 * 1024) -> Iterator[bytes]:
    remaining = end - start + 1
    with path.open("rb") as fh:
        fh.seek(start)
        while remaining > 0:
            data = fh.read(min(chunk, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data
