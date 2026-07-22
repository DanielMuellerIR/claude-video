#!/usr/bin/env python3
"""Sicher erzeugte und wieder loeschbare Arbeitsverzeichnisse fuer /watch."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path


MARKER_NAME = ".watch-workdir.json"
MARKER_SCHEMA = 1


def create_work_dir(base_dir: str | Path | None = None) -> Path:
    """Erzeuge immer ein exklusives Kindverzeichnis, auch bei --out-dir."""
    if base_dir is None:
        work = Path(tempfile.mkdtemp(prefix="watch-"))
    else:
        base = Path(base_dir).expanduser().resolve()
        base.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="watch-", dir=base))

    marker = {
        "schema": MARKER_SCHEMA,
        "owner": "watch",
        "work_dir": str(work.resolve()),
    }
    (work / MARKER_NAME).write_text(
        json.dumps(marker, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return work


def is_owned_work_dir(path: str | Path) -> bool:
    """Pruefe Marker, Namenskonvention und den darin gebundenen exakten Pfad."""
    work = Path(path).expanduser().resolve()
    if not work.is_dir() or not work.name.startswith("watch-"):
        return False

    marker_path = work / MARKER_NAME
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    return (
        marker.get("schema") == MARKER_SCHEMA
        and marker.get("owner") == "watch"
        and marker.get("work_dir") == str(work)
    )


def cleanup_work_dir(path: str | Path) -> None:
    """Loesche nur ein von create_work_dir erzeugtes, verifiziertes Verzeichnis."""
    work = Path(path).expanduser().resolve()
    if not is_owned_work_dir(work):
        raise ValueError(f"refusing to delete unowned work directory: {work}")
    shutil.rmtree(work)
