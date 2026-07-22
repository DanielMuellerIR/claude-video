# Changelog

All notable changes to `/watch` are documented here.

## [0.1.6] — 2026-07-22

### Security
- `--out-dir` is now a parent for an exclusive marked `watch-*` directory; cleanup uses a marker-checking helper instead of recursive deletion of an arbitrary path.
- Each yt-dlp invocation writes to a fresh child directory, so failed downloads cannot reuse stale video, subtitle, or metadata files.
- Reports serialize external metadata, frame paths, and captions as untrusted JSON, and the skill contract explicitly forbids following instructions embedded in media.
- Focused Whisper runs extract and upload only the requested audio range.

### Fixed
- Scene extraction always includes the effective range start, and duration-derived frame budgets now apply consistently up to the documented 100-frame default.
- WebVTT parsing accepts both `MM:SS.mmm` and `HH:MM:SS.mmm`, long hour values, and cue settings.
- Cloud audio chunks overlap and deduplicate matching boundary segments while using one absolute chunk offset.
- Whisper backend selection is shared by runtime, setup, and the session hook with precedence `CLI > environment preference > available backend`.

### Tests
- Added headless regression coverage for all review fixes, including safe cleanup, stale-download isolation, range clipping, prompt-fence containment, scene starts, WebVTT variants, backend precedence, and chunk overlap.

## [0.1.3] — 2026-05-09

### Fixed
- Windows: `video.info.json` is read as UTF-8 (#4). Previously `Path.read_text()` defaulted to cp1252 on Windows and crashed on yt-dlp's UTF-8 output, silently dropping Title/Uploader from the report. Same fix applied to `.env` reads/writes in `whisper.py` and `setup.py`.
- `download.py` now logs info.json parse failures to stderr instead of swallowing them.

### Security
- Hardened subprocess argv against option injection (#2): inserted `--` before the URL in the yt-dlp argv, and tightened `is_url` to reject `-`-prefixed sources and require a non-empty netloc. Resolved video/audio paths to absolute via `Path.resolve()` before passing to `ffmpeg`/`ffprobe`, so a relative path starting with `-` can't be misinterpreted as a flag.

## [0.1.2] — 2026-04-24

### Fixed
- Windows console crash: removed the emoji from the long-video warning in `watch.py`; cp1252 consoles couldn't encode it.
- `setup.py` now prints `winget` / `pip` install commands on Windows instead of "unsupported platform" — matches what the README already promised.

### Changed
- `SKILL.md` notes that on Windows the scripts must be invoked with `python`, not `python3` (the latter is the Microsoft Store stub on Windows).

## [0.1.1] — 2026-04-24

### Fixed
- Added `commands/watch.md` shim so `/watch` is callable when installed as a Claude Code plugin. Without it, the plugin loaded but the skill wasn't exposed as a slash command.
- `scripts/build-skill.sh` now strips `commands/` from the claude.ai `.skill` bundle alongside `hooks/` and `.claude-plugin/`.

## [0.1.0] — 2026-04-24

Initial marketplace release.

### Added
- `/watch <url-or-path> [question]` slash command.
- yt-dlp download with native caption extraction (manual + auto-subs).
- ffmpeg frame extraction with auto-scaled fps (≤2 fps, ≤100 frames, duration-aware budget).
- `--start` / `--end` focused mode with denser frame budget and transcript range filtering.
- Whisper fallback (Groq preferred, OpenAI secondary) for videos without captions.
- `setup.py` preflight: silent `--check`, structured `--json`, and installer that auto-runs `brew install` on macOS.
- Session-start hook that prints a one-line status on first run / partial config.
- `.skill` bundle packaging for claude.ai upload via `scripts/build-skill.sh`.
