"""로그인: 비밀번호 해시, 세션 토큰, 로그인 실패 제한. 저장은 lawca.db.repo가 한다.

- 비밀번호는 scrypt(표준 라이브러리)로 해시한다. 형식: scrypt$n$r$p$salt(hex)$hash(hex)
- 세션 토큰은 쿠키로만 주고, DB에는 SHA-256 해시만 둔다(DB가 새도 토큰을 쓸 수 없다).
- 같은 아이디로 연달아 틀리면 잠시 막는다. 프로세스 메모리에 두므로 서버를 다시 띄우면 풀린다.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from datetime import timedelta

ROLE_LABELS = {"clerk": "사무원", "lawyer": "변호사", "admin": "관리자"}
SESSION_COOKIE = "lawca_session"
MIN_PASSWORD_LENGTH = 8
MAX_FAILURES = 5
LOCK_SECONDS = 300

_N, _R, _P = 2**14, 8, 1


def session_ttl() -> timedelta:
    """로그인 유지 시간. 설정(SESSION_HOURS, 기본 12시간)에서 읽는다."""
    from lawca.config import get_settings

    return timedelta(hours=get_settings().session_hours)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
    except ValueError:
        return False
    return hmac.compare_digest(actual.hex(), digest)


def password_problem(password: str) -> str | None:
    """비밀번호 규칙에 맞지 않으면 이유를 돌려준다."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다."
    if password.isdigit() or password.isalpha():
        return "비밀번호에는 글자와 숫자를 함께 넣어 주세요."
    return None


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", "ignore")).hexdigest()


class LoginLimiter:
    """아이디별 연속 실패 횟수. MAX_FAILURES번 틀리면 LOCK_SECONDS 동안 막는다."""

    def __init__(self, max_failures: int = MAX_FAILURES, lock_seconds: float = LOCK_SECONDS, clock=time.monotonic):
        self._max = max_failures
        self._lock_seconds = lock_seconds
        self._clock = clock
        self._failures: dict[str, tuple[int, float]] = {}
        self._mutex = threading.Lock()

    def locked_for(self, username: str) -> float:
        """막힌 남은 시간(초). 막히지 않았으면 0."""
        with self._mutex:
            count, since = self._failures.get(username.lower(), (0, 0.0))
            if count < self._max:
                return 0
            remaining = self._lock_seconds - (self._clock() - since)
            if remaining <= 0:
                del self._failures[username.lower()]
                return 0
            return remaining

    def failed(self, username: str) -> None:
        with self._mutex:
            count, _ = self._failures.get(username.lower(), (0, 0.0))
            self._failures[username.lower()] = (count + 1, self._clock())

    def succeeded(self, username: str) -> None:
        with self._mutex:
            self._failures.pop(username.lower(), None)
