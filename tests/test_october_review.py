import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import frames
import whisper
import watch


class FrameRangeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg fehlt')
    def test_low_rate_range_keeps_the_frame_displayed_at_its_start(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / 'clock.mkv'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            "nullsrc=s=16x16:r=1:d=4,geq=lum='32+40*N':cb=128:cr=128",
                            '-c:v', 'ffv1', str(video)], check=True, capture_output=True)
            for start, end in ((.25, .75), (1.01, 1.9)):
                with self.subTest(start=start):
                    result = frames.extract(str(video), root / 'frames', fps=2,
                                            resolution=16, start_seconds=start, end_seconds=end)
                    for frame in result:
                        pixels = subprocess.run(['ffmpeg', '-v', 'error', '-i', frame['path'],
                                                 '-f', 'rawvideo', '-pix_fmt', 'gray', '-'],
                                                check=True, capture_output=True).stdout
                        expected = (32 + 40 * int(frame['timestamp_seconds']) - 16) * 255 / 219
                        self.assertAlmostEqual(sum(pixels) / len(pixels), expected, delta=3)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg fehlt')
    def test_variable_rate_range_keeps_preceding_source_frames(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); video = root / 'vfr.mkv'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            "nullsrc=s=16x16:r=25:d=4,select='eq(n,0)+eq(n,10)+eq(n,45)+eq(n,75)',geq=lum='32+40*N':cb=128:cr=128",
                            '-fps_mode', 'vfr', '-c:v', 'ffv1', str(video)],
                           check=True, capture_output=True)
            result = frames.extract(str(video), root / 'frames', fps=2, resolution=16,
                                    start_seconds=.45, end_seconds=2.2)
            self.assertEqual([f['timestamp_seconds'] for f in result], [.45, .95, 1.45, 1.95])
            for frame, source_index in zip(result, [1, 1, 1, 2]):
                pixels = subprocess.run(['ffmpeg', '-v', 'error', '-i', frame['path'],
                                         '-f', 'rawvideo', '-pix_fmt', 'gray', '-'],
                                        check=True, capture_output=True).stdout
                expected = (32 + 40 * source_index - 16) * 255 / 219
                self.assertAlmostEqual(sum(pixels) / len(pixels), expected, delta=3)

    def test_bad_cli_options_fail_before_source_probe(self):
        for option in (['--fpps', '.1'], ['--fps']):
            result = subprocess.run([sys.executable, str(Path(frames.__file__)),
                                     'missing-video', 'missing-out', *option], capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn(b'usage:', result.stderr)
            self.assertNotIn(b'Traceback', result.stderr)


class WhisperTransportTests(unittest.TestCase):
    def test_truncated_http_body_retries_then_exits_in_a_controlled_way(self):
        class Socket:
            def makefile(self, *_):
                return io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{}')
        def response(*_, **__):
            result = http.client.HTTPResponse(Socket())
            result.begin()
            return result
        with tempfile.TemporaryDirectory() as td:
            audio = Path(td) / 'audio.mp3'; audio.write_bytes(b'fixture')
            with mock.patch.object(whisper, 'urlopen', side_effect=response) as request, \
                 mock.patch.object(whisper.time, 'sleep'), \
                 mock.patch.object(whisper, 'MAX_ATTEMPTS', 2):
                with self.assertRaises(SystemExit):
                    whisper._post_whisper('https://example.test', 'fixture-key', 'fixture', audio)
            self.assertEqual(request.call_count, 2)

    def test_watch_keeps_frames_and_report_after_transport_retries_fail(self):
        def fail(*_, **__):
            with mock.patch.object(whisper, 'urlopen', side_effect=http.client.IncompleteRead(b'{}', 98)), \
                 mock.patch.object(whisper.time, 'sleep'), mock.patch.object(whisper, 'MAX_ATTEMPTS', 2):
                return whisper._post_whisper('https://example.test', 'fixture', 'fixture', audio)
        with tempfile.TemporaryDirectory() as td:
            audio = Path(td) / 'audio.mp3'; audio.write_bytes(b'fixture')
            frame = Path(td) / 'frame.jpg';frame.write_bytes(b'frame')
            with mock.patch.object(sys, 'argv', ['watch.py', 'fixture.mp4', '--out-dir', td]), \
                 mock.patch.object(watch, 'download', return_value={'video_path': 'fixture.mp4', 'subtitle_path': None, 'info': {}}), \
                 mock.patch.object(watch, 'get_metadata', return_value={'duration_seconds': 10, 'has_audio': True}), \
                 mock.patch.object(watch, 'extract_smart', return_value=([{'index': 0, 'timestamp_seconds': 0, 'path': str(frame)}], {'method': 'uniform_fallback'})), \
                 mock.patch.object(watch, 'resolve_whisper_backend', return_value=type('Resolution', (), {'backend': 'groq', 'credential': 'fixture', 'reason': ''})()), \
                 mock.patch.object(watch, 'transcribe_video', side_effect=fail), \
                 contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(watch.main(), 0)
            self.assertIn('# watch: video report', output.getvalue())
            self.assertTrue(frame.exists())
            self.assertEqual(len(list(Path(td).glob('watch-*'))), 1)

    def test_alias_requires_cpp_flags_and_returns_the_probed_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            binary = root / 'whisper'
            binary.write_text('#!/bin/sh\necho "--model --output_dir --language"\n')
            binary.chmod(0o700)
            with mock.patch.dict(os.environ, {'PATH': str(root)}):
                self.assertIsNone(whisper._find_whisper_cli())
                binary.write_text('#!/bin/sh\necho "-m MODEL -of FILE -oj -t N -l LANG"\n')
                self.assertEqual(whisper._find_whisper_cli(), str(binary))

class SkillPackageTests(unittest.TestCase):
    def test_zip_failures_and_silent_noop_cannot_publish_development_files(self):
        source = Path(__file__).resolve().parents[1]
        for zip_status in (None, 0, 12):
            with self.subTest(zip_status=zip_status), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                files = ['SKILL.md', 'AGENTS.md', 'CLAUDE.md', 'scripts/build-skill.sh',
                         'scripts/frames.py', 'hooks/config.json', 'commands/watch.md',
                         '.claude-plugin/plugin.json', '.codex-plugin/plugin.json']
                for relative in files:
                    target = root / relative; target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes((source / relative).read_bytes() if (source / relative).is_file()
                                       else b'fixture')
                environment = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
                def git(*args):
                    subprocess.run(['git', '-C', str(root), '-c', 'core.hooksPath=/dev/null',
                                    '-c', 'commit.gpgsign=false', '-c', 'user.name=Fixture',
                                    '-c', 'user.email=fixture@example.test', *args],
                                   env=environment, check=True, capture_output=True)
                git('init', '-q');git('add', *files);git('commit', '-qm', 'fixture')
                if zip_status is not None:
                    shim = root / 'shim';shim.mkdir()
                    binary = shim / 'zip';binary.write_text(f'#!/bin/sh\nexit {zip_status}\n');binary.chmod(0o700)
                    environment['PATH'] = str(shim) + os.pathsep + environment['PATH']
                result = subprocess.run(['bash', str(root / 'scripts/build-skill.sh')],
                                        cwd=root, env=environment, capture_output=True)
                if zip_status is None:
                    self.assertEqual(result.returncode, 0, result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn(b'built ', result.stdout)
