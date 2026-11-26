"""Search provider abstraction. `POST /v1/search` / `POST /v1/resolve`
never know which backend answered a query — today that's always
Postgres (queries the catalog's own database directly, zero extra
infrastructure), but keeping search behind this interface means a
second backend could be added later (see api/search/factory.py)
without touching a single consumer."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List
from uuid import UUID

from sqlalchemy.orm import Session


@dataclass
class SearchHit:
    entity_id: UUID
    score: float


class SearchProvider(ABC):
    @abstractmethod
    def search(self, session: Session, query: str, limit: int = 20) -> List[SearchHit]:
        raise NotImplementedError
