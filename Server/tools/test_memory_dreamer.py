"""Unit tests for interval dreamer helpers (no live LLM)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Allow `python Server/tools/test_memory_dreamer.py` from repo root
_SERVER = Path(__file__).resolve().parent.parent
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

from memorySystem import memoryDreamer as md  # noqa: E402


class TestDreamerParse(unittest.TestCase):
    def test_parse_labelled(self):
        raw = """memories:
[THINGS I REMEMBER]
likes coffee	preference	0.9

behaviours:
- often codes at night (0.4)
"""
        parsed = md._parse_dream_output(raw)
        self.assertIsNotNone(parsed)
        memories, behaviours = parsed
        self.assertIn("likes coffee", memories)
        self.assertIn("codes at night", behaviours)

    def test_parse_fail_keeps_none(self):
        self.assertIsNone(md._parse_dream_output("sorry I cannot help"))
        self.assertIsNone(md._parse_dream_output(""))


class TestDreamerCursor(unittest.TestCase):
    def test_collect_and_advance_only_on_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            conv_dir = tmp_path / "conversations"
            mem_dir = tmp_path / "memories"
            conv_dir.mkdir()
            mem_dir.mkdir()

            conv_id = "20260802_120000"
            (conv_dir / f"{conv_id}.json").write_text(
                json.dumps(
                    {
                        "id": conv_id,
                        "messages": [
                            {"role": "user", "content": "My brother is Vinayak"},
                            {
                                "role": "assistant",
                                "content": "Got it, I'll remember that.",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (mem_dir / "last_checked.txt").write_text(
                "00000000_000000", encoding="utf-8"
            )
            (mem_dir / "memories.txt").write_text(
                "[THINGS I REMEMBER]\n(none)\n", encoding="utf-8"
            )
            (mem_dir / "behaviours.txt").write_text("(none)\n", encoding="utf-8")

            with patch.object(md, "_CONVERSATIONS", conv_dir), patch.object(
                md, "_MEMORIES_DIR", mem_dir
            ), patch.object(md, "_LAST_CHECK_FILE", mem_dir / "last_checked.txt"), patch.object(
                md, "_MEMORIES_FILE", mem_dir / "memories.txt"
            ), patch.object(
                md, "_BEHAVIOURS_FILE", mem_dir / "behaviours.txt"
            ), patch.object(
                md,
                "periodicMemory",
                return_value=False,
            ):
                status = md.run_dream_once()
                self.assertEqual(status["status"], "parse_failed")
                self.assertEqual(
                    (mem_dir / "last_checked.txt").read_text(encoding="utf-8").strip(),
                    "00000000_000000",
                )

            with patch.object(md, "_CONVERSATIONS", conv_dir), patch.object(
                md, "_MEMORIES_DIR", mem_dir
            ), patch.object(md, "_LAST_CHECK_FILE", mem_dir / "last_checked.txt"), patch.object(
                md, "_MEMORIES_FILE", mem_dir / "memories.txt"
            ), patch.object(
                md, "_BEHAVIOURS_FILE", mem_dir / "behaviours.txt"
            ), patch.object(
                md,
                "periodicMemory",
                return_value=True,
            ):
                status = md.run_dream_once()
                self.assertEqual(status["status"], "dreamed")
                self.assertEqual(
                    (mem_dir / "last_checked.txt").read_text(encoding="utf-8").strip(),
                    conv_id,
                )


if __name__ == "__main__":
    unittest.main()
