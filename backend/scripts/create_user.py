"""사용자를 만든다. 처음 설치할 때 관리자 계정을 만드는 데 쓴다(그다음부터는 관리자가 화면에서 만든다).

    uv run python scripts/create_user.py admin 관리자 --role admin
    uv run python scripts/create_user.py dev-clerk 개발사무원 --role clerk --password-env LAWCA_DEV_PASSWORD

비밀번호는 입력창에서 받는다(화면에 보이지 않음). --password-env를 주면 그 환경 변수(또는 레포 루트 .env)에서 읽는다.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from lawca.auth import ROLE_LABELS, password_problem
from lawca.db import repo
from lawca.db.session import get_session_factory

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def env_value(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name:
                return value.strip()
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("name")
    parser.add_argument("--role", choices=list(ROLE_LABELS), required=True)
    parser.add_argument("--password-env", help="비밀번호를 읽을 환경 변수 이름")
    args = parser.parse_args()

    if args.password_env:
        password = env_value(args.password_env)
        if not password:
            sys.exit(f"{args.password_env} 값이 없습니다.")
    else:
        password = getpass.getpass("비밀번호: ")
        if password != getpass.getpass("비밀번호 확인: "):
            sys.exit("비밀번호가 서로 다릅니다.")
    if problem := password_problem(password):
        sys.exit(problem)

    factory = get_session_factory()
    with factory() as session:
        if repo.find_user(session, args.username):
            sys.exit(f"이미 있는 아이디입니다: {args.username}")
        repo.create_user(session, args.username, args.name, args.role, password)
        session.commit()
    print(f"만들었습니다: {args.username} ({ROLE_LABELS[args.role]})")


if __name__ == "__main__":
    main()
