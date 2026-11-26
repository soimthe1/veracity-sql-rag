"""Small helper for upserting engine-side status into catalog_health —
consumed by the API's /v1/status endpoint and any external status
widget polling it. Deliberately just a few upserted rows, never
an events table."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from shared.models import CatalogHealth


def set_component_health(session: Session, component: str, status: str, detail: Optional[dict] = None) -> None:
    row = session.query(CatalogHealth).filter_by(component=component).one_or_none()
    if row is None:
        row = CatalogHealth(component=component, status=status, detail=detail or {})
        session.add(row)
    else:
        row.status = status
        row.detail = detail or {}
        row.updated_at = datetime.now(timezone.utc)
