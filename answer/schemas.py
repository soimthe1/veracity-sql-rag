"""
The catalog answerer's small, consumer-agnostic contract. Each result is
a RAG-style context chunk (chunk_id, doc_id, text, level, category, score,
title, source_uri, heading_path, page, origin) so a retrieval pipeline can
cite it like a document passage — `origin == "database"` results slot
into an existing citation/prompt pipeline with no new code there. See
docs/integration.md.

This is intentionally the ONLY file another project needs to read to
integrate — everything else in answer/ is this process's own internals.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from shared.access.cells import coerce_grant


class GrantIn(BaseModel):
    """One (level, category) pair the caller is cleared for — the exact
    shape a caller's resolved principal typically carries. Passing the caller's real resolved grants here is
    what makes this endpoint's access filtering meaningful; passing none
    is a valid, deliberately UNprivileged choice (see AskIn.grants).

    Forgiving on case/whitespace/number-as-text ({"level": "20", "category":
    "b"} or just "20b"), strict on everything else: a malformed grant is a
    422 naming it, never dropped (shared/access/cells.py coerce_grant)."""
    level: int
    category: str

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, value):
        return coerce_grant(value)


class AskIn(BaseModel):
    question: str = Field(..., min_length=1)
    # The caller's resolved (level, category) grant pairs — never trust a
    # single level/category value alone, since a real principal usually
    # holds several. Empty (the default) means "no data-tier access": the
    # answerer returns zero chunks rather than guessing, matching the
    # default-deny posture of the access model.
    # Grants must be resolved server-side by the CALLER before reaching
    # here —
    # this endpoint does not resolve identity, it only enforces the
    # pairs it's given.
    grants: List[GrantIn] = Field(default_factory=list)
    limit: int = 5
    # Optional: only look in this database (the Ask page's database
    # picker). Omitted = all databases the caller can query.
    database: Optional[str] = None


class AskChunkOut(BaseModel):
    chunk_id: str
    doc_id: str
    text: str
    level: int
    category: str
    score: float
    title: Optional[str] = None
    source_uri: Optional[str] = None
    heading_path: Optional[str] = None
    page: Optional[int] = None
    origin: str = "database"
    # Purely additive (see answer/grounding.py's worst_trust()) — the
    # catalog's own confidence in the weakest table/relationship this
    # chunk relied on. TRUST is no longer a gate on whether this chunk
    # exists at all (only the (level, category) grant check above is);
    # it's reported here so a caller can show it, not so it can be used
    # to silently withhold the answer.
    trust_label: Optional[str] = None
    trust_score: Optional[float] = None


class AskGraphNodeOut(BaseModel):
    id: str
    label: str  # "schema.object"
    status: str
    confidence: float


class AskGraphEdgeOut(BaseModel):
    from_id: str
    to_id: str
    status: str


class AskAccessOut(BaseModel):
    """Additive: how this question was access-checked and how sensitive
    the answer is."""
    mode: str                          # open | user | service
    full_access: bool = False          # admin, or auth-disabled testing bypass
    cells: List[str] = Field(default_factory=list)       # caller's cells, level then category: ["10A", "40C"]
    cells_used: List[str] = Field(default_factory=list)  # cells of the data this answer drew on, e.g. ["40C"]
    label_level: Optional[int] = None  # the answer's classification = most sensitive cell used
    label_category: Optional[str] = None
    columns_withheld: int = 0          # columns of grounded tables outside the caller's access


class AskResultSetOut(BaseModel):
    """One executed query's rows, structured (the same rows the chunk text
    carries), for tables on screen and in created documents. Values are
    strings so big numbers survive JSON unchanged."""
    purpose: str
    columns: List[str] = Field(default_factory=list)
    rows: List[List[Optional[str]]] = Field(default_factory=list)


class AskQueryOut(BaseModel):
    """A generated query — returned only to administrators and roles with
    "Can see generated SQL"; everyone else never receives the SQL."""
    purpose: Optional[str] = None
    sql: Optional[str] = None
    verdict: str                      # ALLOWED | REJECTED | ERROR
    rows: Optional[int] = None
    reason: Optional[str] = None


class AskOut(BaseModel):
    chunks: List[AskChunkOut] = Field(default_factory=list)
    took_ms: float
    notes: str
    # Purely additive (defaults None) — see answer/synthesis.py. A ready-
    # made natural-language sentence answering `question` from the
    # executed rows, for a caller (this project's own Ask tab, or a
    # direct /v1/ask caller) that wants a finished answer rather than
    # chunks to synthesize itself. `chunks` keeps its original, documented
    # docs/integration.md shape regardless of whether this is present.
    answer: Optional[str] = None
    # Mirrors chunks[0]'s trust_label/trust_score (same value — copied up
    # here purely so a caller showing `answer` doesn't need to reach into
    # `chunks` just to also show how reliable it is). None when no
    # database context was used at all.
    trust_label: Optional[str] = None
    trust_score: Optional[float] = None
    # Purely additive — just the tables this question was grounded on and
    # the relationships between them (answer/grounding.py), for a caller
    # that wants to show what the answer actually relied on rather than
    # only prose. Empty when nothing was grounded.
    graph_nodes: List[AskGraphNodeOut] = Field(default_factory=list)
    graph_edges: List[AskGraphEdgeOut] = Field(default_factory=list)
    # Additive: how this question was access-checked, which cells the
    # answer drew on, and its resulting classification.
    access: Optional[AskAccessOut] = None
    # Additive: this question's audit reference (catalog_ask_log.id) —
    # what Create uses to stamp and log a document.
    ask_id: Optional[str] = None
    database_name: Optional[str] = None     # the database that answered
    results: List[AskResultSetOut] = Field(default_factory=list)
    # None unless the caller may see generated SQL.
    queries: Optional[List[AskQueryOut]] = None


class SuggestedQuestionOut(BaseModel):
    question: str
    database_name: str
    table: str          # "schema.object" the question is built from
    trust_label: str    # that table's current trust status
    kind: str = "count"  # count | breakdown | latest | list — the question's shape
    domain: Optional[str] = None


class ScopeDatabaseOut(BaseModel):
    database_name: str
    tables: int            # tables/views in the caller's cells
    tables_usable: int     # ... that also clear the Ask trust dial


class ScopeOut(BaseModel):
    """What these cells can query — the right "has access?" signal (a
    person can have access and still get no suggestions)."""
    cells: List[str] = Field(default_factory=list)   # level then category, e.g. ["40D"]
    full_access: bool = False
    tables: int = 0
    tables_usable: int = 0   # what Ask will actually use right now
    min_confidence: float = 0.0
    databases: List[ScopeDatabaseOut] = Field(default_factory=list)


class RecentQuestionOut(BaseModel):
    question: str
    asked_at: Optional[datetime] = None
    answered: bool      # did any query for it return rows
    # Additive (from catalog_ask_log)
    ask_id: Optional[str] = None
    outcome: Optional[str] = None
    trust_label: Optional[str] = None
    trust_score: Optional[float] = None
    tables: int = 0
    database_name: Optional[str] = None
    label_level: Optional[int] = None
    label_category: Optional[str] = None


class HistoryOut(BaseModel):
    items: List[RecentQuestionOut] = Field(default_factory=list)
    total: int = 0


class CreateContentIn(BaseModel):
    """What goes into a created document — the answer as the asker sees
    it. Who asked, when and the classification come from the audit log
    (catalog_ask_log), never from here."""
    answer: Optional[str] = None
    notes: Optional[str] = None
    results: List[AskResultSetOut] = Field(default_factory=list)
    tables: List[str] = Field(default_factory=list)
    trust_label: Optional[str] = None
    trust_score: Optional[float] = None


class CreateIn(BaseModel):
    ask_id: str
    format: str                       # docx | pptx | pdf | email
    lang: str = "en"
    content: CreateContentIn = Field(default_factory=CreateContentIn)


class AnswerHealthOut(BaseModel):
    status: str  # HEALTHY | DEGRADED | DISABLED | UNAVAILABLE
    answer_enabled: bool
    catalog_db_connected: bool
    llm_enabled: bool
    # Mirrors AnswerConfig.require_grants (shared/config.py) so a UI can
    # tell "no data-tier access control right now" apart from the normal
    # exact-match-grants posture, rather than silently looking the same.
    require_grants: bool = True
