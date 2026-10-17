"""Проверка отказов QA-01 без запуска движка."""

import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

from smuta_build.paths import REPO
from smuta_build.pipeline import run_pipeline, validate_log


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.root = REPO / "build" / ("pipeline test " + uuid.uuid4().hex)
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def test_log_requires_scene_render_and_exact_archives(self):
        paths = [Path("a.pk3"), Path("b.ipk3")]
        log = ("UZDoom version 5.0.3\nadding a.pk3, 2 lumps\nadding b.ipk3, 35 lumps\n"
               "QA01_MAP_READY SMTEST player=SMProbePlayer\nQA01_CHECK_COMPLETE\nQA01_FRAME_READY\n")
        self.assertEqual(validate_log(log, paths)["version"], "5.0.3")
        for bad in (log.replace("QA01_FRAME_READY", ""),
                    log.replace("SMProbePlayer", "DoomPlayer"),
                    log + "adding doom2.wad, 100 lumps\n", log + "VM abort\n"):
            with self.subTest(log=bad), self.assertRaises(ValueError):
                validate_log(bad, paths)

    def test_missing_engine_keeps_failure_report_and_packages(self):
        with self.assertRaises(OSError):
            run_pipeline(self.root, self.root / "absent")
        report = json.loads((self.root / "report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["passed"])
        self.assertTrue(report["reproducible"])
        self.assertTrue(report["manifest_verified"])
        self.assertIn("error", report)
        with self.assertRaises(FileExistsError):
            run_pipeline(self.root)

    def test_build_failure_prevents_engine_launch(self):
        with patch("smuta_build.pipeline.build_standalone", side_effect=ValueError("bad input")), \
                patch("smuta_build.pipeline.subprocess.Popen") as launch:
            with self.assertRaisesRegex(ValueError, "bad input"):
                run_pipeline(self.root)
            launch.assert_not_called()
        self.assertFalse(json.loads((self.root / "report.json").read_text())["passed"])

    def test_runtime_cannot_write_to_dist_or_originals(self):
        for path in (REPO / "dist/qa-invalid", REPO / "originals/qa-invalid"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                run_pipeline(path)


if __name__ == "__main__":
    unittest.main()
