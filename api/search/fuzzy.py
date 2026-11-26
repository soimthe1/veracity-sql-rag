"""
Typo tolerance for catalog search, with no Postgres extension required
(same constraint as postgres_search.py: pg_trgm / fuzzystrmatch can't be
assumed on a managed/shared instance).

How it works: every database/table/view/column name, semantic name, domain
and synonym in the catalog is split into word tokens ("EmpQualification" ->
"emp", "qualification"; "DATE_OF_BIRTH" -> "date", "birth") and kept as
a vocabulary. A question term that isn't in that vocabulary is matched
against it with difflib's similarity ratio, so "qualifcation",
"employe", "invetory" or "brith" still find "qualification", "employee",
"inventory", "birth". The corrected tokens are ADDED to the search terms,
never substituted — if the original spelling was right after all
(a real word the catalog just doesn't happen to use), nothing is lost.

The vocabulary is cached per process and rebuilt every
_VOCAB_TTL_SECONDS, because the catalog keeps evolving (discovery keeps
adding objects and the LLM keeps adding semantic names) — a new table
should become fuzzy-findable within minutes, without a restart.
Candidate matching is bucketed by token length so a lookup never scans
the full vocabulary.
"""
from __future__ import annotations

import difflib
import logging
import re
import threading
import time
from typing import Dict, List, Set

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("api.search.fuzzy")

_VOCAB_TTL_SECONDS = 300
_MIN_TOKEN_LEN = 3
_CUTOFF = 0.78          # difflib ratio; ~1 typo in a 5-letter word, ~2 in a 10-letter one
_MAX_CORRECTIONS_PER_TERM = 3

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SPLIT_RE = re.compile(r"[^0-9A-Za-z؀-ۿ]+")

_lock = threading.Lock()
_vocab: Set[str] = set()
_by_len: Dict[int, List[str]] = {}
_built_at: float = 0.0


def tokenize(value: str) -> List[str]:
    """Identifier-aware word split: snake_case, camelCase, PascalCase,
    spaces and punctuation all become separate lowercase tokens."""
    if not value:
        return []
    spaced = _CAMEL_RE.sub(" ", value)
    return [t.lower() for t in _SPLIT_RE.split(spaced) if len(t) >= _MIN_TOKEN_LEN]


def _rebuild(session: Session) -> None:
    global _vocab, _by_len, _built_at
    rows = session.execute(text("""
        SELECT object_name AS v FROM catalog_entities
        UNION SELECT semantic_name FROM catalog_entities WHERE semantic_name IS NOT NULL
        UNION SELECT domain FROM catalog_entities WHERE domain IS NOT NULL
        UNION SELECT unnest(synonyms) FROM catalog_entities WHERE synonyms IS NOT NULL
        UNION SELECT database_name FROM catalog_databases
        UNION SELECT column_name FROM catalog_fields
        UNION SELECT semantic_name FROM catalog_fields WHERE semantic_name IS NOT NULL
    """)).fetchall()
    vocab: Set[str] = set()
    for (v,) in rows:
        vocab.update(tokenize(v or ""))
    by_len: Dict[int, List[str]] = {}
    for tok in vocab:
        by_len.setdefault(len(tok), []).append(tok)
    _vocab, _by_len, _built_at = vocab, by_len, time.monotonic()
    logger.info("Fuzzy search vocabulary rebuilt: %d tokens", len(vocab))


def _ensure_vocab(session: Session) -> None:
    if _vocab and time.monotonic() - _built_at < _VOCAB_TTL_SECONDS:
        return
    with _lock:
        if _vocab and time.monotonic() - _built_at < _VOCAB_TTL_SECONDS:
            return
        try:
            _rebuild(session)
        except Exception:  # noqa: BLE001 - fuzzy expansion is a bonus, never a reason for search to fail
            logger.exception("Could not rebuild fuzzy search vocabulary; continuing with exact terms only")
            session.rollback()


def expand_terms(session: Session, terms: List[str]) -> List[str]:
    """Returns catalog-vocabulary tokens that are close misspellings of
    `terms` (excluding terms already spelled exactly as the catalog
    spells them). Never raises; returns [] if the vocabulary is empty or
    unavailable."""
    _ensure_vocab(session)
    if not _vocab:
        return []
    out: List[str] = []
    seen: Set[str] = set()
    for term in terms:
        term = term.lower()
        if len(term) < _MIN_TOKEN_LEN or term in _vocab:
            continue
        pool: List[str] = []
        spread = max(1, len(term) // 4)
        for n in range(len(term) - spread, len(term) + spread + 1):
            pool.extend(_by_len.get(n, ()))
        for match in difflib.get_close_matches(term, pool, n=_MAX_CORRECTIONS_PER_TERM, cutoff=_CUTOFF):
            if match not in seen:
                seen.add(match)
                out.append(match)
    return out
