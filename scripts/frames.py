#!/usr/bin/env python3
"""Probe video metadata and extract frames.

Extraction strategy:
  1. SCENE-CHANGE detection via ffmpeg `select='gt(scene,thr)'` — grabs one
     frame per slide/screen transition.  Timestamps are parsed from showinfo
     stderr (pts_time field).  Up to MAX_SCENE_FRAMES kept; if more arrive we
     keep the most evenly spread set.
  2. FALLBACK: if scene detection yields fewer than MIN_SCENE_FRAMES (<5) the
     old uniform-fps approach is used so the result is never empty on static /
     talking-head videos.
  3. CLASSIFICATION (optional, --no-classify to skip): each frame is sent to a
     local vision-LLM helper, configured through the LLM_RUN and LLM_HOST
     environment variables.  Frames classified VERWERFEN are deleted from disk.
     Any connectivity error is caught and classification is silently skipped
     (all frames kept).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


MAX_FPS = 2.0
MAX_SCENE_FRAMES = 60   # Obergrenze für Szenen-Frames vor dem Ausdünnen
MIN_SCENE_FRAMES = 5    # Untergrenze; darunter → Fallback auf gleichmäßiges Sampling

# Pfad zum llm_run-Helper und Ziel-Host — über Umgebungsvariablen konfigurieren.
# Wenn eine der beiden Variablen leer ist, wird die Klassifikation übersprungen.
_LLM_RUN = os.environ.get("LLM_RUN", "")
_LLM_HOST = os.environ.get("LLM_HOST", "")


def _clamp_fps(fps: float, duration_seconds: float, max_frames: int) -> tuple[float, int]:
    fps = min(fps, MAX_FPS)
    target = min(max_frames, max(1, int(round(fps * duration_seconds))))
    return fps, target


def parse_time(value: str | float | int | None) -> float | None:
    """Parse SS, MM:SS, or HH:MM:SS (with optional .ms) into seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    parts = s.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        pass
    raise SystemExit(f"Cannot parse time value: {value!r} (expected SS, MM:SS, or HH:MM:SS)")


