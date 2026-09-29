"""로그인·권한·사용자 관리 테스트."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lawca.api.app import app
from lawca.api.users import limiter
from lawca.auth import MAX_FAILURES, LoginLimiter, hash_password, password_problem, verify_password
from lawca.db import repo
from lawca.db.models import AuditLog
from tests.conftest import TEST_PASSWORD, login, make_user
from tests.test_api import chat, client, new_conversation, upload  # noqa: F401 (픽스처 재사용)
from tests.test_deadline_records import confirm, document  # noqa: F401

# 순수 함수


def test_password_hash_roundtrip():
    stored = hash_password("abc12345")
    assert stored.startswith("scrypt$") and "abc12345" not in stored
    assert verify_password("abc12345", stored)
    assert not verify_password("abc12346", stored)
    assert not verify_password("abc12345", "garbage")
    assert hash_password("abc12345") != stored  # 솔트가 매번 다르다


def test_password_rules():
    assert password_problem("short1")
    assert password_problem("12345678")
    assert password_problem("abcdefgh")
    assert password_problem("abcd1234") is None


def test_limiter_locks_and_expires():
    now = [0.0]
    limiter_ = LoginLimiter(max_failures=3, lock_seconds=60, clock=lambda: now[0])
    for _ in range(3):
        limiter_.failed("Kim")
    assert limiter_.locked_for("kim") == 60  # 대소문자 무시
    now[0] = 61
    assert limiter_.locked_for("kim") == 0


# 로그인


@pytest.fixture
def anon(db):
    return TestClient(app)


def test_login_logout_and_me(anon, db):
    make_user(db, "hong", "clerk", "홍길동")
    assert anon.get("/api/auth/me").status_code == 401
    res = login(anon, "HONG")  # 아이디는 대소문자를 가리지 않는다
    assert res.status_code == 200
    assert res.json()["role_label"] == "사무원" and res.json()["name"] == "홍길동"
    cookie = res.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert anon.get("/api/auth/me").json()["username"] == "hong"
    assert anon.post("/api/auth/logout").status_code == 204
    assert anon.get("/api/auth/me").status_code == 401


def test_wrong_password_and_unknown_user_look_the_same(anon, db):
    make_user(db, "hong", "clerk")
    wrong = login(anon, "hong", "wrong-pass-1")
    unknown = login(anon, "nobody", "wrong-pass-1")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_repeated_failures_lock_the_account(anon, db):
    make_user(db, "locked", "clerk")
    try:
        for _ in range(MAX_FAILURES):
            login(anon, "locked", "wrong-pass-1")
        assert login(anon, "locked").status_code == 429  # 맞는 비밀번호도 잠시 막힌다
    finally:
        limiter.succeeded("locked")


def test_everything_but_health_needs_login(anon):
    assert anon.get("/api/health").status_code == 200
    for path in ("/api/conversations", "/api/deadlines", "/api/rules", "/api/users"):
        assert anon.get(path).status_code == 401, path


def test_change_own_password(anon, db):
    make_user(db, "hong", "clerk")
    login(anon, "hong")
    bad = anon.post("/api/auth/password", json={"current_password": "nope-1234", "new_password": "new-pass-2"})
    assert bad.status_code == 400
    weak = anon.post("/api/auth/password", json={"current_password": TEST_PASSWORD, "new_password": "short"})
    assert weak.status_code == 422
    ok = anon.post("/api/auth/password", json={"current_password": TEST_PASSWORD, "new_password": "new-pass-2"})
    assert ok.status_code == 204
    assert anon.get("/api/auth/me").status_code == 401  # 다시 로그인해야 한다
    assert login(anon, "hong").status_code == 401
    assert login(anon, "hong", "new-pass-2").status_code == 200


# 사용자 관리


@pytest.fixture
def admin(anon, db):
    make_user(db, "admin", "admin", "관리자")
    login(anon, "admin")
    return anon


def test_admin_adds_lists_and_deletes_users(admin, db):
    res = admin.post("/api/users", json={"username": "lee", "name": "이변호", "role": "lawyer", "password": "lee-pass-1"})
    assert res.status_code == 201 and res.json()["role_label"] == "변호사"
    assert [u["username"] for u in admin.get("/api/users").json()] == ["admin", "lee"]

    other = TestClient(app)
    assert login(other, "lee", "lee-pass-1").status_code == 200
    assert admin.delete(f"/api/users/{res.json()['id']}").status_code == 204
    assert other.get("/api/auth/me").status_code == 401  # 삭제하면 로그인이 끊긴다
    assert login(other, "lee", "lee-pass-1").status_code == 401
    assert [u["username"] for u in admin.get("/api/users").json()] == ["admin"]

    # 삭제한 아이디는 다시 쓸 수 있다
    again = admin.post("/api/users", json={"username": "lee", "name": "이새로", "role": "clerk", "password": "lee-pass-2"})
    assert again.status_code == 201

    with db() as session:
        logs = [(a.actor, a.action) for a in session.scalars(select(AuditLog).order_by(AuditLog.id))]
        assert ("admin", "user.create") in logs and ("admin", "user.delete") in logs


def test_admin_input_checks(admin):
    base = {"username": "park", "name": "박사무", "role": "clerk", "password": "park-pass-1"}
    assert admin.post("/api/users", json=base).status_code == 201
    assert admin.post("/api/users", json=base).status_code == 409  # 중복
    assert admin.post("/api/users", json=base | {"username": "kim", "password": "123"}).status_code == 422
    assert admin.post("/api/users", json=base | {"username": "김사무"}).status_code == 422  # 아이디는 영문·숫자
    assert admin.post("/api/users", json=base | {"username": "kim", "role": "boss"}).status_code == 422
    me = admin.get("/api/auth/me").json()
    assert admin.delete(f"/api/users/{me['id']}").status_code == 400  # 본인 삭제 금지


def test_admin_resets_password(admin, db):
    user_id = make_user(db, "hong", "clerk")
    assert admin.post(f"/api/users/{user_id}/password", json={"password": "temp-pass-9"}).status_code == 204
    other = TestClient(app)
    assert login(other, "hong").status_code == 401
    assert login(other, "hong", "temp-pass-9").status_code == 200


# 권한


def test_roles_are_enforced(admin, db):
    assert admin.get("/api/conversations").status_code == 403  # 관리자는 업무 기능을 쓰지 않는다
    make_user(db, "hong", "clerk")
    clerk = TestClient(app)
    login(clerk, "hong")
    assert clerk.get("/api/users").status_code == 403
    assert clerk.post("/api/users", json={"username": "x1", "name": "x", "role": "admin", "password": "x-pass-11"}).status_code == 403


def test_conversations_are_private(client, db):  # noqa: F811
    conversation_id = new_conversation(client)
    make_user(db, "clerk2", "clerk")
    other = TestClient(app)
    login(other, "clerk2")
    assert other.get("/api/conversations").json() == []
    assert other.get(f"/api/conversations/{conversation_id}").status_code == 404
    res = other.post("/api/chat", json={"conversation_id": conversation_id, "message": "안녕", "file_ids": []})
    assert res.status_code == 404


def test_clerk_cancels_and_restores_but_only_lawyer_deletes(client, document, db):  # noqa: F811
    record = confirm(client, document).json()
    url = f"/api/deadlines/{record['id']}"
    assert client.patch(url, json={"status": "done"}).status_code == 200
    assert client.patch(url, json={"status": "confirmed"}).status_code == 200  # 복원
    assert client.patch(url, json={"status": "cancelled"}).status_code == 200
    assert client.delete(url).status_code == 403
    make_user(db, "lawyer1", "lawyer")
    login(client, "lawyer1")
    assert client.delete(url).status_code == 204


def test_only_lawyers_review_drafts(client, db):  # noqa: F811
    with db() as session:
        session.info["actor"] = "clerk1"
        file = repo.save_file(session, "초안.docx", b"docx", mime="application/octet-stream")
        draft = repo.save_draft(session, case=None, form_id="fact_inquiry", file=file, values={}, blanks=[], job_id=None)
        session.commit()
        draft_id = str(draft.id)
    assert client.get(f"/api/drafts/{draft_id}").json()["created_by"] == "clerk1"
    assert client.post(f"/api/drafts/{draft_id}/review").status_code == 403
    make_user(db, "lawyer1", "lawyer")
    login(client, "lawyer1")
    body = client.post(f"/api/drafts/{draft_id}/review").json()
    assert body["reviewed_by"] == "lawyer1" and body["reviewed_at"]


def test_session_lifetime_follows_the_setting(anon, db, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from lawca.config import get_settings
    from lawca.db.models import UserSession

    monkeypatch.setattr(get_settings(), "session_hours", 2)
    make_user(db, "worker1", "clerk")
    response = login(anon, "worker1")
    assert "Max-Age=604800" in response.headers["set-cookie"]  # 쿠키는 절대 상한(7일)까지 두고, 실제 만료는 서버가 정한다
    with db() as session:
        expires = session.scalars(select(UserSession)).one().expires_at
    assert timedelta(hours=1, minutes=59) < expires - datetime.now(UTC) <= timedelta(hours=2)  # 서버가 정한 만료


def test_session_slides_while_used_and_has_absolute_limit(anon, db, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from lawca.config import get_settings
    from lawca.db.models import UserSession

    monkeypatch.setattr(get_settings(), "session_hours", 2)
    make_user(db, "worker1", "clerk")
    login(anon, "worker1")

    def row(**changes):
        with db() as session:
            item = session.scalars(select(UserSession)).one()
            for key, value in changes.items():
                setattr(item, key, value)
            session.commit()
            return item.expires_at, item.created_at

    now = datetime.now(UTC)
    row(expires_at=now + timedelta(minutes=30))  # 쓰지 않고 1시간 반이 지난 상태
    assert anon.get("/api/auth/me").status_code == 200
    expires, _ = row()
    assert timedelta(hours=1, minutes=59) < expires - datetime.now(UTC) <= timedelta(hours=2)  # 쓰면 2시간으로 밀린다

    row(expires_at=now - timedelta(minutes=1))  # 쓰지 않아 만료
    assert anon.get("/api/auth/me").status_code == 401

    login(anon, "worker1")
    with db() as session:
        item = session.scalars(select(UserSession).order_by(UserSession.created_at.desc())).first()
        item.created_at = now - timedelta(days=7) + timedelta(minutes=30)  # 절대 상한 30분 전
        item.expires_at = now + timedelta(minutes=10)
        session.commit()
    assert anon.get("/api/auth/me").status_code == 200
    with db() as session:
        item = session.scalars(select(UserSession).order_by(UserSession.created_at.desc())).first()
        assert item.expires_at - datetime.now(UTC) <= timedelta(minutes=31)  # 상한을 넘겨 늘어나지 않는다
        item.created_at = now - timedelta(days=8)
        session.commit()
    assert anon.get("/api/auth/me").status_code == 401  # 절대 상한 지남
