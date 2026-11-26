from __future__ import annotations

from typing import Iterator

from sqlalchemy.orm import Session

from shared.config import AppConfig, load_config
from shared.db import get_session_factory


def get_config() -> AppConfig:
    return load_config()


def get_db() -> Iterator[Session]:
    Session = get_session_factory(get_config())
    session = Session()
    try:
        yield session
    finally:
        session.close()
