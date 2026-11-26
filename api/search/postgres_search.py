"""
Default search backend — queries the catalog's OWN Postgres directly
(the same "existing infrastructure" database catalog.database already
points at). No extra service, no extension dependency (deliberately
avoids requiring pg_trgm, which some managed/shared Postgres instances
won't let an app create for itself) — combines a plain-text-search
tsvector match with an ILIKE fallback so both natural-language queries
("employee skills and assignments") and exact code-like queries
("DP_CD") work reasonably well.

Human-tolerant by design (users type questions, not identifiers):
- Filler words are dropped before matching ("tell me about", "what is",
  "show me the") — without this, a 2-letter word like "me" ILIKE-matches
  every table whose name happens to contain "me" (Name, Member, Time...),
  which buried the real matches under noise.
- COLUMNS count, not just table names: "date of birth" or
  "qualification" usually live in a column of an Employees table, not in
  the table's own name.
- Misspellings are tolerated: api/search/fuzzy.py adds the closest
  catalog-vocabulary spellings of any term the catalog doesn't contain
  verbatim ("qualifcation" -> "qualification", "employe" -> "employee").
- Identifier-aware: "EmpQualification" / "EMP_QUALIFICATION" are split
  into words on the vocabulary side, so natural-language terms find them.
- The DATABASE name counts too: "what is Inventory" is usually a question
  about the database Inventory as a whole, whose tables are
  named INV_StockItem etc. — every table in a matching database is a
  (weaker) hit, ranked by the engine's own relevance_score so business
  tables come before logs/system tables.

Multi-word queries use OR semantics across terms, not AND: a caller
asking "employee assignments" should get back both the entity that
matches "employee" and the one that matches "assignments" even though
no single entity's combined text contains both words. Early-catalog
entities are often only partially described (only one field carries a
semantic_name yet), so requiring every term to hit one row would make
search go quiet exactly when it's most useful — during the LEARNING
phase before the LLM/evidence pipeline has filled everything in.
Ranking still rewards rows that match MORE of the terms.
"""
from __future__ import annotations

import re
from typing import List

from sqlalchemy import text
from sqlalchemy.orm import Session

from api.search.base import SearchHit, SearchProvider
from api.search.fuzzy import expand_terms

_WORD_RE = re.compile(r"[0-9A-Za-z_؀-ۿ]+")
_MAX_TERMS = 16  # bound the generated SQL for pathological long queries
_MIN_TERM_LEN = 3

# Question filler, not data vocabulary. Deliberately does NOT include
# words that commonly ARE data vocabulary ("details", "records", "list"
# as in a "PriceList" table, "status", "type", "name", "date").
_STOPWORDS = {
    # English
    "a", "an", "the", "of", "for", "in", "on", "at", "to", "from", "by", "with", "and", "or", "about",
    "tell", "me", "us", "show", "give", "find", "get", "fetch", "display", "please", "can", "could",
    "would", "should", "you", "your", "i", "we", "my", "our", "is", "are", "was", "were", "be", "been",
    "do", "does", "did", "what", "whats", "which", "who", "whom", "whose", "how", "many", "much", "when",
    "where", "why", "there", "this", "that", "these", "those", "all", "any", "some", "know", "want",
    "need", "looking", "information", "info", "anything", "everything", "something", "it", "its",
    "has", "have", "had", "let", "see", "into", "than", "then", "also", "just", "like", "more", "most",
    # Arabic question filler
    "ما", "ماذا", "من", "عن", "في", "على", "هل", "كم", "هو", "هي", "أخبرني", "اخبرني", "اعرض", "أعطني",
    "اعطني", "عندي", "لدينا", "كل", "الى", "إلى", "مع", "هذا", "هذه", "التي", "الذي",
}

# Light Arabic normalisation, applied to the question here and to the
# catalog text in SQL (translate() below), so spelling variants match:
# أ/إ/آ/ٱ -> ا, ة -> ه, ى -> ي; harakat and tatweel dropped (question side).
# ponytail: no real stemming (plural/suffix forms like الموظفين vs موظف
# still differ beyond the ال prefix); add an Arabic stemmer if users miss
# matches.
_AR_FROM, _AR_TO = "أإآٱةى", "ااااهي"
_AR_TABLE = str.maketrans(_AR_FROM, _AR_TO)
_AR_MARKS_RE = re.compile("[ً-ْـ]")


def normalize_arabic(text_: str) -> str:
    return _AR_MARKS_RE.sub("", text_).translate(_AR_TABLE)


def _strip_article(word: str) -> str:
    """"الموظف" -> "موظف" (substring match then finds both forms). Short words keep it."""
    return word[2:] if word.startswith("ال") and len(word) >= 5 else word


_STOPWORDS = {normalize_arabic(w) for w in _STOPWORDS}