def format_time(seconds: float) -> str:
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, sec = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def get_metadata(video_path: str) -> dict:
    if shutil.which("ffprobe") is None:
        raise SystemExit("ffprobe is not installed. Install with: brew install ffmpeg")

    result = subprocess.run(
        [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(Path(video_path).resolve()),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ffprobe failed: {result.stderr.strip()}")

    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = float(fmt.get("duration") or video_stream.get("duration") or 0)
    return {
        "duration_seconds": duration,
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "codec": video_stream.get("codec_name"),
        "size_bytes": int(fmt.get("size") or 0),
        "has_audio": audio_stream is not None,
    }


def auto_fps(duration_seconds: float, max_frames: int = 100) -> tuple[float, int]:
    """Pick fps that targets a sensible frame budget for full-video scans."""
    if duration_seconds <= 0:
        return 1.0, 1

    if duration_seconds <= 30:
        target = min(max_frames, max(12, int(round(duration_seconds))))
    elif duration_seconds <= 60:
        target = min(max_frames, 40)
    elif duration_seconds <= 180:  # 3 min
        target = min(max_frames, 60)
    elif duration_seconds <= 600:  # 10 min
        target = min(max_frames, 80)
    else:
        target = max_frames

    return _clamp_fps(target / duration_seconds, duration_seconds, max_frames)


def auto_fps_focus(duration_seconds: float, max_frames: int = 100) -> tuple[float, int]:
    """Denser budget for user-specified ranges — they are zooming in for detail."""
    if duration_seconds <= 0:
        return min(MAX_FPS, 2.0), 2

    if duration_seconds <= 5:
        target = min(max_frames, max(10, int(round(duration_seconds * 6))))
    elif duration_seconds <= 15:
        target = min(max_frames, max(30, int(round(duration_seconds * 4))))
    elif duration_seconds <= 30:
        target = min(max_frames, 60)
    elif duration_seconds <= 60:
        target = min(max_frames, 80)
    elif duration_seconds <= 180:
        target = max_frames
    else:
        target = max_frames

    return _clamp_fps(target / duration_seconds, duration_seconds, max_frames)


# ── Szenen-Erkennung ──────────────────────────────────────────────────────────

def _parse_pts_times(showinfo_stderr: str) -> list[float]:
    """Extrahiert pts_time-Werte aus der showinfo-Ausgabe von ffmpeg.

    showinfo schreibt Zeilen wie:
      [Parsed_showinfo_1 @ …] n:   0 pts:   512 pts_time:0.512 …
    Wir lesen alle pts_time-Werte heraus.
    """
    times: list[float] = []
    for m in re.finditer(r"pts_time:(\d+(?:\.\d+)?)", showinfo_stderr):
        times.append(float(m.group(1)))
    return times


def _pick_spread(timestamps: list[float], n: int) -> list[float]:
    """Wählt n möglichst gleichmäßig verteilte Zeitstempel aus der Liste aus."""
    if len(timestamps) <= n:
        return timestamps
    # Einfaches gleichmäßiges Ausdünnen: jeden k-ten Eintrag behalten
    step = len(timestamps) / n
    indices = {int(i * step) for i in range(n)}
    return [timestamps[i] for i in sorted(indices)]


def extract_scene(
    video_path: str,
    out_dir: Path,
    resolution: int = 1600,
    scene_threshold: float = 0.3,
    max_frames: int = MAX_SCENE_FRAMES,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
) -> list[dict] | None:
    """Extrahiert Frames an Szenenübergängen.

    Gibt None zurück, wenn zu wenige Szenen erkannt wurden (→ Fallback).
    Gibt eine Liste von Frame-Dicts zurück bei Erfolg.
    """
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed. Install with: brew install ffmpeg")

    out_dir.mkdir(parents=True, exist_ok=True)
    for existing in out_dir.glob("frame_*.jpg"):
        existing.unlink()

    # Erster Durchlauf: nur Zeitstempel sammeln (kein Bild-Output, sehr schnell)
    probe_cmd: list[str] = ["ffmpeg", "-hide_banner", "-y"]
    if start_seconds is not None:
        probe_cmd += ["-ss", f"{start_seconds:.3f}"]
    if end_seconds is not None:
        probe_cmd += ["-to", f"{end_seconds:.3f}"]
    probe_cmd += [
        "-i", str(Path(video_path).resolve()),
        "-vf", f"select='gt(scene,{scene_threshold})',showinfo",
        "-fps_mode", "passthrough",   # neueres Äquivalent zu -vsync vfr
        "-f", "null",
        "-",
    ]

    probe = subprocess.run(probe_cmd, capture_output=True, text=True)
    # ffmpeg schreibt showinfo nach stderr; exit-Code ist 0 auch bei 0 Szenen
    timestamps = _parse_pts_times(probe.stderr)

    # Offset durch -ss korrigieren: pts_time ist relativ zum Clip-Start nach -ss
    # codereview-ok: -ss vor -i ohne -copyts liefert relatives pts_time, +offset ist die korrekte Absolut-Umrechnung (empirisch mit ffmpeg 8.1 verifiziert) (2026-07-01)
    offset = start_seconds or 0.0
    timestamps = [t + offset for t in timestamps]

    if len(timestamps) < MIN_SCENE_FRAMES:
        # Zu wenige Szenen → Fallback signalisieren
        return None

    # Ausdünnen auf max_frames
    # codereview-ok: max_frames ist der bewusste User-Cap (watch.py: default 60, hard max 100); MAX_SCENE_FRAMES ist nur der Default-Parameter, kein harter Deckel (2026-07-16)
    kept_times = _pick_spread(timestamps, max_frames)

    # Zweiter Durchlauf: Frames an den ausgewählten Zeitstempeln extrahieren
    frames: list[dict] = []
    for idx, ts in enumerate(sorted(kept_times)):
        out_path = out_dir / f"frame_{idx:04d}.jpg"
        frame_cmd: list[str] = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{ts:.3f}",
            "-i", str(Path(video_path).resolve()),
            # 'min(resolution,iw)' verhindert Hochskalieren über die Quellbreite
            "-vf", f"scale='min({resolution},iw)':-2",
            "-frames:v", "1",
            "-q:v", "4",
            str(out_path),
        ]
        result = subprocess.run(frame_cmd, capture_output=True, text=True)
        if result.returncode == 0 and out_path.exists():
            frames.append({
                "index": idx,
                "timestamp_seconds": round(ts, 2),
                "path": str(out_path),
                "scene_detected": True,
            })
        else:
            print(
                f"[frames] Warnung: Frame bei t={ts:.2f}s konnte nicht extrahiert werden",
                file=sys.stderr,
            )

    return frames if frames else None


# ── Gleichmäßiges Sampling (Fallback) ────────────────────────────────────────

def extract(
    video_path: str,
    out_dir: Path,
    fps: float,
    resolution: int = 1600,
    max_frames: int = 100,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
) -> list[dict]:
    """Extrahiert Frames gleichmäßig verteilt (klassisches fps-Sampling)."""
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed. Install with: brew install ffmpeg")

    out_dir.mkdir(parents=True, exist_ok=True)
    for existing in out_dir.glob("frame_*.jpg"):
        existing.unlink()

    output_pattern = str(out_dir / "frame_%04d.jpg")
    cmd: list[str] = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
    ]

    # -ss vor -i = schneller Seek (Keyframe-Snap, reicht für Vorschau-Frames)
    if start_seconds is not None:
        cmd += ["-ss", f"{start_seconds:.3f}"]
    if end_seconds is not None:
        cmd += ["-to", f"{end_seconds:.3f}"]

    cmd += [
        "-i", str(Path(video_path).resolve()),
        # 'min(resolution,iw)' verhindert Hochskalieren über die Quellbreite
        "-vf", f"fps={fps},scale='min({resolution},iw)':-2",
        "-frames:v", str(max_frames),
        "-q:v", "4",
        output_pattern,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg frame extraction failed: {result.stderr.strip()}")

    offset = start_seconds or 0.0
    frames = sorted(out_dir.glob("frame_*.jpg"))
    return [
        {
            "index": i,
            "timestamp_seconds": round(offset + (i / fps if fps > 0 else 0.0), 2),
            "path": str(p),
            "scene_detected": False,
        }
        for i, p in enumerate(frames)
    ]


# ── Vision-Klassifikation ─────────────────────────────────────────────────────

def classify_frames(frames: list[dict]) -> tuple[list[dict], int, int]:
    """Klassifiziert Frames mit einem lokalen Vision-LLM.

    Frames, die als reine Sprecherkopf-/Logo-/Deko-Aufnahme klassifiziert
    werden (VERWERFEN), werden von der Festplatte gelöscht.

    Gibt (kept_frames, n_kept, n_deleted) zurück.
    Falls llm_run.py nicht erreichbar ist oder ein Fehler auftritt, werden
    alle Frames behalten und eine Warnung ausgegeben.
    """
    # Beide Env-Vars müssen gesetzt sein, sonst ist keine Verbindung möglich.
    if not _LLM_RUN or not _LLM_HOST:
        print(
            "[frames] Klassifikation übersprungen: LLM_RUN oder LLM_HOST nicht gesetzt",
            file=sys.stderr,
        )
        return frames, len(frames), 0

    kept: list[dict] = []
    deleted = 0
    prompt = (
        "Zeigt dieses Bild primär Bildschirminhalt/Slide/Code/Text/Diagramm "
        "(nützlich) oder nur eine sprechende Person/Gesicht/Logo/Deko "
        "(unbrauchbar)? Antworte mit EINEM Wort: NÜTZLICH oder VERWERFEN."
    )

    # enumerate liefert die Position gleich mit — der Exception-Handler unten
    # braucht sie, um die restlichen Frames ohne fragile index()-Suche zu behalten.
    for frame_pos, frame in enumerate(frames):
        frame_path = frame["path"]
        if not Path(frame_path).exists():
            kept.append(frame)
            continue
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    _LLM_RUN,
                    _LLM_HOST,
                    "--model", "gemma4:12b",
                    "--no-think",
                    "--image", frame_path,
                    prompt,
                ],
                capture_output=True,
                text=True,
                timeout=60,  # 60 s pro Frame sollte großzügig sein
            )
            answer = result.stdout.strip().upper()
            # Strenger Match: nur löschen, wenn die Antwort exakt "VERWERFEN" ist
            # (oder als erstes Token steht). "nicht VERWERFEN" o. ä. würde sonst
            # fälschlicherweise zum Löschen führen.
            first_token = answer.split()[0] if answer.split() else ""
            if first_token == "VERWERFEN":
                os.remove(frame_path)
                deleted += 1
                print(
                    f"[frames] VERWERFEN  t={format_time(frame['timestamp_seconds'])} "
                    f"({frame_path})",
                    file=sys.stderr,
                )
            else:
                kept.append(frame)
                print(
                    f"[frames] NÜTZLICH   t={format_time(frame['timestamp_seconds'])} "
                    f"({frame_path})",
                    file=sys.stderr,
                )
        except subprocess.TimeoutExpired:
            print(
                f"[frames] Klassifikation-Timeout für {frame_path} — Frame behalten",
                file=sys.stderr,
            )
            kept.append(frame)
        except Exception as exc:  # noqa: BLE001
            print(
                f"[frames] Klassifikations-Fehler ({exc}) — alle verbleibenden Frames behalten",
                file=sys.stderr,
            )
            # Restliche Frames unklassifiziert behalten
            kept.append(frame)
            # Verbleibende Frames direkt durchreichen
            kept.extend(frames[frame_pos + 1:])
            break

    return kept, len(kept), deleted


