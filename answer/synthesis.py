"""
Turns executed query rows into a natural-language answer to the
original question, using the same LLM provider that proposed the SQL
(answer/sql_generation.py). This is the "final mile" step standalone
users of the Ask tab actually want (see AskOut.answer), and it's the
step that makes the one-pipeline rule real: question -> catalog ->
generate query/queries -> execute -> narrate from REAL rows, full
stop — there is no separate metadata-only "describe" shortcut for
broad questions like "what is Inventory". Instead, sql_generation.py
proposes several small exploratory queries for those (a row count, a
sample, distinct status values, ...), and this step is what reads all
of them together and writes the actual sentence — "the query
generation + execution pipeline alone gets you validated, access-
filtered *data*; "tell me about employee 4821" or "what is Inventory"
wants a sentence, not a row dump.

Deliberately SEPARATE from AskChunkOut.text (answer/formatting.py),
which stays exactly as it was: a raw, unsynthesized row dump. That's
the documented integration contract (docs/integration.md) — a server
caller wires its OWN LLM to weave that raw chunk in alongside
document chunks, and must see the same shape it always has. AskOut.answer
is purely additive (defaults to None) so that contract never changes
under it; it exists for THIS project's own Ask tab, and for a caller
of /v1/ask directly that wants a ready-made sentence instead of chunks
to synthesize itself.

Same fail-safe contract as sql_generation.py and engine/semantic.py:
never raises. A None return just means "no synthesized answer this
time" (LLM unavailable/declined/erroring) and callers fall back to
showing the raw chunk text alone — the database context itself is
never lost, only the extra sentence on top of it.
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from shared.config import InferenceConfig
from shared.llm.base import LlmProvider, strip_reasoning

from answer.formatting import QuerySection

logger = logging.getLogger("answer.synthesis")

# Same "bounded, never an unbounded dump of a live table" principle as
# answer/formatting.py's MAX_CHUNK_CHARS — this prompt can afford to be
# a bit larger since it's a one-shot LLM call, not a UI render, but it
# still has to fit a context window regardless of how many rows a
# generated query happens to return. Applied PER SECTION now that a
# question can run several queries, so one huge section can't crowd the
# others out of the prompt entirely.
MAX_ROWS_IN_PROMPT = 200
MAX_VALUE_CHARS = 300

_SYSTEM_PROMPT = """You answer a user's question in plain, natural language using ONLY the \
query result rows given below - they are the complete, authoritative answer to the question. \
The question may have needed more than one query to explore (each is labeled with its \
purpose, e.g. "row count", "sample rows", "distinct status values") - read all of them \
together and weave them into ONE coherent answer, the way a person would summarize several \
related facts they just looked up. This applies even to broad or descriptive questions \
("what is X", "what does Y do") - answer from what the rows actually show (row counts, \
sample values, distinct categories, etc.), never with a generic or invented description. \
The queries have already been run for you; never say you lack database access or suggest \
running a query yourself. Be direct and concise: lead with the answer itself, then add \
brief supporting detail only if it helps. If every section's rows are empty, say plainly \
that nothing matching the question was found in the catalog. Never invent a value that is \
not present in the rows. Respond with plain text only - no JSON, no markdown code fences, \
no preamble like "Based on the data provided"."""


def _format_value(v: Any) -> str:
    s = "" if v is None else str(v)
    return s if len(s) <= MAX_VALUE_CHARS else s[:MAX_VALUE_CHARS] + "…"


def _build_prompt(question: str, sections: List[QuerySection]) -> str:
    parts = [f"Question: {question}", ""]
    for sec in sections:
        parts.append(f"Query result — {sec.purpose} ({len(sec.rows)} row(s) total):")
        for i, row in enumerate(sec.rows[:MAX_ROWS_IN_PROMPT], start=1):
            parts.append(f"  Row {i}: " + ", ".join(f"{k}={_format_value(v)}" for k, v in row.items()))
        if len(sec.rows) > MAX_ROWS_IN_PROMPT:
            parts.append(f"  ... ({len(sec.rows) - MAX_ROWS_IN_PROMPT} more row(s) included in the count above but not listed here)")
        parts.append("")
    return "\n".join(parts)


def synthesize_answer(
    provider: Optional[LlmProvider], cfg: InferenceConfig, question: str, sections: List[QuerySection],
) -> Optional[str]:
    """Returns None (never raises) if the LLM is unavailable, times out,
    errors, or there are no sections at all — callers must treat None as
    "no synthesized sentence this time" and fall back to the raw
    formatted chunk text alone, the same fail-safe contract every other
    LLM call site in this project follows."""
    if provider is None or not sections:
        return None

    prompt = _build_prompt(question, sections)
    result = provider.complete(prompt, system=_SYSTEM_PROMPT, json_mode=False)
    if not result.ok:
        logger.info("Answer synthesis skipped: %s", result.error)
        return None

    text = strip_reasoning(result.text).strip()
    return text or None
