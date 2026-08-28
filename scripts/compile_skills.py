#!/usr/bin/env python3
"""Compile or verify the reviewed shadow skill catalog artifact."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.compiler import compile_catalog  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--root", default="skills")
    parser.add_argument("--output", default="skills/catalog.v1.json")
    args = parser.parse_args()
    rendered = compile_catalog(args.root).model_dump_json(indent=2) + "\n"
    output = Path(args.output)
    if args.check:
        if not output.is_file() or output.read_text() != rendered:
            print("skill catalog artifact is stale", file=sys.stderr)
            return 1
        return 0
    output.write_text(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
