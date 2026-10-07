"""Zeitbezug, Captions/Whisper und Fehlerfälle der Textframe-Transkripte."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import textframes


class TranscriptTimelineTests(unittest.TestCase):
    def test_overlapping_and_unsorted_segments_embed_each_frame_once_in_order(self):
        segments = [{"start": 2, "end": 5, "text": "second"},
                    {"start": 0, "end": 3, "text": "first"}]
        images = [{"time_sec": t, "timestamp": textframes.fmt_ts(t), "frame": f"frames/{i}.jpg", "text": f"image {i}"}
                  for i, t in enumerate([6, 2, 0, 1])]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            textframes.write_transcript(segments, images, root, "fixture", {"status": "completed"})
            output = (root / "transkript.md").read_text()
            headings = [line for line in output.splitlines() if line.startswith("## ")]
            self.assertEqual(headings, [
                "## 00:00:00.000 — Text frame", "## 00:00:00.000–00:00:03.000 — Speech",
                "## 00:00:01.000 — Text frame", "## 00:00:02.000 — Text frame",
                "## 00:00:02.000–00:00:05.000 — Speech", "## 00:00:06.000 — Text frame",
            ])
            for image in images:
                self.assertEqual(output.count(f"]({image['frame']})"), 1)

    def test_empty_ocr_or_speech_and_fence_payloads_are_supported(self):
        for segments, images in (([], []), ([{"start": 0, "end": 1, "text": "```\n# injected"}], []),
                                  ([], [{"time_sec": .5, "timestamp": "00:00:00.500", "frame": "frames/a.jpg", "text": "```\n# injected"}])):
            with self.subTest(segments=segments), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                textframes.write_transcript(segments, images, root, "source\n```\n# injected", {"status": "unavailable"})
                output = (root / "transkript.md").read_text()
                self.assertNotIn("\n# injected", output)
                self.assertEqual(json.loads((root / "transkript.json").read_text()), segments)

    def test_invalid_intervals_are_rejected_and_input_is_not_mutated(self):
        source = [{"start": 2, "end": 3, "text": "later"}, {"start": 0, "end": 2, "text": "earlier"}]
        self.assertEqual(textframes._validated_segments(source), list(reversed(source)))
        self.assertEqual(source[0]["start"], 2)
        for start, end in ((float("nan"), 1), (0, float("inf")), (2, 1), (-1, 1)):
            with self.subTest(start=start), self.assertRaises(ValueError):
                textframes._validated_segments([{"start": start, "end": end, "text": "bad"}])


class TranscriptSourceTests(unittest.TestCase):
    def test_native_captions_take_precedence_even_with_no_whisper(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); captions = root / "captions.vtt"
            captions.write_text("WEBVTT\n\n00:00.000 --> 00:02.000\nFirst\n\n00:02.000 --> 00:04.000\nSecond\n")
            with mock.patch.object(textframes, "resolve_whisper_backend") as resolve:
                segments, stats = textframes.load_transcript({"subtitle_path": str(captions)}, root, None, True)
                resolve.assert_not_called()
            self.assertEqual([seg["text"] for seg in segments], ["First", "Second"])
            self.assertEqual(stats["source"], "captions")

    def test_shared_whisper_receives_backend_and_absolute_segments(self):
        resolution = SimpleNamespace(reason=None, backend="local", credential="local")
        source = [{"start": .5, "end": 1.5, "text": "speech"}]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch.object(textframes, "get_metadata", return_value={"has_audio": True}), \
                 mock.patch.object(textframes, "resolve_whisper_backend", return_value=resolution) as resolve, \
                 mock.patch.object(textframes, "transcribe_video", return_value=(source, "local")) as transcribe:
                segments, stats = textframes.load_transcript({"video_path": "fixture.mp4"}, root, "local", False)
            resolve.assert_called_once_with("local")
            transcribe.assert_called_once_with("fixture.mp4", root / "audio.mp3", backend="local", api_key="local")
            self.assertEqual(segments, source)
            self.assertEqual(stats["source"], "whisper (local)")

    def test_audio_free_video_or_disabled_whisper_never_resolves_backend(self):
        for no_whisper in (False, True):
            with mock.patch.object(textframes, "get_metadata", return_value={"has_audio": False}), \
                 mock.patch.object(textframes, "resolve_whisper_backend") as resolve:
                segments, stats = textframes.load_transcript({"video_path": "fixture"}, Path("work"), None, no_whisper)
                resolve.assert_not_called()
            self.assertEqual(segments, [])
            self.assertEqual(stats["status"], "unavailable")

    def test_whisper_failure_retains_frame_only_transcript_and_marks_failure(self):
        resolution = SimpleNamespace(reason=None, backend="local", credential="local")
        with mock.patch.object(textframes, "get_metadata", return_value={"has_audio": True}), \
             mock.patch.object(textframes, "resolve_whisper_backend", return_value=resolution), \
             mock.patch.object(textframes, "transcribe_video", side_effect=SystemExit("failed\n```\nremote text")), \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            segments, stats = textframes.load_transcript({"video_path": "fixture"}, Path("work"), None, False)
        self.assertEqual(segments, [])
        self.assertEqual(stats["status"], "failed")
        self.assertEqual(len(stderr.getvalue().splitlines()), 1)
        self.assertIn("untrusted", stderr.getvalue())

    def test_invalid_cli_combinations_fail_before_ocr(self):
        for options in (("--whisper", "local"), ("--no-whisper",), ("--transcript", "--no-whisper", "--whisper", "local")):
            result = subprocess.run([sys.executable, str(ROOT / "scripts/textframes.py"), "missing", *options],capture_output=True,text=True)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
