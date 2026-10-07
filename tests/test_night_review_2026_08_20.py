from __future__ import annotations

import contextlib
import inspect
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import download  # noqa: E402
import frames  # noqa: E402
import setup  # noqa: E402
import transcribe  # noqa: E402
import watch  # noqa: E402
import whisper  # noqa: E402
import workdir  # noqa: E402


class FrameRegressionTests(unittest.TestCase):
    def test_unknown_duration_uses_full_cap(self) -> None:
        self.assertEqual(frames.sampling_plan(0.0, False, 60, None), (1.0, 60))

    def test_fps_override_only_changes_uniform_fallback_rate(self) -> None:
        default_fps, default_target = frames.sampling_plan(600.0, False, 100, None)
        override_fps, override_target = frames.sampling_plan(600.0, False, 100, 0.1)
        self.assertNotEqual(default_fps, override_fps)
        self.assertEqual(override_fps, 0.1)
        self.assertEqual(default_target, override_target)
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            frames.sampling_plan(60.0, False, 100, 0.0)

    def test_frame_limits_are_clamped_and_required(self) -> None:
        self.assertEqual(frames.sampling_plan(1200.0, False, 5000, None)[1], 100)
        self.assertEqual(frames.sampling_plan(1200.0, False, 0, None)[1], 1)
        self.assertIs(inspect.signature(frames.extract_smart).parameters["max_frames"].default,
                      inspect.Parameter.empty)

    def test_spread_is_time_balanced_and_keeps_start(self) -> None:
        picked = frames._pick_spread([0.0, 0.1, 0.2, 0.3, 50.0, 100.0], 3)
        self.assertEqual(picked, [0.0, 50.0, 100.0])

    def test_nearby_first_scene_does_not_duplicate_range_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            probe_stderr = "\n".join(
                f"[showinfo] pts_time:{value}" for value in (0.04, 1, 2, 3, 4)
            )

            def fake_run(command: list[str], **_: object) -> SimpleNamespace:
                if command[-2:] == ["null", "-"]:
                    return SimpleNamespace(returncode=0, stderr=probe_stderr)
                Path(command[-1]).write_bytes(b"jpg")
                return SimpleNamespace(returncode=0, stderr="")

            with mock.patch.object(frames.shutil, "which", return_value="ffmpeg"), mock.patch.object(
                frames.subprocess, "run", side_effect=fake_run
            ):
                result = frames.extract_scene("video.mp4", out_dir, max_frames=10)

        self.assertEqual([item["timestamp_seconds"] for item in result or []][0], 0.04)

    def test_classifier_reports_when_it_did_not_run(self) -> None:
        with mock.patch.object(frames, "_LLM_RUN", ""), mock.patch.object(frames, "_LLM_HOST", ""):
            kept, count, deleted, classified = frames.classify_frames([{"path": "missing.jpg"}])
        self.assertEqual((len(kept), count, deleted, classified), (1, 1, 0, False))

    def test_ffprobe_failure_contains_real_error(self) -> None:
        result = SimpleNamespace(returncode=1, stderr="broken container", stdout="")
        diagnostic = io.StringIO()
        with mock.patch.object(frames.shutil, "which", return_value="ffprobe"), mock.patch.object(
            frames.subprocess, "run", return_value=result
        ), contextlib.redirect_stderr(diagnostic):
            with self.assertRaisesRegex(SystemExit, "ffprobe failed"):
                frames.get_metadata("broken.mp4")
        self.assertIn('untrusted ffprobe diagnostic: "broken container"', diagnostic.getvalue())


