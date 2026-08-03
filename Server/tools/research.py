"""
AVA Research Agent v2 — agentic, multi-level deep research.

Levels 1–5 scale breadth (searches, pages, PDFs) and depth (rounds, tokens).
Hard context budget: 100K tokens. Tools return parsed text only (no raw HTML).

Tools the model may call:
  - web_search   : Tavily (or fallback) search
  - fetch_page   : download + clean HTML → truncated text
  - read_pdf     : download text-PDF → truncated text
  - finish       : commit final answer (optional; silent stop also works)
"""

from __future__ import annotations

import json
import os
import traceback
from dataclasses import dataclass, field
from typing import Any, Optional

from openai import OpenAI

from .research_fetch import ResearchFetchTools, approx_tokens, truncate_text


# ── Level profiles ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LevelConfig:
    level: int
    label: str
    description: str
    max_rounds: int
    max_searches: int
    max_pages: int
    max_pdfs: int
    max_results_per_search: int
    search_depth: str  # basic | advanced
    page_chars: int
    pdf_chars: int
    pdf_max_pages: int
    max_context_tokens: int
    max_output_tokens: int
    temperature: float
    # Person research extras (Instagram public profiles)
    allow_instagram: bool = False
    max_instagram: int = 0
    instagram_max_posts: int = 0


# Absolute hard caps
HARD_MAX_CONTEXT_TOKENS = 100_000
HARD_MAX_ROUNDS = 24

LEVELS: dict[int, LevelConfig] = {
    1: LevelConfig(
        level=1,
        label="quick",
        description="Fast fact check / one-angle lookup",
        max_rounds=3,
        max_searches=2,
        max_pages=1,
        max_pdfs=0,
        max_results_per_search=3,
        search_depth="basic",
        page_chars=2500,
        pdf_chars=0,
        pdf_max_pages=0,
        max_context_tokens=12_000,
        max_output_tokens=1200,
        temperature=0.4,
        allow_instagram=False,
        max_instagram=0,
        instagram_max_posts=0,
    ),
    2: LevelConfig(
        level=2,
        label="light",
        description="Light multi-source check",
        max_rounds=5,
        max_searches=3,
        max_pages=3,
        max_pdfs=1,
        max_results_per_search=4,
        search_depth="basic",
        page_chars=4000,
        pdf_chars=6000,
        pdf_max_pages=15,
        max_context_tokens=24_000,
        max_output_tokens=2000,
        temperature=0.4,
        allow_instagram=False,
        max_instagram=0,
        instagram_max_posts=0,
    ),
    3: LevelConfig(
        level=3,
        label="standard",
        description="Balanced research with page deep-dives",
        max_rounds=8,
        max_searches=5,
        max_pages=5,
        max_pdfs=2,
        max_results_per_search=5,
        search_depth="advanced",
        page_chars=7000,
        pdf_chars=10000,
        pdf_max_pages=25,
        max_context_tokens=40_000,
        max_output_tokens=3500,
        temperature=0.45,
        allow_instagram=False,
        max_instagram=0,
        instagram_max_posts=0,
    ),
    4: LevelConfig(
        level=4,
        label="deep",
        description="Deep multi-angle research with PDFs + Instagram for people",
        max_rounds=12,
        max_searches=8,
        max_pages=10,
        max_pdfs=4,
        max_results_per_search=6,
        search_depth="advanced",
        page_chars=9000,
        pdf_chars=14000,
        pdf_max_pages=35,
        max_context_tokens=70_000,
        max_output_tokens=5000,
        temperature=0.45,
        allow_instagram=True,
        max_instagram=3,
        instagram_max_posts=6,
    ),
    5: LevelConfig(
        level=5,
        label="exhaustive",
        description="Maximum depth under the 100K token budget; Instagram for people",
        max_rounds=18,
        max_searches=12,
        max_pages=15,
        max_pdfs=6,
        max_results_per_search=8,
        search_depth="advanced",
        page_chars=10000,
        pdf_chars=18000,
        pdf_max_pages=40,
        max_context_tokens=100_000,
        max_output_tokens=6500,
        temperature=0.5,
        allow_instagram=True,
        max_instagram=5,
        instagram_max_posts=10,
    ),
}


def resolve_level(level: Any) -> LevelConfig:
    try:
        n = int(level)
    except (TypeError, ValueError):
        n = 3
    n = max(1, min(5, n))
    cfg = LEVELS[n]
    # Enforce hard ceiling
    if cfg.max_context_tokens > HARD_MAX_CONTEXT_TOKENS:
        # Shouldn't happen; defensive
        return LEVELS[5]
    return cfg


