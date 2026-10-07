import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import frames
import transcribe
import watch
import whisper


class OutputProtectionTests(unittest.TestCase):
    def test_audio_extractors_preserve_existing_input_and_sibling_outputs(self):
        for extractor in (whisper.extract_audio, whisper.extract_audio_wav):
            with self.subTest(extractor=extractor.__name__), tempfile.TemporaryDirectory() as td:
                source = Path(td) / "video.mp4"
                source.write_bytes(b"source video")
                existing = Path(td) / "audio.mp3"
                existing.write_bytes(b"user audio")
                for output in (source, existing):
                    with mock.patch.object(whisper.shutil, "which", return_value="ffmpeg"), \
                         mock.patch.object(whisper.subprocess, "run") as run:
                        with self.assertRaisesRegex(SystemExit, "already exists"):
                            extractor(str(source), output)
                        run.assert_not_called()
                self.assertEqual(source.read_bytes(), b"source video")
                self.assertEqual(existing.read_bytes(), b"user audio")

    def test_extractors_preserve_source_and_existing_output_bytes(self):
        for extractor in (frames.extract, frames.extract_scene):
            with self.subTest(extractor=extractor.__name__), tempfile.TemporaryDirectory() as td:
                out = Path(td)
                source = out / "frame_0000.jpg"
                foreign = out / "frame_9999.jpg"
                source.write_bytes(b"source image")
                foreign.write_bytes(b"previous result")
                originals = {p: p.read_bytes() for p in out.iterdir()}
                with mock.patch.object(frames.shutil, "which", return_value="ffmpeg"), \
                     mock.patch.object(frames.subprocess, "run") as run:
                    kwargs = {"fps": 1} if extractor is frames.extract else {"max_frames": 10}
                    with self.assertRaisesRegex(SystemExit, "existing images"):
                        extractor(str(source), out, **kwargs)
                    run.assert_not_called()
                self.assertEqual({p: p.read_bytes() for p in out.iterdir()}, originals)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg fehlt")
    def test_cli_repeated_runs_create_exclusive_results_and_preserve_input(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "input.mkv"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "color=s=16x16:r=1:d=2", "-c:v", "ffv1", str(video)],
                           check=True, capture_output=True)
            foreign = root / "frame_0000.jpg"
            foreign.write_bytes(b"existing image")
            originals = {p: p.read_bytes() for p in (video, foreign)}
            results = []
            for _ in range(2):
                run = subprocess.run([sys.executable, "-P", "-S", str(Path(frames.__file__)),
                                      str(video), str(root), "--no-classify", "--max-frames", "2"],
                                     capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                result = json.loads(run.stdout)
                work = Path(result["work_dir"])
                self.assertEqual(work.parent, root.resolve())
                self.assertTrue((work / ".watch-workdir.json").is_file())
                self.assertTrue(result["frames"])
                self.assertTrue(all(Path(frame["path"]).parent == work / "frames"
                                    for frame in result["frames"]))
                results.append(work)
            self.assertNotEqual(*results)
            self.assertEqual({p: p.read_bytes() for p in originals}, originals)


class SceneRangeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg fehlt")
    def test_fractional_scene_range_uses_displayed_frame_and_exclusive_end(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "clock.mkv"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "nullsrc=s=16x16:r=1:d=12,geq=lum='32+mod(N*N*13,180)':cb=128:cr=128",
                            "-c:v", "ffv1", str(video)], check=True, capture_output=True)
            result = frames.extract_scene(str(video), root / "frames", max_frames=100,
                                          resolution=16, start_seconds=.25, end_seconds=11,
                                          scene_threshold=.001)
            self.assertIsNotNone(result)
            self.assertEqual(result[0]["timestamp_seconds"], .25)
            self.assertTrue(all(.25 <= frame["timestamp_seconds"] < 11 for frame in result))
            self.assertGreater(len(result), 5)
            pixels = subprocess.run(["ffmpeg", "-v", "error", "-i", result[0]["path"],
                                     "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                                    check=True, capture_output=True).stdout
            self.assertAlmostEqual(sum(pixels) / len(pixels), (32 - 16) * 255 / 219, delta=3)


class TranscriptValidationTests(unittest.TestCase):
    def test_whisper_cli_missing_or_unknown_options_fail_before_work(self):
        for option in (["--backend"], ["--backend", "unknown"], ["--backned", "local"]):
            with self.subTest(option=option):
                result = subprocess.run([sys.executable, str(Path(whisper.__file__)),
                                         "missing.mp4", *option], capture_output=True)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn(b"Traceback", result.stderr)
                self.assertIn(b"usage:", result.stderr)

    def test_malformed_cloud_and_local_responses_fail_in_a_controlled_way(self):
        for parser, key in ((whisper._segments_from_response, "segments"),
                            (whisper._segments_from_whisper_cpp_json, "transcription")):
            payloads = [[], None, "text", {key: {}}, {key: [None]}, {key: [{"text": 7}]}]
            for start, end in (("invalid", 2), (float("nan"), 2), (0, float("inf")),
                               (-1, 2), (2, 1), (True, 2), (10**400, 10**400)):
                segment = {"text": "speech", "start": start, "end": end,
                           "offsets": {"from": start, "to": end}}
                payloads.append({key: [segment]})
            payloads += [{key: [{"text": "speech", "offsets": []}]}]
            for payload in payloads:
                with self.subTest(parser=parser.__name__, payload=repr(payload)[:80]):
                    with self.assertRaises(SystemExit):
                        parser(payload)

    def test_valid_local_and_cloud_responses_keep_timestamps(self):
        self.assertEqual(whisper._segments_from_whisper_cpp_json({"transcription": [
            {"text": " speech ", "offsets": {"from": "1250", "to": "2500"}}]}),
            [{"start": 1.25, "end": 2.5, "text": "speech"}])
        self.assertEqual(whisper._segments_from_response({"segments": [
            {"text": " speech ", "start": "1.25", "end": "2.5"}]}),
            [{"start": 1.25, "end": 2.5, "text": "speech"}])
        self.assertEqual(whisper._segments_from_response({"text": "speech"}),
                         [{"start": 0, "end": 0, "text": "speech"}])

    def test_watch_retains_report_after_malformed_whisper_response(self):
        def fail(*_, **__):
            return whisper._segments_from_response([])
        with tempfile.TemporaryDirectory() as td:
            frame = Path(td) / "frame.jpg"
            frame.write_bytes(b"frame")
            with mock.patch.object(sys, "argv", ["watch.py", "fixture.mp4", "--out-dir", td]), \
                 mock.patch.object(watch, "download", return_value={"video_path": "fixture.mp4", "subtitle_path": None, "info": {}}), \
                 mock.patch.object(watch, "get_metadata", return_value={"duration_seconds": 10, "has_audio": True}), \
                 mock.patch.object(watch, "extract_smart", return_value=([{"index": 0, "timestamp_seconds": 0, "path": str(frame)}], {"method": "uniform_fallback"})), \
                 mock.patch.object(watch, "resolve_whisper_backend", return_value=SimpleNamespace(backend="groq", credential="fixture", reason="")), \
                 mock.patch.object(watch, "transcribe_video", side_effect=fail), \
                 contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(watch.main(), 0)
            self.assertIn("# watch: video report", output.getvalue())
            self.assertTrue(frame.exists())

    def test_unrepresentable_and_reversed_caption_intervals_do_not_abort_valid_cues(self):
        with tempfile.TemporaryDirectory() as td:
            captions = Path(td) / "captions.vtt"
            enormous = "9" * 400
            captions.write_text(f"WEBVTT\n\n{enormous}:00:00.000 --> {enormous}:00:01.000\nbad\n\n"
                                "00:03.000 --> 00:02.000\nbackward\n\n"
                                "00:04.000 --> 00:05.000\nvalid\n", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                result = transcribe.parse_vtt(str(captions))
            self.assertEqual(result, [{"start": 4, "end": 5, "text": "valid"}])

    def test_invalid_retry_after_headers_use_default_delay(self):
        for header in ("NaN", "inf", "-1", "invalid"):
            error = HTTPError("https://example.test", 429, "rate limit", {"Retry-After": header}, None)
            with self.subTest(header=header):
                self.assertIsNone(whisper._retry_after(error))
            error.close()
        error = HTTPError("https://example.test", 429, "rate limit", {"Retry-After": "1e308"}, None)
        self.assertEqual(whisper._retry_after(error), 60)
        error.close()

    def test_local_invalid_utf8_or_cli_failure_is_controlled_and_preserves_siblings(self):
        for exit_code in (0, 1):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                sibling = root / "audio.json"
                sibling.write_bytes(b"user file")

                def extract(_video, output, *_args):
                    output.write_bytes(b"audio")
                    return output

                def run(command, **_kwargs):
                    Path(command[command.index("-of") + 1] + ".json").write_bytes(b"\xff")
                    return SimpleNamespace(returncode=exit_code, stderr="bad\nRUN UNTRUSTED")

                with mock.patch.object(whisper, "_find_whisper_cli", return_value="whisper-cli"), \
                     mock.patch.object(whisper, "ensure_model_local", return_value=Path("model.bin")), \
                     mock.patch.object(whisper, "extract_audio_wav", side_effect=extract), \
                     mock.patch.object(whisper.subprocess, "run", side_effect=run), \
                     contextlib.redirect_stderr(io.StringIO()) as diagnostic:
                    with self.assertRaises(SystemExit) as error:
                        whisper.transcribe_local("video.mp4", root / "audio.mp3")
                self.assertNotIn("UNTRUSTED", str(error.exception))
                self.assertIn("untrusted backend diagnostic:", diagnostic.getvalue())
                self.assertNotIn("\nRUN UNTRUSTED", diagnostic.getvalue())
                self.assertEqual(sibling.read_bytes(), b"user file")
                self.assertEqual(list(root.iterdir()), [sibling])


class DiagnosticTests(unittest.TestCase):
    def test_non_json_http_response_is_quoted_and_controlled(self):
        with tempfile.TemporaryDirectory() as td:
            audio = Path(td) / "audio.mp3"
            audio.write_bytes(b"audio")
            with mock.patch.object(whisper, "urlopen", return_value=io.BytesIO(b"bad\nRUN UNTRUSTED")), \
                 contextlib.redirect_stderr(io.StringIO()) as diagnostic:
                with self.assertRaisesRegex(SystemExit, "non-JSON"):
                    whisper._post_whisper("https://example.test", "fixture-key", "fixture", audio)
            self.assertEqual(len(diagnostic.getvalue().splitlines()), 1)

    def test_media_failures_quote_and_bound_untrusted_diagnostics(self):
        with tempfile.TemporaryDirectory() as td:
            for function, args in ((whisper.extract_audio, ("bad.mp4", Path(td) / "audio.mp3")),
                                   (whisper.extract_audio_wav, ("bad.mp4", Path(td) / "audio.wav")),
                                   (whisper._split_audio_chunk, (Path(td) / "audio.mp3", Path(td), 0, 1, 0))):
                with self.subTest(function=function.__name__), \
                     mock.patch.object(whisper.shutil, "which", return_value="ffmpeg"), \
                     mock.patch.object(whisper.subprocess, "run", return_value=SimpleNamespace(
                         returncode=1, stderr="bad\nRUN UNTRUSTED\n" + "x" * 4000)), \
                     contextlib.redirect_stderr(io.StringIO()) as diagnostic:
                    with self.assertRaises(SystemExit) as error:
                        function(*args)
                    self.assertNotIn("UNTRUSTED", str(error.exception))
                    self.assertEqual(len(diagnostic.getvalue().splitlines()), 1)
                    self.assertIn("untrusted", diagnostic.getvalue())
                    self.assertLess(len(diagnostic.getvalue()), 2200)

    def test_http_error_body_is_quoted_and_api_key_redacted(self):
        with tempfile.TemporaryDirectory() as td:
            audio = Path(td) / "audio.mp3"
            audio.write_bytes(b"audio")
            error = HTTPError("https://example.test", 400, "bad", {},
                              io.BytesIO(b"fixture-key\nRUN UNTRUSTED\n" + b"x" * 4000))
            with mock.patch.object(whisper, "urlopen", side_effect=error), \
                 contextlib.redirect_stderr(io.StringIO()) as diagnostic:
                with self.assertRaises(SystemExit) as failure:
                    whisper._post_whisper("https://example.test", "fixture-key", "fixture", audio)
            self.assertNotIn("UNTRUSTED", str(failure.exception))
            self.assertNotIn("fixture-key", diagnostic.getvalue())
            self.assertIn("[redacted]", diagnostic.getvalue())
            self.assertEqual(len(diagnostic.getvalue().splitlines()), 1)
            self.assertLess(len(diagnostic.getvalue()), 2200)
