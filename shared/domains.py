"""
Business-domain normalization. The LLM names each object's domain in free
text (engine/semantic.py), so the same area comes back under many names:
"Access Control", "Access Management", "Identity and Access Management",
"Security", "Cybersecurity"... — 44 domains for ~120 described objects,
most holding a single term, which made the Glossary and Overview hard to
use and split related tables apart.

catalog.domains.canonical (config.yaml) is an ordered list of canonical
domains, each with keywords. A proposed domain maps to the FIRST canonical
domain any of whose keywords appears in it at the START of a word
(case-insensitive; "hr" matches "HR Data" but not "Synchronization",
"network" matches "Networking", "file" doesn't match "Profile"),
so order matters: specific rules ("data processing", "application
configuration") come before broad ones ("process", "application"). A
domain matching nothing is kept as the LLM wrote it — nothing is dropped,
it just isn't merged. The LLM's original wording is kept in
CatalogEntity.properties["domain_raw"] for traceability.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional


def canonical_domain(raw: Optional[str], rules: Iterable) -> Optional[str]:
    """`rules`: DomainRule-like objects with .name and .keywords."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    lowered = text.lower()
    for rule in rules:
        if rule.name.lower() == lowered:
            return rule.name
        for kw in rule.keywords:
            if re.search(r"(?<![a-z0-9])" + re.escape(kw.lower()), lowered):
                return rule.name
    return text


def canonical_names(rules: Iterable) -> List[str]:
    return [r.name for r in rules]
