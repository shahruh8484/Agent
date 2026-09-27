"""Exit 0 if an agent cycle is running right now, 1 otherwise.

Used by deploy/autoupdate.sh so an update waits for the cycle to finish
instead of cutting it off: `python -m amzagent.busy`.
"""
import sys

from amzagent.config import get_settings
from amzagent.store import Store


def main() -> int:
    return 0 if Store(get_settings().data_dir).run_in_progress() else 1


if __name__ == "__main__":
    sys.exit(main())
