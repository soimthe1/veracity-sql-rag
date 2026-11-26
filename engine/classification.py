"""
Automatic relevance classification (spec: "Smart relevance
classification" + "Catalog everything, deeply understand what matters").

Every discovered object gets a classification and a 0..1 relevance
score, both computed here from purely structural signals (names,
column shapes, PK/FK connectivity, row-count tier) — no manual
include/exclude lists, per the spec's autonomy requirement. This runs
at discovery time for every object (cheap, Level 1). engine/coverage.py
later ranks objects by relevance_score to decide the deep-analysis (
Level 2) slice; engine/relationships.py's `refine_relevance()` nudges
the score once real usage evidence (view/proc references, executed
joins) exists, without changing the classification itself — a
structural reclassification only happens when the object's own
fingerprint changes (see engine/discovery.py).

This is deliberately heuristic, not a promise of perfect
classification — see DECISIONS.md for why "roughly right, cheap, and
autonomous" beats "perfect but requires a human to tune per database."
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

CLASSIFICATIONS = (
    "BUSINESS_ENTITY", "TRANSACTIONAL_DATA", "REFERENCE_DATA", "RELATIONSHIP_TABLE",
    "AUDIT_HISTORY", "SYSTEM_TECHNICAL", "STAGING", "TEMPORARY", "ARCHIVE",
    "LOGGING", "CONFIGURATION", "UNKNOWN",
)

_SYSTEM_TECHNICAL_RE = re.compile(
    r"^(__|ms_|dt_|trace_|cdc_)|migrations?history|"
    r"^aspnet_|^hangfire|sysdiagrams|^__msvirtualtrigger", re.IGNORECASE,
)
_TEMPORARY_RE = re.compile(r"(^tmp_|^temp_|_bak$|_backup\d*$|_old$|_copy\d*$|#)", re.IGNORECASE)
_STAGING_RE = re.compile(r"^(stg_|staging_|etl_|import_|export_|raw_)", re.IGNORECASE)
_AUDIT_HISTORY_RE = re.compile(r"(_audit$|_history$|_hist$|^audit_|^history_|_auditlog$)", re.IGNORECASE)
_LOGGING_RE = re.compile(r"(_log$|^log_|_logs$|eventlog|errorlog)", re.IGNORECASE)
_ARCHIVE_RE = re.compile(r"(_archive$|^archive_|_arch$)", re.IGNORECASE)
_CONFIGURATION_RE = re.compile(r"(^config|_config$|_settings$|^settings|_options$)", re.IGNORECASE)
_REFERENCE_NAME_RE = re.compile(r"(_type$|_status$|_code$|^ref_|_lookup$|_category$|^lu_)", re.IGNORECASE)

# Fallback for camelCase/PascalCase names with no underscore separators
# (e.g. "AppLog", "ErrorHistory") — substring regexes above miss these,
# but a naive substring match on "log" would false-positive on names
# like "Catalog"/"Dialog". Tokenizing on case/underscore boundaries and
# matching whole tokens avoids that.
_TOKEN_SPLIT_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")
_TOKEN_CATEGORY_KEYWORDS = {
    "SYSTEM_TECHNICAL": {"sys", "internal", "diagnostics", "trace", "cdc"},
    "TEMPORARY": {"tmp", "temp", "bak", "backup", "old", "scratch"},
    "STAGING": {"stg", "staging", "etl", "import", "export", "raw", "landing"},
    "AUDIT_HISTORY": {"audit", "history", "hist"},
    "LOGGING": {"log", "logs"},
    "ARCHIVE": {"archive", "arch"},
    "CONFIGURATION": {"config", "settings", "options", "preferences"},
}


def _tokenize(name: str) -> List[str]:
    return [t.lower() for t in _TOKEN_SPLIT_RE.findall(name) if t]


def _token_pattern_classification(name: str) -> Optional[str]:
    tokens = set(_tokenize(name))
    for category, keywords in _TOKEN_CATEGORY_KEYWORDS.items():
        if tokens & keywords:
            return category
    return None

_DESCRIPTIVE_COLUMN_RE = re.compile(
    r"(name|title|desc|description|email|address|note|comment|summary|label)", re.IGNORECASE
)
_DATE_COLUMN_RE = re.compile(r"(date|_at$|_dt$|timestamp|time$)", re.IGNORECASE)
_TEXTY_TYPES = {"varchar", "nvarchar", "char", "nchar", "text", "ntext",
                "character varying", "character", "varchar2", "nvarchar2", "clob", "string"}


@dataclass
class ObjectFacts:
    object_type: str  # TABLE | VIEW | PROCEDURE | FUNCTION | SYNONYM
    schema_name: str
    object_name: str
    columns: List[dict] = field(default_factory=list)  # column_name, data_type, is_nullable, ...
    pk_columns: List[str] = field(default_factory=list)
    fk_out_count: int = 0
    fk_in_count: int = 0
    row_count_estimate: int = 0
    referenced_by_count: int = 0  # views/procs/functions that reference this object


@dataclass
class Classification:
    classification: str
    relevance_score: float
    signals: Dict[str, object]


def _name_pattern_classification(name: str) -> Optional[str]:
    if _SYSTEM_TECHNICAL_RE.search(name):
        return "SYSTEM_TECHNICAL"
    if _TEMPORARY_RE.search(name):
        return "TEMPORARY"
    if _STAGING_RE.search(name):
        return "STAGING"
    if _CONFIGURATION_RE.search(name):
        return "CONFIGURATION"
    if _ARCHIVE_RE.search(name):
        return "ARCHIVE"
    if _AUDIT_HISTORY_RE.search(name):
        return "AUDIT_HISTORY"
    if _LOGGING_RE.search(name):
        return "LOGGING"
    return _token_pattern_classification(name)


def _has_descriptive_columns(columns: List[dict]) -> bool:
    return any(_DESCRIPTIVE_COLUMN_RE.search(c.get("column_name", "")) for c in columns)


def _has_date_columns(columns: List[dict]) -> bool:
    return any(_DATE_COLUMN_RE.search(c.get("column_name", "")) for c in columns)


def _texty_column_ratio(columns: List[dict]) -> float:
    if not columns:
        return 0.0
    texty = sum(1 for c in columns if (c.get("data_type") or "").lower() in _TEXTY_TYPES)
    return texty / len(columns)


def classify_structural(facts: ObjectFacts) -> Classification:
    signals: Dict[str, object] = {}
    name = facts.object_name

    pattern_hit = _name_pattern_classification(name)
    if pattern_hit:
        signals["name_pattern"] = pattern_hit
        score = 0.15 if pattern_hit in ("SYSTEM_TECHNICAL", "TEMPORARY") else 0.3
        return Classification(pattern_hit, score, signals)

    if facts.object_type in ("PROCEDURE", "FUNCTION"):
        # Structural signals for code objects are thin at discovery time;
        # real relevance comes from usage (engine/relationships.py's
        # refine_relevance, once dependency evidence exists).
        base = 0.35 if facts.referenced_by_count > 0 else 0.25
        return Classification("UNKNOWN", base, {"reason": "code_object_awaiting_usage_evidence"})

    if facts.object_type == "SYNONYM":
        return Classification("UNKNOWN", 0.3, {"reason": "synonym_awaiting_target_resolution"})

    n_cols = len(facts.columns)
    non_key_cols = n_cols - len(facts.pk_columns)
    descriptive = _has_descriptive_columns(facts.columns)
    dated = _has_date_columns(facts.columns)
    texty_ratio = _texty_column_ratio(facts.columns)
    signals.update({
        "n_cols": n_cols, "non_key_cols": non_key_cols, "descriptive": descriptive,
        "dated": dated, "texty_ratio": round(texty_ratio, 2),
        "fk_in": facts.fk_in_count, "fk_out": facts.fk_out_count,
        "row_count_estimate": facts.row_count_estimate,
    })

    # Junction/relationship table: mostly FK columns, little else.
    if facts.fk_out_count >= 2 and non_key_cols <= 3:
        classification = "RELATIONSHIP_TABLE"
    # Small, mostly-static, heavily-referenced -> reference/lookup data.
    elif (facts.row_count_estimate or 0) < 1000 and facts.fk_in_count >= 1 and n_cols <= 6:
        classification = "REFERENCE_DATA"
    elif _REFERENCE_NAME_RE.search(name) and facts.row_count_estimate < 50000:
        classification = "REFERENCE_DATA"
    # Central, descriptive, referenced by other tables -> core entity.
    elif facts.fk_in_count >= 1 and descriptive and len(facts.pk_columns) <= 2:
        classification = "BUSINESS_ENTITY"
    # High-volume, date-stamped, points outward but isn't pointed at much
    # -> transactional/event data.
    elif dated and (facts.row_count_estimate or 0) > 10000 and facts.fk_out_count >= 1:
        classification = "TRANSACTIONAL_DATA"
    elif descriptive and facts.fk_in_count == 0 and facts.fk_out_count == 0 and n_cols >= 3:
        # Standalone, descriptive, unconnected — still plausibly a core
        # entity (e.g. a table not yet joined by anything else in this
        # database), just less certain than the fk_in>=1 case above.
        classification = "BUSINESS_ENTITY"
    else:
        classification = "UNKNOWN"

    score = _relevance_score(classification, facts, descriptive, texty_ratio)
    return Classification(classification, score, signals)


def _relevance_score(classification: str, facts: ObjectFacts, descriptive: bool, texty_ratio: float) -> float:
    score = 0.3
    if classification in ("BUSINESS_ENTITY", "RELATIONSHIP_TABLE"):
        score += 0.3
    elif classification == "REFERENCE_DATA":
        score += 0.15
    elif classification in ("SYSTEM_TECHNICAL", "TEMPORARY"):
        score -= 0.4
    elif classification in ("STAGING", "LOGGING", "ARCHIVE"):
        score -= 0.2

    score += min(0.15, 0.05 * facts.fk_in_count)
    score += min(0.1, 0.03 * facts.fk_out_count)
    score += min(0.15, 0.03 * facts.referenced_by_count)
    if descriptive:
        score += 0.1
    if 0.2 <= texty_ratio <= 0.8:
        score += 0.05  # a healthy mix of text + typed columns, not a pure blob or pure code table

    return max(0.0, min(1.0, round(score, 4)))


def refine_relevance(current_score: float, referenced_by_count: int, executed_join_evidence_count: int) -> float:
    """Nudges (never replaces) a relevance score once Level-2 usage
    evidence exists — repeated real-world use is a stronger signal than
    structure alone (spec: "Repeated operational use should
    substantially increase confidence")."""
    bump = min(0.2, 0.02 * referenced_by_count) + min(0.15, 0.03 * executed_join_evidence_count)
    return max(0.0, min(1.0, round(current_score + bump, 4)))
