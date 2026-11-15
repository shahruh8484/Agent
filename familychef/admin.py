"""Server admin commands.

On the server:
  docker exec familychef python -m familychef.admin plus someone@gmail.com 30   # give Plus for 30 days
  docker exec familychef python -m familychef.admin free someone@gmail.com      # take Plus away
  docker exec familychef python -m familychef.admin user someone@gmail.com      # show a user
  docker exec familychef python -m familychef.admin stats                       # totals
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

from familychef.accounts import Accounts, AuthError


def _fmt(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ts else "-"


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    acc = Accounts()
    cmd, args = argv[0], argv[1:]
    if cmd == "stats":
        for k, v in acc.stats().items():
            print(f"{k}: {v}")
        return 0
    if cmd in ("plus", "free", "user") and args:
        try:
            user = acc.find_user_by_email(args[0])
        except AuthError:
            user = None
        if not user:
            print(f"Нет пользователя с почтой {args[0]} (он должен хотя бы раз войти в FamilyChef).")
            return 1
        if cmd == "plus":
            days = int(args[1]) if len(args) > 1 else 30
            start = max(time.time(), user["plus_until"])
            acc.set_plus(user["id"], int(start + days * 86400))
        elif cmd == "free":
            acc.set_plus(user["id"], 0)
        user = acc.get_user(user["id"])
        print(f"{user['email']} ({user['name']}): {'Plus до ' + _fmt(user['plus_until']) if user['plus'] else 'бесплатный тариф'}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
