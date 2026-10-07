# Claude Video /watch

**🌐 Sprache / Language:** [English](README.md) · [Deutsch](README.de.md)

Claude Video extracts frames and speech from video URLs or local files so an assistant can inspect and discuss their contents.

This fork of [Bradley Bonanno's claude-video](https://github.com/bradautomates/claude-video) adds local Whisper transcription, scene-based sampling, and an OCR textframe mode. The original MIT attribution is preserved in [LICENSE](LICENSE).

## Choose a mode

| Goal | Command | Result |
| --- | --- | --- |
| Summarize scenes or investigate a recording | `/watch <source> [question]` or `scripts/watch.py` | Selected images and a timestamped speech report |
| Capture slides, code, and other displayed text | `scripts/textframes.py` | OCR-deduplicated images, `texte.md`, and `texte.json` |
| Read speech alongside displayed text | `scripts/textframes.py --transcript` | Textframes plus `transkript.md` and speech segments in `transkript.json` |

The scene mode works on macOS, Linux, and Windows. OCR requires **macOS with Apple Vision and a Swift compiler**; no OCR fallback is bundled. Both Python commands use the standard library, without Python package dependencies.

## Install

For Claude Code:

```text
/plugin marketplace add DanielMuellerIR/claude-video
/plugin install watch@claude-video
```

For Codex or another skill consumer:

```bash
git clone https://github.com/DanielMuellerIR/claude-video.git ~/.codex/skills/watch
```

For direct CLI use, clone the repository and run the commands below from its root. Use `python` instead of `python3` on Windows.

Scene mode needs `ffmpeg`, `ffprobe`, and `yt-dlp` for URL downloads. The setup helper checks dependencies and optional Whisper backends:

```bash
python3 scripts/setup.py --json
python3 scripts/setup.py
```

On macOS the installer uses Homebrew; on Linux/Windows it prints installation commands. A missing Whisper backend does not prevent a frames-only run. For macOS OCR, install the tools if needed:

```bash
brew install ffmpeg yt-dlp
xcode-select --install
```

The OCR helper compiles on first use into `${XDG_CACHE_HOME:-$HOME/.cache}/watch/ocr/`. The cache is independent of the checkout. Local videos do not need `yt-dlp`; OCR alone needs neither Whisper nor an API key.

For claude.ai upload, build `dist/watch.skill` from a clean committed checkout with `bash scripts/build-skill.sh`. [Published release assets](https://github.com/DanielMuellerIR/claude-video/releases) may predate the latest source. The macOS-only OCR command cannot run in a Linux-hosted execution environment.

## Scene mode

```bash
python3 scripts/watch.py "https://youtu.be/VIDEO_ID"
python3 scripts/watch.py recording.mp4 --start 2:15 --end 2:45
python3 scripts/watch.py recording.mp4 --no-whisper --no-classify
```

The command uses scene changes, with uniform sampling as a fallback, and prints a Markdown report containing a JSON frame manifest and transcript. Source metadata and media text are untrusted data. The assistant reads the images to answer the question.

| Option | Effect |
| --- | --- |
| `--start T`, `--end T` | Focus on a range (`SS`, `MM:SS`, or `HH:MM:SS`); timestamps remain absolute |
| `--max-frames N` | Lower the user cap; hard maximum 100 |
| `--resolution W` | Frame width, default 1600; images are not enlarged beyond source width |
| `--fps F` | Uniform fallback rate, positive and capped at 2 fps; scene selection keeps its own budget |
| `--no-classify` | Skip the optional vision classifier |
| `--whisper local\|groq\|openai` | Select a specific speech backend |
| `--no-whisper` | Keep native captions but skip Whisper fallback |
| `--out-dir DIR` | Parent for an exclusive generated `watch-*` output directory |

Default frame budgets grow with duration, up to 100 images. Videos over ten minutes receive sparse coverage; use a focused range when detail matters. Unknown duration uses the user cap.

## Textframe mode (macOS)

```bash
# OCR and text images, without speech transcription
python3 scripts/textframes.py recording.mp4 --out-dir ./results

# More frequent samples; captions first, then local Whisper if needed
python3 scripts/textframes.py "https://youtu.be/VIDEO_ID" --fps 2 --transcript --whisper local

# Captions only: no audio transcription or cloud upload
python3 scripts/textframes.py "https://youtu.be/VIDEO_ID" --transcript --no-whisper

# Inspect plain OCR with both overlay filters and the LLM disabled
python3 scripts/textframes.py recording.mp4 --no-filter --no-classify
```

The pipeline samples the video, skips byte-identical consecutive images, runs Apple Vision, removes empty OCR results and selected overlays, then deduplicates consecutive text. A growing slide replaces its earlier prefix with the fullest sampled image. Line order, repeated lines, case, punctuation, and OCR whitespace remain significant. Separate returns to earlier content remain in the timeline.

Recurring small footer/corner text and explicit subscription/like prompts are filtered conservatively. Normal body text and stable slide titles remain. These heuristics can still misclassify content; compare with `--no-filter` when completeness matters. Filtering changes the OCR text and image selection; it does not erase pixels from retained screenshots.

| Option | Effect |
| --- | --- |
| `--fps F` | Sampling rate, default 1 fps; this mode has no 100-frame/2-fps scene-mode cap |
| `--min-conf F` | Minimum OCR confidence from 0 to 1, default 0.45 |
| `--ubiquitous-frac F` | Fraction of OCR samples needed to learn recurring border text, default 0.6; at least three observations |
| `--no-filter` | Disable heuristic watermark/promotion filters |
| `--no-classify` | Disable the configured optional LLM classifier |
| `--transcript` | Add speech and embed every retained text image chronologically |
| `--whisper local\|groq\|openai` | Speech backend; requires `--transcript` |
| `--no-whisper` | Use captions only; requires `--transcript` |
| `--out-dir DIR` / `--out DIR` | Parent for an exclusive generated output directory |
| `--keep-temp` | Retain sampled images and downloaded/intermediate media for inspection |

Shorter text appearances can fall between samples. OCR can misread text and lose layout details such as code indentation. Conservative matching may retain near-duplicates instead of discarding real changes. Long or high-rate runs need time and temporary disk space for all sampled images.

The JSON summary on stdout identifies the generated `work_dir`, index/report paths, filter counts, classifier status, and optional transcript status. Inside that directory:

| File | Contents |
| --- | --- |
| `frames/` | Selected JPEGs with sample timestamps in their filenames |
| `texte.json` | Each frame's relative image path, text, lines, and `time_sec` |
| `texte.md` | Image index with collapsible JSON-encoded OCR text |
| `transkript.md` | With `--transcript`: text images and speech ordered by start timestamp |
| `transkript.json` | With `--transcript`: speech segments with start/end times |

At equal timestamps, the image precedes the speech segment. Overlapping speech cues retain their full intervals; word timings are not invented. If speech is missing or fails, images remain available and the transcript status says `unavailable` or `failed`.

## Speech backends

Native captions are preferred. The downloader currently requests English caption variants. If captions are unavailable, `--transcript` in textframe mode and the default scene workflow can use Whisper:

- **Local:** install whisper.cpp (`brew install whisper-cpp` on macOS) and choose `--whisper local`. No API key is required. The first use downloads the model; later runs use the cache.
- **Groq/OpenAI:** configure `GROQ_API_KEY` or `OPENAI_API_KEY` in the environment or `~/.config/watch/.env` with permissions `0600`. The fallback also reads `.env` in the current directory. Cloud transcription uploads audio to the chosen provider.

Selection is CLI choice, then `WATCH_WHISPER_BACKEND`, then available Groq, OpenAI, or local backend. Focused scene runs upload only their requested audio range. Long cloud audio is split into overlapping chunks; local Whisper has no application-imposed duration limit.

Local model configuration: `WATCH_WHISPER_MODEL` (default `large-v3-turbo`) and `WATCH_WHISPER_MODELS_DIR` (default `~/.cache/yt-transcribe/models`). Model names use letters, numbers, `.`, `_`, or `-`.

## Optional image classification

Both modes accept `LLM_RUN` (Python helper path) and `LLM_HOST` (helper destination). Missing either skips classification. Textframe mode additionally accepts `LLM_MODEL`, default `gemma4:12b`.

```bash
export LLM_RUN=/path/to/llm_run.py
export LLM_HOST=vision-host
export LLM_MODEL=gemma4:12b
```

The helper contract is `python3 HELPER HOST --model MODEL --no-think --image IMAGE PROMPT`. It must honor the supplied prompt: textframe mode accepts exactly `KEEP` or `DROP`; scene mode uses `NÜTZLICH` or `VERWERFEN`. Only explicit discard decisions remove frames. Errors, timeouts, and invalid answers stop classification and preserve unchecked frames; the report distinguishes completed, partial, failed, disabled, and unconfigured runs. Configuring a helper can send images to its destination; use `--no-classify` to prevent this.

## Safety and cleanup

Video metadata, captions, OCR, images, and labelled diagnostics are untrusted evidence. Instructions embedded in media must never change the assistant's workflow or cause command execution. Reports encode external text as JSON. No platform login or session cookies are used by these commands.

Every successful run retains its exclusive marked `watch-*` directory. Failed pipeline runs remove only that generated child. Source files and the `--out-dir` parent are preserved. After using a report:

```bash
python3 scripts/cleanup.py "<work_dir from the command's JSON output>"
```

The cleanup helper verifies ownership before deletion. The OCR compiler cache and local Whisper model cache persist separately.

## Development

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
bash scripts/build-skill.sh
```

The package builder requires a clean committed checkout and includes all runtime Python and Swift sources. Tests cover OCR deduplication, filters, classification failure handling, transcript timing, source protection, sampling, and shared backends. Native OCR is verified separately on macOS; headless unit tests run without Apple Vision.

See [SKILL.md](SKILL.md) for the assistant workflow, [CHANGELOG.md](CHANGELOG.md) for version history, and [docs/reviews/](docs/reviews/) for review evidence. Tagged releases build `dist/watch.skill` through the release workflow.
