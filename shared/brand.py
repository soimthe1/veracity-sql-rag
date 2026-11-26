"""The product brand: branding/brand.local/brand.json when present, else branding/brand.example/brand.json."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent / "branding"


@lru_cache(maxsize=1)
def brand() -> dict:
    for folder in ("brand.local", "brand.example"):
        path = _ROOT / folder / "brand.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {"name": "Veracity SQL", "mark": "SA"}
