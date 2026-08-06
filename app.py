#!/usr/bin/env python3
"""Entry point. Runnable from anywhere: `python3 /path/to/app.py --port 8765`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spoofer.server import main  # noqa: E402

if __name__ == "__main__":
    main()
