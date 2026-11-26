"""
LLM-based SQL generation. Mirrors engine/semantic.py's own pattern
exactly (strict JSON-only system prompt, defensive `{`/`}` extraction,
never raises, a None/failed provider is always a clean "no SQL
proposed" rather than an error) — same "AI must never be a single
point of failure" principle, just proposing queries instead of a name.

The LLM only ever sees the grounded whitelist from grounding.py — never
the full schema, and never asked to reason about access control itself
(that's already been decided before this module is called). Its output
is NOT trusted as safe SQL; guardrails.py is the actual enforcement
point, exactly as confidence.py (not the LLM) is the actual arbiter of
entity confidence.

This is the ONE query-generation step in the app's single pipeline
(question -> catalog -> generate query/queries -> execute -> narrate
from real rows — no separate metadata-only "describe" path). A single
narrow question ("how many active employees") is answered by one SELECT.
A broad/descriptive question ("what is Inventory", "tell me about John
Smith") still has to be answered from REAL rows, not a static summary —
it just usually needs a few exploratory queries first (a row count, a
handful of sample rows, the distinct values of a status/type column)
across the grounded tables, so the narration step downstream has enough
real evidence to write from. Hence `queries` is a LIST, not a single
optional string: the LLM proposes as many read-only SELECTs as the
question genuinely needs (usually 1, rarely more than a handful), each
independently guardrail-checked and executed.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import List, Optional

from shared.config import InferenceConfig
from shared.llm.base import LlmProvider

from answer.grounding import GroundedTable, GroundingResult
from answer.understanding import QuestionUnderstanding

logger = logging.getLogger("answer.sql_generation")

_SYSTEM_PROMPT = """You are a careful, read-only T-SQL query writer for a SQL Server \
database. You are given a small, pre-approved list of tables and columns - the ONLY \
objects you are allowed to reference - and a question in plain language. Your job is to \
propose one or more read-only T-SQL SELECT statements whose combined results give enough \
real data to answer the question, using ONLY those tables and columns.

This includes broad or descriptive questions ("what is X", "what does Y do", "tell me \
about Z") - for those, do NOT decline just because no single query fully answers it. \
Instead propose EXPLORATORY queries across the relevant approved tables: for example a \
row count, a handful of sample rows, or the distinct values of a status/type/category \
column. A separate step downstream reads all of your query results and writes the actual \
prose answer from them, so no single query needs to already contain a finished sentence -
it only needs to surface real, relevant data.

