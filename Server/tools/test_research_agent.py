"""Unit tests for research agent levels + fetch helpers (no live network)."""

from __future__ import annotations

import json
import unittest

from tools.research import HARD_MAX_CONTEXT_TOKENS, LEVELS, ResearchAgent, resolve_level
from tools.research_fetch import (
    approx_tokens,
    extract_html_text,
    extract_pdf_text,
    truncate_text,
)


class LevelTests(unittest.TestCase):
    def test_levels_exist_and_ordered(self):
        self.assertEqual(sorted(LEVELS.keys()), [1, 2, 3, 4, 5])
        # Budgets should generally increase with level
        for a, b in zip(range(1, 5), range(2, 6)):
            self.assertLessEqual(LEVELS[a].max_searches, LEVELS[b].max_searches)
            self.assertLessEqual(LEVELS[a].max_context_tokens, LEVELS[b].max_context_tokens)

    def test_hard_cap_100k(self):
        for cfg in LEVELS.values():
            self.assertLessEqual(cfg.max_context_tokens, HARD_MAX_CONTEXT_TOKENS)
        self.assertEqual(LEVELS[5].max_context_tokens, HARD_MAX_CONTEXT_TOKENS)

    def test_resolve_level_clamp(self):
        self.assertEqual(resolve_level(0).level, 1)
        self.assertEqual(resolve_level(99).level, 5)
        self.assertEqual(resolve_level("2").level, 2)
        self.assertEqual(resolve_level("nope").level, 3)
        self.assertEqual(resolve_level(None).level, 3)

    def test_level1_no_pdfs(self):
        self.assertEqual(LEVELS[1].max_pdfs, 0)
        self.assertGreaterEqual(LEVELS[4].max_pdfs, 1)


class FetchHelperTests(unittest.TestCase):
    def test_truncate(self):
        self.assertEqual(truncate_text("hi", 100), "hi")
        out = truncate_text("abcdefghij" * 50, 40)
        self.assertLessEqual(len(out), 40 + 5)
        self.assertIn("truncated", out)

    def test_approx_tokens(self):
        self.assertEqual(approx_tokens(""), 0)
        self.assertGreaterEqual(approx_tokens("a" * 40), 10)

    def test_extract_html_strips_scripts(self):
        html = """
        <html><head><title>Hello</title>
        <script>evil()</script><style>.x{}</style></head>
        <body>
          <nav>Home About</nav>
          <article><h1>Main</h1><p>Useful content here.</p></article>
          <footer>© 2026</footer>
        </body></html>
        """
        parsed = extract_html_text(html, max_chars=500)
        self.assertEqual(parsed["title"], "Hello")
        self.assertIn("Useful content", parsed["text"])
        self.assertNotIn("evil", parsed["text"])

    def test_extract_pdf_missing_or_invalid(self):
        # Invalid PDF bytes
        out = extract_pdf_text(b"not a pdf", max_chars=1000)
        self.assertFalse(out.get("text"))
        self.assertTrue(out.get("error"))


class BudgetDispatchTests(unittest.TestCase):
    def test_search_budget_blocks(self):
        agent = ResearchAgent(tavily_key=None, groq_key=None, openai_api_key=None)
        # Manually exercise budget via _dispatch with a fake budget
        from tools.research import _Budget, resolve_level

        budget = _Budget(cfg=resolve_level(1))
        budget.searches = budget.cfg.max_searches
        res = agent._dispatch_tool("web_search", {"query": "x"}, budget)
        self.assertEqual(res["status"], "error")
        self.assertIn("exhausted", res["error"])

    def test_compact_tool_result(self):
        agent = ResearchAgent(tavily_key=None)
        cfg = resolve_level(1)
        fat = {
            "status": "success",
            "text": "x" * 50_000,
            "results": [
                {"title": "t", "url": "http://a", "snippet": "s" * 2000}
                for _ in range(20)
            ],
        }
        out = agent._compact_tool_result(fat, cfg)
        self.assertLess(len(out["text"]), 50_000)
        self.assertLessEqual(len(out["results"]), cfg.max_results_per_search)
        self.assertLessEqual(len(out["results"][0]["snippet"]), 500)

    def test_trim_messages(self):
        agent = ResearchAgent(tavily_key=None)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "question"},
        ]
        for i in range(30):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": f"c{i}",
                    "content": json.dumps({"text": "word " * 2000}),
                }
            )
        trimmed = agent._trim_messages(messages, max_tokens=3000)
        self.assertLess(agent._messages_tokens(trimmed), agent._messages_tokens(messages))
        self.assertEqual(trimmed[0]["role"], "system")
        self.assertEqual(trimmed[1]["role"], "user")


if __name__ == "__main__":
    unittest.main()
