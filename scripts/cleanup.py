#!/usr/bin/env python3
"""Validierter Cleanup-Helfer fuer von /watch erzeugte Arbeitsverzeichnisse."""
from __future__ import annotations

import sys

from workdir import cleanup_work_dir


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: cleanup.py <watch-work-dir>", file=sys.stderr)
        return 2
    try:
        cleanup_work_dir(sys.argv[1])
    except ValueError as exc:
        print(f"[watch] {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