# ── OpenAI tool schemas for the agent loop ──────────────────────────────────

def _tool_schemas(cfg: LevelConfig) -> list[dict[str, Any]]:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "Search the web. Returns titles, URLs, and short snippets. "
                    "Use focused queries; run several different angles rather than one vague search."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Specific search query",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "fetch_page",
                "description": (
                    "Download a web page and extract clean readable text (scripts/nav stripped). "
                    "Text is truncated to protect the context budget. Use for promising links "
                    "from search results. Do NOT use for PDF URLs — use read_pdf."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "http(s) page URL"},
                        "focus": {
                            "type": "string",
                            "description": "Optional: what to prioritize in the page (logged only)",
                        },
                    },
                    "required": ["url"],
                },
            },
        },
    ]
    if cfg.max_pdfs > 0:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "read_pdf",
                    "description": (
                        "Download a text-based PDF and extract plain text (image/scanned PDFs "
                        "are not supported yet). Use for papers, reports, specs. Truncated to budget."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "Direct PDF http(s) URL"},
                        },
                        "required": ["url"],
                    },
                },
            }
        )
    if cfg.allow_instagram and cfg.max_instagram > 0:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "instagram_profile",
                    "description": (
                        "Fetch a public Instagram profile snapshot (bio, counts, recent captions). "
                        "Use ONLY when researching a person and you have (or found) their Instagram "
                        "username or profile URL. Text-only — no media download. Private accounts "
                        "return limited info. Do not invent usernames."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "username": {
                                "type": "string",
                                "description": (
                                    "Instagram username or profile URL "
                                    "(e.g. 'cristiano' or 'https://instagram.com/cristiano')"
                                ),
                            },
                        },
                        "required": ["username"],
                    },
                },
            }
        )
    tools.append(
        {
            "type": "function",
            "function": {
                "name": "finish",
                "description": (
                    "Commit the final research report when you have enough evidence. "
                    "Include a clear answer, key findings, caveats, and source URLs."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "report": {
                            "type": "string",
                            "description": "Full final research report in markdown-friendly prose",
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                            "description": "How confident you are in the synthesis",
                        },
                    },
                    "required": ["report"],
                },
            },
        }
    )
    return tools


def _system_prompt(cfg: LevelConfig) -> str:
    pdf_line = (
        f"- PDFs: up to {cfg.max_pdfs} via read_pdf (text PDFs only)"
        if cfg.max_pdfs > 0
        else "- PDFs: not available at this level"
    )
    if cfg.allow_instagram and cfg.max_instagram > 0:
        ig_line = (
            f"- Instagram: up to {cfg.max_instagram} public profile lookups via "
            f"instagram_profile (person research only; ~{cfg.instagram_max_posts} recent captions)"
        )
        ig_workflow = (
            "\nWhen the subject is a person: search for their Instagram handle, then call "
            "instagram_profile with that username. Include social footprint in the report "
            "when relevant. Do not invent handles."
        )
    else:
        ig_line = "- Instagram: not available at this level"
        ig_workflow = ""
    return f"""You are AVA's Research Agent (level {cfg.level} — {cfg.label}).
{cfg.description}.

MISSION
Answer the user's research question using tools. Do not invent sources or URLs.
Prefer primary / authoritative sources. Note disagreements and uncertainty.

BUDGET (strict)
- Max tool rounds: {cfg.max_rounds}
- Max web_search calls: {cfg.max_searches}
- Max fetch_page calls: {cfg.max_pages}
{pdf_line}
{ig_line}
- Context budget ~{cfg.max_context_tokens} tokens (hard cap 100000)
- Search results and page text are pre-truncated — dig deeper only when needed

WORKFLOW
1. Decompose the question into 1–few concrete angles.
2. web_search with specific queries (not the whole question dumped once).
3. fetch_page (and read_pdf when relevant) on the best links.
4. Cross-check claims across sources; chase gaps if budget remains.
5. Call finish with a structured report when done.{ig_workflow}

REPORT FORMAT (for finish.report)
- Direct answer / executive summary first
- Key findings (prose, not giant bullet walls)
- Nuance / disagreements / unknowns
- Sources: list real URLs you used
Keep length appropriate to level {cfg.level} ({cfg.label}).

If a tool fails, try a different query or source. Never fabricate content from failed fetches.
When the budget is nearly exhausted, finish with what you have rather than looping.
"""


