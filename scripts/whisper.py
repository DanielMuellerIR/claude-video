#!/usr/bin/env python3
"""Transcribe a video via Groq or OpenAI Whisper API, or local whisper.cpp.

Strategy: extract audio, then either:
  - local backend: run whisper-cli locally via whisper.cpp (no API key needed,
    no length limit — handles arbitrarily long videos offline)
  - groq / openai backend: upload mono 16kHz mp3 to the Whisper cloud API.
    The cloud APIs accept up to ~25 MB per request (~50 min at 64 kbps).
    For longer audio the file is automatically split into time-based chunks,
    each chunk is transcribed independently, and the returned segments are
    merged with correct cumulative time offsets.

Returns segments in the same shape as transcribe.parse_vtt so the rest of the
pipeline (filter_range, format_transcript) doesn't care where the transcript
came from.

Pure stdlib — no `pip install groq` or `pip install openai` needed.

Local backend configuration (env vars):
  WATCH_WHISPER_BACKEND=local   force local backend (no API key required)
  WATCH_WHISPER_MODEL=<name>    whisper.cpp model name, default: large-v3-turbo
  WATCH_WHISPER_MODELS_DIR=<p>  models cache dir, default: ~/.cache/yt-transcribe/models
"""
from __future__ import annotations

import io
import json
import mimetypes
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.request import Request, urlopen


GROQ_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"

OPENAI_ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_MODEL = "whisper-1"

# Local whisper.cpp backend — model downloaded from Hugging Face on first use.
# Mirrors the defaults used by the yt-transcribe skill.
_LOCAL_HF_BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
_LOCAL_DEFAULT_MODEL = "large-v3-turbo"
_LOCAL_DEFAULT_MODELS_DIR = Path.home() / ".cache" / "yt-transcribe" / "models"


def load_api_key(preferred: str | None = None) -> tuple[str, str] | tuple[None, None]:
    """Return (backend, api_key). Prefers Groq, falls back to OpenAI.

    If `preferred` is "groq" or "openai", only that backend's key is considered.
    If `preferred` is "local", or the env var WATCH_WHISPER_BACKEND=local is set,
    returns ("local", "local") immediately — no API key needed.
    """
    # Local backend: no API key needed, just whisper-cli on PATH.
    env_backend = os.environ.get("WATCH_WHISPER_BACKEND", "").strip().lower()
    if preferred == "local" or env_backend == "local":
        return "local", "local"

    def _from_env(name: str) -> str | None:
        value = os.environ.get(name)
        return value.strip() if value else None

    def _from_dotenv(path: Path, name: str) -> str | None:
        if not path.exists():
            return None
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() != name:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                    value = value[1:-1]
                return value or None
        except OSError:
            return None
        return None

    dotenv_paths = [
        Path.home() / ".config" / "watch" / ".env",
        Path.cwd() / ".env",
    ]

    candidates = (("GROQ_API_KEY", "groq"), ("OPENAI_API_KEY", "openai"))
    if preferred is not None:
        candidates = tuple(c for c in candidates if c[1] == preferred)

    for key_name, backend in candidates:
        value = _from_env(key_name)
        if not value:
            for candidate in dotenv_paths:
                value = _from_dotenv(candidate, key_name)
                if value:
                    break
        if value:
            return backend, value

    return None, None


def extract_audio(video_path: str, out_path: Path) -> Path:
    """Extract mono 16kHz 64kbps mp3 — ~480 kB/min, fits any Whisper limit."""
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed. Install with: brew install ffmpeg")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(Path(video_path).resolve()),
        "-vn",
        "-acodec", "libmp3lame",
        "-ar", "16000",
        "-ac", "1",
        "-b:a", "64k",
        str(out_path.resolve()),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg audio extraction failed: {result.stderr.strip()}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise SystemExit("ffmpeg produced no audio — video may have no audio track")
    return out_path


def extract_audio_wav(video_path: str, out_path: Path) -> Path:
    """Extract 16kHz mono PCM-S16LE WAV — the format whisper.cpp expects.

    Distinct from extract_audio() which produces mp3 for the cloud APIs.
    """
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed. Install with: brew install ffmpeg")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(Path(video_path).resolve()),
        "-vn",
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(out_path.resolve()),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg WAV extraction failed: {result.stderr.strip()}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise SystemExit("ffmpeg produced no WAV — video may have no audio track")
    return out_path


