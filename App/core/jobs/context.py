"""
context.py - Compact worker packs + artifact storage for long jobs.

Token rules:
  - Workers never get main chat history
  - Clip context / results for inject and inbox
  - Long outputs land on disk under data/tasks/artifacts/
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

ARTIFACTS_DIR = (
    Path(__file__).resolve().parent.parent.parent / "data" / "tasks" / "artifacts"
)

# Soft limits (chars)
CLIP_SUMMARY = 600
CLIP_INJECT = 200
CLIP_CONTEXT = 2000
CLIP_SNIPPET = 300


def clip_text(text: str, limit: int = CLIP_SUMMARY) -> str:
    if text is None:
        return ""
    s = str(text).strip()
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)] + "..."


def compact_worker_user_pack(
    objective: str,
    context: str = "",
    expected_output: str = "",
) -> str:
    """Token-cheap user message for a background worker."""
    parts = [f"Objective:\n{clip_text(objective, 1500)}"]
    if context:
        parts.append(f"Context:\n{clip_text(context, CLIP_CONTEXT)}")
    if expected_output:
        parts.append(f"Expected output:\n{clip_text(expected_output, 400)}")
    return "\n\n".join(parts)


WORKER_SYSTEM_BASE = (
    "You are a focused background AI worker for AVA. "
    "Complete the delegated task independently. Use available tools when they "
    "materially help — including plugins such as whatsapp_plugin when the "
    "objective involves messaging contacts. Be practical and return only the "
    "useful final result. If you create a file, include its path.\n\n"
    "MESSAGING: For WhatsApp, call whatsapp_plugin action search_contacts with "
    "the contact name, then send_message with the number and the message body "
    "(include current time via get_time_date if the objective says so). "
    "Do not claim success without a successful tool result.\n\n"
    "CLARIFICATION: If you need a user preference, budget, personal fact, or "
    "irreversible choice not in the objective/context, call request_clarification "
    "once and wait for the answer string. Do NOT invent user preferences. "
    "Do NOT ask for trivia you can web-search yourself. "
    "Prefer finishing with stated defaults when possible."
)


def worker_system_prompt(extra: str = "") -> str:
    if extra:
        return WORKER_SYSTEM_BASE + "\n\n" + clip_text(extra, 500)
    return WORKER_SYSTEM_BASE


_SAFE_ID = re.compile(r"[^a-zA-Z0-9_\-]+")


def write_artifact(
    job_id: str,
    content: str,
    *,
    title: str = "",
    ext: str = "txt",
) -> Optional[str]:
    """
    Persist full result body for a long job. Returns absolute path string or None.
    Only writes when content is non-trivial.
    """
    body = str(content or "").strip()
    if not body or len(body) < 40:
        return None
    try:
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        safe = _SAFE_ID.sub("_", job_id or "job")[:32] or "job"
        path = ARTIFACTS_DIR / f"{safe}.{ext.lstrip('.')}"
        header = f"# {title}\n# id={job_id}\n\n" if title else f"# id={job_id}\n\n"
        path.write_text(header + body, encoding="utf-8")
        return str(path.resolve())
    except Exception as e:
        print(f"[Artifacts] write failed for {job_id}: {e}")
        return None


def clean_outcome_summary(text: str, limit: int = 220) -> str:
    """
    Turn worker monologue into a short user-facing outcome.
    Drops intermediate 'let me verify' chatter; keeps last useful paragraph.
    """
    s = (text or "").strip()
    if not s:
        return ""
    # Split paragraphs / sentences
    chunks = [c.strip() for c in re.split(r"\n+", s) if c.strip()]
    if not chunks:
        return clip_text(s, limit)

    skip_starts = (
        "let me ",
        "i'll ",
        "i will ",
        "i am going",
        "i'm going",
        "trying ",
        "now i ",
        "next i ",
        "wait,",
        "hmm",
    )
    useful = []
    for c in chunks:
        low = c.lower()
        if any(low.startswith(p) for p in skip_starts):
            continue
        # Keep update/result lines
        useful.append(c)
    if not useful:
        useful = chunks[-2:] if len(chunks) > 1 else chunks

    # Prefer last chunk that mentions a path, run instruction, or Updated/Created
    preferred = None
    for c in reversed(useful):
        low = c.lower()
        if any(
            k in low
            for k in (
                "updated",
                "created",
                "wrote",
                "run with",
                "run it",
                ".py",
                "desktop",
                "done",
                "fixed",
                "added",
            )
        ):
            preferred = c
            break
    body = preferred or useful[-1]
    return clip_text(body, limit)


def parse_result_brief(result: Any) -> dict:
    """
    Extract a human-readable summary + optional path from a task result.

    Research L3+ / OpenCode return JSON with summary/report_path/session_id.
    Never returns the full raw JSON blob for display.
    """
    import json

    out = {
        "summary": "",
        "report_path": "",
        "level": None,
        "raw_preview": "",
        "session_id": "",
        "directory": "",
        "tools": [],
        "status": "",
    }
    if result is None:
        return out

    if isinstance(result, dict):
        data = result
    else:
        raw = str(result).strip()
        out["raw_preview"] = clip_text(raw, 160)
        if not raw:
            return out
        data = None
        if raw.startswith("{") and (
            "summary" in raw
            or "report_path" in raw
            or "report" in raw
            or "session_id" in raw
        ):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    data = parsed
            except Exception:
                data = None
        if data is None:
            out["summary"] = clean_outcome_summary(raw, CLIP_SUMMARY)
            return out

    summary = data.get("summary") or data.get("content") or data.get("report") or ""
    if data.get("summary"):
        summary = data.get("summary")
    path = (
        data.get("report_path")
        or data.get("path")
        or data.get("artifact_path")
        or data.get("directory")
        or ""
    )
    out["summary"] = clean_outcome_summary(str(summary or "").strip(), CLIP_SUMMARY)
    out["report_path"] = str(path or "").strip()
    out["session_id"] = str(data.get("session_id") or "").strip()
    out["directory"] = str(data.get("directory") or path or "").strip()
    tools = data.get("tools") or []
    if isinstance(tools, list):
        out["tools"] = [str(t)[:80] for t in tools[:6]]
    out["status"] = str(data.get("status") or "").strip()
    try:
        out["level"] = data.get("level")
    except Exception:
        out["level"] = None
    if not out["summary"] and out["report_path"]:
        out["summary"] = "Full report saved to disk."
    if not out["summary"]:
        out["summary"] = out["raw_preview"] or "Completed."
    return out


def summary_for_inbox(result: str, limit: int = CLIP_SUMMARY) -> str:
    """
    Prefer structured research/worker JSON fields (summary, report_path)
    so long jobs never flood the inbox with full bodies.
    """
    brief = parse_result_brief(result)
    parts = []
    if brief.get("summary"):
        parts.append(str(brief["summary"]))
    if brief.get("report_path"):
        parts.append(f"Full report: {brief['report_path']}")
    if brief.get("level") is not None:
        parts.append(f"(research level {brief['level']})")
    if parts:
        return clip_text(" ".join(parts), limit)
    return clip_text(str(result or ""), limit)
