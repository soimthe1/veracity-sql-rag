from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from answer import audit as audit_mod
from answer import executor as executor_mod
from answer import formatting
from answer import sql_generation
from answer import synthesis
from answer.deps import get_config, get_db
from answer.formatting import QuerySection
from answer.grounding import GroundingResult, ground_question
from answer.guardrails import validate_sql
from answer.access_context import AskAccess, resolve_ask_access
from answer.schemas import AskAccessOut, AskChunkOut, AskIn, AskOut, AskQueryOut, AskResultSetOut
from shared.access.cells import cell_token, cell_tokens
from answer.demand import record_unanswered
from answer.understanding import understand_question
from shared.config import AppConfig
from shared.models import CatalogDatabase
from shared.llm.factory import build_llm_provider

logger = logging.getLogger("answer.routers.ask")

router = APIRouter(prefix="/v1", tags=["ask"])


def _access_out(access: Optional[AskAccess], grounding: Optional[GroundingResult]) -> Optional[AskAccessOut]:
    if access is None:
        return None
    used = sorted(grounding.cells, key=lambda c: (-c[0], c[1])) if grounding else []
    return AskAccessOut(
        mode=access.mode, full_access=access.bypass,
        cells=cell_tokens(access.pairs),
        cells_used=[cell_token(lv, cat) for lv, cat in used],
        label_level=used[0][0] if used else None, label_category=used[0][1] if used else None,
        columns_withheld=sum(len(v) for v in grounding.denied_columns.values()) if grounding else 0,
    )


def _empty(
    took_ms: float, notes: str, grounding: Optional[GroundingResult] = None, answer: Optional[str] = None,
    access: Optional[AskAccess] = None,
) -> AskOut:
    # Even when there's no chunk/answer to return (SQL declined, query
    # returned no rows, a guardrail rejection), grounding may still have
    # found real tables worth showing — the trust score and the little
    # graph of what was looked at are informative on their own, not just
    # alongside a successful answer.
    return AskOut(
        chunks=[], took_ms=took_ms, notes=notes, answer=answer,
        database_name=grounding.database.database_name if grounding and grounding.database else None,
        trust_label=grounding.trust_label if grounding else None,
        trust_score=grounding.trust_score if grounding else None,
        graph_nodes=grounding.graph_nodes if grounding else [],
        graph_edges=grounding.graph_edges if grounding else [],
        access=_access_out(access, grounding),
    )


def _run_queries(db, req, cfg, grounding, proposal, source_cfg, source_name, database_name,
                 entities_touched, started) -> Tuple[List[QuerySection], List[str]]:
    """Guardrail-check and execute EVERY generated query (a broad question
    may get several exploratory ones). One query failing a safety check or
    erroring doesn't sink the others. Each attempt gets its own audit row."""
    sections: List[QuerySection] = []
    failures: List[str] = []
    grants = [g.model_dump() for g in req.grants]
    for gq in proposal.queries:
        guardrail = validate_sql(gq.sql, grounding.whitelist, cfg.catalog.answer,
                                 denied_columns=grounding.denied_columns, allowed_columns=grounding.allowed_columns,
                                 dialect=source_cfg.dialect)
        if not guardrail.ok:
            logger.warning("Ask guardrail rejected generated SQL (%s): %s", gq.purpose, guardrail.reason)
            audit_mod.record(
                db, question=req.question, requested_grants=grants,
                source_name=source_name, database_name=database_name, generated_sql=gq.sql,
                entities_touched=entities_touched, guardrail_verdict="REJECTED",
                rejection_reason=f"[{gq.purpose}] {guardrail.reason}", row_count=None,
                duration_ms=(time.perf_counter() - started) * 1000, error=None,
                enabled=cfg.catalog.answer.audit_enabled, purpose=gq.purpose,
            )
            failures.append(f"{gq.purpose}: failed a safety check ({guardrail.reason})")
            continue

        exec_result = executor_mod.execute(source_cfg, database_name, guardrail.sql_to_execute, cfg.catalog.answer)
        if not exec_result.ok:
            audit_mod.record(
                db, question=req.question, requested_grants=grants,
                source_name=source_name, database_name=database_name, generated_sql=guardrail.sql_to_execute,
                entities_touched=entities_touched, guardrail_verdict="ERROR",
                rejection_reason=None, row_count=None,
                duration_ms=(time.perf_counter() - started) * 1000, error=exec_result.error,
                enabled=cfg.catalog.answer.audit_enabled, purpose=gq.purpose,
            )
            failures.append(f"{gq.purpose}: failed to execute ({exec_result.error})")
            continue

        audit_mod.record(
            db, question=req.question, requested_grants=grants,
            source_name=source_name, database_name=database_name, generated_sql=guardrail.sql_to_execute,
            entities_touched=entities_touched, guardrail_verdict="ALLOWED", rejection_reason=None,
            row_count=len(exec_result.rows), duration_ms=(time.perf_counter() - started) * 1000, error=None,
            enabled=cfg.catalog.answer.audit_enabled, purpose=gq.purpose,
        )
        sections.append(QuerySection(
            purpose=gq.purpose, sql=guardrail.sql_to_execute,
            rows=exec_result.rows[: cfg.catalog.answer.max_rows],
        ))
    return sections, failures


