"""Wire shapes. Deliberately identical to what graph.json holds, so export is
just this serialisation written to a file - one representation, not two."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

NODE_TYPES = ("text", "image", "video", "link", "file", "meta")
EDGE_KINDS = ("relates", "causes", "supports", "contradicts", "part-of", "example-of")
PROVENANCE = ("user", "import", "ai")

DEFAULT_SETTINGS: dict[str, Any] = {
    "theme": "midnight",
    "palette": {},
    "viewport": {"x": 0, "y": 0, "zoom": 1},
}


class NodeDTO(BaseModel):
    id: str
    parent: Optional[str] = None
    type: Literal["text", "image", "video", "link", "file", "meta"] = "text"
    pos: list[float] = Field(default_factory=lambda: [0.0, 0.0])
    size: list[Optional[float]] = Field(default_factory=lambda: [None, None])
    z: int = 0
    title: str = ""
    content: dict[str, Any] = Field(default_factory=dict)
    style: dict[str, Any] = Field(default_factory=dict)
    collapsed: bool = True
    provenance: Literal["user", "import", "ai"] = "user"


class EdgeDTO(BaseModel):
    id: str
    source: str
    target: str
    sourceHandle: Optional[str] = None
    targetHandle: Optional[str] = None
    label: str = ""
    kind: Literal["relates", "causes", "supports", "contradicts", "part-of", "example-of"] = "relates"
    directed: bool = True
    style: dict[str, Any] = Field(default_factory=dict)
    provenance: Literal["user", "import", "ai"] = "user"


class Envelope(BaseModel):
    opId: str
    op: dict[str, Any]
    origin: Literal["user", "import", "ai"] = "user"


class OpsRequest(BaseModel):
    baseRevision: Optional[int] = None
    envelopes: list[Envelope]


class BoardCreate(BaseModel):
    title: str = "Untitled board"
    description: str = ""


class BoardPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    settings: Optional[dict[str, Any]] = None
