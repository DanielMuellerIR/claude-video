#!/usr/bin/env python3
"""Parse a WebVTT subtitle file into a clean, timestamped transcript.

YouTube auto-subs emit rolling-duplicate cues (each line appears 2-3 times as it
scrolls). We dedupe consecutive identical cues and merge their time ranges.
"""
from __future__ import annotations

import html
import json
import math
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from frames import format_time  # noqa: E402

_TS_TOKEN = r"(?:(\d+):)?(\d{2,}):(\d{2,})[.,](\d{3})"
TS_RE = re.compile(rf"^\s*{_TS_TOKEN}\s+-->\s+{_TS_TOKEN}(?:\s+.*)?$")
TAG_RE = re.compile(r"<[^>]+>")


def _to_seconds(h: str | None, m: str, s: str, ms: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_vtt(path: str) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()

    segments: list[dict] = []
    i = 0
    while i < len(lines):
        match = TS_RE.match(lines[i])
        if not match:
            if "-->" in lines[i]:
                print(
                    "[watch] untrusted malformed caption timestamp: "
                    f"{json.dumps(lines[i][:2000], ensure_ascii=False)}",
                    file=sys.stderr,
                )
            i += 1
            continue

        try:
            start = _to_seconds(*match.groups()[:4])
            end = _to_seconds(*match.groups()[4:])
            if not (math.isfinite(start) and math.isfinite(end) and 0 <= start <= end):
                raise ValueError("invalid caption interval")
        except (ValueError, OverflowError):
            print("[watch] untrusted invalid caption interval: "
                  + json.dumps(lines[i][:2000], ensure_ascii=False), file=sys.stderr)
            i += 1
            continue
        i += 1

        cue_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            cleaned = html.unescape(TAG_RE.sub("", lines[i])).strip()
            if cleaned:
                cue_lines.append(cleaned)
            i += 1

        cue_text = " ".join(cue_lines).strip()
        if cue_text:
            segments.append({"start": round(start, 2), "end": round(end, 2), "text": cue_text})
        i += 1

    return _dedupe(segments)


def _dedupe(segments: list[dict]) -> list[dict]:
    """Collapse rolling duplicates common in YouTube auto-subs."""
    out: list[dict] = []
    for seg in segments:
        overlaps = bool(out) and max(seg["start"], out[-1]["start"]) < min(seg["end"], out[-1]["end"])
        if overlaps and seg["text"] == out[-1]["text"]:
            out[-1]["end"] = max(out[-1]["end"], seg["end"])
            continue
        if overlaps and seg["text"].startswith(out[-1]["text"] + " "):
            out[-1]["text"] = seg["text"]
            out[-1]["end"] = max(out[-1]["end"], seg["end"])
            continue
        out.append(dict(seg))
    return out


def filter_range(
    segments: list[dict],
    start_seconds: float | None,
    end_seconds: float | None,
) -> list[dict]:
    """Return segments with positive overlap with [start, end)."""
    if start_seconds is None and end_seconds is None:
        return segments
    lo = start_seconds if start_seconds is not None else float("-inf")
    hi = end_seconds if end_seconds is not None else float("inf")
    return [seg for seg in segments if seg["end"] > lo and seg["start"] < hi]


def format_transcript(segments: list[dict]) -> str:
    lines = []
    for seg in segments:
        stamp = f"[{format_time(seg['start'])}]"
        lines.append(f"{stamp} {seg['text']}")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: transcribe.py <vtt-path>", file=sys.stderr)
        raise SystemExit(2)
    print(format_transcript(parse_vtt(sys.argv[1])))
