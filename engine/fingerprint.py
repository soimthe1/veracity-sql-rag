"""
Change-detection fingerprints (spec section 7/"Change detection").
Cheap, deterministic hashes over structural shape — if a fingerprint is
unchanged since last_seen_at, discovery does almost nothing for that
object this cycle; a reprocess is only triggered when it changes.
"""
from __future__ import annotations

import hashlib
from typing import Iterable, Sequence


def _hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x1f")  # unit separator, avoids accidental collisions across fields
    return h.hexdigest()


def table_fingerprint(columns: Sequence[dict], pk_columns: Sequence[str], indexes: Sequence[dict]) -> str:
    """Column structure fingerprint — name/type/nullability/order, plus
    PK and index key-column shape. Deliberately excludes row counts and
    other volatile stats — those aren't a "schema changed" signal."""
    col_sig = "|".join(
        f"{c['column_name']}:{c['data_type']}:{c['is_nullable']}:{c['ordinal_position']}"
        for c in sorted(columns, key=lambda c: c["ordinal_position"])
    )
    pk_sig = ",".join(sorted(pk_columns))
    idx_sig = "|".join(sorted(f"{i['index_name']}:{i.get('key_columns','')}" for i in indexes))
    return _hash(col_sig, pk_sig, idx_sig)


def definition_fingerprint(definition: str) -> str:
    """View/procedure/function body fingerprint. Whitespace-normalized
    so a purely cosmetic reformat doesn't trigger a full re-analysis."""
    normalized = " ".join((definition or "").split())
    return _hash(normalized)


def database_fingerprint(entity_fingerprints: Iterable[str]) -> str:
    """Rolls up every entity fingerprint into one database-level value
    so "did anything in this database change at all" is a single cheap
    comparison before descending into per-object diffing."""
    return _hash("|".join(sorted(entity_fingerprints)))
