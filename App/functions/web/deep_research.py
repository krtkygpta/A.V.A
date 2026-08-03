"""
Client entry for AVA research agent.

All research runs on the Server (`POST /tools/research`) as an agentic
multi-level (1–5) loop with web search, page extraction, and text PDFs.

Level 3+: full report is written to disk (Desktop/AVA_Research) and only a
short summary + path are returned to the main agent (avoids context floods).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Optional, Union

import requests


def _server_url() -> str:
    return os.getenv("AVA_SERVER_URL", "http://127.0.0.1:8765").rstrip("/")


def _clamp_level(level: Any) -> int:
    try:
        n = int(level)
    except (TypeError, ValueError):
        n = 3
    return max(1, min(5, n))


def _slugify(text: str, max_len: int = 48) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (text or "research").strip())
    s = s.strip("_")[:max_len] or "research"
    return s


def _make_summary(report: str, max_chars: int = 520) -> str:
    """Compact executive summary from the full report (no second LLM call)."""
    body = (report or "").strip()
    if not body:
        return "Research completed with an empty report."

    # Drop trailing Sources block for the summary body
    cut = re.split(r"\n+\s*Sources\s*:?\s*\n", body, maxsplit=1, flags=re.I)
    main = cut[0].strip()

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", main) if p.strip()]
    if paragraphs:
        # Prefer first 1–2 non-heading-only paragraphs
        picked: list[str] = []
        for p in paragraphs:
            if re.fullmatch(r"#{1,6}\s+.*", p) and len(picked) == 0:
                # keep title-ish heading as context
                picked.append(re.sub(r"^#{1,6}\s+", "", p).strip())
                continue
            picked.append(p)
            if len(" ".join(picked)) >= max_chars * 0.6 and len(picked) >= 1:
                break
            if len(picked) >= 2:
                break
        summary = " ".join(x.replace("\n", " ") for x in picked)
    else:
        summary = main.replace("\n", " ")

    summary = re.sub(r"\s+", " ", summary).strip()
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3].rsplit(" ", 1)[0] + "..."
    return summary


def _save_full_report(
    question: str,
    level: int,
    report: str,
    sources: list[str],
    meta: dict[str, Any],
) -> Optional[str]:
    """Write full markdown report under Desktop/AVA_Research. Returns abs path."""
    try:
        from utils.paths import research_output_dir
    except Exception:
        # Fallback if import path differs when run as script
        from pathlib import Path

        desk = Path.home() / "Desktop"
        desk.mkdir(exist_ok=True)
        out_dir = desk / "AVA_Research"
        out_dir.mkdir(exist_ok=True)

        def research_output_dir():  # type: ignore
            return out_dir

    try:
        out_dir = research_output_dir()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = _slugify(question)
        path = out_dir / f"research_L{level}_{stamp}_{slug}.md"

        header_lines = [
            f"# Research report (level {level})",
            "",
            f"**Question:** {question}",
            f"**Generated:** {datetime.now().isoformat(timespec='seconds')}",
            f"**Confidence:** {meta.get('confidence', 'n/a')}",
            f"**Model:** {meta.get('model', 'n/a')}",
            f"**Stats:** rounds={meta.get('rounds')}, searches={meta.get('searches')}, "
            f"pages={meta.get('pages_fetched')}, pdfs={meta.get('pdfs_read')}",
            "",
            "---",
            "",
        ]
        body = report if report is not None else ""
        # Ensure sources section if we have URLs and report lacks them
        if sources and "http" not in body.lower():
            body = body.rstrip() + "\n\n## Sources\n" + "\n".join(
                f"- {u}" for u in sources[:40]
            )

        path.write_text("\n".join(header_lines) + body + "\n", encoding="utf-8")
        return str(path.resolve())
    except Exception as e:
        print(f"[Research] failed to save report: {e}")
        return None


def research(
    query: str,
    level: Union[int, str] = 3,
    *,
    timeout: Optional[float] = None,
) -> str:
    """
    Run research on the AVA server.

    Args:
        query: Research question / topic.
        level: 1 (quick) … 5 (exhaustive). Default 3.
        timeout: HTTP timeout seconds (defaults scale with level).

    Returns:
        JSON string for the main agent / task result.
        Level 1–2: includes full report.
        Level 3–5: full report on disk; payload has summary + report_path only.
    """
    question = (query or "").strip()
    if not question:
        return json.dumps({"status": "error", "content": "Empty research query"})

    lvl = _clamp_level(level)
    # Higher levels need more wall-clock time (tool rounds + LLM)
    default_timeout = {
        1: 120.0,
        2: 240.0,
        3: 480.0,
        4: 900.0,
        5: 1500.0,
    }.get(lvl, 480.0)
    base = float(os.getenv("AVA_SERVER_TIMEOUT", "4"))
    wait = float(timeout) if timeout is not None else max(base, default_timeout)

    server = _server_url()
    try:
        response = requests.post(
            f"{server}/tools/research",
            json={"question": question, "level": lvl},
            timeout=wait,
        )
        response.raise_for_status()
        result = response.json()
    except requests.exceptions.RequestException as e:
        return json.dumps(
            {
                "status": "error",
                "content": f"Server research request failed: {e}",
            }
        )

    if not (isinstance(result, dict) and result.get("status") == "success"):
        if isinstance(result, dict):
            err = result.get("content") or result.get("error") or result
            return json.dumps({"status": "error", "content": err}, ensure_ascii=False)
        return json.dumps({"status": "error", "content": str(result)})

    content = result.get("content") or ""
    meta = result.get("meta") or {}
    sources = list(meta.get("sources") or [])
    # Cap sources list in the *return* payload always
    sources_preview = sources[:12]

    stats = {
        "rounds": meta.get("rounds"),
        "searches": meta.get("searches"),
        "pages_fetched": meta.get("pages_fetched"),
        "pdfs_read": meta.get("pdfs_read"),
    }

    # ── Level 3+: file on disk + short summary only ─────────────────────────
    if lvl >= 3:
        report_path = _save_full_report(
            question=question,
            level=lvl,
            report=str(content),
            sources=sources,
            meta=meta,
        )
        summary = _make_summary(str(content), max_chars=520)
        payload = {
            "status": "success",
            "level": meta.get("level", lvl),
            "label": meta.get("label"),
            "summary": summary,
            "report_path": report_path,
            "sources": sources_preview,
            "source_count": len(sources),
            "confidence": meta.get("confidence"),
            "stats": stats,
            "note": (
                "Full research report saved to disk. "
                "Do NOT dump the full report into chat — speak the summary and "
                "offer the file path. Use open_file if the user wants it opened."
            ),
        }
        if not report_path:
            # Fall back: still avoid dumping entire report if save failed
            payload["summary"] = summary
            payload["report_preview"] = str(content)[:800]
            payload["note"] = (
                "Could not save full report to disk; only a short preview is included."
            )
        return json.dumps(payload, ensure_ascii=False)

    # ── Level 1–2: full report is small enough to return ────────────────────
    return json.dumps(
        {
            "status": "success",
            "level": meta.get("level", lvl),
            "label": meta.get("label"),
            "report": content,
            "sources": sources_preview,
            "confidence": meta.get("confidence"),
            "stats": stats,
        },
        ensure_ascii=False,
    )


# CLI smoke helper (optional)
if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    lvl = 2
    if not q:
        q = input("Research question: ").strip()
        raw = input("Level 1-5 [2]: ").strip() or "2"
        lvl = _clamp_level(raw)
    print(research(q, level=lvl))
