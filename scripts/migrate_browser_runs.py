#!/usr/bin/env python3
"""Apply the idempotent docs/22 BrowserRun projection backfill."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.browser_migrations import migrate  # noqa: E402

if __name__ == "__main__":
    print(json.dumps(asyncio.run(migrate()), sort_keys=True))