def ensure_model_local(model: str | None = None) -> Path:
    """Ensure the ggml model binary is present; download from Hugging Face if needed.

    Returns the local path to ggml-<model>.bin.
    """
    if not model:
        model = os.environ.get("WATCH_WHISPER_MODEL", _LOCAL_DEFAULT_MODEL).strip()
    models_dir_env = os.environ.get("WATCH_WHISPER_MODELS_DIR", "")
    models_dir = Path(models_dir_env).expanduser() if models_dir_env else _LOCAL_DEFAULT_MODELS_DIR
    models_dir.mkdir(parents=True, exist_ok=True)

    path = models_dir / f"ggml-{model}.bin"
    if path.exists() and path.stat().st_size > 0:
        return path

    url = f"{_LOCAL_HF_BASE}/ggml-{model}.bin"
    print(f"[watch] downloading whisper.cpp model '{model}' from Hugging Face (first run only)…",
          file=sys.stderr)

    def _progress(blocks: int, block_size: int, total: int) -> None:
        if total > 0:
            pct = min(100, blocks * block_size * 100 // total)
            print(f"\r  {pct:3d}%", end="", file=sys.stderr, flush=True)

    tmp = path.with_suffix(".part")
    try:
        urllib.request.urlretrieve(url, tmp, _progress)
        print("", file=sys.stderr)
        tmp.rename(path)
    except Exception as exc:
        if tmp.exists():
            tmp.unlink()
        raise SystemExit(f"[watch] model download failed ({url}): {exc}") from exc
    return path


def _find_whisper_cli() -> str | None:
    """Return the first whisper.cpp binary name found on PATH, or None.

    whisper.cpp has been shipped under several names across versions/packages:
      whisper-cli  (current brew formula: whisper-cpp ≥ 1.7)
      main         (older builds from source)
      whisper      (some Linux distro packages)
    """
    for name in ("whisper-cli", "main", "whisper"):
        if shutil.which(name):
            return name
    return None


def _segments_from_whisper_cpp_json(data: dict) -> list[dict]:
    """Convert whisper.cpp JSON output to the {start, end, text} segment format.

    whisper.cpp JSON shape:
      {"transcription": [{"offsets": {"from": <ms>, "to": <ms>}, "text": "…"}, …]}

    offsets are in MILLISECONDS — divide by 1000 to get seconds.
    """
    out: list[dict] = []
    for seg in data.get("transcription") or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        offsets = seg.get("offsets") or {}
        start_sec = round(float(offsets.get("from", 0)) / 1000.0, 2)
        end_sec = round(float(offsets.get("to", 0)) / 1000.0, 2)
        out.append({"start": start_sec, "end": end_sec, "text": text})
    return out


def transcribe_local(video_path: str, audio_out: Path) -> tuple[list[dict], str]:
    """Transcribe locally using whisper.cpp — no API key, no network after model download.

    Returns (segments, "local").
    """
    binary = _find_whisper_cli()
    if binary is None:
        raise SystemExit(
            "whisper-cli not found. Install whisper.cpp:\n"
            "  macOS:  brew install whisper-cpp\n"
            "  Linux:  build from source: https://github.com/ggerganov/whisper.cpp\n"
            "          make -j && sudo cp build/bin/whisper-cli /usr/local/bin/"
        )

    model_path = ensure_model_local()

    # Use a WAV path next to the requested audio_out (different extension).
    wav_path = audio_out.with_suffix(".wav")
    print(f"[watch] extracting audio for local whisper.cpp…", file=sys.stderr)
    extract_audio_wav(video_path, wav_path)
    size_kb = wav_path.stat().st_size / 1024
    print(f"[watch] audio: {size_kb:.0f} kB — running local whisper.cpp ({binary})…",
          file=sys.stderr)

    # JSON output file: whisper-cli appends .json to the -of base name.
    json_base = audio_out.with_suffix("")  # strip any extension; -of is a path base
    json_file = Path(str(json_base) + ".json")

    threads = str(max(4, (os.cpu_count() or 4)))
    cmd = [
        binary,
        "-m", str(model_path),
        "-f", str(wav_path),
        "-of", str(json_base),
        "-oj",          # write JSON output
        "-t", threads,
        "-l", "auto",   # auto-detect language
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"whisper.cpp failed (exit {result.returncode}):\n{result.stderr.strip()}")

    if not json_file.exists():
        raise SystemExit(f"whisper.cpp produced no JSON output (expected: {json_file})")

    try:
        data = json.loads(json_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SystemExit(f"whisper.cpp JSON unreadable: {exc}") from exc

    segments = _segments_from_whisper_cpp_json(data)
    if not segments:
        raise SystemExit("whisper.cpp returned no transcript segments")

    # Clean up temporary files.
    for tmp in (wav_path, json_file):
        try:
            tmp.unlink()
        except OSError:
            pass

    print(f"[watch] transcribed {len(segments)} segments via local whisper.cpp", file=sys.stderr)
    return segments, "local"


def _build_multipart(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
    """Assemble a multipart/form-data body the Whisper APIs accept.

    Whisper's multipart upload is small and predictable — doing it by hand
    keeps us on pure stdlib instead of pulling requests/groq/openai SDKs.
    """
    boundary = f"----WatchBoundary{uuid.uuid4().hex}"
    eol = b"\r\n"
    buf = io.BytesIO()

    for name, value in fields.items():
        buf.write(f"--{boundary}".encode()); buf.write(eol)
        buf.write(f'Content-Disposition: form-data; name="{name}"'.encode()); buf.write(eol)
        buf.write(eol)
        buf.write(str(value).encode()); buf.write(eol)

    mimetype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    buf.write(f"--{boundary}".encode()); buf.write(eol)
    buf.write(
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"'.encode()
    )
    buf.write(eol)
    buf.write(f"Content-Type: {mimetype}".encode()); buf.write(eol)
    buf.write(eol)
    buf.write(file_path.read_bytes())
    buf.write(eol)
    buf.write(f"--{boundary}--".encode()); buf.write(eol)

    return buf.getvalue(), boundary


MAX_ATTEMPTS = 4       # initial + 3 retries
MAX_429_RETRIES = 2
RETRY_BASE_DELAY = 2.0


def _post_whisper(endpoint: str, api_key: str, model: str, audio_path: Path) -> dict:
    fields = {
        "model": model,
        "response_format": "verbose_json",
        "temperature": "0",
    }
    body, boundary = _build_multipart(fields, audio_path)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        # Groq sits behind Cloudflare — the default `Python-urllib/3.x` UA
        # trips WAF rule 1010 (403) before auth even runs. Any non-default
        # UA clears it; we identify honestly.
        "User-Agent": "watch-skill/1.0 (+claude-code; python-urllib)",
    }

    context = ssl.create_default_context()
    rate_limit_hits = 0
    last_exc: Exception | None = None
    last_detail = ""

    for attempt in range(MAX_ATTEMPTS):
        request = Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=300, context=context) as response:
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = _read_error_body(exc)
            last_exc, last_detail = exc, detail

            # 4xx other than 429 are client errors — no retry will fix them.
            if 400 <= exc.code < 500 and exc.code != 429:
                raise SystemExit(f"Whisper request failed: {exc}{detail}")

            if exc.code == 429:
                rate_limit_hits += 1
                if rate_limit_hits >= MAX_429_RETRIES:
                    raise SystemExit(f"Whisper request failed: {exc}{detail}")
                delay = _retry_after(exc) or RETRY_BASE_DELAY * (2 ** attempt) + 1
            else:
                delay = RETRY_BASE_DELAY * (2 ** attempt)

            if attempt < MAX_ATTEMPTS - 1:
                print(
                    f"[watch] whisper HTTP {exc.code} — retrying in {delay:.1f}s "
                    f"(attempt {attempt + 2}/{MAX_ATTEMPTS})",
                    file=sys.stderr,
                )
                time.sleep(delay)
            continue
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, OSError) as exc:
            last_exc, last_detail = exc, ""
            if attempt < MAX_ATTEMPTS - 1:
                delay = RETRY_BASE_DELAY * (attempt + 1)
                print(
                    f"[watch] whisper network error ({type(exc).__name__}: {exc}) — "
                    f"retrying in {delay:.1f}s (attempt {attempt + 2}/{MAX_ATTEMPTS})",
                    file=sys.stderr,
                )
                time.sleep(delay)
            continue

        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Whisper returned non-JSON response: {exc}: {payload[:200]}")

    raise SystemExit(
        f"Whisper request failed after {MAX_ATTEMPTS} attempts: {last_exc}{last_detail}"
    )


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read()
    except Exception:
        return ""
    if not body:
        return ""
    try:
        return f" — {body.decode('utf-8', errors='replace')[:400]}"
    except Exception:
        return ""


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    header = exc.headers.get("Retry-After") if getattr(exc, "headers", None) else None
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def _segments_from_response(data: dict) -> list[dict]:
    """Convert Whisper verbose_json into our {start, end, text} segment format."""
    out: list[dict] = []
    for seg in data.get("segments") or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "start": round(float(seg.get("start") or 0.0), 2),
            "end": round(float(seg.get("end") or 0.0), 2),
            "text": text,
        })

    if not out:
        full = (data.get("text") or "").strip()
        if full:
            out.append({"start": 0.0, "end": 0.0, "text": full})

    return out


