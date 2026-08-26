#!/usr/bin/env python3
"""Plan docs/24 migration by default; pass --apply for additive backfill."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.data_source_migrations import migrate  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--founder-id", default="founder")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(migrate(
        args.founder_id, apply=args.apply)), sort_keys=True))


if __name__ == "__main__":
    main()
