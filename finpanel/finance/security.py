"""bcrypt password hashing. Generate ADMIN_PASSWORD_HASH with:
    python -m finance.security "your-password"
"""
from __future__ import annotations

import sys

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print('Usage: python -m finance.security "your-password"')
        sys.exit(1)
    print(hash_password(sys.argv[1]))
