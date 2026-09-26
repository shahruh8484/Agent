"""Print a bcrypt hash for ADMIN_PASSWORD_HASH:
    python -m amzagent.security "your-password"
"""
import sys

import bcrypt

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit('usage: python -m amzagent.security "your-password"')
    print(bcrypt.hashpw(sys.argv[1].encode(), bcrypt.gensalt()).decode())
