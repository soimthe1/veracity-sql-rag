"""
Question understanding — the first half of "go through the catalog
smartly". Runs BEFORE catalog search (answer/grounding.py) and turns
how a person actually asks into what the catalog can actually match:

  "tell me about john smith"
      -> search_terms: ["employee", "person", "staff", "name"]
         values:       ["John Smith"]
  "wat is the shop aplication doing"
      -> corrected:    "what is the Shop application doing"
         search_terms: ["shop", "application", "status"]

Why this is needed at all: catalog search matches table/column
METADATA, never row data. A person's name, an application number, or a
city is a VALUE that lives inside rows — no table is called "John
Smith" — so searching for it directly finds nothing (or noise). The LLM
knows "John Smith" is a person and people live in employee/person/user
tables; the generated SQL (answer/sql_generation.py) is then what
actually filters rows by that value, using `values` from here.

This is part of the ONE pipeline (question -> catalog -> queries ->
execute -> narrate), not a shortcut around it: nothing here is ever
shown to the user as an answer. Same fail-safe contract as every other
LLM call site: never raises; if the LLM is unavailable or answers
garbage, grounding simply searches with the raw question (and
api/search's own stopword + typo handling still applies).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from shared.config import InferenceConfig
from shared.llm.base import LlmProvider

logger = logging.getLogger("answer.understanding")

_MAX_TERMS = 10
_MAX_VALUES = 6

_SYSTEM_PROMPT = """You prepare a user's question for searching a database catalog. The \
catalog only knows table names, column names and short descriptions of what each table \
holds - it does NOT contain row data such as people's names, IDs, cities or dates. The user \
may type casually, in English or Arabic, with spelling mistakes.

Return:
- "corrected_question": the question with obvious spelling mistakes fixed, same meaning and \
language, nothing added.
- "search_terms": up to 10 short English words or two-word phrases describing the KINDS of \
data needed, as they would plausibly appear in table/column names or descriptions. Include \
the obvious synonyms (e.g. employee, staff, person, personnel; qualification, education, \
degree; birth, dob). Keep any product/system/application names the user mentions (e.g. \
"inventory"), spelled correctly. Never put a specific person's name or ID here.
- "values": the specific values the user wants rows filtered by (person names, IDs, codes, \
cities, dates), exactly as the user meant them, spelling fixed only if clearly a typo. Empty \
list if the question is general.

Respond with ONLY one JSON object, no other text:
{"corrected_question": string, "search_terms": [string, ...], "values": [string, ...]}"""


@dataclass
class QuestionUnderstanding:
    corrected_question: str
    search_terms: List[str] = field(default_factory=list)
    values: List[str] = field(default_factory=list)

    def search_text(self, original: str) -> str:
        """What catalog search should look for: the original words (so
        nothing the user typed is lost) plus the LLM's data-kind terms.
        Values are deliberately left out — they're row data, not
        metadata, and would only add noise to a metadata search."""
        extra = " ".join(self.search_terms)
        return f"{self.corrected_question or original} {extra}".strip()


def _str_list(raw, limit: int) -> List[str]:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, (str, int, float)) and str(item).strip():
            out.append(str(item).strip()[:120])
    return out[:limit]


def _parse(text: str, original: str) -> Optional[QuestionUnderstanding]:
    try:
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        logger.warning("Question understanding response was not valid JSON, ignoring: %s", text[:200])
        return None
    corrected = data.get("corrected_question")
    corrected = corrected.strip()[:1000] if isinstance(corrected, str) and corrected.strip() else original
    return QuestionUnderstanding(
        corrected_question=corrected,
        search_terms=_str_list(data.get("search_terms"), _MAX_TERMS),
        values=_str_list(data.get("values"), _MAX_VALUES),
    )


def understand_question(
    provider: Optional[LlmProvider], cfg: InferenceConfig, question: str,
) -> Optional[QuestionUnderstanding]:
    """None (never raises) when there's no LLM or it failed — callers
    fall back to searching with the raw question."""
    if provider is None:
        return None
    result = provider.complete(f"Question: {question}", system=_SYSTEM_PROMPT, json_mode=True)
    if not result.ok:
        logger.info("Question understanding skipped: %s", result.error)
        return None
    u = _parse(result.text, question)
    if u:
        logger.info("Understood %r -> terms=%s values=%s", question, u.search_terms, u.values)
    return u
