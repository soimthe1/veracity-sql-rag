"""
Renders one question's executed query result(s) into a single
RAG-style context chunk (answer/schemas.py's AskChunkOut) — one chunk
per question, not one per row or per query, so a calling retrieval
pipeline's LLM gets one coherent block of database context to weave in
alongside document chunks, the same way it receives one chunk per
document passage (docs/integration.md).

A question can now run more than one generated query (answer/
sql_generation.py's SqlProposal.queries — e.g. a row count plus a
sample plus a distinct-values query for a broad/descriptive question),
so this renders a list of labeled "sections", one per query, in the
same combined block. A single-query question is just the len==1 case.

Bounded like every other place this project renders live data as text
(engine/profiling.py's top_values, engine/semantic.py's column sample)
— never an unbounded dump of a live table.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

MAX_CHUNK_CHARS = 6000
MAX_VALUE_CHARS = 200


@dataclass
class QuerySection:
    """One executed query's result, labeled with what it was for —
    the unit both this module and answer/synthesis.py work with."""
    purpose: str
    sql: str
    rows: List[Dict[str, Any]] = field(default_factory=list)


def format_value(v: Any) -> str:
    s = "" if v is None else str(v)
    return s if len(s) <= MAX_VALUE_CHARS else s[:MAX_VALUE_CHARS] + "…"


_format_value = format_value


def render_sections(question: str, table_labels: List[str], sections: List[QuerySection]) -> str:
    total_rows = sum(len(s.rows) for s in sections)
    header = (
        f"Live database result for: {question}\nTables: {', '.join(table_labels)}\n"
        f"Queries run: {len(sections)}, total rows: {total_rows}\n"
    )
    lines = [header]
    total = len(header)
    for sec in sections:
        sec_header = f"\n[{sec.purpose}] ({len(sec.rows)} row(s)):"
        if total + len(sec_header) > MAX_CHUNK_CHARS:
            lines.append("... (further query results truncated)")
            break
        lines.append(sec_header)
        total += len(sec_header)
        for i, row in enumerate(sec.rows, start=1):
            line = "  Row " + str(i) + ": " + ", ".join(f"{k}={_format_value(v)}" for k, v in row.items())
            if total + len(line) > MAX_CHUNK_CHARS:
                lines.append(f"  ... ({len(sec.rows) - i + 1} more row(s) truncated)")
                break
            lines.append(line)
            total += len(line)
    return "\n".join(lines)
