"""API 공통 의존성: DB 세션, 로그인한 사용자, 권한.

권한
- 사무원(clerk): 문서 처리·기한 확정·서식 초안·조회. 확정한 기한은 '완료'로만 바꿀 수 있다.
- 변호사(lawyer): 사무원이 하는 일 전부 + 기한 취소·되돌리기 + 초안 검토 완료 표시.
- 관리자(admin): 사용자 관리만 한다. 업무 기능은 쓰지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session, sessionmaker

from lawca.auth import SESSION_COOKIE
from lawca.db import repo
from lawca.db.models import User
from lawca.db.session import get_session_factory

SessionFactory = Annotated[sessionmaker[Session], Depends(get_session_factory)]


def get_session(factory: SessionFactory) -> Iterator[Session]:
    with factory() as session:
        yield session


DB = Annotated[Session, Depends(get_session)]


def get_current_user(request: Request, session: DB) -> User:
    """쿠키의 세션 토큰으로 사용자를 찾는다. 이 요청의 DB 세션에 감사 기록용 actor를 넣는다."""
    token = request.cookies.get(SESSION_COOKIE)
    user = repo.user_for_token(session, token) if token else None
    if user is None:
        raise HTTPException(401, "로그인이 필요합니다.")
    session.info["actor"] = user.username
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def _role(*roles: str):
    def check(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(403, "이 기능을 쓸 권한이 없습니다.")
        return user

    return Depends(check)


Worker = Annotated[User, _role("clerk", "lawyer")]
"""업무 기능(문서·기한·서식·조회)을 쓰는 사람."""
Lawyer = Annotated[User, _role("lawyer")]
Admin = Annotated[User, _role("admin")]
