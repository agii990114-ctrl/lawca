"""테스트 공통 설정. DB가 필요한 테스트는 docker-compose의 lawca_test 데이터베이스를 쓴다."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from lawca.api.app import app
from lawca.db.models import Base
from lawca.db.session import get_session_factory

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://lawca:lawca@localhost:5433/lawca_test"
)


@pytest.fixture(scope="session")
def test_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(TEST_DATABASE_URL)
    try:
        engine.connect().close()
    except OperationalError:
        pytest.skip("테스트 DB에 연결할 수 없습니다. 레포 루트에서 docker compose up -d 로 띄우세요.")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def db(test_factory: sessionmaker[Session]) -> Iterator[sessionmaker[Session]]:
    """테이블을 비우고 앱이 테스트 DB를 쓰게 한다."""
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with test_factory() as session:
        session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        session.commit()
    app.dependency_overrides[get_session_factory] = lambda: test_factory
    yield test_factory
    app.dependency_overrides.pop(get_session_factory, None)
