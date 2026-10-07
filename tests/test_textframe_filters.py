"""Overlay-Filter und optionale Klassifikation ohne Netzwerk absichern."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import textframes


def line(text, box=(.1, .5, .7, .05)):
    return {"text": text, "box": box}


def frame(timestamp, *lines):
    return {"path": Path(f"{timestamp}.jpg"), "time": timestamp, "lines": list(lines)}


class OverlayTests(unittest.TestCase):
    def test_repeated_footer_is_removed_while_slide_title_and_changes_remain(self):
        source = [frame(i, line("Stable title"), line(f"Value {i}"),
                        line("example.com", (.8, .02, .18, .03))) for i in range(4)]
        result, stats = textframes.filter_overlays(source)
        self.assertEqual(len(result), 4)
        self.assertEqual(stats["removed_lines"], 4)
        self.assertEqual([f["lines"] for f in result], [f["lines"][:2] for f in source])
        self.assertEqual(len(source[0]["lines"]), 3)

    def test_one_off_border_content_and_nonborder_repetition_are_retained(self):
        source = [frame(i, line("Stable body text"), line(f"Footnote {i}", (.1, .02, .5, .03)))
                  for i in range(4)]
        self.assertEqual(textframes.filter_overlays(source)[0], source)
        few = [frame(i, line("small sample", (.1, .02, .5, .03))) for i in range(2)]
        self.assertEqual(textframes.filter_overlays(few)[0], few)

    def test_watermark_only_frames_and_pure_promotions_are_removed(self):
        source = [frame(i, line("example.com", (.8, .02, .18, .03))) for i in range(3)]
        source += [frame(3, line("LIKE AND SUBSCRIBE")), frame(4, line("GEFÄLLT"), line("MIR"))]
        result, stats = textframes.filter_overlays(source, .5)
        self.assertEqual(result, [])
        self.assertEqual(stats["removed_frames"], 5)

    def test_promo_overlay_does_not_delete_substantive_text(self):
        source = frame(0, line("Actual slide content"), line("Kanal abonnieren"))
        result, _ = textframes.filter_overlays([source])
        self.assertEqual(result[0]["lines"], [source["lines"][0]])
        for text in ("The subscribe method returns a token", "Glocke als Musikinstrument", "Subscribe API usage"):
            self.assertFalse(textframes.is_promo(text))


class ClassifierTests(unittest.TestCase):
    def setUp(self):
        self.source = [frame(i, line(f"Content {i}")) for i in range(3)]

    def test_missing_or_disabled_configuration_does_not_launch_helper(self):
        for env, disabled in (({}, False), ({"LLM_RUN": "helper.py"}, False),
                              ({"LLM_RUN": "helper.py", "LLM_HOST": "vision-host"}, True)):
            with self.subTest(env=env), mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(textframes.subprocess, "run") as run:
                result, stats = textframes.classify_textframes(self.source, disabled)
                self.assertEqual(result, self.source)
                self.assertEqual(stats["status"], "disabled" if disabled else "not_configured")
                run.assert_not_called()

    def test_strict_decisions_and_model_configuration(self):
        replies = [SimpleNamespace(returncode=0, stdout=reply) for reply in ("KEEP", "DROP", "KEEP")]
        with mock.patch.dict(os.environ, {"LLM_RUN": "helper.py", "LLM_HOST": "vision-host", "LLM_MODEL": "fixture-model"}, clear=True), \
             mock.patch.object(textframes.subprocess, "run", side_effect=replies) as run:
            result, stats = textframes.classify_textframes(self.source)
        self.assertEqual(result, [self.source[0], self.source[2]])
        self.assertEqual(stats, {"status": "completed", "checked": 3, "removed": 1})
        self.assertIn("fixture-model", run.call_args.args[0])
        self.assertIn("vision-host", run.call_args.args[0])

    def test_failures_and_ambiguous_responses_retain_unchecked_frames(self):
        failures = [SimpleNamespace(returncode=2, stdout=""),
                    SimpleNamespace(returncode=0, stdout="Do not DROP"),
                    OSError("cannot start"), subprocess.TimeoutExpired("helper", 60)]
        for failure in failures:
            with self.subTest(failure=failure), \
                 mock.patch.dict(os.environ, {"LLM_RUN": "helper.py", "LLM_HOST": "vision-host"}, clear=True), \
                 mock.patch.object(textframes.subprocess, "run", side_effect=[SimpleNamespace(returncode=0, stdout="DROP"), failure]), \
                 contextlib.redirect_stderr(io.StringIO()):
                result, stats = textframes.classify_textframes(self.source)
            self.assertEqual(result, self.source[1:])
            self.assertEqual(stats["status"], "partial")
            self.assertEqual(stats["checked"], 1)

    def test_initial_failure_keeps_all_textframes(self):
        with mock.patch.dict(os.environ, {"LLM_RUN": "helper.py", "LLM_HOST": "vision-host"}, clear=True), \
             mock.patch.object(textframes.subprocess, "run", side_effect=OSError()), \
             contextlib.redirect_stderr(io.StringIO()):
            result, stats = textframes.classify_textframes(self.source)
        self.assertEqual(result, self.source)
        self.assertEqual(stats["status"], "failed")


if __name__ == "__main__":
    unittest.main()
