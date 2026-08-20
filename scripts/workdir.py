"""Sicher erzeugte und wieder loeschbare Arbeitsverzeichnisse fuer /watch."""
from __future__ import annotations

from contextlib import contextmanager
import json
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path


MARKER_NAME = ".watch-workdir.json"
MARKER_SCHEMA = 1


def create_work_dir(base_dir: str | Path | None = None) -> Path:
    """Erzeuge immer ein exklusives Kindverzeichnis, auch bei --out-dir."""
    try:
        if base_dir is None or not str(base_dir).strip():
            work = Path(tempfile.mkdtemp(prefix="watch-"))
        else:
            base = Path(base_dir).expanduser().resolve()
            base.mkdir(parents=True, exist_ok=True)
            if not base.is_dir():
                raise NotADirectoryError(f"not a directory: {base}")
            work = Path(tempfile.mkdtemp(prefix="watch-", dir=base))
    except OSError as exc:
        raise SystemExit(f"cannot create watch working directory: {exc}") from exc

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


@contextmanager
def work_dir(base_dir: str | Path | None = None) -> Iterator[Path]:
    """Behalte erfolgreiche Laeufe, raeume abgebrochene Laeufe sicher auf."""
    work = create_work_dir(base_dir)
    try:
        yield work
    except BaseException:
        cleanup_work_dir(work)
        raise


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
