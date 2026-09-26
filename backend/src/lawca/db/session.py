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


@cache
def get_checkpointer():  # noqa: ANN201
    """LangGraph 체크포인터(PostgreSQL). 되묻기로 멈춘 작업을 서버를 다시 띄워도 이어갈 수 있게 한다."""
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    conninfo = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
    pool = ConnectionPool(conninfo, kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}, open=True)
    saver = PostgresSaver(pool)
    saver.setup()
    return saver
