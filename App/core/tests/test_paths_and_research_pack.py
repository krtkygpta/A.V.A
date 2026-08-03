"""Tests for user path resolution and research L3+ packaging."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# App root on path
APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from utils.paths import (  # noqa: E402
    get_desktop,
    resolve_location,
    research_output_dir,
)
from functions.web import deep_research as dr  # noqa: E402
from functions.productivity import document_creator as dc  # noqa: E402


class PathResolveTests(unittest.TestCase):
    def test_desktop_alias_is_absolute_not_cwd(self):
        p = resolve_location("desktop", create=False)
        self.assertTrue(p.is_absolute())
        self.assertNotEqual(p, (Path.cwd() / "desktop").resolve())
        # Should match real desktop (or OneDrive desktop)
        desk = get_desktop()
        self.assertEqual(p.resolve(), desk.resolve())

    def test_desktop_case_insensitive(self):
        a = resolve_location("Desktop", create=False)
        b = resolve_location("DESKTOP", create=False)
        self.assertEqual(a.resolve(), b.resolve())

    def test_desktop_subdir(self):
        p = resolve_location("desktop/AVA_Research", create=True)
        self.assertTrue(p.is_absolute())
        self.assertEqual(p.name, "AVA_Research")
        self.assertEqual(p.parent.resolve(), get_desktop().resolve())

    def test_userprofile_env(self):
        up = os.environ.get("USERPROFILE")
        if not up:
            self.skipTest("no USERPROFILE")
        p = resolve_location(r"%USERPROFILE%\Desktop", create=False)
        self.assertTrue(str(p).lower().endswith("desktop") or "onedrive" in str(p).lower())

    def test_save_text_goes_to_real_desktop(self):
        name = f"ava_path_test_{os.getpid()}.txt"
        result = json.loads(dc.save_text("hello-path-test", name, "desktop"))
        self.assertEqual(result["status"], "success")
        path = Path(result["path"])
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent.resolve(), get_desktop().resolve())
        self.assertEqual(path.read_text(encoding="utf-8"), "hello-path-test")
        try:
            path.unlink()
        except OSError:
            pass

    def test_research_output_dir_under_desktop(self):
        d = research_output_dir()
        self.assertTrue(d.is_dir())
        self.assertEqual(d.name, "AVA_Research")
        self.assertEqual(d.parent.resolve(), get_desktop().resolve())


class ResearchPackTests(unittest.TestCase):
    def test_make_summary_short(self):
        report = (
            "Executive answer about quantum widgets.\n\n"
            "They are useful in labs and industry for measurement.\n\n"
            "Sources:\n- https://example.com/a\n- https://example.com/b\n"
        )
        s = dr._make_summary(report, max_chars=200)
        self.assertLessEqual(len(s), 210)
        self.assertIn("quantum", s.lower())
        self.assertNotIn("https://example.com", s)

    def test_level3_returns_summary_not_full_report(self):
        long_report = "FINDING: " + ("important detail " * 400)
        fake = {
            "status": "success",
            "content": long_report,
            "meta": {
                "level": 3,
                "label": "standard",
                "sources": ["https://a.example", "https://b.example"],
                "confidence": "high",
                "rounds": 4,
                "searches": 3,
                "pages_fetched": 2,
                "pdfs_read": 0,
                "model": "test",
            },
        }

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return fake

        with patch.object(dr.requests, "post", return_value=_Resp()):
            out = json.loads(dr.research("quantum widgets overview", level=3))

        self.assertEqual(out["status"], "success")
        self.assertIn("summary", out)
        self.assertNotIn("report", out)
        self.assertTrue(out.get("report_path"))
        self.assertLess(len(out["summary"]), len(long_report))
        # Full text should be on disk
        path = Path(out["report_path"])
        self.assertTrue(path.is_file())
        disk = path.read_text(encoding="utf-8")
        self.assertIn("FINDING:", disk)
        try:
            path.unlink()
        except OSError:
            pass

    def test_level1_still_returns_report(self):
        fake = {
            "status": "success",
            "content": "Short answer.",
            "meta": {
                "level": 1,
                "label": "quick",
                "sources": [],
                "confidence": "medium",
                "rounds": 1,
                "searches": 1,
                "pages_fetched": 0,
                "pdfs_read": 0,
            },
        }

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return fake

        with patch.object(dr.requests, "post", return_value=_Resp()):
            out = json.loads(dr.research("what time is leap second", level=1))
        self.assertIn("report", out)
        self.assertEqual(out["report"], "Short answer.")
        self.assertNotIn("report_path", out)


if __name__ == "__main__":
    unittest.main()
