"""로그인과 사용자 관리 API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from lawca.api.deps import DB, Admin, CurrentUser
from lawca.api.schemas import LoginRequest, PasswordChange, PasswordReset, UserCreate, UserOut
from lawca.auth import ROLE_LABELS, SESSION_COOKIE, SESSION_TTL, LoginLimiter, password_problem, verify_password
from lawca.config import Settings, get_settings
from lawca.db import repo
from lawca.db.models import User

router = APIRouter()
limiter = LoginLimiter()


def user_out(user: User) -> UserOut:
    return UserOut(
        id=str(user.id),
        username=user.username,
        name=user.name,
        role=user.role,  # type: ignore[arg-type]
        role_label=ROLE_LABELS[user.role],
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def _check_password(password: str) -> None:
    problem = password_problem(password)
    if problem:
        raise HTTPException(422, problem)


@router.post("/api/auth/login")
def login(
    req: LoginRequest, response: Response, session: DB, settings: Annotated[Settings, Depends(get_settings)]
) -> UserOut:
    wait = limiter.locked_for(req.username)
    if wait:
        raise HTTPException(429, f"로그인에 여러 번 실패했습니다. {int(wait // 60) + 1}분 뒤에 다시 시도하세요.")
    user = repo.authenticate(session, req.username, req.password)
    if user is None:
        limiter.failed(req.username)
        raise HTTPException(401, "아이디 또는 비밀번호가 맞지 않습니다.")
    limiter.succeeded(req.username)
    token = repo.start_session(session, user)
    session.commit()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    return user_out(user)


@router.post("/api/auth/logout", status_code=204)
def logout(request: Request, response: Response, session: DB) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        repo.end_session(session, token)
        session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/api/auth/me")
def me(user: CurrentUser) -> UserOut:
    return user_out(user)


@router.post("/api/auth/password", status_code=204)
def change_password(req: PasswordChange, user: CurrentUser, session: DB, response: Response) -> None:
    """본인 비밀번호를 바꾼다. 다른 곳의 로그인도 모두 끊기므로 다시 로그인해야 한다."""
    if not verify_password(req.current_password, user.password_hash):
        raise HTTPException(400, "현재 비밀번호가 맞지 않습니다.")
    _check_password(req.new_password)
    repo.set_password(session, user, req.new_password, by_admin=False)
    session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


# 사용자 관리(관리자)


@router.get("/api/users")
def users(_: Admin, session: DB) -> list[UserOut]:
    return [user_out(u) for u in repo.list_users(session)]


@router.post("/api/users", status_code=201)
def create_user(req: UserCreate, _: Admin, session: DB) -> UserOut:
    if repo.find_user(session, req.username):
        raise HTTPException(409, f"이미 있는 아이디입니다: {req.username}")
    _check_password(req.password)
    user = repo.create_user(session, req.username, req.name, req.role, req.password)
    session.commit()
    return user_out(user)


def _target(session, user_id: str) -> User:
    user = repo.get_user(session, user_id)
    if user is None:
        raise HTTPException(404, "사용자를 찾을 수 없습니다.")
    return user


@router.delete("/api/users/{user_id}", status_code=204)
def delete_user(user_id: str, admin: Admin, session: DB) -> None:
    user = _target(session, user_id)
    if user.id == admin.id:
        raise HTTPException(400, "본인 계정은 삭제할 수 없습니다.")
    repo.delete_user(session, user)
    session.commit()


@router.post("/api/users/{user_id}/password", status_code=204)
def reset_password(user_id: str, req: PasswordReset, _: Admin, session: DB) -> None:
    """관리자가 임시 비밀번호를 정해 준다. 그 사용자의 로그인은 모두 끊긴다."""
    user = _target(session, user_id)
    _check_password(req.password)
    repo.set_password(session, user, req.password, by_admin=True)
    session.commit()
