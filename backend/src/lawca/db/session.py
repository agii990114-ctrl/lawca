"""DB 연결."""

from __future__ import annotations

from functools import cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from lawca.config import get_settings


@cache
def get_session_factory() -> sessionmaker[Session]:
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    return sessionmaker(engine, expire_on_commit=False)
