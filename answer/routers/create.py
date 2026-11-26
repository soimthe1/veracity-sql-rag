"""
Create — turn an answer into a document:

  POST /v1/ask/create {ask_id, format, lang, content}
    format docx | pptx -> the file
    format email       -> {"ok": true, "subject": ..., "body": ...}
    format html        -> a print-ready page (print / "Save as PDF")
    format pdf         -> {"ok": true}   (this app's UI prints it itself; logged here)

Rules:
- Only the person who asked (or an administrator) can create from an
  answer; everything else is a 404, so ask ids can't be probed. A service
  caller (another application that resolves its own users) names that
  person in X-On-Behalf-Of.
- Who asked, when, and the CLASSIFICATION come from the question's audit
  record (catalog_ask_log) — never from the request.
- The SQL appendix is included only for people who may see generated SQL,
  and is read from the audit trail, not from the request.
- Every document created is logged (catalog_ask_exports).
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from answer import audit as audit_mod
from answer import exports
from answer.access_context import optional_ask_access
from answer.deps import get_config, get_db
from answer.schemas import CreateIn
from shared.access.cells import cell_token
from shared.access.lattice import load_lattice
from shared.config import AppConfig
from shared.models import CatalogAskAudit, CatalogAskLog

router = APIRouter(prefix="/v1/ask", tags=["ask"])

FORMATS = {"docx", "pptx", "pdf", "email", "html"}
_MEDIA = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def marking_for(cfg: AppConfig, level: Optional[int], category: Optional[str], lang: str) -> exports.Marking:
    """"RESTRICTED · 40D · Ops Restricted", coloured by level."""
    if level is None or not category:
        return exports.Marking(text=exports._T.get(lang, exports._T["en"])["no_label"])
    lat = load_lattice(cfg)
    lv = lat.level(level)
    name = (lv.name_ar if lang == "ar" and getattr(lv, "name_ar", None) else lv.name) if lv else str(level)
    parts = [name.upper() if lang != "ar" else name, cell_token(level, category)]
    label = lat.cell_label(level, category) or {}
    text = (label.get("name_ar") if lang == "ar" else None) or label.get("name")
    if text:
        parts.append(text)
    return exports.Marking(text=" · ".join(parts), color=(lv.color if lv and lv.color else "#6B7280"))


def _load_owned(db: Session, access, ask_id: str) -> CatalogAskLog:
    try:
        key = uuid.UUID(ask_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Answer not found.") from exc
    log = db.get(CatalogAskLog, key)
    if log is None:
        raise HTTPException(status_code=404, detail="Answer not found.")
    if access.mode == "service" and not access.username:
        raise HTTPException(status_code=403, detail="Send X-On-Behalf-Of with the user who asked.")
    if access.mode in ("user", "service") and not access.is_admin and log.asked_by != access.username:
        raise HTTPException(status_code=404, detail="Answer not found.")
    return log


def _queries(db: Session, log: CatalogAskLog) -> List[tuple]:
    rows = (db.query(CatalogAskAudit).filter(CatalogAskAudit.ask_id == log.id, CatalogAskAudit.generated_sql.isnot(None),
                                             CatalogAskAudit.guardrail_verdict == "ALLOWED")
            .order_by(CatalogAskAudit.created_at).all())
    return [(audit_mod._purpose_from(r.rejection_reason), r.generated_sql, r.row_count) for r in rows]


@router.post("/create")
def create(body: CreateIn, request: Request, db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config)):
    fmt = body.format.lower().strip()
    if fmt not in FORMATS:
        raise HTTPException(status_code=400, detail=f"format must be one of {', '.join(sorted(FORMATS))}")
    access = optional_ask_access(request, db, cfg)
    log = _load_owned(db, access, body.ask_id)
    lang = "ar" if body.lang == "ar" else "en"
    audit_mod.record_export(db, log, access.username, fmt, enabled=cfg.catalog.answer.audit_enabled)
    if fmt == "pdf":
        # This app's own UI prints the PDF itself; this only records it.
        return {"ok": True}

    c = body.content
    doc = exports.ExportDoc(
        question=log.question, answer=(c.answer or c.notes or "")[:20000],
        marking=marking_for(cfg, log.label_level, log.label_category, lang),
        asked_by=log.asked_by, asked_at=log.created_at, ask_id=str(log.id), lang=lang,
        database_name=log.database_name, trust_label=log.trust_label, trust_score=log.trust_score,
        results=exports.clamp_results(c.results), tables=[t[:200] for t in (c.tables or [])][:50],
        queries=_queries(db, log) if access.can_view_sql else [],
    )
    if fmt == "email":
        subject, text = exports.build_email(doc)
        return {"ok": True, "subject": subject, "body": text}
    if fmt == "html":
        # A print-ready page (open it and print / "Save as PDF") — how a
        # caller without its own renderer offers PDF.
        return Response(content=exports.build_html(doc), media_type="text/html; charset=utf-8")
    data = exports.build_docx(doc) if fmt == "docx" else exports.build_pptx(doc)
    stamp = (log.created_at.strftime("%Y%m%d-%H%M") if log.created_at else "answer")
    filename = f"answer-{stamp}-{str(log.id)[:8]}.{fmt}"
    return Response(content=data, media_type=_MEDIA[fmt],
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