def _attempt_summary(proposal, sections: List[QuerySection], failures: List[str]) -> List[str]:
    """What a first attempt did and got, in a form the LLM can learn from
    on the retry (sql_generation's previous_attempt)."""
    lines = [f"- [{s.purpose}] {s.sql} -> {len(s.rows)} row(s)" for s in sections]
    lines += [f"- {f}" for f in failures]
    return lines or [f"- [{q.purpose}] {q.sql}" for q in proposal.queries]


@dataclass
class _DbAttempt:
    """What trying to answer from ONE grounded database produced."""
    sections: List[QuerySection] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    proposal: Optional[sql_generation.SqlProposal] = None
    total_queries: int = 0
    retried: bool = False
    note: str = ""  # why there were no queries at all, when that happened

    @property
    def data_sections(self) -> List[QuerySection]:
        return [s for s in self.sections if s.rows]


def _answer_from_database(db, req, cfg, llm_provider, understanding, grounding, started) -> _DbAttempt:
    """Steps 3-5 of the pipeline against one grounded database: generate
    queries, run them, and retry once with feedback if nothing came back."""
    grants = [g.model_dump() for g in req.grants]
    entities_touched = [f"{s}.{t}" for s, t in sorted(grounding.whitelist)]
    source_name = grounding.source.name if grounding.source else None
    database_name = grounding.database.database_name if grounding.database else None

    def audit_no_query(reason: str, verdict: str, error: Optional[str] = None) -> None:
        audit_mod.record(
            db, question=req.question, requested_grants=grants,
            source_name=source_name, database_name=database_name, generated_sql=None,
            entities_touched=entities_touched, guardrail_verdict=verdict,
            rejection_reason=None if error else reason, row_count=None,
            duration_ms=(time.perf_counter() - started) * 1000, error=error,
            enabled=cfg.catalog.answer.audit_enabled,
        )

    configured = next((s for s in cfg.catalog.sources if s.name == source_name), None)
    dialect = configured.dialect if configured is not None else "mssql"
    proposal = sql_generation.generate_sql(
        llm_provider, cfg.catalog.inference, req.question, grounding, understanding=understanding,
        dialect=dialect,
    )
    if proposal is None or not proposal.ok:
        if proposal is None:
            reason = "No LLM is configured (catalog.inference) — nothing was asked."
        elif proposal.reasoning:
            reason = f"The assistant didn't propose a query for {database_name}: {proposal.reasoning}"
        else:
            reason = f"The assistant didn't propose a query for {database_name}."
        audit_no_query(reason, "REJECTED")
        return _DbAttempt(proposal=proposal, note=reason)

    # grounding.source is a CatalogSource ORM row (name/host only) — the
    # live SourceConfig (credentials, driver, timeouts) lives in
    # cfg.catalog.sources, matched by name.
    source_cfg = next((s for s in cfg.catalog.sources if s.name == source_name), None)
    if source_cfg is None or not source_cfg.is_configured:
        audit_no_query("source not configured", "ERROR", error="source not configured")
        return _DbAttempt(proposal=proposal, note=f"The source for {database_name} is not configured.")

    run_args = (db, req, cfg, grounding)
    run_kwargs = dict(source_cfg=source_cfg, source_name=source_name, database_name=database_name,
                      entities_touched=entities_touched, started=started)
    attempt = _DbAttempt(proposal=proposal, total_queries=len(proposal.queries))
    attempt.sections, attempt.failures = _run_queries(*run_args, proposal=proposal, **run_kwargs)

    # Tolerance: a first attempt that found nothing (a misspelled name,
    # the value stored in a different column/format, a query that
    # errored) gets ONE retry, with the LLM shown exactly what it tried.
    if not attempt.data_sections:
        retry = sql_generation.generate_sql(
            llm_provider, cfg.catalog.inference, req.question, grounding, understanding=understanding,
            previous_attempt=_attempt_summary(proposal, attempt.sections, attempt.failures),
            dialect=dialect,
        )
        if retry is not None and retry.ok:
            attempt.retried = True
            more_sections, more_failures = _run_queries(*run_args, proposal=retry, **run_kwargs)
            attempt.sections += more_sections
            attempt.failures += more_failures
            attempt.total_queries += len(retry.queries)
            attempt.proposal = retry

    # One line per database tried, in logs/answer.log — enough to
    # self-monitor without opening the UI or the audit table.
    logger.info(
        "Ask %r -> db=%s tables=%d queries=%d ok=%d failed=%d rows=%d retried=%s trust=%s(%.2f) %.0fms",
        req.question, database_name, len(grounding.tables), attempt.total_queries, len(attempt.sections),
        len(attempt.failures), sum(len(s.rows) for s in attempt.sections), attempt.retried,
        grounding.trust_label, grounding.trust_score or 0.0, (time.perf_counter() - started) * 1000,
    )
    return attempt