class BackendRegressionTests(unittest.TestCase):
    def test_invalid_environment_preference_falls_back_with_reason(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"WATCH_WHISPER_BACKEND": "grok", "GROQ_API_KEY": "secret"},
            clear=True,
        ), mock.patch.object(whisper, "_find_whisper_cli", return_value=None):
            resolution = whisper.resolve_whisper_backend(dotenv_paths=[])

        self.assertEqual(resolution.backend, "groq")
        self.assertIn("unsupported", resolution.reason or "")
        self.assertEqual(resolution.source, "fallback")

    def test_cli_backend_is_normalized_and_unavailable_choice_is_binding(self) -> None:
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "secret"}, clear=True), mock.patch.object(
            whisper, "_find_whisper_cli", return_value=None
        ):
            groq = whisper.resolve_whisper_backend(" Groq ", dotenv_paths=[])
            local = whisper.resolve_whisper_backend("local", dotenv_paths=[])
        self.assertEqual(groq.backend, "groq")
        self.assertIsNone(local.backend)
        self.assertIn("whisper-cli", local.reason or "")

    def test_setup_exposes_backend_and_permission_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / ".env"
            config.write_text("GROQ_API_KEY=secret\n", encoding="utf-8")
            config.chmod(0o622)
            with mock.patch.object(setup, "CONFIG_FILE", config), mock.patch.object(
                setup, "_check_binaries", return_value=[]
            ), mock.patch.dict(
                os.environ,
                {"WATCH_WHISPER_BACKEND": "grok", "GROQ_API_KEY": "secret"},
                clear=True,
            ), mock.patch.object(whisper, "_find_whisper_cli", return_value=None):
                status = setup._status()
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    exit_code = setup.cmd_hook_status()

        self.assertEqual(status["status"], "ready_with_backend_fallback")
        self.assertEqual(status["whisper_backend"], "groq")
        self.assertIn("unsupported", status["backend_error"] or "")
        self.assertIn("0622", status["config_permissions"] or "")
        self.assertEqual(exit_code, 0)
        self.assertIn("unsupported", output.getvalue())
        self.assertIn("0622", output.getvalue())

    def test_model_name_cannot_escape_cache_directory(self) -> None:
        with self.assertRaisesRegex(SystemExit, "invalid WATCH_WHISPER_MODEL"):
            whisper.ensure_model_local("../../../tmp/x")