# Maximale Upload-Größe für Cloud-APIs in Bytes (~24 MB Sicherheitspuffer unter dem 25 MB Limit).
_CLOUD_MAX_BYTES = 24 * 1024 * 1024


def _get_audio_duration_seconds(audio_path: Path) -> float:
    """Ermittle die Dauer einer Audiodatei in Sekunden via ffprobe.

    Gibt 0.0 zurück wenn ffprobe nicht verfügbar oder der Aufruf scheitert.
    """
    if shutil.which("ffprobe") is None:
        return 0.0
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return 0.0
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def _split_audio_chunk(audio_path: Path, chunk_dir: Path, start_sec: float, duration_sec: float, index: int) -> Path:
    """Schneide einen Zeitabschnitt aus der Audiodatei heraus.

    Nutzt ffmpeg mit -ss/-t für präzise, schnelle Segmentierung ohne Re-Encoding
    (copy-Codec für mp3).  Gibt den Pfad zur erzeugten Chunk-Datei zurück.
    """
    chunk_path = chunk_dir / f"chunk_{index:04d}.mp3"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(audio_path),
        "-ss", str(start_sec),
        "-t", str(duration_sec),
        "-c:a", "copy",  # kein Re-Encoding — schnell und verlustfrei
        str(chunk_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg chunk split failed (chunk {index}): {result.stderr.strip()}")
    if not chunk_path.exists() or chunk_path.stat().st_size == 0:
        raise SystemExit(f"ffmpeg produced empty chunk {index}")
    return chunk_path


def _transcribe_cloud_chunked(
    audio_path: Path,
    endpoint: str,
    api_key: str,
    model: str,
) -> list[dict]:
    """Transkribiere eine lange Audiodatei in zeitbasierten Chunks.

    Ablauf:
    1. Bestimme die Gesamtdauer via ffprobe.
    2. Berechne die Chunk-Länge so, dass jeder Chunk unter _CLOUD_MAX_BYTES bleibt.
    3. Schneide die Chunks mit ffmpeg (copy-Codec, kein Qualitätsverlust).
    4. Transkribiere jeden Chunk separat über _post_whisper.
    5. Addiere auf die Zeitstempel jedes Chunks den kumulierten Zeitoffset aller
       vorherigen Chunks (chunk_start_sec), damit das Ergebnis-Merge korrekte
       absolute Zeitangaben enthält.
    6. Lösche den Temp-Ordner in jedem Fall (try/finally).

    Gibt die zusammengeführte Segment-Liste zurück.
    """
    import tempfile

    total_duration = _get_audio_duration_seconds(audio_path)
    if total_duration <= 0:
        # Fallback: Dauer unbekannt, direkt hochladen — schlägt ggf. mit 413 fehl
        print(
            "[watch] Warnung: Audiodauer konnte nicht ermittelt werden, lade direkt hoch…",
            file=sys.stderr,
        )
        response = _post_whisper(endpoint, api_key, model, audio_path)
        return _segments_from_response(response)

    # Bit-Rate der MP3-Datei (64 kbps), ergibt ~480 kB/min.
    # Wir berechnen die maximale Chunk-Länge in Sekunden aus der Dateigröße und Dauer.
    file_size = audio_path.stat().st_size
    if file_size > 0 and total_duration > 0:
        bytes_per_sec = file_size / total_duration
    else:
        bytes_per_sec = 64 * 1024 / 8  # 64 kbps als sicherer Fallback

    # Maximale Chunk-Dauer mit 10 % Sicherheitspuffer
    max_chunk_sec = (_CLOUD_MAX_BYTES / bytes_per_sec) * 0.90
    if max_chunk_sec < 10:
        max_chunk_sec = 10  # Mindest-Chunk-Länge: 10 Sekunden

    # Anzahl der Chunks
    num_chunks = int(total_duration / max_chunk_sec) + 1
    print(
        f"[watch] audio zu lang für direkt-Upload ({file_size / 1024:.0f} kB / "
        f"{total_duration / 60:.1f} min) — wird in {num_chunks} Chunks aufgeteilt…",
        file=sys.stderr,
    )

    tmp_dir = Path(tempfile.mkdtemp(prefix="watch_whisper_chunks_"))
    all_segments: list[dict] = []
    cumulative_offset = 0.0  # Summe der Dauern aller bisher transkribierten Chunks

    try:
        chunk_index = 0
        chunk_start = 0.0

        while chunk_start < total_duration:
            chunk_duration = min(max_chunk_sec, total_duration - chunk_start)
            chunk_path = _split_audio_chunk(audio_path, tmp_dir, chunk_start, chunk_duration, chunk_index)

            print(
                f"[watch]   chunk {chunk_index + 1}/{num_chunks}: "
                f"{chunk_start / 60:.1f}–{(chunk_start + chunk_duration) / 60:.1f} min …",
                file=sys.stderr,
            )

            response = _post_whisper(endpoint, api_key, model, chunk_path)
            chunk_segments = _segments_from_response(response)

            # Zeitoffset auf alle Segmente dieses Chunks addieren
            for seg in chunk_segments:
                all_segments.append({
                    "start": round(seg["start"] + cumulative_offset, 2),
                    "end": round(seg["end"] + cumulative_offset, 2),
                    "text": seg["text"],
                })

            cumulative_offset += chunk_duration
            chunk_start += chunk_duration
            chunk_index += 1

    finally:
        # Temp-Verzeichnis in jedem Fall löschen — auch bei Fehler
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass  # Cleanup-Fehler niemals den Haupt-Fehler überlagern lassen

    return all_segments


def transcribe_video(
    video_path: str,
    audio_out: Path,
    backend: str | None = None,
    api_key: str | None = None,
) -> tuple[list[dict], str]:
    """Run the full flow: extract audio → upload → parse segments.

    Returns (segments, backend_used). Raises SystemExit on any failure.
    """
    if backend is None or api_key is None:
        detected_backend, detected_key = load_api_key()
        backend = backend or detected_backend
        api_key = api_key or detected_key

    # Local whisper.cpp path — no API key required.
    if backend == "local":
        return transcribe_local(video_path, audio_out)

    if not backend or not api_key:
        setup_py = Path(__file__).resolve().parent / "setup.py"
        raise SystemExit(
            "No Whisper API key available. Set GROQ_API_KEY (preferred) or OPENAI_API_KEY "
            "in the environment or in ~/.config/watch/.env, or set WATCH_WHISPER_BACKEND=local "
            "to use a local whisper.cpp installation. "
            f"Run `python3 {setup_py}` to configure."
        )

    print(f"[watch] extracting audio for Whisper ({backend})…", file=sys.stderr)
    audio_path = extract_audio(video_path, audio_out)
    size_kb = audio_path.stat().st_size / 1024
    file_size = audio_path.stat().st_size

    if backend == "groq":
        endpoint, model = GROQ_ENDPOINT, GROQ_MODEL
    elif backend == "openai":
        endpoint, model = OPENAI_ENDPOINT, OPENAI_MODEL
    else:
        raise SystemExit(f"Unknown whisper backend: {backend}")

    if file_size > _CLOUD_MAX_BYTES:
        # Datei zu groß für direkten Upload — automatisch in Chunks aufteilen
        segments = _transcribe_cloud_chunked(audio_path, endpoint, api_key, model)
    else:
        print(f"[watch] audio: {size_kb:.0f} kB — uploading to {backend} Whisper…", file=sys.stderr)
        response = _post_whisper(endpoint, api_key, model, audio_path)
        segments = _segments_from_response(response)

    if not segments:
        raise SystemExit("Whisper returned no transcript segments")

    print(f"[watch] transcribed {len(segments)} segments via {backend}", file=sys.stderr)
    return segments, backend


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: whisper.py <video-path> [<audio-out.mp3>] [--backend groq|openai|local]", file=sys.stderr)
        raise SystemExit(2)

    video = sys.argv[1]
    audio_out = Path(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else Path("audio.mp3")
    backend_override = None
    if "--backend" in sys.argv:
        backend_override = sys.argv[sys.argv.index("--backend") + 1]

    segments, backend = transcribe_video(video, audio_out, backend=backend_override)
    print(json.dumps({"backend": backend, "segments": segments}, indent=2))