@router.post("/ask", response_model=AskOut)
def ask(req: AskIn, request: Request, db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config)) -> AskOut:
    """See _ask for the pipeline. This wrapper resolves who is asking and
    writes the question's audit record (catalog_ask_log) once, whatever
    the outcome."""
    # Who is asking and what they may see (answer/access_context.py): a
    # signed-in user's cells come from their session, never from the body.
    access = resolve_ask_access(request, db, cfg, req.grants)
    req.grants = access.grants
    audit_mod.current_asker.set(access.username)
    tracker = audit_mod.AskTracker()
    token = audit_mod.current_ask.set(tracker)
    try:
        out = _ask(req, db, cfg, access)
    except Exception:
        _log_question(db, cfg, req, access, tracker, None)
        raise
    finally:
        audit_mod.current_ask.reset(token)
    out.ask_id = str(tracker.ask_id)
    if access.can_view_sql:
        out.queries = [AskQueryOut(purpose=q.purpose, sql=q.sql, verdict=q.verdict, rows=q.rows, reason=q.reason)
                       for q in tracker.queries if q.sql or q.reason]
    _log_question(db, cfg, req, access, tracker, out)
    return out


def _log_question(db, cfg, req: AskIn, access: AskAccess, tracker, out: Optional[AskOut]) -> None:
    acc = out.access if out else None
    disabled = not cfg.catalog.enabled or not cfg.catalog.answer.enabled
    outcome = "ERROR" if out is None else audit_mod.outcome_for(
        tracker, answered=bool(out.chunks), grounded=bool(out.graph_nodes), disabled=disabled)
    audit_mod.record_question(
        db, tracker, question=req.question, asked_by=access.username, mode=access.mode,
        scope_database=req.database, database_name=out.database_name if out else None, outcome=outcome,
        trust_label=out.trust_label if out else None, trust_score=out.trust_score if out else None,
        label_level=acc.label_level if acc else None, label_category=acc.label_category if acc else None,
        cells_held=cell_tokens(access.pairs),
        cells_used=list(acc.cells_used) if acc else [],
        tables=[n.label for n in out.graph_nodes] if out else [],
        columns_withheld=acc.columns_withheld if acc else 0,
        enabled=cfg.catalog.answer.audit_enabled,
    )


