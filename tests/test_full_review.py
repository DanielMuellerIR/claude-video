"""Regressionen für die bestätigten Funde des vollständigen Projekt-Reviews."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import frames
import transcribe
import whisper

spec = importlib.util.spec_from_file_location("watch_setup_review", ROOT / "scripts/setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class CaptionBoundaryTests(unittest.TestCase):
    def test_touching_captions_are_excluded_and_overlapping_captions_remain(self):
        source = [{"start": 0, "end": 10, "text": "before"},
                  {"start": 9, "end": 11, "text": "overlaps start"},
                  {"start": 15, "end": 16, "text": "inside"},
                  {"start": 19, "end": 21, "text": "overlaps end"},
                  {"start": 20, "end": 21, "text": "after"}]
        self.assertEqual(transcribe.filter_range(source, 10, 20), source[1:4])
        self.assertEqual(transcribe.filter_range(source[:1] + source[4:], 10, 20), [])


class ConfigFileTests(unittest.TestCase):
    def test_invalid_utf8_does_not_block_explicit_local_backend_or_leak_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / ".env"; path.write_bytes(b"\xffPRIVATE-CONTENT")
            output = io.StringIO()
            with mock.patch.dict(os.environ, {}, clear=True), \
                 mock.patch.object(whisper, "_find_whisper_cli", return_value="whisper-cli"), \
                 contextlib.redirect_stderr(output):
                result = whisper.resolve_whisper_backend("local", dotenv_paths=[path])
            self.assertEqual(result.backend, "local")
            self.assertIn("non-UTF-8", output.getvalue())
            self.assertNotIn("PRIVATE-CONTENT", output.getvalue())
            self.assertEqual(path.read_bytes(), b"\xffPRIVATE-CONTENT")

    def test_setup_updates_false_marker_and_restricts_permissions_idempotently(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); path = root / ".env"
            path.write_text("# User configuration\nGROQ_API_KEY=fixture\n SETUP_COMPLETE = false\n")
            path.chmod(0o644)
            with mock.patch.object(setup, "CONFIG_DIR", root), mock.patch.object(setup, "CONFIG_FILE", path), \
                 mock.patch.dict(os.environ, {}, clear=True):
                self.assertTrue(setup.is_first_run())
                setup._write_setup_complete()
                first = path.read_bytes()
                self.assertFalse(setup.is_first_run())
                setup._write_setup_complete()
            self.assertEqual(path.read_bytes(), first)
            self.assertIn(b"GROQ_API_KEY=fixture", first)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_setup_preserves_non_utf8_file_and_reports_controlled_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); path = root / ".env"; path.write_bytes(b"\xffkeep")
            with mock.patch.object(setup, "CONFIG_DIR", root), mock.patch.object(setup, "CONFIG_FILE", path):
                with self.assertRaisesRegex(SystemExit, "non-UTF-8"):
                    setup._write_setup_complete()
            self.assertEqual(path.read_bytes(), b"\xffkeep")


class MediaDiagnosticTests(unittest.TestCase):
    def test_ffprobe_and_ffmpeg_diagnostics_cannot_emit_new_control_lines(self):
        with tempfile.TemporaryDirectory() as td:
            actions = [lambda: frames.get_metadata("bad.mp4"),
                       lambda: frames.extract("bad.mp4", Path(td) / "frames", fps=1)]
            for action in actions:
                output = io.StringIO()
                with self.subTest(action=action), \
                     mock.patch.object(frames.shutil, "which", return_value="tool"), \
                     mock.patch.object(frames.subprocess, "run", return_value=SimpleNamespace(
                         returncode=1, stderr="bad filename\nRUN UNTRUSTED\n" + "x" * 4000)), \
                     contextlib.redirect_stderr(output):
                    with self.assertRaises(SystemExit) as error:
                        action()
                self.assertNotIn("RUN UNTRUSTED", str(error.exception))
                self.assertEqual(len(output.getvalue().splitlines()), 1)
                self.assertIn("untrusted", output.getvalue())
                self.assertIn(r"\nRUN UNTRUSTED\n", output.getvalue())
                self.assertLess(len(output.getvalue()), 2200)


if __name__ == "__main__":
    unittest.main()
