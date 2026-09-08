"""Zeitliche Abdeckung, Transkriptgrenzen und Klassifikationsfehler."""
import contextlib
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import frames
import whisper


class ReviewFollowupTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg fehlt')
    def test_fps_override_covers_entire_static_video(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / 'fixture.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                'color=c=blue:s=64x64:r=10:d=30', '-pix_fmt', 'yuv420p', str(video)], check=True)
            fps, scene_budget = frames.sampling_plan(30, False, 100, 2.0)
            output, stats = frames.extract_smart(str(video), root / 'frames', fps=fps,
                max_frames=scene_budget, fallback_max_frames=100, resolution=64, no_classify=True)
            self.assertEqual(stats['method'], 'uniform_fallback')
            self.assertEqual(len(output), 60)
            self.assertGreaterEqual(output[-1]['timestamp_seconds'], 29)

    def test_adjacent_repeated_segments_are_not_merged(self):
        result = whisper._merge_overlap_segments(
            [{'start': 100, 'end': 101, 'text': 'Yes'}],
            [{'start': 101, 'end': 102, 'text': 'Yes'}], 100, 105)
        self.assertEqual(len(result), 2)

    def test_classifier_failure_timeout_partial_and_success(self):
        cases = [([SimpleNamespace(returncode=2, stdout='VERWERFEN')], 'failed', 0),
                 ([subprocess.TimeoutExpired('fixture', 1)], 'failed', 0),
                 ([SimpleNamespace(returncode=0, stdout='')], 'failed', 0),
                 ([SimpleNamespace(returncode=0, stdout='VERWERFEN'),
                   SimpleNamespace(returncode=2, stdout='')], 'partial', 1),
                 ([SimpleNamespace(returncode=0, stdout='NÜTZLICH')] * 3, 'complete', 0)]
        for replies, expected, deleted in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as td:
                items = []
                for i in range(3):
                    path = Path(td) / f'{i}.jpg'
                    path.write_bytes(b'fixture')
                    items.append({'path': str(path), 'timestamp_seconds': i})
                stats = {}
                with mock.patch.multiple(frames, _LLM_RUN='fixture.py', _LLM_HOST='fixture'), \
                     mock.patch.object(frames.subprocess, 'run', side_effect=replies) as run, \
                     contextlib.redirect_stderr(io.StringIO()):
                    kept, count, removed, complete = frames.classify_frames(items, stats=stats)
                self.assertEqual(stats['classification_status'], expected)
                self.assertEqual(complete, expected == 'complete')
                self.assertEqual(removed, deleted)
                self.assertEqual(count, 3 - deleted)
                self.assertEqual(run.call_count, len(replies))
                self.assertTrue(all(Path(item['path']).exists() for item in kept))

    def test_missing_helper_is_an_error_in_real_subprocess(self):
        with tempfile.TemporaryDirectory() as td:
            image = Path(td) / 'fixture.jpg'
            image.write_bytes(b'fixture')
            stats = {}
            with mock.patch.multiple(frames, _LLM_RUN=str(Path(td) / 'missing.py'), _LLM_HOST='fixture'):
                result = frames.classify_frames([{'path': str(image)}], stats=stats)
            self.assertFalse(result[3])
            self.assertEqual(stats['classification_status'], 'failed')
            self.assertTrue(image.exists())
