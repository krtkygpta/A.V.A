"""
Task dossiers — durable context for background jobs (all types).

Lifecycle:
  spawn  → origin pack (conversation snapshot + mission)
  run    → workers/main use origin
  finish → result summary + artifacts + continue hints; HELD for later
  later  → main agent retrieves via inject / manage_background_tasks context

"Finished" means the worker stopped, not that memory is deleted.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.jobs.context import CLIP_CONTEXT, CLIP_SUMMARY, clip_text, parse_result_brief

DOSSIERS_DIR = (
    Path(__file__).resolve().parent.parent.parent / "data" / "tasks" / "dossiers"
)

# Soft limits
MAX_ORIGIN_TURNS = int(os.getenv("AVA_DOSSIER_MAX_TURNS", "12"))
MAX_TURN_CHARS = int(os.getenv("AVA_DOSSIER_MAX_TURN_CHARS", "500"))
MAX_RECENT_INJECT = int(os.getenv("AVA_DOSSIER_RECENT_INJECT", "8"))
MAX_HOLD_DAYS = int(os.getenv("AVA_DOSSIER_HOLD_DAYS", "14"))

_lock = threading.RLock()

# Task types that always keep a full held dossier after finish
RESULTFUL_TYPES = frozenset(
    {
        "research",
        "delegate",
        "mission",
        "demo",
        "scrape",
        "opencode",
        "coding",
        "general",
    }
)


@dataclass
class OriginTurn:
    role: str
    content: str


@dataclass
class ArtifactRef:
    path: str
    kind: str = "file"  # file | report | folder | url
    label: str = ""


@dataclass
class TaskDossier:
    """Stable package the main agent can use after a job finishes."""

    task_id: str
    task_type: str = "general"
    name: str = ""
    status: str = "pending"
    conversation_id: str = ""
    conversation_name: str = ""
    original_query: str = ""
    mission: str = ""
    origin_user_request: str = ""
    origin_turns: List[Dict[str, str]] = field(default_factory=list)
    artifacts: List[Dict[str, str]] = field(default_factory=list)
    continue_hints: List[str] = field(default_factory=list)
    result_summary: str = ""
    # auto | result_only | silent_ok | always
    announce_policy: str = "auto"
    # user_result | internal_ask (for wake classification)
    last_wake_kind: str = "user_result"
    held: bool = True
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    parent_task_id: str = ""
    follow_up_main_action: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskDossier":
        if not isinstance(data, dict):
            return cls(task_id="")
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        if "task_id" not in filtered:
            filtered["task_id"] = data.get("id") or ""
        return cls(**filtered)

    def short_line(self) -> str:
        """One line for system-prompt inject."""
        summary = clip_text(self.result_summary or self.mission or self.original_query, 120)
        arts = []
        for a in (self.artifacts or [])[:2]:
            p = (a.get("path") or "").strip()
            if p:
                arts.append(p)
        art_s = f" | files: {'; '.join(arts)}" if arts else ""
        hints = ""
        if self.continue_hints:
            hints = f" | next: {self.continue_hints[0][:80]}"
        st = self.status or "?"
        return (
            f"- {self.task_id}: [{self.task_type}] {clip_text(self.name or self.mission, 50)} "
            f"({st}) — {summary}{art_s}{hints}"
        )

    def format_for_main(self, *, full: bool = False) -> str:
        """Multi-line block for DONE inject or manage_background_tasks context."""
        lines = [
            f"[TASK DOSSIER id={self.task_id} type={self.task_type} status={self.status}]",
            f"Name: {self.name or '(unnamed)'}",
            f"Started in: \"{self.conversation_name or '?'}\" ({self.conversation_id or '?'})",
        ]
        if self.origin_user_request:
            lines.append(f"User originally asked: {clip_text(self.origin_user_request, 400)}")
        if self.mission and self.mission != self.origin_user_request:
            lines.append(f"Mission: {clip_text(self.mission, 300)}")
        if self.original_query and self.original_query not in (
            self.mission,
            self.origin_user_request,
        ):
            lines.append(f"Objective: {clip_text(self.original_query, 300)}")
        if self.follow_up_main_action:
            lines.append(f"Main action after finish: {self.follow_up_main_action}")
        if self.result_summary:
            lines.append(f"Result: {clip_text(self.result_summary, 400 if full else 220)}")
        if self.artifacts:
            lines.append("Artifacts:")
            for a in self.artifacts[:8]:
                label = a.get("label") or a.get("kind") or "file"
                lines.append(f"  - {label}: {a.get('path', '')}")
        if self.continue_hints:
            lines.append("Continue hints (use if user asks to run/open/continue):")
            for h in self.continue_hints[:6]:
                lines.append(f"  - {h}")
        if full and self.origin_turns:
            lines.append("Origin conversation (clipped):")
            for t in self.origin_turns[-MAX_ORIGIN_TURNS:]:
                role = (t.get("role") or "?").upper()
                lines.append(f"  {role}: {clip_text(t.get('content') or '', 200)}")
        pol = self.announce_policy or "auto"
        if pol == "result_only":
            lines.append(
                "SPEAK POLICY: result_only — tell the user only the meaningful outcome "
                "(e.g. message sent). Do NOT narrate the timer or internal steps."
            )
        elif pol == "silent_ok":
            lines.append(
                "SPEAK POLICY: silent_ok — you may stay silent if nothing user-facing needs saying."
            )
        elif pol == "always":
            lines.append(
                "SPEAK POLICY: summarize the useful outcome briefly; offer paths if any."
            )
        else:
            lines.append(
                "SPEAK POLICY: only speak if the user would care; prefer the outcome over process."
            )
        lines.append(
            "This dossier STAYS after finish. If the user later says 'run it' / 'open that' / "
            "'continue', use these artifacts and hints (or manage_background_tasks action context)."
        )
        return "\n".join(lines)

    def origin_pack_for_worker(self, limit: int = CLIP_CONTEXT) -> str:
        """Compact origin for research/delegate workers."""
        parts = []
        if self.origin_user_request:
            parts.append(f"User request: {self.origin_user_request}")
        if self.mission:
            parts.append(f"Mission: {self.mission}")
        if self.origin_turns:
            chat_bits = []
            for t in self.origin_turns[-8:]:
                role = t.get("role") or "?"
                chat_bits.append(f"{role}: {t.get('content') or ''}")
            parts.append("Prior chat:\n" + "\n".join(chat_bits))
        return clip_text("\n\n".join(parts), limit)


def _dossier_path(task_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_\-]+", "_", task_id or "job")[:48] or "job"
    return DOSSIERS_DIR / f"{safe}.json"


def save_dossier(d: TaskDossier) -> None:
    if not d or not d.task_id:
        return
    with _lock:
        try:
            DOSSIERS_DIR.mkdir(parents=True, exist_ok=True)
            path = _dossier_path(d.task_id)
            path.write_text(
                json.dumps(d.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[Dossier] save failed for {d.task_id}: {e}")


def load_dossier(task_id: str) -> Optional[TaskDossier]:
    if not task_id:
        return None
    with _lock:
        path = _dossier_path(task_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return TaskDossier.from_dict(data)
        except Exception as e:
            print(f"[Dossier] load failed for {task_id}: {e}")
            return None


def _clip_msg_content(content: Any, limit: int = MAX_TURN_CHARS) -> str:
    if content is None:
        return ""
    if isinstance(content, list):
        # multimodal-ish
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        s = " ".join(parts)
    else:
        s = str(content)
    s = s.strip()
    # Drop huge tool/system noise
    if s.startswith("[BACKGROUND TASK") or s.startswith("[TASK "):
        return ""
    if s.startswith("[PENDING MAIN ACTION"):
        return ""
    return clip_text(s, limit)


def capture_origin_snapshot(
    *,
    max_turns: int = MAX_ORIGIN_TURNS,
    max_chars: int = MAX_TURN_CHARS,
) -> Dict[str, Any]:
    """
    Snapshot recent user/assistant turns from the live main chat.
    Safe if messageHandler is unavailable.
    """
    turns: List[Dict[str, str]] = []
    origin_user = ""
    try:
        from core.messageHandler import messages, messages_lock

        with messages_lock:
            raw = list(messages)
    except Exception:
        raw = []

    for msg in raw:
        role = (msg.get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            continue
        # Skip pure tool-call-only assistant shells with no text
        content = _clip_msg_content(msg.get("content"), max_chars)
        if not content:
            # assistant sometimes has tool_calls only
            if role == "assistant" and msg.get("tool_calls"):
                continue
            continue
        # Skip stop_chat markers only lines
        cleaned = content.replace("[stop_chat]", "").replace("[STOP_CHAT]", "").strip()
        if not cleaned:
            continue
        turns.append({"role": role, "content": cleaned})
        if role == "user":
            origin_user = cleaned

    if max_turns > 0 and len(turns) > max_turns:
        turns = turns[-max_turns:]

    return {
        "origin_turns": turns,
        "origin_user_request": origin_user,
    }


def infer_announce_policy(task: Any) -> str:
    """
    How the main agent should speak when this job finishes.
    result_only: timer + main_action → only the action outcome
    silent_ok: pure silent timer with no action (rare)
    always: research / long resultful work
    auto: default judgment
    """
    meta = getattr(task, "metadata", None) or {}
    if meta.get("announce_policy"):
        return str(meta["announce_policy"])

    task_type = (getattr(task, "task_type", None) or "").lower()
    has_main_action = bool(str(meta.get("follow_up_main_action") or "").strip())
    has_follow_up = bool(meta.get("follow_up") or meta.get("follow_up_spawned_ids"))

    if task_type == "timer" and has_main_action:
        return "result_only"
    if task_type == "timer" and has_follow_up:
        return "result_only"
    if task_type == "timer":
        # Pure timer / alarm — user usually wants to hear it
        mode = (getattr(task, "alert_mode", None) or "silent").lower()
        if mode in ("short", "continuous", "notify"):
            return "always"
        return "silent_ok"
    if task_type in ("research", "mission", "delegate", "demo", "opencode", "coding"):
        return "always"
    if task_type == "smarthome":
        return "result_only"
    return "auto"


def infer_continue_hints(task: Any, brief: Optional[dict] = None) -> List[str]:
    hints: List[str] = []
    brief = brief or {}
    meta = getattr(task, "metadata", None) or {}
    task_type = (getattr(task, "task_type", None) or "").lower()

    path = (
        (brief.get("report_path") or "").strip()
        or (getattr(task, "artifact_path", None) or "")
        or ""
    )
    if path:
        hints.append(f"Open or read: {path}")
        lower = path.lower()
        if lower.endswith(".py"):
            hints.append(f"Run: python \"{path}\"")
        elif lower.endswith((".html", ".htm")):
            hints.append(f"Open in browser: {path}")
        elif lower.endswith((".md", ".txt", ".pdf", ".docx")):
            hints.append(f"Open file for user: {path}")
        # If path is a directory-looking report folder
        p = Path(path)
        if p.suffix == "" or p.is_dir():
            hints.append(f"Working directory: {path}")

    # Explicit metadata from workers
    for key in ("continue_hints", "run_command", "entry_point", "cwd"):
        val = meta.get(key)
        if isinstance(val, list):
            hints.extend(str(x) for x in val if x)
        elif isinstance(val, str) and val.strip():
            if key == "run_command":
                hints.append(f"Run: {val.strip()}")
            elif key == "entry_point":
                hints.append(f"Entry: {val.strip()}")
            elif key == "cwd":
                hints.append(f"cwd: {val.strip()}")
            elif key == "continue_hints":
                hints.append(val.strip())

    if task_type == "research" and path:
        hints.append("User may ask to open the full research report or summarize further.")
    if task_type in ("delegate", "mission", "opencode", "coding"):
        hints.append(
            "If user says run/open/continue the work, use artifacts above or inspect the result."
        )
    # OpenCode / Delegate session token for continue
    sid = (
        meta.get("opencode_session_id")
        or meta.get("delegate_session_id")
        or meta.get("session_id")
        or ""
    )
    if not sid and isinstance(getattr(task, "result", None), str):
        try:
            import json as _json

            parsed = _json.loads(task.result)
            if isinstance(parsed, dict):
                sid = parsed.get("session_id") or ""
                if parsed.get("directory"):
                    hints.append(f"cwd: {parsed.get('directory')}")
        except Exception:
            pass
    if sid:
        if task_type in ("opencode", "coding"):
            hints.append(f"Continue OpenCode: session_id={sid}")
            hints.append(
                f'background_task task_type=opencode session_id={sid} objective="..."'
            )
        elif task_type == "delegate":
            hints.append(f"Continue Delegate: session_id={sid}")
            hints.append(
                f'background_task task_type=delegate session_id={sid} objective="..."'
            )
        else:
            hints.append(f"Continue session_id={sid}")
    if meta.get("follow_up_main_action"):
        # After tools run, dossier still records what was done
        hints.append(f"Post-action was: {meta.get('follow_up_main_action')}")

    # Dedupe preserve order
    seen = set()
    out: List[str] = []
    for h in hints:
        h = str(h).strip()
        if not h or h in seen:
            continue
        seen.add(h)
        out.append(h)
    return out[:10]


def _mission_from_task(task: Any) -> str:
    oq = (getattr(task, "original_query", None) or "").strip()
    desc = (getattr(task, "description", None) or "").strip()
    name = (getattr(task, "name", None) or "").strip()
    return oq or desc or name


def attach_origin_to_task(task: Any, *, extra_context: str = "") -> TaskDossier:
    """
    Capture origin snapshot and attach to task.metadata + disk.
    Call once when the job is created (spawn).
    """
    snap = capture_origin_snapshot()
    mission = _mission_from_task(task)
    origin_user = snap.get("origin_user_request") or mission
    meta = getattr(task, "metadata", None)
    if meta is None:
        task.metadata = {}
        meta = task.metadata

    # Preserve any pre-set follow_up info
    parent_id = str(meta.get("follow_up_parent_id") or "")
    main_action = str(meta.get("follow_up_main_action") or "")

    # If follow_up main_action will be set later, announce_policy may update on finalize
    policy = infer_announce_policy(task)

    d = TaskDossier(
        task_id=getattr(task, "id", "") or "",
        task_type=getattr(task, "task_type", "") or "general",
        name=getattr(task, "name", "") or "",
        status=getattr(getattr(task, "status", None), "value", None)
        or str(getattr(task, "status", "pending")),
        conversation_id=getattr(task, "conversation_id", "") or "",
        conversation_name=getattr(task, "conversation_name", "") or "",
        original_query=getattr(task, "original_query", "") or "",
        mission=mission,
        origin_user_request=origin_user,
        origin_turns=list(snap.get("origin_turns") or []),
        artifacts=[],
        continue_hints=[],
        result_summary="",
        announce_policy=policy,
        held=True,
        parent_task_id=parent_id,
        follow_up_main_action=main_action,
    )
    if extra_context:
        # Stash worker-provided context into origin as a synthetic note
        d.origin_turns.append(
            {
                "role": "system",
                "content": clip_text(f"Spawn context: {extra_context}", MAX_TURN_CHARS),
            }
        )

    meta["dossier_id"] = d.task_id
    meta["origin_user_request"] = d.origin_user_request
    meta["mission"] = d.mission
    meta["announce_policy"] = d.announce_policy
    meta["origin_turns_count"] = len(d.origin_turns)
    # Compact copy for registry JSON (not full turns to keep tasks.json smaller)
    meta["dossier_brief"] = {
        "origin_user_request": d.origin_user_request,
        "mission": d.mission,
        "announce_policy": d.announce_policy,
        "held": True,
    }
    save_dossier(d)
    return d


def finalize_dossier(task: Any) -> TaskDossier:
    """
    Update dossier with result, artifacts, continue hints after terminal status.
    Always holds the dossier for later main-agent use.
    """
    task_id = getattr(task, "id", "") or ""
    d = load_dossier(task_id)
    if d is None:
        d = attach_origin_to_task(task)

    status = getattr(getattr(task, "status", None), "value", None) or str(
        getattr(task, "status", "completed")
    )
    d.status = status
    d.name = getattr(task, "name", None) or d.name
    d.task_type = getattr(task, "task_type", None) or d.task_type
    d.completed_at = datetime.now().isoformat()
    d.held = True

    meta = getattr(task, "metadata", None) or {}
    d.follow_up_main_action = str(meta.get("follow_up_main_action") or d.follow_up_main_action)
    d.announce_policy = infer_announce_policy(task)

    brief = parse_result_brief(
        getattr(task, "result", None)
        if getattr(task, "result", None) is not None
        else getattr(task, "error", None)
    )
    d.result_summary = (brief.get("summary") or "").strip() or (
        str(getattr(task, "error", None) or getattr(task, "result", None) or "completed")[:400]
    )

    artifacts: List[Dict[str, str]] = list(d.artifacts or [])
    paths_seen = {a.get("path") for a in artifacts if a.get("path")}

    def _add_art(path: str, kind: str = "file", label: str = "") -> None:
        path = (path or "").strip()
        if not path or path in paths_seen:
            return
        paths_seen.add(path)
        artifacts.append({"path": path, "kind": kind, "label": label or kind})

    report = (brief.get("report_path") or "").strip()
    if report:
        _add_art(report, "report", "research report" if d.task_type == "research" else "report")
    art = getattr(task, "artifact_path", None) or ""
    if art:
        _add_art(str(art), "file", "artifact")
    # metadata paths
    for key, kind, label in (
        ("report_path", "report", "report"),
        ("output_path", "file", "output"),
        ("project_path", "folder", "project"),
        ("cwd", "folder", "working directory"),
        ("entry_point", "file", "entry point"),
        ("opencode_directory", "folder", "opencode cwd"),
    ):
        val = meta.get(key)
        if isinstance(val, str) and val.strip():
            _add_art(val.strip(), kind, label)

    # OpenCode result JSON may embed directory + session
    try:
        raw = getattr(task, "result", None)
        if isinstance(raw, str) and raw.strip().startswith("{"):
            import json as _json

            parsed = _json.loads(raw)
            if isinstance(parsed, dict):
                if parsed.get("directory"):
                    _add_art(str(parsed["directory"]), "folder", "opencode project")
                if parsed.get("session_id"):
                    meta["opencode_session_id"] = parsed["session_id"]
                    meta["session_id"] = parsed["session_id"]
    except Exception:
        pass

    d.artifacts = artifacts
    d.continue_hints = infer_continue_hints(task, brief)

    # Keep origin fields fresh if spawn was thin
    if not d.origin_user_request:
        d.origin_user_request = meta.get("origin_user_request") or _mission_from_task(task)
    if not d.mission:
        d.mission = meta.get("mission") or _mission_from_task(task)

    save_dossier(d)

    # Mirror brief into task metadata for list UIs
    try:
        meta["dossier_brief"] = {
            "origin_user_request": d.origin_user_request,
            "mission": d.mission,
            "result_summary": clip_text(d.result_summary, 200),
            "artifacts": d.artifacts[:5],
            "continue_hints": d.continue_hints[:5],
            "announce_policy": d.announce_policy,
            "held": True,
            "completed_at": d.completed_at,
        }
        meta["announce_policy"] = d.announce_policy
        task.metadata = meta
    except Exception:
        pass

    return d


def get_dossier_for_task(task: Any) -> Optional[TaskDossier]:
    tid = getattr(task, "id", None) or ""
    d = load_dossier(tid)
    if d:
        return d
    # Fallback from metadata brief
    meta = getattr(task, "metadata", None) or {}
    brief = meta.get("dossier_brief") or {}
    if not brief and not meta.get("origin_user_request"):
        return None
    return TaskDossier(
        task_id=tid,
        task_type=getattr(task, "task_type", "") or "",
        name=getattr(task, "name", "") or "",
        status=str(getattr(getattr(task, "status", None), "value", "") or ""),
        conversation_id=getattr(task, "conversation_id", "") or "",
        conversation_name=getattr(task, "conversation_name", "") or "",
        original_query=getattr(task, "original_query", "") or "",
        mission=brief.get("mission") or meta.get("mission") or "",
        origin_user_request=brief.get("origin_user_request")
        or meta.get("origin_user_request")
        or "",
        result_summary=brief.get("result_summary") or "",
        artifacts=list(brief.get("artifacts") or []),
        continue_hints=list(brief.get("continue_hints") or []),
        announce_policy=brief.get("announce_policy")
        or meta.get("announce_policy")
        or "auto",
        held=True,
    )


def format_task_context(task_id: str, *, full: bool = True) -> str:
    d = load_dossier(task_id)
    if d is None:
        # Try registry
        try:
            from core.TaskManager import TaskRegistry

            task = TaskRegistry().get_task(task_id)
            if task:
                d = get_dossier_for_task(task)
                if d is None:
                    d = finalize_dossier(task) if task.result is not None else attach_origin_to_task(task)
        except Exception:
            d = None
    if d is None:
        return f"No dossier found for task {task_id}."
    return d.format_for_main(full=full)


def origin_context_for_worker(task: Any) -> str:
    d = get_dossier_for_task(task) or load_dossier(getattr(task, "id", "") or "")
    if d is None:
        return ""
    return d.origin_pack_for_worker()


def recent_completed_prompt(
    *,
    max_items: int = MAX_RECENT_INJECT,
    max_age_hours: Optional[float] = None,
) -> str:
    """
    Short block for system prompt: held finished jobs the main agent can still use.
    """
    if max_age_hours is None:
        max_age_hours = float(os.getenv("AVA_DOSSIER_RECENT_HOURS", "72"))

    lines: List[str] = []
    try:
        from core.TaskManager import TaskRegistry, TaskStatus

        registry = TaskRegistry()
        tasks = [
            t
            for t in registry.get_all_tasks()
            if t.status
            in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
                TaskStatus.NOTIFIED,
            )
        ]
        tasks.sort(
            key=lambda t: (t.completed_at or t.created_at or datetime.min),
            reverse=True,
        )
    except Exception as e:
        print(f"[Dossier] recent list failed: {e}")
        tasks = []

    now = datetime.now()
    count = 0
    for task in tasks:
        if count >= max_items:
            break
        ts = task.completed_at or task.created_at
        if ts and max_age_hours > 0:
            age_h = (now - ts).total_seconds() / 3600.0
            if age_h > max_age_hours:
                continue
        d = get_dossier_for_task(task)
        if d is None:
            # Thin synthetic line from task fields
            brief = parse_result_brief(task.result if task.result is not None else task.error)
            summary = brief.get("summary") or task.original_query or "done"
            path = brief.get("report_path") or task.artifact_path or ""
            line = (
                f"- {task.id}: [{task.task_type}] {clip_text(task.name, 40)} — "
                f"{clip_text(summary, 100)}"
            )
            if path:
                line += f" | {path}"
            lines.append(line)
        else:
            if not d.result_summary and task.result is not None:
                d = finalize_dossier(task)
            lines.append(d.short_line())
        count += 1

    if not lines:
        return "(none — no recent held task dossiers)"

    header = (
        "RECENT HELD TASK DOSSIERS (finished work you can still use — "
        "run/open/continue from artifacts; use manage_background_tasks action "
        "\"context\" with task_id for full origin + hints):\n"
    )
    return header + "\n".join(lines)


def speak_policy_lines(task: Any) -> List[str]:
    """Extra lines for to_compact_done about whether/how to inform the user."""
    policy = infer_announce_policy(task)
    meta = getattr(task, "metadata", None) or {}
    lines = [f"Announce policy: {policy}"]

    if policy == "result_only":
        action = str(meta.get("follow_up_main_action") or "").strip()
        lines.append(
            "SPEAK TO USER: only the final meaningful result "
            "(e.g. 'Message sent to Vinayak.'). "
            "Do NOT say the timer ended. Do NOT narrate internal steps."
        )
        if action:
            lines.append(f"User-facing intent: {action}")
        lines.append(
            "If tools succeed and the user already expected this, one short outcome line is enough. "
            "Skip process chatter."
        )
    elif policy == "silent_ok":
        lines.append(
            "SPEAK TO USER: optional. Stay silent if there is nothing useful to say. "
            "Do not invent a status monologue."
        )
    elif policy == "always":
        lines.append(
            "SPEAK TO USER: brief useful summary of the outcome; offer file path if any. "
            "Still skip empty filler."
        )
    else:
        lines.append(
            "SPEAK TO USER: only if they would care. Prefer outcome over process. "
            "You may stay silent for pure bookkeeping."
        )

    # Internal clarification wakes are different — handled in clarify inject
    lines.append(
        "Never re-explain old jobs. Dossier is held for later — if they ask to run/open later, use it."
    )
    return lines


def dossier_block_for_done(task: Any) -> str:
    """Compact origin + result for DONE inject (not the full chat dump)."""
    d = get_dossier_for_task(task)
    if d is None:
        d = finalize_dossier(task)
    else:
        # Refresh summary if thin
        if not d.result_summary and (
            getattr(task, "result", None) is not None or getattr(task, "error", None)
        ):
            d = finalize_dossier(task)

    lines = [
        "ORIGIN (why this job exists — use for wording the outcome):",
        f"  User wanted: {clip_text(d.origin_user_request or d.mission or d.original_query, 250)}",
    ]
    if d.conversation_name:
        lines.append(f"  From chat: {d.conversation_name}")
    if d.artifacts:
        lines.append("Artifacts still held:")
        for a in d.artifacts[:4]:
            lines.append(f"  - {a.get('path')}")
    if d.continue_hints:
        lines.append("Continue hints:")
        for h in d.continue_hints[:4]:
            lines.append(f"  - {h}")
    lines.extend(speak_policy_lines(task))
    return "\n".join(lines)


def enrich_clarification_context(job_id: str, context_snippet: str = "") -> str:
    """Append origin pack so main agent knows what the worker is building."""
    parts = []
    if context_snippet:
        parts.append(str(context_snippet).strip())
    d = load_dossier(job_id)
    if d is None:
        try:
            from core.TaskManager import TaskRegistry

            task = TaskRegistry().get_task(job_id)
            if task:
                d = get_dossier_for_task(task)
        except Exception:
            d = None
    if d:
        origin = d.origin_pack_for_worker(limit=800)
        if origin:
            parts.append("Job origin:\n" + origin)
        if d.mission:
            parts.append(f"Mission: {d.mission}")
    text = "\n\n".join(p for p in parts if p)
    return clip_text(text, 1500)


def should_hold_dossier(task: Any) -> bool:
    """Whether this job type keeps a dossier after finish (almost always yes if resultful)."""
    t = (getattr(task, "task_type", None) or "").lower()
    if t in RESULTFUL_TYPES:
        return True
    meta = getattr(task, "metadata", None) or {}
    if meta.get("follow_up_main_action") or meta.get("follow_up"):
        return True
    if getattr(task, "artifact_path", None):
        return True
    if t == "timer":
        return True  # thin hold for "what did you send" later
    return True
