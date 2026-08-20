#!/usr/bin/env python3
"""Setup / preflight for /watch.

Modes:
  setup.py --check      Silent preflight. Exit 0 if ready, 2/3/4 on failure.
  setup.py --json       Machine-readable status for Claude to parse.
  setup.py              Installer. Auto-installs deps, scaffolds .env, marks SETUP_COMPLETE.

Design:
- Silent on success: --check exits 0 with no output when everything's ready so
  that /watch doesn't spam "setup is complete" on every turn.
- Idempotent: re-running the installer is safe — it never clobbers existing
  keys and only appends missing ones.
- SETUP_COMPLETE=true in ~/.config/watch/.env tells us the user has been
  through a successful installer run at least once.
- Never sudo. On macOS, auto-install via brew. Elsewhere, print exact commands.
- Never write an API key to disk automatically — only scaffold placeholders.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from whisper import _dotenv_value, resolve_whisper_backend  # noqa: E402


REQUIRED_BINARIES = ["ffmpeg", "ffprobe", "yt-dlp"]
CONFIG_DIR = Path.home() / ".config" / "watch"
CONFIG_FILE = CONFIG_DIR / ".env"
ENV_TEMPLATE = """# /watch API configuration
#
# Whisper transcription fallback — used only when yt-dlp cannot get captions
# (or when you point /watch at a local file with no subtitles).
#
# Option 1 — Local whisper.cpp (no API key, fully offline after model download):
#   Install whisper.cpp, then run /watch with --whisper local, or set:
#     WATCH_WHISPER_BACKEND=local
#   Model is downloaded from Hugging Face on first use (large-v3-turbo by default).
#   Override model:      WATCH_WHISPER_MODEL=<model-name>
#   Override cache dir:  WATCH_WHISPER_MODELS_DIR=~/.cache/yt-transcribe/models
#
# Option 2 — Groq cloud API (preferred cloud option: cheaper, faster):
#   Get a key: https://console.groq.com/keys
#   Runs whisper-large-v3.
#
# Option 3 — OpenAI cloud API (fallback):
#   Get a key: https://platform.openai.com/api-keys
#   Runs whisper-1.
#
# Leave all options unconfigured to disable Whisper — /watch will still work,
# but videos without native captions will come back frames-only.

GROQ_API_KEY=
OPENAI_API_KEY=
"""


def _which(name: str) -> str | None:
    return shutil.which(name)


def _check_binaries() -> list[str]:
    return [b for b in REQUIRED_BINARIES if not _which(b)]


def _file_permission_warning(path: Path) -> str | None:
    """Liefere eine Warnung, wenn Gruppe oder andere irgendein Recht haben."""
    try:
        mode = path.stat().st_mode
        if mode & 0o077:
            return (
                f"{path} permissions are too broad ({mode & 0o777:04o}); "
                f"run: chmod 600 {path}"
            )
    except OSError:
        pass
    return None


def _read_env_key(name: str) -> str | None:
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    return _dotenv_value(CONFIG_FILE, name)


def is_first_run() -> bool:
    """True if the installer hasn't completed successfully yet."""
    return _read_env_key("SETUP_COMPLETE") != "true"


def _scaffold_env() -> bool:
    """Create ~/.config/watch/.env with placeholders if missing."""
    if CONFIG_FILE.exists():
        return False
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(ENV_TEMPLATE, encoding="utf-8")
    try:
        CONFIG_FILE.chmod(0o600)
    except OSError:
        pass
    return True