def _scope_ids(db: Session, name: Optional[str]) -> Optional[Set[UUID]]:
    if not name:
        return None
    return {r[0] for r in db.query(CatalogDatabase.id).filter(func.lower(CatalogDatabase.database_name) == name.lower())}


def _ask(req: AskIn, db: Session, cfg: AppConfig, access: AskAccess) -> AskOut:
    """The app's ONE pipeline, for every kind of question:

        understand the question (typos, "who/what is this about")
        -> find the relevant tables in the catalog
        -> generate one or more read-only queries
        -> run them against the real database
        -> if nothing came back, try ONCE more with a looser approach
        -> still nothing: move on to the next most likely database
           (the same kind of data often exists in several of them)
        -> narrate a human answer from the real rows, with its trust score

    Also the integration contract (docs/integration.md): a server caller's
    own ask pipeline calls this as an OPTIONAL fallback data source. It never raises for
    an ordinary "couldn't answer" outcome — a disabled module, no
    matching entities, an LLM that declined, or a guardrail rejection
    are all just an empty-chunks 200, so the caller can fold an empty
    result in and proceed with documents alone."""
    started = time.perf_counter()
    grants = [g.model_dump() for g in req.grants]

    if not cfg.catalog.enabled or not cfg.catalog.answer.enabled:
        return _empty((time.perf_counter() - started) * 1000, "Catalog answering is disabled.", access=access)

    only_databases = _scope_ids(db, req.database)
    if only_databases is not None and not only_databases:
        return _empty((time.perf_counter() - started) * 1000,
                      f'The database "{req.database}" is not in the catalog.', access=access)

    llm_provider = build_llm_provider(cfg.catalog.inference)

    # 1. Understand how the person asked (spelling, what KIND of data a
    #    name/value refers to) before searching metadata for it.
    understanding = understand_question(llm_provider, cfg.catalog.inference, req.question)

    # 2-5. Catalog -> queries -> execute (-> retry), database by database:
    #      best-matching database first; if it can't answer, the next.
    search_text = understanding.search_text(req.question) if understanding else None
    max_dbs = max(1, cfg.catalog.answer.max_databases_per_question)
    tried: Set[UUID] = set()
    tried_names: List[str] = []
    empty_sections: List[QuerySection] = []
    demand_ids: List[UUID] = []
    last_grounding: Optional[GroundingResult] = None
    last_attempt: Optional[_DbAttempt] = None

    for n in range(max_dbs):
        grounding = ground_question(
            db, cfg.catalog.search, cfg.catalog.answer, cfg.catalog.confidence, req.question, req.grants,
            search_text=search_text, exclude_databases=tried, bypass_grants=access.bypass,
            only_databases=only_databases,
        )
        if not grounding.ok:
            if n == 0:
                record_unanswered(db, grounding.demand_candidate_ids)
                audit_mod.record(
                    db, question=req.question, requested_grants=grants,
                    source_name=None, database_name=None, generated_sql=None, entities_touched=None,
                    guardrail_verdict="REJECTED", rejection_reason=grounding.notes, row_count=None,
                    duration_ms=(time.perf_counter() - started) * 1000, error=None,
                    enabled=cfg.catalog.answer.audit_enabled,
                )
                return _empty((time.perf_counter() - started) * 1000, grounding.notes, grounding, access=access)
            break  # no further databases match

        attempt = _answer_from_database(db, req, cfg, llm_provider, understanding, grounding, started)
        db_name = grounding.database.database_name if grounding.database else "?"
        if attempt.data_sections:
            return _success(req, cfg, llm_provider, grounding, attempt, tried_names, started, access)

        last_grounding, last_attempt = grounding, attempt
        demand_ids += grounding.demand_candidate_ids
        tried.add(grounding.database.id)
        tried_names.append(db_name)
        for sec in attempt.sections:
            empty_sections.append(QuerySection(purpose=f"{db_name}: {sec.purpose}", sql=sec.sql, rows=[]))
        if grounding.other_candidate_databases == 0:
            break

    # Nothing anywhere. If queries did run, that IS an answer ("no employee
    # named X in A, B or C") — narrated as a sentence, not a bare "0 rows".
    # It's also the demand signal: the tables this question relied on
    # weren't enough, so they're flagged for deeper analysis.
    record_unanswered(db, demand_ids)
    took_ms = (time.perf_counter() - started) * 1000
    where = ", ".join(tried_names)
    if empty_sections:
        answer = synthesis.synthesize_answer(llm_provider, cfg.catalog.inference, req.question, empty_sections)
        return _empty(
            took_ms,
            f"Searched {len(tried_names)} database(s) ({where}); the generated queries ran but matched no rows. "
            f"{last_grounding.notes}",
            last_grounding, answer=answer, access=access,
        )
    reasons = "; ".join(filter(None, [last_attempt.note] + last_attempt.failures)) if last_attempt else ""
    return _empty(
        took_ms,
        f"{last_grounding.notes} Searched {where}. No database context was returned — "
        f"{reasons or 'none of the generated queries could be run'}.",
        last_grounding, access=access,
    )