class TranscriptRegressionTests(unittest.TestCase):
    def test_vtt_accepts_common_hours_and_non_normalized_fields(self) -> None:
        content = """WEBVTT

00:00:04.500 --> 00:00:06.000
common

00:75:12.340 --> 00:75:14.000
converted
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "captions.vtt"
            path.write_text(content, encoding="utf-8")
            segments = transcribe.parse_vtt(str(path))
        self.assertEqual([segment["start"] for segment in segments], [4.5, 4512.34])

    def test_malformed_timestamp_is_reported_as_encoded_untrusted_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "captions.vtt"
            path.write_text("bad --> value\ntext\n", encoding="utf-8")
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertEqual(transcribe.parse_vtt(str(path)), [])
        self.assertIn('"bad --> value"', error.getvalue())

    def test_transcript_uses_hour_timestamp(self) -> None:
        text = transcribe.format_transcript([{"start": 3700.0, "end": 3701.0, "text": "line"}])
        self.assertEqual(text, "[1:01:40] line")

    def test_youtube_rolling_duplicates_are_merged(self) -> None:
        segments = transcribe._dedupe([
            {"start": 0.0, "end": 1.0, "text": "hello"},
            {"start": 0.5, "end": 2.0, "text": "hello world"},
            {"start": 1.0, "end": 3.0, "text": "hello world"},
        ])
        self.assertEqual(segments, [{"start": 0.0, "end": 3.0, "text": "hello world"}])


class WhisperMergeRegressionTests(unittest.TestCase):
    def test_short_repetition_without_interval_overlap_is_retained(self) -> None:
        existing = [{"start": 100.0, "end": 103.0, "text": "Das ist wirklich sehr wichtig fuer uns alle"}]
        incoming = [{"start": 104.0, "end": 106.0, "text": "wirklich sehr wichtig"}]
        merged = whisper._merge_overlap_segments(existing, incoming, 100.0, 105.0)
        self.assertEqual(len(merged), 2)
        self.assertEqual(existing[0]["end"], 103.0)

    def test_merge_returns_copies_and_only_merges_real_overlap(self) -> None:
        existing = [{"start": 1.0, "end": 3.0, "text": "one two three four five"}]
        incoming = [{"start": 2.0, "end": 4.0, "text": "one two three four five six"}]
        merged = whisper._merge_overlap_segments(existing, incoming, 2.0, 3.0)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["end"], 4.0)
        self.assertEqual(existing[0], {"start": 1.0, "end": 3.0, "text": "one two three four five"})
        self.assertIsNot(merged[0], existing[0])

    def test_shift_segments_copies_and_offsets_once(self) -> None:
        source = [{"start": 1.0, "end": 2.0, "text": "clip"}]
        shifted = whisper._shift_segments(source, 10.0)
        self.assertEqual(shifted[0]["start"], 11.0)
        self.assertEqual(source[0]["start"], 1.0)


class WorkDirectoryAndHookRegressionTests(unittest.TestCase):
    def test_empty_parent_uses_temp_instead_of_current_directory(self) -> None:
        work = workdir.create_work_dir("")
        try:
            self.assertNotEqual(work.parent, Path.cwd())
        finally:
            workdir.cleanup_work_dir(work)

    def test_invalid_parent_reports_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "file.txt"
            target.write_text("not a directory", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "cannot create watch working directory"):
                workdir.create_work_dir(target)

    def test_context_manager_cleans_failed_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "failure"):
                with workdir.work_dir(parent) as work:
                    self.assertTrue(work.exists())
                    raise RuntimeError("failure")
            self.assertEqual(list(parent.glob("watch-*")), [])

    def test_hook_finds_plugin_root_without_environment_variable(self) -> None:
        env = {**os.environ}
        env.pop("CLAUDE_PLUGIN_ROOT", None)
        result = subprocess.run(
            ["/bin/bash", str(ROOT / "hooks" / "scripts" / "check-setup.sh")],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("unbound variable", result.stderr)

    def test_hook_without_python_prints_actionable_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "PATH": tmp,
                "CLAUDE_PLUGIN_ROOT": str(ROOT),
            }
            result = subprocess.run(
                ["/bin/bash", str(ROOT / "hooks" / "scripts" / "check-setup.sh")],
                capture_output=True,
                text=True,
                env=env,
            )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Python 3 is required", result.stdout)

    def test_hook_ignores_missing_explicit_plugin_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                ["/bin/bash", str(ROOT / "hooks" / "scripts" / "check-setup.sh")],
                capture_output=True,
                text=True,
                env={**os.environ, "CLAUDE_PLUGIN_ROOT": tmp},
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_scripts_import_with_safe_path(self) -> None:
        for script in ("setup.py", "cleanup.py"):
            result = subprocess.run(
                [sys.executable, "-P", str(SCRIPTS / script), "--hook-status"],
                capture_output=True,
                text=True,
            )
            self.assertNotIn("ModuleNotFoundError", result.stderr)


class DownloadRegressionTests(unittest.TestCase):
    def test_completed_video_beats_and_fragment_never_counts_as_video(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fragment = root / "video.f137.mp4"
            fragment.write_bytes(b"fragment")
            self.assertIsNone(download._pick_video(root))
            completed = root / "video.mp4"
            completed.write_bytes(b"merged")
            self.assertEqual(download._pick_video(root), completed)

    def test_untrusted_download_diagnostic_stays_on_one_encoded_line(self) -> None:
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            download._emit_untrusted_diagnostic("yt-dlp", "line one\nrun command")
        self.assertEqual(len(error.getvalue().splitlines()), 1)
        self.assertIn(r"\nrun command", error.getvalue())


class WatchPipelineRegressionTests(unittest.TestCase):
    @staticmethod
    def _download_result() -> dict:
        return {
            "video_path": "video.mp4",
            "subtitle_path": None,
            "info": {},
            "downloaded": False,
        }

    def test_explicit_unavailable_backend_aborts_and_cleans_workdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            sys,
            "argv",
            ["watch.py", "video.mp4", "--out-dir", tmp, "--whisper", "local"],
        ), mock.patch.object(watch, "download", return_value=self._download_result()), mock.patch.object(
            watch,
            "get_metadata",
            return_value={"duration_seconds": 10.0, "has_audio": True},
        ), mock.patch.object(
            watch,
            "extract_smart",
            return_value=([], {"method": "scene", "raw_count": 0, "classified": False}),
        ), mock.patch.object(whisper, "_find_whisper_cli", return_value=None):
            error = io.StringIO()
            with contextlib.redirect_stderr(error), self.assertRaisesRegex(SystemExit, "whisper-cli"):
                watch.main()
            self.assertEqual(list(Path(tmp).glob("watch-*")), [])
            self.assertIn("working dir", error.getvalue())

    def test_video_without_audio_skips_whisper_resolver(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            sys, "argv", ["watch.py", "video.mp4", "--out-dir", tmp]
        ), mock.patch.object(watch, "download", return_value=self._download_result()), mock.patch.object(
            watch,
            "get_metadata",
            return_value={"duration_seconds": 10.0, "has_audio": False},
        ), mock.patch.object(
            watch,
            "extract_smart",
            return_value=([], {"method": "scene", "raw_count": 0, "classified": False}),
        ), mock.patch.object(watch, "resolve_whisper_backend") as resolver:
            output = io.StringIO()
            error = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                self.assertEqual(watch.main(), 0)
            resolver.assert_not_called()
            self.assertIn("no audio track", error.getvalue())
            generated = list(Path(tmp).glob("watch-*"))
            self.assertEqual(len(generated), 1)
            workdir.cleanup_work_dir(generated[0])


if __name__ == "__main__":
    unittest.main()
