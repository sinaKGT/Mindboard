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


class AskTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=20000)


class AskRequest(BaseModel):
    """A question about exactly these cards (groups bring what is inside them)."""
    question: str = Field(min_length=1, max_length=4000)
    cards: list[str] = Field(default_factory=list, max_length=500)
    history: list[AskTurn] = Field(default_factory=list, max_length=40)
    think: bool = False
    # the cards earlier answers in this conversation drew on: they stay in
    # context, and a search adds to them
    keep: list[str] = Field(default_factory=list, max_length=200)
    # Explain more: answer from the conversation and its cards - no search
    explain: bool = False


class DraftRequest(BaseModel):
    """An answer to turn into a card for the board."""
    question: str = Field(min_length=1, max_length=4000)
    answer: str = Field(min_length=1, max_length=20000)
    cited: list[str] = Field(default_factory=list, max_length=100)
    # every card the conversation brought up, and the conversation itself
    # (DESIGN.md 53): an Explain more answer cites nothing to link to
    candidates: list[str] = Field(default_factory=list, max_length=200)
    history: list[AskTurn] = Field(default_factory=list, max_length=40)


class GuideRequest(BaseModel):
    """A question for the guide: which expert can answer it?"""
    question: str = Field(min_length=1, max_length=4000)


class ExpertBody(BaseModel):
    """An expert's name, brief, boards and colour; on an edit, what changes."""
    name: Optional[str] = Field(default=None, max_length=60)
    brief: Optional[str] = Field(default=None, max_length=600)
    boards: Optional[list[str]] = Field(default=None, max_length=100)
    colour: Optional[str] = Field(default=None, max_length=20)
    shape: Optional[str] = Field(default=None, max_length=20)
    voice: Optional[str] = Field(default=None, max_length=20)


class SpeakRequest(BaseModel):
    """A sentence for the voice."""
    text: str = Field(min_length=1, max_length=2000)
    voice: str = Field(default="af_heart", max_length=20)
    speed: float = Field(default=1.0, ge=0.5, le=1.6)
