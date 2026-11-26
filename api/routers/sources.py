from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.access import require_admin
from api.deps import get_config, get_db
from api.schemas import SourceConfigOut, SourceOut
from api.serializers import serialize_source
from shared.auth.login import Principal
from shared.config import AppConfig
from shared.models import CatalogDatabase, CatalogSource

router = APIRouter(prefix="/v1/sources", tags=["sources"])


@router.get("", response_model=List[SourceOut])
def list_sources(db: Session = Depends(get_db)) -> List[SourceOut]:
    counts = dict(
        db.query(CatalogDatabase.source_id, func.count(CatalogDatabase.id))
        .group_by(CatalogDatabase.source_id).all()
    )
    sources = db.query(CatalogSource).order_by(CatalogSource.name).all()
    return [serialize_source(s, counts.get(s.id, 0)) for s in sources]


@router.get("/config", response_model=List[SourceConfigOut])
def list_source_config(
    db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config), _: Principal = Depends(require_admin),
) -> List[SourceConfigOut]:
    """Instance-level sweeping configuration, merged with whatever the
    engine has discovered so far — see SourceConfigOut's docstring for
    why this stops at the instance, never a per-database control.
    Admin-only when catalog.auth.enabled (host/port/login username are
    internal infrastructure details, not ordinary catalog content)."""
    counts = dict(
        db.query(CatalogDatabase.source_id, func.count(CatalogDatabase.id))
        .group_by(CatalogDatabase.source_id).all()
    )
    live_by_name = {s.name: s for s in db.query(CatalogSource).all()}

    out: List[SourceConfigOut] = []
    for s in cfg.catalog.sources:
        live = live_by_name.get(s.name)
        out.append(SourceConfigOut(
            name=s.name, enabled=s.enabled, is_configured=s.is_configured,
            type=s.type, host=s.display_host, port=s.port, driver=s.display_driver, login_user=s.user,
            trust_server_certificate=s.trust_server_certificate, encrypt=s.encrypt,
            connect_timeout_seconds=s.connect_timeout_seconds,
            databases_to_catalog=s.databases_to_catalog,
            autodiscover_databases=(len(s.databases_to_catalog) == 0),
            live_status=live.status if live else None,
            live_database_count=counts.get(live.id, 0) if live else 0,
            last_discovery_at=live.last_discovery_at if live else None,
            last_validation_at=live.last_validation_at if live else None,
            consecutive_failures=live.consecutive_failures if live else 0,
            last_error=live.last_error if live else None,
        ))
    return out
