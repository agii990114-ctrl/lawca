"""테스트 공통 설정. DB가 필요한 테스트는 docker-compose의 lawca_test 데이터베이스를 쓴다."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from lawca.api.app import app
from lawca.db import repo
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
    with engine.begin() as conn:  # 자료실 검색에 쓰는 확장(docker-compose 이미지에 들어 있다)
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
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


TEST_PASSWORD = "test-pass-1"


def make_user(factory: sessionmaker[Session], username: str, role: str, name: str | None = None) -> str:
    """테스트 사용자를 만들고 id를 돌려준다. 비밀번호는 TEST_PASSWORD."""
    with factory() as session:
        user = repo.create_user(session, username, name or username, role, TEST_PASSWORD)
        session.commit()
        return str(user.id)


def login(client, username: str, password: str = TEST_PASSWORD):
    return client.post("/api/auth/login", json={"username": username, "password": password})