def query_terms(query: str) -> List[str]:
    """The meaningful words of a free-text question: lowercased, Arabic
    normalised, filler removed, too-short tokens dropped. Falls back to
    every word if the question was nothing BUT filler, so a search never
    silently becomes empty."""
    words = [normalize_arabic(w.lower()) for w in _WORD_RE.findall(query or "")]
    kept = [_strip_article(w) for w in words if w not in _STOPWORDS and len(w) >= _MIN_TERM_LEN]
    if not kept:
        kept = [w for w in words if len(w) >= 2]
    # de-dup, keep order
    seen = set()
    return [w for w in kept if not (w in seen or seen.add(w))]


class PostgresSearchProvider(SearchProvider):
    def search(self, session: Session, query: str, limit: int = 20) -> List[SearchHit]:
        query = (query or "").strip()
        if not query:
            return []

        terms = query_terms(query)
        terms = (terms + [t for t in expand_terms(session, terms) if t not in terms])[:_MAX_TERMS]
        if not terms:
            return []

        # Placeholder names only are interpolated into the SQL text (w0, w1, ...,
        # like0, like1, ...) — the actual query content always travels as bound
        # parameter values, never string-formatted into the SQL itself.
        tsquery_expr = " || ".join(f"plainto_tsquery('english', :w{i})" for i in range(len(terms)))

        def n(col: str) -> str:  # catalog text, Arabic-normalised like the question (normalize_arabic)
            return f"translate({col}, :ar_from, :ar_to)"

        synonyms = n("array_to_string(coalesce(e.synonyms, ARRAY[]::varchar[]), ' ')")
        entity_like = [
            f"({n('e.object_name')} ILIKE :like{i} OR {n('e.semantic_name')} ILIKE :like{i} "
            f"OR {n('e.description')} ILIKE :like{i} OR {n('e.domain')} ILIKE :like{i} "
            f"OR {synonyms} ILIKE :like{i})"
            for i in range(len(terms))
        ]
        db_like = " OR ".join(f"{n('d.database_name')} ILIKE :like{i}" for i in range(len(terms)))
        field_like = [
            f"EXISTS (SELECT 1 FROM catalog_fields f WHERE f.entity_id = e.id AND "
            f"({n('f.column_name')} ILIKE :like{i} OR {n('f.semantic_name')} ILIKE :like{i} "
            f"OR {n('f.description')} ILIKE :like{i}))"
            for i in range(len(terms))
        ]
        full_hit = f"{n('e.object_name')} ILIKE :full OR {n('e.semantic_name')} ILIKE :full"
        # Score: exact whole-phrase hit dominates, then full-text rank,
        # then how MANY distinct terms hit the table itself (strong) or
        # one of its columns (weaker) — so "employee qualification" ranks
        # a table matching both above one matching just one.
        entity_hits = " + ".join(f"(CASE WHEN {c} THEN 1 ELSE 0 END)" for c in entity_like)
        field_hits = " + ".join(f"(CASE WHEN {c} THEN 1 ELSE 0 END)" for c in field_like)
        doc = (
            "to_tsvector('english', coalesce(e.semantic_name, '') || ' ' || e.object_name || ' ' || "
            "coalesce(e.description, '') || ' ' || coalesce(e.domain, '') || ' ' || "
            "array_to_string(coalesce(e.synonyms, ARRAY[]::varchar[]), ' '))"
        )

        sql = text(f"""
            SELECT id, score FROM (
                SELECT
                    e.id AS id,
                    (CASE WHEN {full_hit} THEN 1.0 ELSE 0.0 END)
                    + COALESCE(ts_rank({doc}, ({tsquery_expr})), 0.0)
                    + 0.25 * ({entity_hits})
                    + 0.10 * ({field_hits})
                    + (CASE WHEN ({db_like}) THEN 0.05 + 0.10 * LEAST(GREATEST(e.relevance_score, 0.0), 1.0) ELSE 0.0 END)
                    AS score
                FROM catalog_entities e
                JOIN catalog_databases d ON d.id = e.database_id
                WHERE
                    ({full_hit})
                    OR ({" OR ".join(entity_like)})
                    OR ({" OR ".join(field_like)})
                    OR ({db_like})
                    OR {doc} @@ ({tsquery_expr})
            ) ranked
            ORDER BY score DESC
            LIMIT :limit
        """)

        params = {"full": f"%{normalize_arabic(query)}%", "limit": limit, "ar_from": _AR_FROM, "ar_to": _AR_TO}
        for i, w in enumerate(terms):
            params[f"w{i}"] = w
            params[f"like{i}"] = f"%{w}%"

        rows = session.execute(sql, params).fetchall()
        return [SearchHit(entity_id=r.id, score=float(r.score)) for r in rows]