@dataclass
class _Budget:
    cfg: LevelConfig
    searches: int = 0
    pages: int = 0
    pdfs: int = 0
    instagram: int = 0
    sources: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add_source(self, url: str) -> None:
        url = (url or "").strip()
        if url and url not in self.sources:
            self.sources.append(url)

    def remaining_ok(self, kind: str) -> Optional[str]:
        if kind == "web_search" and self.searches >= self.cfg.max_searches:
            return f"search budget exhausted ({self.cfg.max_searches})"
        if kind == "fetch_page" and self.pages >= self.cfg.max_pages:
            return f"page budget exhausted ({self.cfg.max_pages})"
        if kind == "read_pdf" and self.pdfs >= self.cfg.max_pdfs:
            return f"pdf budget exhausted ({self.cfg.max_pdfs})"
        if kind == "instagram_profile":
            if not self.cfg.allow_instagram or self.cfg.max_instagram <= 0:
                return "instagram not available at this research level"
            if self.instagram >= self.cfg.max_instagram:
                return f"instagram budget exhausted ({self.cfg.max_instagram})"
        return None


class ResearchAgent:
    """Agentic multi-level research agent (server-side)."""

    def __init__(
        self,
        *,
        tavily_key: Optional[str] = None,
        groq_key: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        openai_base_url: Optional[str] = None,
        model_name: Optional[str] = None,
    ):
        self.fetch = ResearchFetchTools(tavily_key=tavily_key)

        # Prefer shared OpenAI-compatible key pool (same as main LLM + dreamer).
        from services.key_rotator import (
            collect_openai_keys,
            normalize_openai_base_url,
        )

        main_base = normalize_openai_base_url(
            openai_base_url or os.getenv("OPENAI_BASE_URL")
        )
        main_model = (
            os.getenv("AVA_MODEL_HEAVY")
            or model_name
            or os.getenv("MODEL_NAME")
            or ""
        )
        pool = collect_openai_keys(primary=openai_api_key, env=True)
        self.base_url = main_base
        self.model = main_model
        self._use_key_pool = bool(pool)
        self._groq_key = groq_key
        # Legacy single client only for Groq fallback when pool empty
        self.client: Optional[OpenAI] = None
        if not pool and groq_key:
            self.client = OpenAI(
                api_key=groq_key,
                base_url="https://api.groq.com/openai/v1",
            )
            self.model = self.model or "llama-3.3-70b-versatile"
            self._use_key_pool = False

        if not self.model:
            self.model = "llama-3.3-70b-versatile"

        if self._use_key_pool:
            print(f"[ResearchAgent] using shared key pool n={len(pool)}")

    def _llm_create(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        """Chat completion via shared key pool (or Groq fallback client)."""
        if self._use_key_pool:
            from services.key_rotator import chat_completions_create

            return chat_completions_create(
                model=self.model,
                messages=messages,
                base_url=self.base_url,
                label="Research",
                **kwargs,
            )
        if self.client is None:
            raise RuntimeError("No LLM client (OPENAI_API_KEYS / GROQ_API_KEY)")
        return self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **kwargs,
        )

    # ── public entry ────────────────────────────────────────────────────────

    def execute(self, question: str, level: int = 3) -> dict[str, Any]:
        question = (question or "").strip()
        if not question:
            return {"status": "error", "content": "Missing research question."}

        cfg = resolve_level(level)
        print(
            f"[ResearchAgent] start level={cfg.level}({cfg.label}) "
            f"q={question[:120]!r} model={self.model}"
        )

        if not self._use_key_pool and self.client is None:
            return {
                "status": "error",
                "content": "Research agent has no LLM client configured "
                "(OPENAI_API_KEYS / GROQ_API_KEY).",
            }

        try:
            return self._run_loop(question, cfg)
        except Exception as e:
            print(f"[ResearchAgent] fatal: {e}\n{traceback.format_exc()}")
            return {"status": "error", "content": f"Research failed: {e}"}

    # ── agent loop ──────────────────────────────────────────────────────────

    def _run_loop(self, question: str, cfg: LevelConfig) -> dict[str, Any]:
        budget = _Budget(cfg=cfg)
        tools = _tool_schemas(cfg)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _system_prompt(cfg)},
            {
                "role": "user",
                "content": (
                    f"Research question:\n{question}\n\n"
                    f"Level {cfg.level} ({cfg.label}). "
                    "Use tools, then finish with a solid report."
                ),
            },
        ]

        final_report: Optional[str] = None
        confidence = "medium"
        rounds_used = 0

        for round_i in range(min(cfg.max_rounds, HARD_MAX_ROUNDS)):
            rounds_used = round_i + 1
            # Soft nudge when budget low
            ctx_tokens = self._messages_tokens(messages)
            if ctx_tokens > int(cfg.max_context_tokens * 0.92):
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            f"Context ~{ctx_tokens} tokens near the "
                            f"{cfg.max_context_tokens} limit. "
                            "Call finish NOW with the best report you can."
                        ),
                    }
                )

            print(
                f"[ResearchAgent] round {rounds_used}/{cfg.max_rounds} "
                f"ctx~{ctx_tokens} tok searches={budget.searches} "
                f"pages={budget.pages} pdfs={budget.pdfs} ig={budget.instagram}"
            )

            try:
                response = self._llm_create(
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=cfg.temperature,
                    max_tokens=cfg.max_output_tokens,
                )
            except Exception as e:
                # One retry without max_tokens if provider rejects it
                print(f"[ResearchAgent] LLM error (retry): {e}")
                response = self._llm_create(
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=cfg.temperature,
                )

            msg = response.choices[0].message
            tool_calls = list(msg.tool_calls or [])

            # Persist assistant turn
            assistant_payload: dict[str, Any] = {
                "role": "assistant",
                "content": msg.content or "",
            }
            if tool_calls:
                assistant_payload["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments or "{}",
                        },
                    }
                    for tc in tool_calls
                ]
            messages.append(assistant_payload)

            if not tool_calls:
                # Model stopped with a prose answer
                final_report = (msg.content or "").strip()
                if final_report:
                    break
                messages.append(
                    {
                        "role": "user",
                        "content": "You returned an empty answer. Call finish with your report.",
                    }
                )
                continue

            # Execute tools
            stop = False
            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    if not isinstance(args, dict):
                        args = {}
                except json.JSONDecodeError:
                    args = {}

                if name == "finish":
                    final_report = str(args.get("report") or msg.content or "").strip()
                    confidence = str(args.get("confidence") or "medium")
                    tool_result = {
                        "status": "success",
                        "message": "Report accepted. Research complete.",
                    }
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(tool_result),
                        }
                    )
                    stop = True
                    break

                result = self._dispatch_tool(name, args, budget)
                # Compress tool payload for context
                payload = self._compact_tool_result(result, cfg)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(payload, ensure_ascii=False),
                    }
                )

            # Prune context if over budget
            messages = self._trim_messages(messages, cfg.max_context_tokens)

            if stop:
                break
        else:
            # Exhausted rounds without finish
            print("[ResearchAgent] max rounds hit — forcing synthesis")
            final_report = self._force_synthesis(messages, question, cfg, budget)

        if not final_report:
            final_report = (
                "Research did not produce a final report. "
                f"Sources consulted: {', '.join(budget.sources[:20]) or 'none'}."
            )

        # Append source footer if model forgot
        if budget.sources and "http" not in final_report.lower():
            src_block = "\n\nSources:\n" + "\n".join(f"- {u}" for u in budget.sources[:30])
            final_report = final_report.rstrip() + src_block

        meta = {
            "level": cfg.level,
            "label": cfg.label,
            "model": self.model,
            "rounds": rounds_used,
            "searches": budget.searches,
            "pages_fetched": budget.pages,
            "pdfs_read": budget.pdfs,
            "instagram_lookups": budget.instagram,
            "sources": budget.sources,
            "confidence": confidence,
            "approx_context_tokens": self._messages_tokens(messages),
            "max_context_tokens": cfg.max_context_tokens,
        }
        print(
            f"[ResearchAgent] done level={cfg.level} rounds={rounds_used} "
            f"sources={len(budget.sources)} chars={len(final_report)}"
        )
        return {
            "status": "success",
            "content": final_report,
            "meta": meta,
        }

    # ── tool dispatch ───────────────────────────────────────────────────────

    def _dispatch_tool(
        self, name: str, args: dict[str, Any], budget: _Budget
    ) -> dict[str, Any]:
        cfg = budget.cfg
        err = budget.remaining_ok(name)
        if err:
            return {"status": "error", "error": err, "tool": name}

        if name == "web_search":
            budget.searches += 1
            q = str(args.get("query") or "").strip()
            result = self.fetch.web_search(
                q,
                max_results=cfg.max_results_per_search,
                search_depth=cfg.search_depth,
            )
            for r in result.get("results") or []:
                if isinstance(r, dict):
                    budget.add_source(r.get("url") or "")
            return result

        if name == "fetch_page":
            budget.pages += 1
            url = str(args.get("url") or "").strip()
            result = self.fetch.fetch_page(url, max_chars=cfg.page_chars)
            if result.get("status") == "success":
                budget.add_source(result.get("url") or url)
            return result

        if name == "read_pdf":
            if cfg.max_pdfs <= 0:
                return {"status": "error", "error": "PDFs disabled at this research level"}
            budget.pdfs += 1
            url = str(args.get("url") or "").strip()
            result = self.fetch.read_pdf(
                url,
                max_chars=cfg.pdf_chars,
                max_pages=cfg.pdf_max_pages,
            )
            if result.get("status") == "success":
                budget.add_source(result.get("url") or url)
            return result

        if name == "instagram_profile":
            budget.instagram += 1
            uname = str(args.get("username") or "").strip()
            result = self.fetch.instagram_profile(
                uname,
                max_posts=cfg.instagram_max_posts,
            )
            if result.get("status") in ("success", "partial"):
                budget.add_source(result.get("profile_url") or f"https://www.instagram.com/{uname}/")
            return result

        return {"status": "error", "error": f"unknown tool: {name}"}

    @staticmethod
    def _compact_tool_result(result: dict[str, Any], cfg: LevelConfig) -> dict[str, Any]:
        """Ensure tool payloads stay small enough for the context budget."""
        if not isinstance(result, dict):
            return {"status": "error", "error": "invalid tool result"}

        out = dict(result)
        # Cap nested text fields
        if "text" in out and isinstance(out["text"], str):
            cap = max(cfg.page_chars, cfg.pdf_chars, 2000)
            out["text"] = truncate_text(out["text"], cap)
        if "answer" in out and isinstance(out["answer"], str):
            out["answer"] = truncate_text(out["answer"], 800)
        if "results" in out and isinstance(out["results"], list):
            compact = []
            for r in out["results"][: cfg.max_results_per_search]:
                if not isinstance(r, dict):
                    continue
                compact.append(
                    {
                        "title": truncate_text(str(r.get("title") or ""), 200),
                        "url": r.get("url") or "",
                        "snippet": truncate_text(str(r.get("snippet") or ""), 500),
                    }
                )
            out["results"] = compact
        return out

    # ── context management ──────────────────────────────────────────────────

    @staticmethod
    def _messages_tokens(messages: list[dict[str, Any]]) -> int:
        total = 0
        for m in messages:
            total += approx_tokens(str(m.get("content") or ""))
            # tool_calls overhead
            tcs = m.get("tool_calls")
            if tcs:
                total += approx_tokens(json.dumps(tcs, ensure_ascii=False))
        return total

    def _trim_messages(
        self, messages: list[dict[str, Any]], max_tokens: int
    ) -> list[dict[str, Any]]:
        """Drop oldest tool/assistant middle turns if over budget; keep system + user + tail."""
        if self._messages_tokens(messages) <= max_tokens:
            return messages
        if len(messages) <= 3:
            # Truncate the largest tool content in place
            for m in messages:
                c = m.get("content")
                if isinstance(c, str) and approx_tokens(c) > 2000:
                    m["content"] = truncate_text(c, 6000)
            return messages

        # Keep first 2 (system + user question) and last N until under budget
        head = messages[:2]
        tail = messages[2:]
        while tail and self._messages_tokens(head + tail) > max_tokens:
            # Prefer dropping oldest tool results
            dropped = False
            for i, m in enumerate(tail[:-2] if len(tail) > 2 else tail[:1]):
                if m.get("role") == "tool":
                    tail.pop(i)
                    dropped = True
                    break
            if not dropped:
                if len(tail) > 2:
                    tail.pop(0)
                else:
                    break
        # Note that we pruned
        note = {
            "role": "system",
            "content": "[Context pruned earlier tool results to stay under token budget.]",
        }
        return head + [note] + tail

    def _force_synthesis(
        self,
        messages: list[dict[str, Any]],
        question: str,
        cfg: LevelConfig,
        budget: _Budget,
    ) -> str:
        synth_messages = list(messages) + [
            {
                "role": "user",
                "content": (
                    f"Tool budget exhausted. Write the final research report now for:\n"
                    f"{question}\n\n"
                    f"Sources used: {', '.join(budget.sources[:25]) or 'none'}. "
                    "Be honest about gaps."
                ),
            }
        ]
        try:
            response = self._llm_create(
                messages=synth_messages,
                temperature=cfg.temperature,
                max_tokens=cfg.max_output_tokens,
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as e:
            return f"Research incomplete (synthesis error: {e}). Sources: {budget.sources}"
