"""
Name tokenizing for the "auto" bucketing layer: matches the lattice file's
naming conventions (category_folders / folder_conventions) against SQL object
names the way folder names are matched in a folder-based classification.

Matching is whole-word, never substring: "HR" matches "HR_Data" and
"PayrollHR" (camel-case boundary) but not "Chrome"; "top-secret" matches
"TopSecretDocs" and "top_secret". Arabic text is normalized
(diacritics and tatweel stripped, alef variants folded, taa marbuta
folded to haa) so a key written in plain form still matches.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
_TATWEEL = "ـ"
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SPLIT = re.compile(r"[^\w؀-ۿ]+|_+")


def _normalize_arabic(text: str) -> str:
    text = _DIACRITICS.sub("", text).replace(_TATWEEL, "")
    for src in "أإآٱ":
        text = text.replace(src, "ا")
    return text.replace("ة", "ه").replace("ى", "ي")


def tokens(name: str) -> List[str]:
    """'TopSecret_HRData' -> ['top', 'secret', 'hr', 'data']."""
    text = _normalize_arabic(name or "")
    text = _CAMEL.sub(" ", text)
    return [t.lower() for t in _SPLIT.split(text) if t]


def normalize_phrase(phrase: str) -> str:
    return " ".join(tokens(phrase))


def find_phrases(name: str, phrases: Dict[str, object]) -> List[Tuple[str, object]]:
    """All convention keys (already normalize_phrase'd) that occur in `name`
    as a whole-word sequence. Longest keys first."""
    toks = tokens(name)
    if not toks or not phrases:
        return []
    joined = f" {' '.join(toks)} "
    hits = [(k, v) for k, v in phrases.items() if k and f" {k} " in joined]
    hits.sort(key=lambda kv: -len(kv[0]))
    return hits


def match_category(name: str, category_names: Dict[str, str]) -> Optional[Tuple[str, str]]:
    hits = find_phrases(name, category_names)
    return (hits[0][1], hits[0][0]) if hits else None  # (category, matched phrase)


def match_level(name: str, level_names: Dict[str, int]) -> Optional[Tuple[int, str]]:
    """Highest level among all matching phrases (over-classify when unsure)."""
    hits = find_phrases(name, level_names)
    if not hits:
        return None
    best = max(hits, key=lambda kv: kv[1])
    return int(best[1]), best[0]
