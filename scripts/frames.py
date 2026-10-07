#!/usr/bin/env python3
"""Probe video metadata and extract frames.

Extraction strategy:
  1. SCENE-CHANGE detection via ffmpeg `select='gt(scene,thr)'` — grabs one
     frame per slide/screen transition.  Timestamps are parsed from showinfo
     stderr (pts_time field).  The caller supplies the duration-derived frame
     budget; if more scenes arrive, the code keeps a time-balanced subset.
  2. FALLBACK: if scene detection yields fewer than MIN_SCENE_FRAMES (<5) the
     old uniform-fps approach is used so the result is never empty on static /
     talking-head videos.
  3. CLASSIFICATION (optional, --no-classify to skip): each frame is sent to a
     local vision-LLM helper, configured through the LLM_RUN and LLM_HOST
     environment variables.  Frames classified VERWERFEN are deleted from disk.
     Any connectivity error is caught and classification stops with an explicit error status
     (remaining frames kept).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workdir import work_dir


MAX_FPS = 2.0
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
        if math.isfinite(value):
            return float(value)
        raise SystemExit("Time values must be finite")
    s = str(value).strip()
    if not s:
        return None
    parts = s.split(":")
    try:
        if len(parts) == 1:
            parsed = float(parts[0])
        if len(parts) == 2:
            parsed = int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            parsed = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if 1 <= len(parts) <= 3 and math.isfinite(parsed):
            return parsed
    except (ValueError, OverflowError):
        pass
    raise SystemExit(f"Cannot parse time value: {value!r} (expected SS, MM:SS, or HH:MM:SS)")


def validate_range(
    start_seconds: float | None,
    end_seconds: float | None,
    duration_seconds: float,
) -> tuple[float | None, float | None]:
    """Pruefe beide Einstiege gleich und begrenze bekannte Videobereiche."""
    if start_seconds is not None and start_seconds < 0:
        raise SystemExit("--start must be non-negative")
    if end_seconds is not None and end_seconds <= 0:
        raise SystemExit("--end must be positive")
    if end_seconds is not None and start_seconds is not None and end_seconds <= start_seconds:
        raise SystemExit("--end must be greater than --start")
    if duration_seconds > 0 and start_seconds is not None and start_seconds >= duration_seconds:
        raise SystemExit(f"--start {start_seconds:.1f}s is past end of video ({duration_seconds:.1f}s)")
    if end_seconds is not None and duration_seconds > 0 and end_seconds > duration_seconds:
        print(
            f"[watch] --end {end_seconds:.1f}s is past end of video — clamping to {duration_seconds:.1f}s",
            file=sys.stderr,
        )
        end_seconds = duration_seconds
    return start_seconds, end_seconds


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
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(Path(video_path).resolve()),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _fail_media_tool("ffprobe", result.stderr)

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


def _fail_media_tool(tool: str, diagnostic: str) -> None:
    print(f"[frames] untrusted {tool} diagnostic: "
          f"{json.dumps(diagnostic.strip()[:2000], ensure_ascii=False)}", file=sys.stderr)
    raise SystemExit(f"{tool} failed; see the labelled diagnostic.")


def _prepare_output(out_dir: Path) -> None:
    """Vorhandene Bilder koennen Quellen oder fremde Ergebnisse sein."""
    if any(out_dir.glob("frame_*.jpg")):
        raise SystemExit("frame output directory contains existing images; choose an empty directory")
    out_dir.mkdir(parents=True, exist_ok=True)


def auto_fps(duration_seconds: float, max_frames: int = 100) -> tuple[float, int]:
    """Pick fps that targets a sensible frame budget for full-video scans."""
    if duration_seconds <= 0:
        return 1.0, max_frames

    if duration_seconds <= 30:
        target = min(max_frames, 30)
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
        return MAX_FPS, max_frames

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


def sampling_plan(
    duration_seconds: float,
    focused: bool,
    max_frames: int,
    fps_override: float | None,
) -> tuple[float, int]:
    """Plane Szenenbudget und fps des gleichmaessigen Fallbacks gemeinsam."""
    max_frames = max(1, min(max_frames, 100))
    planner = auto_fps_focus if focused else auto_fps
    fps, target_frames = planner(duration_seconds, max_frames=max_frames)
    if fps_override is not None:
        if not math.isfinite(fps_override) or fps_override <= 0:
            raise ValueError("--fps must be finite and greater than zero")
        # Szenen werden nach ihrem eigenen Dauerbudget ausgewaehlt. --fps steuert
        # nur den gleichmaessigen Fallback, wie es die CLI-Hilfe verspricht.
        fps = min(fps_override, MAX_FPS)
    return fps, target_frames


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
    """Waehle n zeitlich statt bloss nach Listenindex verteilte Zeitstempel."""
    if n <= 0:
        raise ValueError("frame count must be positive")
    ordered = sorted(dict.fromkeys(timestamps))
    if len(ordered) <= n:
        return ordered
    if n == 1:
        return [ordered[0]]

    selected = {0}  # Der Bereichsanfang bleibt immer erhalten.
    span = ordered[-1] - ordered[0]
    for position in range(1, n):
        target = ordered[0] + span * position / (n - 1)
        candidates = (index for index in range(len(ordered)) if index not in selected)
        selected.add(min(candidates, key=lambda index: abs(ordered[index] - target)))
    return [ordered[index] for index in sorted(selected)]


def extract_scene(
    video_path: str,
    out_dir: Path,
    max_frames: int,
    resolution: int = 1600,
    scene_threshold: float = 0.3,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
) -> list[dict] | None:
    """Extrahiert Frames an Szenenübergängen.

    Gibt None zurück, wenn zu wenige Szenen erkannt wurden (→ Fallback).
    Gibt eine Liste von Frame-Dicts zurück bei Erfolg.
    """
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed. Install with: brew install ffmpeg")

    _prepare_output(out_dir)

    # Erster Durchlauf: nur Zeitstempel sammeln (kein Bild-Output, sehr schnell)
    probe_cmd: list[str] = ["ffmpeg", "-hide_banner", "-y"]
    if end_seconds is not None:
        probe_cmd += ["-to", str(end_seconds)]
    probe_cmd += [
        "-i", str(Path(video_path).resolve()),
        "-vf", f"setpts=PTS-STARTPTS,select='gt(scene,{scene_threshold})',showinfo",
        "-fps_mode", "passthrough",   # neueres Äquivalent zu -vsync vfr
        "-f", "null",
        "-",
    ]

    probe = subprocess.run(probe_cmd, capture_output=True, text=True)
    # ffmpeg schreibt showinfo nach stderr; exit-Code ist 0 auch bei 0 Szenen
    timestamps = _parse_pts_times(probe.stderr)

    # Ohne Input-Seek bleiben die Quellzeiten auch bei niedriger Bildrate exakt.
    range_start = start_seconds or 0.0
    timestamps = [t for t in timestamps if t >= range_start
                  and (end_seconds is None or t < end_seconds)]

    if len(timestamps) < MIN_SCENE_FRAMES:
        # Zu wenige Szenen → Fallback signalisieren
        return None

    # select=gt(scene,...) meldet nur Uebergaenge. Der Bereichsanfang ist aber
    # oft Titelkarte oder Hook und muss unabhaengig von spaeteren Schnitten mit.
    if abs(timestamps[0] - range_start) > 0.5:
        timestamps.insert(0, range_start)

    # Ausdünnen auf max_frames
    # max_frames ist das aus Dauerbudget und User-Cap berechnete Limit.
    kept_times = _pick_spread(timestamps, max_frames)

    # Zweiter Durchlauf: Frames an den ausgewählten Zeitstempeln extrahieren
    frames: list[dict] = []
    for idx, ts in enumerate(sorted(kept_times)):
        out_path = out_dir / f"frame_{idx:04d}.jpg"
        frame_cmd: list[str] = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-n"]
        filters = ""
        if ts == range_start:
            # Der Bereich kann mitten in der Anzeigedauer eines Quellbilds beginnen.
            filters = f"setpts=PTS-STARTPTS-{ts}/TB,fps=1:round=up:start_time=0,"
        else:
            frame_cmd += ["-ss", str(ts)]
        frame_cmd += [
            "-i", str(Path(video_path).resolve()),
            # 'min(resolution,iw)' verhindert Hochskalieren über die Quellbreite
            "-vf", filters + f"scale='min({resolution},iw)':-2",
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
            out_path.unlink(missing_ok=True)
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

    _prepare_output(out_dir)

    output_pattern = str(out_dir / "frame_%04d.jpg")
    cmd: list[str] = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-n",
    ]

    # Vorherige Quellbilder behalten: Input-Seek verwirft bei niedriger
    # Bildrate das am Bereichsanfang noch angezeigte Bild.
    offset = start_seconds or 0.0
    filters = f"setpts=PTS-STARTPTS-{offset}/TB,fps={fps}:round=up:start_time=0"
    if end_seconds is not None:
        # Ausgabe--t und trim runden auf Sampling-Ticks. Stattdessen genau
        # die Samples vor dem exklusiven Ende zulassen, ohne Rundungsartefakte.
        duration = end_seconds - (start_seconds or 0.0)
        max_frames = min(max_frames, max(1, math.ceil(duration * fps - 1e-9)))
    # 'min(resolution,iw)' verhindert Hochskalieren ueber die Quellbreite.
    filters += f",scale='min({resolution},iw)':-2"
    cmd += [
        "-i", str(Path(video_path).resolve()),
        "-vf", filters,
        "-frames:v", str(max_frames),
        "-q:v", "4",
        output_pattern,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        _fail_media_tool("ffmpeg", result.stderr)

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

def classify_frames(frames: list[dict], *, stats: dict | None = None) -> tuple[list[dict], int, int, bool]:
    """Klassifiziert Frames mit einem lokalen Vision-LLM.

    Frames, die als reine Sprecherkopf-/Logo-/Deko-Aufnahme klassifiziert
    werden (VERWERFEN), werden von der Festplatte gelöscht.

    Gibt (kept_frames, n_kept, n_deleted, classifier_started) zurück.
    Falls llm_run.py nicht erreichbar ist oder ein Fehler auftritt, werden
    alle Frames behalten und eine Warnung ausgegeben.
    """
    stats = stats if stats is not None else {}
    stats["classification_status"] = "not_configured"
    completed = 0
    # Beide Env-Vars müssen gesetzt sein, sonst ist keine Verbindung möglich.
    if not _LLM_RUN or not _LLM_HOST:
        print(
            "[frames] Klassifikation übersprungen: LLM_RUN oder LLM_HOST nicht gesetzt",
            file=sys.stderr,
        )
        return frames, len(frames), 0, False

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
        try:
            if not Path(frame_path).is_file():
                raise FileNotFoundError("Frame fehlt")
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
            if result.returncode != 0:
                raise RuntimeError(f"Helfer-Exit {result.returncode}")
            answer = result.stdout.strip().upper()
            # Strenger Match: nur löschen, wenn die Antwort exakt "VERWERFEN" ist
            # (oder als erstes Token steht). "nicht VERWERFEN" o. ä. würde sonst
            # fälschlicherweise zum Löschen führen.
            if answer not in ("NÜTZLICH", "VERWERFEN"):
                raise ValueError("Ungueltige Klassifikationsantwort")
            if answer == "VERWERFEN":
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
            completed += 1
        except Exception as exc:  # Fehler behalten den unklassifizierten Rest.
            error = "timeout" if isinstance(exc, subprocess.TimeoutExpired) else str(exc)
            stats["classification_status"] = "partial" if completed else "failed"
            stats["classification_error"] = error
            print(f"[frames] Klassifikation abgebrochen ({error}) — Rest behalten", file=sys.stderr)
            kept.extend(frames[frame_pos:])
            return kept, len(kept), deleted, False

    stats["classification_status"] = "complete"
    return kept, len(kept), deleted, True


# ── Haupt-API ─────────────────────────────────────────────────────────────────

def extract_smart(
    video_path: str,
    out_dir: Path,
    fps: float,
    max_frames: int,
    resolution: int = 1600,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    scene_threshold: float = 0.3,
    no_classify: bool = False,
    fallback_max_frames: int | None = None,
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
        "classified": False,
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
            max_frames=fallback_max_frames if fallback_max_frames is not None else max_frames,
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
    kept, n_kept, n_deleted, classified = classify_frames(frames, stats=stats)
    stats["kept_count"] = n_kept
    stats["deleted_count"] = n_deleted
    stats["classified"] = classified
    return kept, stats


# ── CLI ───────────────────────────────────────────────────────────────────────

def positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return parsed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract video preview frames")
    parser.add_argument("video")
    parser.add_argument("out", type=Path, help="Parent for an exclusive watch-* output directory")
    parser.add_argument("--fps", type=positive_float)
    parser.add_argument("--resolution", type=int, default=1600)
    parser.add_argument("--max-frames", type=int, default=100)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--scene-threshold", type=float, default=0.3)
    parser.add_argument("--no-classify", action="store_true")
    options = parser.parse_args()
    video, out = options.video, options.out
    fps_override, resolution, max_frames = options.fps, options.resolution, options.max_frames
    start_arg, end_arg = options.start, options.end
    scene_threshold, no_classify = options.scene_threshold, options.no_classify

    meta = get_metadata(video)
    start_sec = parse_time(start_arg)
    end_sec = parse_time(end_arg)
    full_duration = meta["duration_seconds"]
    start_sec, end_sec = validate_range(start_sec, end_sec, full_duration)

    effective_start = start_sec if start_sec is not None else 0.0
    effective_end = end_sec if end_sec is not None else full_duration
    effective_duration = max(0.0, effective_end - effective_start)

    focused = start_sec is not None or end_sec is not None
    max_frames = max(1, min(max_frames, 100))
    try:
        fps, target = sampling_plan(
            effective_duration,
            focused,
            max_frames,
            fps_override,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    with work_dir(out) as work:
        frames, stats = extract_smart(
            video, work / "frames",
            fps=fps,
            resolution=resolution,
            max_frames=target,
            start_seconds=start_sec,
            end_seconds=end_sec,
            scene_threshold=scene_threshold,
            no_classify=no_classify,
            fallback_max_frames=max_frames,
        )
    print(json.dumps(
        {
            "work_dir": str(work),
            "meta": meta,
            "fps": fps,
            "target": target,
            "focused": focused,
            "frames": frames,
            "extraction_stats": stats,
        },
        indent=2,
    ))
