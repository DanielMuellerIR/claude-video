"""Parallele Erstdownloads ausschließlich mit künstlichen Modelldaten prüfen."""
import concurrent.futures
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import whisper


class ModelDownloadTests(unittest.TestCase):
    def test_parallel_first_downloads_publish_complete_models(self):
        with tempfile.TemporaryDirectory() as directory:
            barrier = threading.Barrier(2, timeout=5)
            targets = []
            def download(url, target, progress):
                targets.append(Path(target))
                Path(target).write_bytes(b'complete synthetic model')
                barrier.wait()
            with mock.patch.dict(os.environ, {'WATCH_WHISPER_MODELS_DIR': directory}), \
                 mock.patch.object(whisper.urllib.request, 'urlretrieve', side_effect=download), \
                 concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(whisper.ensure_model_local, 'tiny') for _ in range(2)]
                results = [future.result(timeout=6) for future in futures]
            self.assertEqual(len(set(targets)), 2)
            self.assertEqual(results[0], results[1])
            self.assertEqual(results[0].read_bytes(), b'complete synthetic model')
            self.assertEqual(list(Path(directory).iterdir()), [results[0]])

    def test_failed_download_removes_only_its_own_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            other = Path(directory) / 'other.part'
            other.write_bytes(b'other download')
            def download(url, target, progress):
                Path(target).write_bytes(b'partial')
                raise OSError('synthetic interruption')
            with mock.patch.dict(os.environ, {'WATCH_WHISPER_MODELS_DIR': directory}), \
                 mock.patch.object(whisper.urllib.request, 'urlretrieve', side_effect=download):
                with self.assertRaises(SystemExit):
                    whisper.ensure_model_local('tiny')
            self.assertEqual(list(Path(directory).iterdir()), [other])
            self.assertEqual(other.read_bytes(), b'other download')