Rules:
- Each item in "queries" is exactly ONE SELECT statement (a WITH ... SELECT common table \
expression is fine). Never combine two statements with a semicolon in one item.
- Propose as many or as few queries as the question genuinely needs - usually 1, rarely \
more than 4 or 5. Do not pad with irrelevant queries.
- NEVER reference a table, view, or column that is not in the approved list.
- NEVER use INSERT/UPDATE/DELETE/MERGE/DROP/ALTER/TRUNCATE/EXEC/CREATE or any other \
non-SELECT statement.
- Always qualify tables as schema.table.
- Only decline (an empty "queries" list) when the approved tables genuinely have nothing \
to do with the question at all - not merely because it's broad, descriptive, or would take \
more than one query to explore properly.
- People type casually and misspell things. When filtering by a value the user typed \
(a person's name, a code, a city), NEVER use exact equality. Use case-insensitive \
LIKE N'%part%' on each word of the value separately (a full name is often split across \
first/last name columns, or stored in a different word order), and when a misspelling is \
plausible also accept SOUNDEX(column) = SOUNDEX(N'part') for the name parts. Use N'...' \
literals so Arabic text works.
- When unsure which column holds the value, check the plausible ones with OR.
- For a person or record lookup, select the descriptive columns a person would want to \
know (names, gender, dates, status, department, qualification ...) - join related approved \
tables if that is where those details live.
- This is Microsoft SQL Server (T-SQL): never use LIMIT, OFFSET without ORDER BY, ILIKE, or \
backtick quoting. To pick the "top N" of something use SELECT TOP (N) ... ORDER BY.
- Make the queries compute what was actually asked. "How many X by Y" means COUNT(*) of \
the X rows GROUP BY Y (join a lookup table only to label Y) - listing the lookup table's \
values alone does not answer it. Totals, averages and latest/oldest likewise need the real \
aggregate or ORDER BY over the main table.
- Tables may be large: prefer GROUP BY summaries and filtered queries over counting or \
scanning an entire big table without a reason.
- Prefer simple, direct queries. Do not add a TOP/row limit yourself - it is applied \
automatically afterward.
- Respond with ONLY a single JSON object, no other text, matching exactly:
{"queries": [{"sql": string, "purpose": string}, ...], "reasoning": string, "confidence_hint": number}
"purpose" is a short label (a few words) of what that one query is for, e.g. "row count", \
"sample rows", "distinct status values". "reasoning" explains your overall approach, or \
honestly explains why you declined. confidence_hint is your own rough self-assessment from \
0.0 to 1.0 that these queries, together, will produce enough to answer the question - be \
honest rather than optimistic."""

# The prompt above is SQL Server's. Other databases get the same prompt with
# the three database-specific parts swapped: the SQL flavour named in the
# opening lines, how to match typed values tolerantly, and syntax pitfalls.
_MSSQL_MATCH = _SYSTEM_PROMPT[_SYSTEM_PROMPT.index("- People type casually"):_SYSTEM_PROMPT.index("- When unsure")]
_MSSQL_SYNTAX = _SYSTEM_PROMPT[_SYSTEM_PROMPT.index("- This is Microsoft SQL Server"):_SYSTEM_PROMPT.index("- Make the queries")]
_MATCH = ("- People type casually and misspell things. When filtering by a value the user typed "
          "(a person's name, a code, a city), NEVER use exact equality. Use {how} on each word of the "
          "value separately (a full name is often split across first/last name columns, or stored in a "
          "different word order).{extra}\n")
_SOUNDEX = " For a plausible misspelling of a name, SOUNDEX(column) = SOUNDEX('part') also works."
_DIALECTS = {
    # dialect: (SQL flavour, database name, tolerant match, syntax rule)
    "postgresql": ("PostgreSQL", "PostgreSQL", _MATCH.format(how="ILIKE '%part%'", extra=""),
                   "- This is PostgreSQL: never use TOP, square-bracket or backtick quoting. Use ILIKE for "
                   "case-insensitive matching and ORDER BY ... LIMIT N for \"top N\".\n"),
    "mysql": ("MySQL", "MySQL/MariaDB",
              _MATCH.format(how="LIKE '%part%' (case-insensitive under the default collation)", extra=_SOUNDEX),
              "- This is MySQL/MariaDB: never use TOP, ILIKE or square-bracket quoting. Use ORDER BY ... "
              "LIMIT N for \"top N\"; quote identifiers with backticks only when needed.\n"),
    "oracle": ("Oracle SQL", "Oracle", _MATCH.format(how="UPPER(column) LIKE UPPER('%part%')", extra=_SOUNDEX),
               "- This is Oracle: never use TOP, LIMIT, ILIKE, square-bracket or backtick quoting, and never "
               "write AS before a table alias. Use ORDER BY ... FETCH FIRST N ROWS ONLY for \"top N\".\n"),
    "sqlite": ("SQLite", "SQLite", _MATCH.format(how="LIKE '%part%' (case-insensitive for ASCII)", extra=""),
               "- This is SQLite: never use TOP, ILIKE or square-bracket quoting. Use ORDER BY ... LIMIT N "
               "for \"top N\".\n"),
}


def system_prompt(dialect: str = "mssql") -> str:
    """The SQL-writing system prompt for one database dialect. Dialects not
    listed in _DIALECTS (Snowflake, BigQuery, DuckDB, ...) get standard-SQL
    guidance naming the dialect, which the model generally knows."""
    if dialect == "mssql":
        return _SYSTEM_PROMPT
    lang, engine, match, syntax = _DIALECTS.get(dialect) or (
        "SQL", dialect, _MATCH.format(how="LOWER(column) LIKE LOWER('%part%')", extra=""),
        f"- This is {dialect}: write SQL that {dialect} accepts. Never use TOP or square-bracket quoting "
        f"unless {dialect} requires it; use its own \"top N\" syntax (usually ORDER BY ... LIMIT N).\n")
    article = "an" if engine[:1].lower() in "aeiou" else "a"
    return (_SYSTEM_PROMPT
            .replace("read-only T-SQL query writer for a SQL Server", f"read-only {lang} query writer for {article} {engine}")
            .replace("read-only T-SQL SELECT", f"read-only {lang} SELECT")
            .replace(_MSSQL_MATCH, match)
            .replace(_MSSQL_SYNTAX, syntax)
            .replace("Do not add a TOP/row limit yourself", "Do not add a row limit yourself"))


_MAX_SAMPLE_VALUES = 5
_MAX_SAMPLE_CHARS = 40


def _describe_table(t: GroundedTable) -> str:
    lines = [f"Table: {t.entity.schema_name}.{t.entity.object_name}"]
    if t.entity.semantic_name or t.entity.description:
        meaning = f"{t.entity.semantic_name or ''} - {t.entity.description or ''}".strip(" -")
        lines.append(f"  Meaning: {meaning}")
    if t.entity.row_count_estimate is not None:
        lines.append(f"  Approx. rows: {t.entity.row_count_estimate}")
    for f in t.fields:
        bits = [f"  - {f.column_name} ({f.data_type})"]
        if f.is_primary_key:
            bits.append("[primary key]")
        if f.semantic_name:
            bits.append(f"[{f.semantic_name}]")
        # A few real values (bounded profiling output, engine/profiling.py)
        # tell the LLM what a column actually holds — e.g. that StatusCode
        # is 'P'/'A'/'R', or that names are stored in Arabic — which
        # column names alone often don't.
        samples = [str(v)[:_MAX_SAMPLE_CHARS] for v in (f.top_values or [])[:_MAX_SAMPLE_VALUES] if v is not None]
        if samples:
            bits.append("e.g. " + ", ".join(repr(v) for v in samples))
        lines.append(" ".join(bits))
    return "\n".join(lines)


def _build_prompt(
    question: str, grounding: GroundingResult, understanding: Optional[QuestionUnderstanding] = None,
    previous_attempt: Optional[List[str]] = None,
) -> str:
    parts = [f"Question: {question}"]
    if understanding is not None:
        if understanding.corrected_question and understanding.corrected_question != question:
            parts.append(f"(Spelling-corrected: {understanding.corrected_question})")
        if understanding.values:
            parts.append("Values to match tolerantly: " + ", ".join(repr(v) for v in understanding.values))
    parts.append("Approved tables and columns (nothing else may be referenced):")
    parts.extend(_describe_table(t) for t in grounding.tables)
    if getattr(grounding, "denied_columns", None):
        parts.append("Some columns of these tables are restricted and are deliberately not listed. Use ONLY the "
                     "columns listed above, always name columns explicitly (never SELECT *), and never guess "
                     "other column names.")
    if previous_attempt:
        parts += [
            "A previous attempt at this question did NOT find an answer:\n" + "\n".join(previous_attempt),
            "Propose a DIFFERENT approach: loosen the filters (match individual name parts, "
            "SOUNDEX, other plausible columns or tables), or run exploratory queries (a few sample "
            "rows, distinct values of the relevant columns) so the data's actual shape becomes visible. "
            "Do not repeat the same queries.",
        ]
    return "\n\n".join(parts)


@dataclass
class GeneratedQuery:
    sql: str
    purpose: str


@dataclass
class SqlProposal:
    queries: List[GeneratedQuery]
    reasoning: str
    confidence_hint: float

    @property
    def ok(self) -> bool:
        return bool(self.queries)

    @property
    def sql(self) -> Optional[str]:
        """Back-compat convenience for any caller that only cares about
        "was there at least one query" (e.g. audit's entities_touched
        logic) — the real, authoritative list is `queries`."""
        return self.queries[0].sql if self.queries else None


def _coerce_queries(raw) -> List[GeneratedQuery]:
    queries: List[GeneratedQuery] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            sql = item.get("sql")
            if not isinstance(sql, str) or not sql.strip():
                continue
            purpose = str(item.get("purpose") or "query")[:200]
            queries.append(GeneratedQuery(sql=sql.strip(), purpose=purpose))
    return queries


def _parse_response(text: str) -> Optional[SqlProposal]:
    """Returns None only when the response is genuinely unusable (not
    valid JSON at all) — anything the LLM actually said, including a
    deliberate empty "queries": [] decline, comes back as a SqlProposal
    with ok=False so the caller can show its own reasoning instead of a
    generic "declined" message with no explanation."""
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        data = json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        logger.warning("SQL generation response was not valid JSON, discarding: %s", text[:200])
        return None

    reasoning = str(data.get("reasoning", ""))[:1000]
    try:
        confidence_hint = max(0.0, min(1.0, float(data.get("confidence_hint", 0.5))))
    except (TypeError, ValueError):
        confidence_hint = 0.5

    queries = _coerce_queries(data.get("queries"))
    if not queries:
        # Back-compat: tolerate an older-shaped {"sql": "...", ...}
        # response (e.g. a cached prompt/model that hasn't picked up the
        # new "queries" list yet) rather than treating it as a decline.
        legacy_sql = data.get("sql")
        if isinstance(legacy_sql, str) and legacy_sql.strip():
            queries = [GeneratedQuery(sql=legacy_sql.strip(), purpose="query")]

    return SqlProposal(queries=queries, reasoning=reasoning, confidence_hint=confidence_hint)


def generate_sql(
    provider: Optional[LlmProvider], cfg: InferenceConfig, question: str, grounding: GroundingResult,
    understanding: Optional[QuestionUnderstanding] = None, previous_attempt: Optional[List[str]] = None,
    dialect: str = "mssql",
) -> Optional[SqlProposal]:
    """Returns None (never raises) only when there was never a real
    attempt to ask the LLM at all — no provider configured, or nothing
    was grounded to ask it about. Once the LLM actually responds, the
    result is always a SqlProposal, even when it declined (ok=False,
    queries=[]) — see its reasoning field, which callers should show
    verbatim rather than collapsing every non-answer into the same
    generic message."""
    if provider is None or not grounding.ok:
        return None

    prompt = _build_prompt(question, grounding, understanding, previous_attempt)
    result = provider.complete(prompt, system=system_prompt(dialect), json_mode=True)
    if not result.ok:
        logger.info("SQL generation skipped: %s", result.error)
        return SqlProposal(queries=[], reasoning=f"The LLM call itself failed: {result.error}", confidence_hint=0.0)

    return _parse_response(result.text)
