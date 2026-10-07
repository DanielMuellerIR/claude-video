# Changelog

All notable changes to `/watch` are documented here.

## [0.1.15] — 2026-10-07

### Fixed
- Frame extraction preserves existing images, including an input image in the output directory. The standalone frame CLI creates an exclusive marked working directory; Python extractors reject existing frame images.
- Audio extraction rejects existing output paths, including the source itself. The standalone Whisper CLI validates missing and unknown options before doing any work.
- Scene detection uses source-relative timestamps, excludes transitions at the requested end, and captures the image already displayed at a fractional range start.
- Malformed local/cloud Whisper response structures, text, intervals, and local JSON encoding produce controlled transcription failures, preserving the video report.
- Whisper tool and HTTP diagnostics are bounded, JSON-encoded, and labelled as untrusted. Echoed API keys are redacted from response diagnostics.
- Invalid negative or non-finite retry delays use the default retry schedule. Unrepresentable or reversed caption intervals are skipped without losing valid cues.

## [0.1.14] — 2026-10-07

### Added
- Textframes `--transcript` reuses native captions and the shared Whisper backends, writing `transkript.md` with every text image at its sample timestamp and speech with complete cue intervals.
- `transkript.json` preserves the speech segments. Missing narration or transcription failures retain the OCR results and report the transcript status; `--no-whisper` uses captions only.

### Documentation
- English and German README editions describe both modes, dependencies, filters, speech/classification configuration, output formats, cleanup, and limitations.
- The skill and plugin command route displayed-text requests to the OCR workflow. Installation links and plugin metadata refer to this fork, preserving upstream attribution.

## [0.1.13] — 2026-10-07

### Added
- Textframe overlay filters remove recurring small border text and explicit subscription/like prompts; `--no-filter` retains all recognized text for verification.
- Optional textframe classification uses `LLM_RUN`, `LLM_HOST`, and `LLM_MODEL`. Missing configuration skips the helper; timeouts, failures, and ambiguous responses retain unchecked frames and report the status. `--no-classify` disables it.

## [0.1.12] — 2026-10-07

### Added
- Standalone `scripts/textframes.py` extracts OCR text frames on macOS using the bundled Apple Vision helper, with no Python packages or LLM configuration required.
- Text-frame output uses exclusive marked working directories, `frames/`, `texte.md`, and `texte.json`; repeated text is collapsed and growing slides keep their fullest sampled frame.
- Exact visual comparisons and conservative text matching retain changes to numbers, punctuation, and case. OCR failures abort instead of silently producing an empty result.

### Fixed
- Text-frame deduplication preserves OCR line order, repeated lines, and whitespace within code.
- Focused transcripts exclude captions that only touch the requested start or end.
- Non-UTF-8 configuration files no longer prevent selecting local Whisper; setup preserves those files and reports a controlled failure.
- Successful setup replaces stale completion markers and reapplies private file permissions.
- ffprobe and uniform frame-extraction failures encode and label untrusted diagnostics.

### Limitations
- Text-frame OCR requires macOS and the Swift compiler. Sampling defaults to one frame per second; shorter text appearances can be missed.
- Watermark/promotion filtering and transcript embedding are not yet included in the standalone text-frame command.

## [0.1.11] — 2026-10-03

### Fixed
- Focused uniform sampling retains the frame already displayed at a fractional start, including low and variable source frame rates.
- Skill packaging rejects failed ZIP exclusions and verifies that development/plugin files are absent.
- Interrupted HTTP response bodies follow transport retries and the existing transcript-free report fallback.
- Local Whisper detection verifies the whisper.cpp flags and uses the verified executable path.
- The standalone frame CLI rejects unknown or incomplete options with argparse; both entry points share finite positive FPS validation.

### Tests
- Real low-rate/VFR video fixtures, HTTP-decoder and Watch fallback checks, isolated CLI probes, and normal/failing/no-op ZIP builds.

## [0.1.10] — 2026-10-02

### Fixed
- Uniform frame sampling keeps the first frame, including subsecond clips and fractional seek positions, and aligns sparse samples with their reported timestamps.
- The standalone frame extractor validates focused ranges and clamps their end to the video duration before planning samples; non-finite time and FPS inputs are rejected.
- Caption deduplication preserves separate utterances and full overlapping intervals; WebVTT character references are decoded.
- Local Whisper uses isolated temporary output, preserving existing sibling WAV/JSON files and rejecting stale transcripts.
- The session hook supports installation paths containing spaces, and cleanup safely rejects malformed ownership markers.
- The upload bundle excludes contributor instructions and Codex plugin metadata.

### Tests
- Added headless regressions and real ffmpeg fixtures for frame content, short clips, fractional seeks, and standalone range handling.

## [0.1.9] — 2026-09-08

### Fixed
- FPS overrides use the user frame cap for uniform sampling independently of the scene budget.
- Adjacent repeated transcript segments remain separate unless their time intervals overlap.
- Classifier errors and partial runs are reported; unclassified frames are retained.

## [0.1.8] — 2026-09-07

### Fixed
- Concurrent local Whisper model downloads use separate temporary files and publish complete models atomically; failed downloads clean up only their own file.

## [0.1.7] — 2026-08-20

### Fixed
- Unknown-duration videos now use the requested frame cap; `--fps` rejects non-positive values and only controls the uniform fallback.
- Whisper backend resolution normalizes CLI input, diagnoses unavailable choices, and falls back from invalid environment preferences when another backend is usable.
- Setup and SessionStart report exact missing binaries, backend-selection errors, and unsafe group/other permissions on the secrets file.
- Download selection rejects yt-dlp fragment streams, skips Whisper for videos without audio, and JSON-encodes externally controlled diagnostics.
- Failed runs remove their exclusive work directory; invalid or empty `--out-dir` values now fail clearly or use system tmp as appropriate.
- WebVTT accepts non-normalized minute/second fields, reports malformed timestamp lines, and formats hour-long transcript positions as `h:mm:ss`.
- Cloud-chunk deduplication requires real interval overlap and a five-word minimum for substring matches without mutating caller-owned segments.
- Local model names reject path traversal, and standalone scripts work with Python safe-path mode.

### Changed
- Scene thinning is time-based, preserves the range start without near-duplicate frames, and uses one shared sampling planner for both entry points.
- Frame reports distinguish a completed classifier run from an unconfigured or explicitly disabled classifier.

### Tests
- Added headless coverage for the 2026-08-20 review findings and a push/pull-request CI workflow.

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
