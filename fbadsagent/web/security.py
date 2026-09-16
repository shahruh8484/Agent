"""Password hashing for the dashboard's single admin login.

Generate a hash to put in .env as ADMIN_PASSWORD_HASH:

    python -m fbadsagent.web.security "your-password-here"
"""
from __future__ import annotations

import sys

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def _main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m fbadsagent.web.security <password>", file=sys.stderr)
        raise SystemExit(1)
    print(hash_password(sys.argv[1]))


if __name__ == "__main__":
    _main()