def _write_setup_complete() -> None:
    """Idempotently append SETUP_COMPLETE=true to .env.

    Used only after a fully successful install (deps + key). Future sessions
    detect this marker to skip wizard-style UI and stay silent.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing = ""
    if CONFIG_FILE.exists():
        existing = CONFIG_FILE.read_text(encoding="utf-8")
        for line in existing.splitlines():
            if line.strip().startswith("SETUP_COMPLETE="):
                return
        if existing and not existing.endswith("\n"):
            existing += "\n"
        CONFIG_FILE.write_text(existing + "SETUP_COMPLETE=true\n", encoding="utf-8")
    else:
        CONFIG_FILE.write_text(ENV_TEMPLATE + "\nSETUP_COMPLETE=true\n", encoding="utf-8")
    try:
        CONFIG_FILE.chmod(0o600)
    except OSError:
        pass


def _brew_pkg(missing: list[str]) -> list[str]:
    pkgs: list[str] = []
    for bin_name in missing:
        if bin_name in ("ffmpeg", "ffprobe"):
            if "ffmpeg" not in pkgs:
                pkgs.append("ffmpeg")
        elif bin_name == "yt-dlp":
            if "yt-dlp" not in pkgs:
                pkgs.append("yt-dlp")
        else:
            pkgs.append(bin_name)
    return pkgs


def _install_macos(missing: list[str]) -> tuple[bool, str]:
    if _which("brew") is None:
        return False, (
            "Homebrew is not installed. Install it from https://brew.sh, then re-run setup. "
            "Or install manually: `brew install " + " ".join(_brew_pkg(missing)) + "`"
        )
    pkgs = _brew_pkg(missing)
    if not pkgs:
        return True, "nothing to install"
    cmd = ["brew", "install", *pkgs]
    print(f"[setup] running: {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        return False, f"brew install failed with exit code {result.returncode}"
    return True, f"installed via brew: {', '.join(pkgs)}"


def _install_hint_linux(missing: list[str]) -> str:
    pkgs = _brew_pkg(missing)
    hints = []
    if "ffmpeg" in pkgs:
        hints.append("apt: `sudo apt install ffmpeg` or dnf: `sudo dnf install ffmpeg`")
    if "yt-dlp" in pkgs:
        hints.append("`pipx install yt-dlp` (recommended) or `pip install --user yt-dlp`")
    return "\n  ".join(hints) if hints else "nothing to install"


def _install_hint_windows(missing: list[str]) -> str:
    pkgs = _brew_pkg(missing)
    hints = []
    if "ffmpeg" in pkgs:
        hints.append("winget: `winget install Gyan.FFmpeg`")
    if "yt-dlp" in pkgs:
        hints.append("winget: `winget install yt-dlp.yt-dlp` or pip: `pip install --user yt-dlp`")
    return "\n  ".join(hints) if hints else "nothing to install"


def _status() -> dict:
    """Structured preflight snapshot."""
    missing = _check_binaries()
    resolution = resolve_whisper_backend()
    has_backend = bool(resolution.backend and resolution.credential)

    if not missing and has_backend:
        status = "ready_with_backend_fallback" if resolution.reason else "ready"
    elif missing and not has_backend:
        status = (
            "needs_install_and_requested_backend"
            if resolution.requested_backend else "needs_install_and_key"
        )
    elif missing:
        status = "needs_install_with_backend_fallback" if resolution.reason else "needs_install"
    else:
        status = "requested_backend_unavailable" if resolution.requested_backend else "needs_key"

    return {
        "status": status,
        "first_run": is_first_run(),
        "missing_binaries": missing,
        "whisper_backend": resolution.backend,
        "has_api_key": has_backend,
        "requested_backend": resolution.requested_backend,
        "available_backends": list(resolution.available_backends),
        "backend_error": resolution.reason,
        "config_permissions": _file_permission_warning(CONFIG_FILE),
        "config_file": str(CONFIG_FILE),
        "platform": platform.system(),
    }


def _status_problems(status: dict) -> list[str]:
    """Formuliere dieselben konkreten Ursachen fuer Check und Session-Hook."""
    problems: list[str] = []
    if status["missing_binaries"]:
        problems.append(f"missing binaries: {', '.join(status['missing_binaries'])}")
    if status["backend_error"]:
        fallback = (
            f"; using {status['whisper_backend']} instead"
            if status["whisper_backend"] else ""
        )
        problems.append(status["backend_error"] + fallback)
    elif not status["has_api_key"]:
        problems.append(
            "no Whisper backend (set GROQ_API_KEY, OPENAI_API_KEY, or install whisper-cli)"
        )
    if status["config_permissions"]:
        problems.append(status["config_permissions"])
    return problems


def cmd_check() -> int:
    """Silent-on-success preflight.

    Exit 0 with no output when ready. On failure, print one actionable line
    to stderr and return:
      2 → binaries missing
      3 → API key missing
      4 → both missing
    """
    s = _status()
    problems = _status_problems(s)
    if s["status"] == "ready" and not problems:
        return 0

    installer = Path(__file__).resolve()
    sys.stderr.write(
        f"[watch] setup status ({'; '.join(problems)}). "
        f"Run: python3 {installer}\n"
    )
    sys.stderr.flush()

    if not s["missing_binaries"] and s["has_api_key"]:
        return 0
    if s["missing_binaries"] and not s["has_api_key"]:
        return 4
    if s["missing_binaries"]:
        return 2
    return 3


def cmd_json() -> int:
    json.dump(_status(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_hook_status() -> int:
    """Knapper SessionStart-Status aus derselben Logik wie Laufzeit/Setup."""
    s = _status()
    problems = _status_problems(s)
    if not problems:
        return 0
    print(
        f"/watch: setup status: {'; '.join(problems)}. Run "
        "`python3 $CLAUDE_PLUGIN_ROOT/scripts/setup.py` to update the configuration."
    )
    return 0


def cmd_install() -> int:
    missing = _check_binaries()
    installed_deps = False
    if missing:
        system = platform.system()
        if system == "Darwin":
            ok, msg = _install_macos(missing)
            print(f"[setup] {msg}", file=sys.stderr)
            if not ok:
                return 2
            still_missing = _check_binaries()
            if still_missing:
                print(f"[setup] still missing after install: {', '.join(still_missing)}", file=sys.stderr)
                return 2
            installed_deps = True
        elif system == "Linux":
            print("[setup] dependencies missing on Linux — please install:", file=sys.stderr)
            print("  " + _install_hint_linux(missing), file=sys.stderr)
            return 2
        elif system == "Windows":
            print("[setup] dependencies missing on Windows — please install:", file=sys.stderr)
            print("  " + _install_hint_windows(missing), file=sys.stderr)
            return 2
        else:
            print(f"[setup] unsupported platform ({system}) for auto-install. Install manually:", file=sys.stderr)
            print(f"  missing: {', '.join(missing)}", file=sys.stderr)
            return 2

    created = _scaffold_env()
    if created:
        print(f"[setup] created config: {CONFIG_FILE}")
    else:
        print(f"[setup] config exists: {CONFIG_FILE}")

    resolution = resolve_whisper_backend()
    if resolution.backend and resolution.credential:
        _write_setup_complete()
        if resolution.reason:
            print(f"[setup] warning: {resolution.reason}; using {resolution.backend} instead")
        print(f"[setup] ready. whisper backend: {resolution.backend}")
        if installed_deps:
            print("[setup] installed dependencies; /watch is fully set up.")
        return 0

    if resolution.reason:
        print(f"[setup] {resolution.reason}")

    print("")
    print("[setup] one step left: configure a Whisper transcription backend.")
    print("")
    print("  Option A — local whisper.cpp (no API key, fully offline after model download):")
    print("    Install: brew install whisper-cpp  (macOS)")
    print("    Then use: --whisper local  or set WATCH_WHISPER_BACKEND=local")
    print("")
    print(f"  Option B — cloud API. Edit {CONFIG_FILE} and set either:")
    print("    GROQ_API_KEY=...    (preferred — cheaper, faster; get one at console.groq.com/keys)")
    print("    OPENAI_API_KEY=...  (fallback; get one at platform.openai.com/api-keys)")
    print("")
    print("  Without either option, /watch still works but videos without captions come back frames-only.")
    return 3


def main() -> int:
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "--check":
            return cmd_check()
        if arg == "--json":
            return cmd_json()
        if arg == "--hook-status":
            return cmd_hook_status()
    return cmd_install()


if __name__ == "__main__":
    raise SystemExit(main())