def _result_set(sec: QuerySection) -> AskResultSetOut:
    columns: List[str] = []
    for row in sec.rows:
        for k in row:
            if k not in columns:
                columns.append(k)
    return AskResultSetOut(
        purpose=sec.purpose, columns=columns,
        rows=[[None if row.get(c) is None else formatting.format_value(row.get(c)) for c in columns] for row in sec.rows],
    )


def _success(req, cfg, llm_provider, grounding, attempt: _DbAttempt, tried_before: List[str], started,
             access: Optional[AskAccess] = None) -> AskOut:
    """6. Narrate from the real rows of the database that answered."""
    took_ms = (time.perf_counter() - started) * 1000
    source_name = grounding.source.name if grounding.source else None
    database_name = grounding.database.database_name if grounding.database else None
    data_sections = attempt.data_sections
    configured = next((s for s in cfg.catalog.sources if s.name == source_name), None)
    dialect = configured.dialect if configured is not None else "db"

    table_labels = [f"{t.entity.schema_name}.{t.entity.object_name}" for t in grounding.tables]
    text = formatting.render_sections(req.question, table_labels, data_sections)
    # The chunk carries the answer's classification: the most sensitive
    # cell among the tables and columns it drew on (docs/integration.md —
    # a caller files this chunk under that cell).
    top = max(grounding.cells, key=lambda c: (c[0], c[1]), default=None)
    best_level = top[0] if top else max((t.entity.access_level or 0 for t in grounding.tables), default=0)
    best_category = top[1] if top else next(
        (t.entity.access_category for t in grounding.tables if t.entity.access_level == best_level), "A")

    chunk = AskChunkOut(
        chunk_id=f"ask:{database_name}:{hash(tuple(s.sql for s in data_sections)) & 0xffffffff:x}",
        doc_id=f"db:{source_name}:{database_name}",
        text=text,
        level=best_level,
        category=best_category or "A",
        score=attempt.proposal.confidence_hint if attempt.proposal else 0.0,
        title=f"Live database — {', '.join(table_labels)}",
        source_uri=f"{dialect}://{source_name}/{database_name}",
        origin="database",
        trust_label=grounding.trust_label,
        trust_score=grounding.trust_score,
    )

    # Never blocks the chunk above: None (LLM unavailable) just means
    # AskOut.answer stays empty and the raw chunk text is what's shown.
    synthesized = synthesis.synthesize_answer(llm_provider, cfg.catalog.inference, req.question, data_sections)

    notes = grounding.notes
    if tried_before:
        notes += f" (First looked in {', '.join(tried_before)}, which had nothing matching.)"
    if attempt.retried:
        notes += " (A second, looser attempt was made after the first found nothing.)"
    if attempt.failures:
        notes += (f" ({len(attempt.failures)} of {attempt.total_queries} generated queries could not be run: "
                  f"{'; '.join(attempt.failures)})")

    return AskOut(
        chunks=[chunk], took_ms=took_ms, notes=notes, answer=synthesized,
        database_name=database_name, results=[_result_set(sec) for sec in data_sections],
        trust_label=grounding.trust_label, trust_score=grounding.trust_score,
        graph_nodes=grounding.graph_nodes, graph_edges=grounding.graph_edges,
        access=_access_out(access, grounding),
    )