# ── Haupt-API ─────────────────────────────────────────────────────────────────

def extract_smart(
    video_path: str,
    out_dir: Path,
    fps: float,
    resolution: int = 1600,
    max_frames: int = 60,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    scene_threshold: float = 0.3,
    no_classify: bool = False,
) -> tuple[list[dict], dict]:
    """Hauptfunktion: Szenen-Erkennung mit Fallback, dann optionale Klassifikation.

    Gibt (kept_frames, stats) zurück.  stats enthält Diagnoseinformationen für
    den watch.py-Report.
    """
    stats: dict = {
        "method": "scene",
        "raw_count": 0,
        "kept_count": 0,
        "deleted_count": 0,
        "classified": not no_classify,
    }

    # Szenen-Erkennung versuchen
    scene_frames = extract_scene(
        video_path, out_dir,
        resolution=resolution,
        scene_threshold=scene_threshold,
        max_frames=max_frames,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
    )

    if scene_frames is None:
        # Fallback: gleichmäßiges Sampling
        print(
            "[frames] Szenen-Erkennung ergab zu wenige Treffer — "
            "Fallback auf gleichmäßiges Sampling",
            file=sys.stderr,
        )
        stats["method"] = "uniform_fallback"
        frames = extract(
            video_path, out_dir,
            fps=fps,
            resolution=resolution,
            max_frames=max_frames,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
        )
    else:
        frames = scene_frames

    stats["raw_count"] = len(frames)

    if no_classify or not frames:
        stats["kept_count"] = len(frames)
        stats["deleted_count"] = 0
        return frames, stats

    # Vision-Klassifikation
    kept, n_kept, n_deleted = classify_frames(frames)
    stats["kept_count"] = n_kept
    stats["deleted_count"] = n_deleted
    return kept, stats


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            "usage: frames.py <video-path> <out-dir> [--fps F] [--resolution W] "
            "[--max-frames N] [--start T] [--end T] [--scene-threshold F] [--no-classify]",
            file=sys.stderr,
        )
        raise SystemExit(2)

    video = sys.argv[1]
    out = Path(sys.argv[2])
    args = sys.argv[3:]

    fps_override = None
    resolution = 1600
    max_frames = 60
    start_arg = None
    end_arg = None
    scene_threshold = 0.3
    no_classify = False
    i = 0
    while i < len(args):
        if args[i] == "--fps":
            fps_override = float(args[i + 1]); i += 2
        elif args[i] == "--resolution":
            resolution = int(args[i + 1]); i += 2
        elif args[i] == "--max-frames":
            max_frames = int(args[i + 1]); i += 2
        elif args[i] == "--start":
            start_arg = args[i + 1]; i += 2
        elif args[i] == "--end":
            end_arg = args[i + 1]; i += 2
        elif args[i] == "--scene-threshold":
            scene_threshold = float(args[i + 1]); i += 2
        elif args[i] == "--no-classify":
            no_classify = True; i += 1
        else:
            i += 1

    meta = get_metadata(video)
    start_sec = parse_time(start_arg)
    end_sec = parse_time(end_arg)
    full_duration = meta["duration_seconds"]

    effective_start = start_sec if start_sec is not None else 0.0
    effective_end = end_sec if end_sec is not None else full_duration
    effective_duration = max(0.0, effective_end - effective_start)

    focused = start_sec is not None or end_sec is not None
    if focused:
        fps, target = auto_fps_focus(effective_duration, max_frames=max_frames)
    else:
        fps, target = auto_fps(effective_duration, max_frames=max_frames)
    if fps_override is not None:
        fps = fps_override
        target = max(1, int(round(fps * effective_duration)))

    frames, stats = extract_smart(
        video, out,
        fps=fps,
        resolution=resolution,
        max_frames=max_frames,
        start_seconds=start_sec,
        end_seconds=end_sec,
        scene_threshold=scene_threshold,
        no_classify=no_classify,
    )
    print(json.dumps(
        {
            "meta": meta,
            "fps": fps,
            "target": target,
            "focused": focused,
            "frames": frames,
            "extraction_stats": stats,
        },
        indent=2,
    ))
