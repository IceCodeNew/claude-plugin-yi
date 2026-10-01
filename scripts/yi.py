"""Run yi directly from a Claude plugin checkout."""

import sys
from pathlib import Path

if sys.version_info < (3, 11):
    sys.stderr.write("yi requires Python 3.11 or later. Install a supported interpreter.\n")
    raise SystemExit(0 if "record" in sys.argv and "--hook" in sys.argv else 1)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yi.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
