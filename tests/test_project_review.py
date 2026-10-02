"""Regressionen fuer Zeitbezug, lokale Ausgabe und Plugin-Pfade."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
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
import workdir
import watch


class CaptionTimelineTests(unittest.TestCase):
    def test_separate_repetitions_do_not_fill_silent_gap(self):
        for second_text in ("Yes", "Yes please"):
            with self.subTest(text=second_text):
                segments = transcribe._dedupe([
                    {"start": 0, "end": 1, "text": "Yes"},
                    {"start": 60, "end": 61, "text": second_text},
                ])
                self.assertEqual(len(segments), 2)
                self.assertEqual(transcribe.filter_range(segments, 30, 31), [])
                self.assertEqual(transcribe.filter_range(segments, 60, 61)[0]["start"], 60)

    def test_overlapping_duplicate_preserves_full_interval_and_input(self):
        source = [{"start": 0, "end": 10, "text": "Yes"},
                  {"start": 1, "end": 2, "text": "Yes"}]
        self.assertEqual(transcribe._dedupe(source), [source[0]])
        self.assertEqual(source[0]["end"], 10)

    def test_character_references_are_decoded_after_markup(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "captions.vtt"
            path.write_text("WEBVTT\n\n00:00.000 --> 00:01.000\n"
                            "<b>Tom &amp; Jerry</b> &lt;3 &gt;0&nbsp;!\n", encoding="utf-8")
            self.assertEqual(transcribe.parse_vtt(str(path))[0]["text"],
                             "Tom & Jerry <3 >0\u00a0!")


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg fehlt")
class FrameTimelineTests(unittest.TestCase):
    @staticmethod
    def make_video(path, source):
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", source,
                        "-c:v", "ffv1", str(path)], check=True, capture_output=True)

    def test_sparse_sampling_matches_content_to_timestamp(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "clock.mkv"
            self.make_video(video, "nullsrc=s=16x16:r=1:d=30,geq=lum='32+4*N':cb=128:cr=128")
            result = frames.extract(str(video), root / "frames", fps=0.1, resolution=16)
            self.assertEqual([f["timestamp_seconds"] for f in result], [0, 10, 20])
            for frame in result:
                pixels = subprocess.run(["ffmpeg", "-v", "error", "-i", frame["path"],
                                         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                                        check=True, capture_output=True).stdout
                # Videobereich 16..235 wird bei Graustufen nach 0..255 skaliert.
                expected = (32 + 4 * frame["timestamp_seconds"] - 16) * 255 / 219
                self.assertAlmostEqual(sum(pixels) / len(pixels), expected, delta=3)

    def test_subsecond_video_produces_first_frame(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "short.mkv"
            self.make_video(video, "color=c=blue:s=16x16:r=25:d=0.1")
            result = frames.extract(str(video), root / "frames", fps=2, resolution=16)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["timestamp_seconds"], 0)

    def test_fractional_start_keeps_sampling_timeline_origin(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "clock.mkv"
            self.make_video(video, "nullsrc=s=16x16:r=25:d=3,geq=lum='32+N':cb=128:cr=128")
            result = frames.extract(str(video), root / "frames", fps=2, resolution=16,
                                    start_seconds=1.01, end_seconds=2.51)
            self.assertEqual(len(result), 3)
            self.assertEqual(result[0]["timestamp_seconds"], 1.01)
            for frame in result:
                pixels = subprocess.run(["ffmpeg", "-v", "error", "-i", frame["path"],
                                         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                                        check=True, capture_output=True).stdout
                expected = (32 + 25 * frame["timestamp_seconds"] - 16) * 255 / 219
                self.assertAlmostEqual(sum(pixels) / len(pixels), expected, delta=3)

    def test_fractional_end_keeps_last_sample_inside_range(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "fixture.mkv"
            self.make_video(video, "color=c=blue:s=16x16:r=25:d=3")
            result = frames.extract(str(video), root / "frames", fps=2, resolution=16,
                                    start_seconds=0, end_seconds=1.2)
            self.assertEqual([f["timestamp_seconds"] for f in result], [0, 0.5, 1])

    @unittest.skipUnless(shutil.which("ffprobe"), "ffprobe fehlt")
    def test_standalone_end_is_clamped_before_sampling(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "ten-seconds.mkv"
            self.make_video(video, "color=c=blue:s=16x16:r=10:d=10")
            result = subprocess.run([
                sys.executable, str(ROOT / "scripts/frames.py"), str(video), str(root / "frames"),
                "--end", "3600", "--resolution", "16", "--no-classify",
            ], capture_output=True, text=True, check=True)
            report = json.loads(result.stdout)
            self.assertEqual(len(report["frames"]), 20)
            self.assertEqual(report["target"], 20)
            self.assertEqual(report["fps"], 2)


class NumericInputTests(unittest.TestCase):
    def test_nonfinite_time_and_fps_are_rejected(self):
        for value in ("nan", "inf", "-inf"):
            with self.subTest(value=value):
                with self.assertRaises(SystemExit):
                    frames.parse_time(value)
                with self.assertRaises(ValueError):
                    frames.sampling_plan(10, False, 100, float(value))
                with self.assertRaises(argparse.ArgumentTypeError):
                    watch._positive_float(value)


class LocalOutputTests(unittest.TestCase):
    def test_local_transcription_does_not_reuse_or_delete_sibling_files(self):
        for produce_json in (False, True):
            with self.subTest(produce_json=produce_json), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                audio = root / "audio.mp3"
                sibling_json = root / "audio.json"
                sibling_wav = root / "audio.wav"
                payload = {"transcription": [{"offsets": {"from": 0, "to": 1000}, "text": "fresh"}]}
                sibling_json.write_text(json.dumps(payload).replace("fresh", "stale"))
                sibling_wav.write_bytes(b"user audio")
                originals = {p: p.read_bytes() for p in (sibling_json, sibling_wav)}

                def extract(_video, path, *_args):
                    path.write_bytes(b"extracted audio")
                    return path

                def run(command, **_kwargs):
                    if produce_json:
                        Path(command[command.index("-of") + 1] + ".json").write_text(json.dumps(payload))
                    return SimpleNamespace(returncode=0, stderr="")

                with mock.patch.object(whisper, "_find_whisper_cli", return_value="whisper-cli"), \
                     mock.patch.object(whisper, "ensure_model_local", return_value=root / "model.bin"), \
                     mock.patch.object(whisper, "extract_audio_wav", side_effect=extract), \
                     mock.patch.object(whisper.subprocess, "run", side_effect=run), \
                     contextlib.redirect_stderr(io.StringIO()):
                    if produce_json:
                        segments, _ = whisper.transcribe_local("video.mp4", audio, 10, 11)
                        self.assertEqual(segments, [{"start": 10, "end": 11, "text": "fresh"}])
                    else:
                        with self.assertRaisesRegex(SystemExit, "no JSON"):
                            whisper.transcribe_local("video.mp4", audio)
                self.assertEqual({p: p.read_bytes() for p in originals}, originals)
                self.assertEqual(set(root.iterdir()), set(originals))


class HookAndCleanupTests(unittest.TestCase):
    def test_hook_command_accepts_spaces_in_plugin_path(self):
        config = json.loads((ROOT / "hooks/hooks.json").read_text())
        command = config["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        with tempfile.TemporaryDirectory(prefix="watch plugin ") as td:
            path = Path(td) / "hooks/scripts/check-setup.sh"
            path.parent.mkdir(parents=True)
            path.write_text("printf 'hook reached'\n")
            result = subprocess.run(["bash", "-c", command], capture_output=True, text=True,
                                    env={**os.environ, "CLAUDE_PLUGIN_ROOT": td})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "hook reached")

    def test_invalid_marker_is_refused_without_deleting_directory(self):
        for content in (b"[]", b"null", b"true", b"\xff"):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as td:
                work = workdir.create_work_dir(td)
                (work / workdir.MARKER_NAME).write_bytes(content)
                self.assertFalse(workdir.is_owned_work_dir(work))
                with self.assertRaisesRegex(ValueError, "unowned"):
                    workdir.cleanup_work_dir(work)
                self.assertTrue(work.exists())


if __name__ == "__main__":
    unittest.main()
