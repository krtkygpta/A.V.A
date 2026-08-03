"""
OpenCode CLI bridge for AVA background jobs.

Uses the installed ``opencode`` binary (user's default model/providers).
Does NOT override --model unless explicitly requested.

Session ids (ses_...) are stored under App/data/tasks/opencode_sessions.json
so the main agent can continue work later.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

SESSIONS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "data"
    / "tasks"
    / "opencode_sessions.json"
)

# Default timeout for a single opencode run (seconds)
DEFAULT_TIMEOUT = int(os.getenv("AVA_OPENCODE_TIMEOUT", "1800"))  # 30 min

_lock = threading.RLock()


def find_opencode_bin() -> str:
    """Resolve opencode executable. Prefer the real .exe over npm .cmd/.ps1 wrappers."""
    env = (os.getenv("AVA_OPENCODE_BIN") or "").strip()
    if env and Path(env).exists():
        return env

    # Prefer native binary (avoids PowerShell wrapper mangling long args)
    candidates = [
        Path.home()
        / "AppData"
        / "Roaming"
        / "npm"
        / "node_modules"
        / "opencode-ai"
        / "bin"
        / "opencode.exe",
        Path.home()
        / "AppData"
        / "Roaming"
        / "npm"
        / "node_modules"
        / "opencode-ai"
        / "bin"
        / "opencode",
    ]
    for c in candidates:
        if c.exists():
            return str(c)

    for name in ("opencode.exe", "opencode", "opencode.cmd"):
        found = shutil.which(name)
        if found:
            # Prefer .exe sibling if .cmd was found
            p = Path(found)
            if p.suffix.lower() in (".cmd", ".ps1"):
                npm_exe = (
                    p.parent
                    / "node_modules"
                    / "opencode-ai"
                    / "bin"
                    / "opencode.exe"
                )
                if npm_exe.exists():
                    return str(npm_exe)
            return found

    raise FileNotFoundError(
        "opencode CLI not found on PATH. Install: npm i -g opencode-ai@latest "
        "or set AVA_OPENCODE_BIN to the binary path."
    )


def _permission_env(use_auto: bool, workdir: str) -> Dict[str, str]:
    """
    Env overrides so headless runs can edit/bash without interactive TUI asks.
    Explicit deny rules still win; --auto covers residual 'ask' defaults.
    """
    env = os.environ.copy()
    if not use_auto:
        # Safe mode: leave user's config alone
        return env

    # Allow everything not denied (external_directory defaults to ask)
    work = str(workdir).replace("\\", "/")
    # also allow parent patterns with **
    perm = {
        "*": "allow",
        "edit": "allow",
        "bash": "allow",
        "read": "allow",
        "write": "allow",
        "glob": "allow",
        "grep": "allow",
        "external_directory": {
            "*": "allow",
            f"{work}/**": "allow",
            f"{work}/*": "allow",
        },
        "doom_loop": "ask",
    }
    # OPENCODE_PERMISSION = inlined permission config
    env["OPENCODE_PERMISSION"] = json.dumps(perm)
    # Inline full config fragment (permission allow-all) if supported
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
        {"$schema": "https://opencode.ai/config.json", "permission": perm}
    )
    return env


def _write_task_files(
    directory: str,
    objective: str,
    *,
    origin: str = "",
    context: str = "",
) -> Tuple[str, str]:
    """
    Write AVA_TASK.md into workdir. Return (task_path, short_cli_message).

    Keeps the CLI argv short (Windows ~8k limit); OpenCode reads the file.
    """
    work = Path(directory)
    work.mkdir(parents=True, exist_ok=True)
    task_path = work / "AVA_TASK.md"
    parts = [
        "# AVA OpenCode Task",
        "",
        "Complete this task fully. Do **not** ask what to work on.",
        "Use tools (write/edit/bash) as needed. Stay under this working directory.",
        "",
        "## Objective",
        (objective or "").strip() or "(missing objective)",
        "",
    ]
    if context:
        parts += ["## Extra context", context.strip()[:2000], ""]
    if origin:
        parts += ["## Origin (why)", origin.strip()[:1500], ""]
    parts += [
        "## Done criteria",
        "- Files created/updated on disk",
        "- Short summary of what you did + how to run/open",
        "- Do not reply with only 'ready' / 'what would you like'",
        "",
    ]
    task_path.write_text("\n".join(parts), encoding="utf-8")

    # Short CLI message — must stay well under Windows arg limits
    short = (
        f"Read and complete the task in AVA_TASK.md in this working directory. "
        f"Use write/edit tools. Do not ask clarifying questions unless blocked. "
        f"Objective summary: {(objective or '')[:400]}"
    )
    return str(task_path), short


@dataclass
class OpenCodeSessionRecord:
    session_id: str
    description: str = ""
    directory: str = ""
    title: str = ""
    job_id: str = ""
    status: str = "unknown"  # running | completed | failed | cancelled
    last_prompt: str = ""
    last_summary: str = ""
    permissions: str = "auto"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    project_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OpenCodeSessionRecord":
        known = set(cls.__dataclass_fields__.keys())  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


class OpenCodeSessionStore:
    """Persistent AVA-side index of OpenCode session tokens + descriptions."""

    def __init__(self, path: Path = SESSIONS_PATH):
        self.path = path
        self._sessions: Dict[str, OpenCodeSessionRecord] = {}
        self._load()

    def _load(self) -> None:
        with _lock:
            if not self.path.exists():
                return
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                for raw in data.get("sessions") or []:
                    if not isinstance(raw, dict) or not raw.get("session_id"):
                        continue
                    rec = OpenCodeSessionRecord(
                        session_id=str(raw["session_id"]),
                        description=str(raw.get("description") or ""),
                        directory=str(raw.get("directory") or ""),
                        title=str(raw.get("title") or ""),
                        job_id=str(raw.get("job_id") or ""),
                        status=str(raw.get("status") or "unknown"),
                        last_prompt=str(raw.get("last_prompt") or ""),
                        last_summary=str(raw.get("last_summary") or ""),
                        permissions=str(raw.get("permissions") or "auto"),
                        created_at=str(raw.get("created_at") or datetime.now().isoformat()),
                        updated_at=str(raw.get("updated_at") or datetime.now().isoformat()),
                        project_id=str(raw.get("project_id") or ""),
                    )
                    self._sessions[rec.session_id] = rec
            except Exception as e:
                print(f"[OpenCode] session store load failed: {e}")

    def _save(self) -> None:
        with _lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                payload = {
                    "updated_at": datetime.now().isoformat(),
                    "sessions": [s.to_dict() for s in self._sessions.values()],
                }
                self.path.write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception as e:
                print(f"[OpenCode] session store save failed: {e}")

    def upsert(self, rec: OpenCodeSessionRecord) -> None:
        with _lock:
            rec.updated_at = datetime.now().isoformat()
            existing = self._sessions.get(rec.session_id)
            if existing and not rec.created_at:
                rec.created_at = existing.created_at
            elif existing and existing.created_at:
                rec.created_at = existing.created_at
            self._sessions[rec.session_id] = rec
            self._save()

    def get(self, session_id: str) -> Optional[OpenCodeSessionRecord]:
        with _lock:
            return self._sessions.get(session_id)

    def list_recent(self, limit: int = 20) -> List[OpenCodeSessionRecord]:
        with _lock:
            items = list(self._sessions.values())
        items.sort(key=lambda s: s.updated_at or s.created_at, reverse=True)
        return items[:limit]

    def find_by_description(self, query: str, limit: int = 5) -> List[OpenCodeSessionRecord]:
        q = (query or "").lower().strip()
        if not q:
            return self.list_recent(limit)
        scored: List[Tuple[int, OpenCodeSessionRecord]] = []
        for s in self.list_recent(50):
            blob = f"{s.description} {s.title} {s.last_prompt} {s.directory}".lower()
            score = sum(1 for tok in q.split() if tok in blob)
            if score or q in blob:
                scored.append((score, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:limit]]


_store: Optional[OpenCodeSessionStore] = None


def get_session_store() -> OpenCodeSessionStore:
    global _store
    if _store is None:
        _store = OpenCodeSessionStore()
    return _store


def list_native_sessions(max_count: int = 20) -> List[Dict[str, Any]]:
    """List sessions from OpenCode itself (session list --format json)."""
    bin_path = find_opencode_bin()
    try:
        proc = subprocess.run(
            [bin_path, "session", "list", "--format", "json", "-n", str(max_count)],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        out = (proc.stdout or "").strip()
        if not out:
            return []
        data = json.loads(out)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[OpenCode] session list failed: {e}")
        return []


def _parse_json_events(stdout: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            events.append(json.loads(line))
        except Exception:
            continue
    return events


def _extract_session_id(events: List[Dict[str, Any]], stdout: str = "") -> str:
    for ev in events:
        sid = ev.get("sessionID") or (ev.get("part") or {}).get("sessionID")
        if sid:
            return str(sid)
    m = re.search(r"ses_[A-Za-z0-9]+", stdout or "")
    return m.group(0) if m else ""


def _extract_text_parts(events: List[Dict[str, Any]]) -> str:
    chunks: List[str] = []
    for ev in events:
        if ev.get("type") == "text":
            part = ev.get("part") or {}
            t = part.get("text") or ""
            if t:
                chunks.append(str(t))
        # some versions nest text differently
        part = ev.get("part") or {}
        if part.get("type") == "text" and part.get("text"):
            t = str(part["text"])
            if t not in chunks:
                chunks.append(t)
    return "\n".join(chunks).strip()


def _extract_tools_summary(events: List[Dict[str, Any]], limit: int = 12) -> List[str]:
    lines: List[str] = []
    for ev in events:
        if ev.get("type") != "tool_use":
            continue
        part = ev.get("part") or {}
        tool = part.get("tool") or "tool"
        state = part.get("state") or {}
        status = state.get("status") or ""
        title = state.get("title") or ""
        err = state.get("error") or ""
        if err:
            lines.append(f"{tool}: error — {str(err)[:120]}")
        else:
            lines.append(f"{tool}: {status} {title}"[:160])
        if len(lines) >= limit:
            break
    return lines


def _slugify(name: str, max_len: int = 40) -> str:
    s = re.sub(r"[^\w\s\-]+", "", (name or "").strip(), flags=re.UNICODE)
    s = re.sub(r"[\s_]+", "-", s).strip("-").lower()
    return (s or "project")[:max_len]


def project_dir_for(title: str = "", description: str = "") -> str:
    """
    Default workspace for a *new* OpenCode build: Desktop/AVA_OpenCode/<slug>.
    Never the AVA repo cwd.
    """
    try:
        from utils.paths import get_desktop

        base = get_desktop() / "AVA_OpenCode"
    except Exception:
        base = Path.home() / "Desktop" / "AVA_OpenCode"
    slug = _slugify(title or description or "session")
    path = base / slug
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return str(path.resolve())


def resolve_directory(
    directory: str = "",
    *,
    title: str = "",
    description: str = "",
    create_project: bool = False,
) -> str:
    """
    Resolve OpenCode working directory to a real absolute path.

    - Empty + create_project → Desktop/AVA_OpenCode/<slug>
    - Empty + not create → real Desktop
    - "desktop", "documents", "downloads", "desktop/foo" via utils.paths
    - Bare relative names like "my-app" → Desktop/AVA_OpenCode/my-app
      (never under the AVA source tree)
    """
    d = (directory or "").strip().strip('"').strip("'")

    # Explicit empty → project folder or desktop
    if not d:
        if create_project:
            return project_dir_for(title, description)
        try:
            from utils.paths import get_desktop

            return str(get_desktop())
        except Exception:
            return str(Path.home() / "Desktop")

    # Prefer shared path resolver (desktop/foo, %USERPROFILE%, …)
    try:
        from utils.paths import resolve_location, get_desktop

        raw = d
        low = raw.replace("\\", "/").strip().lower()
        # Bare project-ish name without path separators → under AVA_OpenCode
        if (
            "/" not in low
            and "\\" not in raw
            and low
            not in (
                "desktop",
                "desk",
                "documents",
                "docs",
                "downloads",
                "download",
                "home",
                "~",
            )
            and not Path(raw).is_absolute()
            and not raw.startswith("%")
            and not raw.startswith("~")
        ):
            path = get_desktop() / "AVA_OpenCode" / _slugify(raw)
            path.mkdir(parents=True, exist_ok=True)
            return str(path.resolve())

        path = resolve_location(raw, create=True)
        return str(path)
    except Exception:
        pass

    # Fallback aliases
    low = d.lower().replace("\\", "/").strip("/")
    if low in ("desktop", "desk"):
        p = Path.home() / "Desktop"
    elif low in ("documents", "docs"):
        p = Path.home() / "Documents"
    elif low in ("downloads", "dl"):
        p = Path.home() / "Downloads"
    else:
        p = Path(d).expanduser()
        if not p.is_absolute():
            # Never resolve relative against AVA repo cwd
            p = Path.home() / "Desktop" / "AVA_OpenCode" / _slugify(d)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    try:
        return str(p.resolve())
    except Exception:
        return str(p)


def _looks_idle(text: str, tools: List[str]) -> bool:
    """True if OpenCode replied without doing work (classic greeting)."""
    if tools:
        return False
    t = (text or "").strip().lower()
    if not t:
        return True
    idle_markers = (
        "what would you like",
        "how can i help",
        "ready.",
        "standing by",
        "i'm ready",
        "i am ready",
        "awaiting",
        "what do you want me to",
    )
    return any(m in t for m in idle_markers)


def run_opencode(
    prompt: str,
    *,
    directory: str = "",
    session_id: str = "",
    title: str = "",
    permissions: str = "auto",
    continue_last: bool = False,
    job_id: str = "",
    description: str = "",
    timeout: Optional[int] = None,
    model: str = "",
    origin: str = "",
    context: str = "",
    on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """
    Run ``opencode run`` non-interactively.

    permissions:
      auto | allow → --auto + OPENCODE_PERMISSION allow (headless-friendly)
      deny | safe  → no --auto
      ask          → caller should resolve to auto/deny before calling

    Does not pass --model unless ``model`` is set (uses OpenCode defaults).

    Long objectives go into AVA_TASK.md so Windows argv length is not exceeded.
    """
    bin_path = find_opencode_bin()
    directory = resolve_directory(directory)
    timeout = int(timeout or DEFAULT_TIMEOUT)
    perm = (permissions or "auto").strip().lower()
    use_auto = perm in ("auto", "allow", "yes", "true", "1", "ask_granted")

    objective = (prompt or "").strip()
    if not objective:
        # Should be filled by OpenCodeAgent; last guard with a usable default
        objective = (
            (description or title or "").strip()
            or "Apply the user's latest coding request in this working directory using tools."
        )
    if not objective.strip():
        raise ValueError(
            "opencode prompt is empty — pass objective/prompt with the coding instruction "
            "(title/session_id alone is not enough)"
        )

    task_path, short_msg = _write_task_files(
        directory,
        objective,
        origin=origin,
        context=context,
    )

    def _build_cmd(message: str, *, force_new: bool = False) -> List[str]:
        cmd: List[str] = [
            bin_path,
            "run",
            "--format",
            "json",
            "--dir",
            directory,
        ]
        if title:
            cmd.extend(["--title", title[:120]])
        # Only continue when explicitly asked — never reuse a dead "ready" session
        if session_id and not force_new:
            cmd.extend(["--session", session_id])
        elif continue_last and not force_new:
            cmd.append("--continue")
        if use_auto:
            cmd.append("--auto")
        if model and str(model).strip():
            cmd.extend(["--model", str(model).strip()])
        # Attach task file so model always sees full objective.
        # --file is an array option; without "--" the message is treated as another file.
        cmd.extend(["--file", task_path])
        cmd.append("--")
        cmd.append(message[:1800])  # hard cap CLI message
        return cmd

    def _run_once(cmd: List[str]) -> Dict[str, Any]:
        print(
            f"[OpenCode] run bin={Path(bin_path).name} dir={directory} "
            f"session={session_id or '(new)'} auto={use_auto} "
            f"argv_chars≈{sum(len(c) for c in cmd)} title={title[:40]!r}"
        )
        stdout_lines: List[str] = []
        stderr_lines: List[str] = []
        events: List[Dict[str, Any]] = []
        env = _permission_env(use_auto, directory)

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=directory if Path(directory).is_dir() else None,
                env=env,
            )
        except Exception as e:
            return {
                "status": "error",
                "content": f"Failed to start opencode: {e}",
                "session_id": session_id or "",
                "directory": directory,
                "events": [],
                "tools": [],
                "text": "",
                "exit_code": -1,
                "stderr_tail": "",
            }

        start = time.time()
        try:
            assert proc.stdout is not None
            while True:
                if is_cancelled and is_cancelled():
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        proc.kill()
                    return {
                        "status": "cancelled",
                        "content": "OpenCode job cancelled.",
                        "session_id": _extract_session_id(events) or session_id,
                        "directory": directory,
                        "events": events,
                        "tools": _extract_tools_summary(events),
                        "text": _extract_text_parts(events),
                        "exit_code": -1,
                        "stderr_tail": "",
                        "events_count": len(events),
                    }
                if time.time() - start > timeout:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        proc.kill()
                    return {
                        "status": "error",
                        "content": f"OpenCode timed out after {timeout}s",
                        "session_id": _extract_session_id(events) or session_id,
                        "directory": directory,
                        "events": events,
                        "tools": _extract_tools_summary(events),
                        "text": _extract_text_parts(events),
                        "exit_code": -1,
                        "stderr_tail": "",
                    }

                line = proc.stdout.readline()
                if line:
                    stdout_lines.append(line)
                    line_s = line.strip()
                    if line_s.startswith("{"):
                        try:
                            ev = json.loads(line_s)
                            events.append(ev)
                            if on_event:
                                try:
                                    on_event(ev)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                elif proc.poll() is not None:
                    rest = proc.stdout.read() or ""
                    if rest:
                        stdout_lines.append(rest)
                        for ln in rest.splitlines():
                            ln = ln.strip()
                            if ln.startswith("{"):
                                try:
                                    events.append(json.loads(ln))
                                except Exception:
                                    pass
                    break
                else:
                    time.sleep(0.05)

            err = ""
            if proc.stderr:
                err = proc.stderr.read() or ""
                stderr_lines.append(err)
        except Exception as e:
            try:
                proc.kill()
            except Exception:
                pass
            return {
                "status": "error",
                "content": f"OpenCode run error: {e}",
                "session_id": session_id or "",
                "directory": directory,
                "events": events,
                "tools": [],
                "text": "",
                "exit_code": -1,
                "stderr_tail": str(e)[:300],
            }

        full_out = "".join(stdout_lines)
        sid = _extract_session_id(events, full_out) or session_id
        text = _extract_text_parts(events)
        tools = _extract_tools_summary(events)
        code = proc.returncode if proc.returncode is not None else -1
        return {
            "status": "success" if code == 0 else "error",
            "content": text or ("".join(stderr_lines)[-400:] if stderr_lines else f"exit={code}"),
            "summary": text[:600] if text else "",
            "session_id": sid,
            "directory": directory,
            "exit_code": code,
            "tools": tools,
            "events": events,
            "events_count": len(events),
            "stderr_tail": ("".join(stderr_lines))[-400:],
            "text": text,
            "_full_out": full_out,
        }

    # Attempt 1: normal (maybe continue session)
    result = _run_once(_build_cmd(short_msg, force_new=False))

    # Retry once if OpenCode only greets (common when session is stale or prompt was truncated)
    if (
        result.get("status") != "cancelled"
        and _looks_idle(result.get("text") or result.get("summary") or "", result.get("tools") or [])
        and not (is_cancelled and is_cancelled())
    ):
        print("[OpenCode] idle/no-tools response — retrying with fresh session + punchier prompt")
        punchy = (
            f"CRITICAL: Execute now. Read AVA_TASK.md. "
            f"Create/edit the required files in {directory}. "
            f"Do not greet. Do not ask what to do. "
            f"Objective: {objective[:500]}"
        )
        result = _run_once(_build_cmd(punchy, force_new=True))

    # Finalize packaging
    sid = (result.get("session_id") or session_id or "").strip()
    text = result.get("text") or result.get("summary") or ""
    tools = result.get("tools") or []
    code = result.get("exit_code", -1)

    if sid:
        store = get_session_store()
        store.upsert(
            OpenCodeSessionRecord(
                session_id=sid,
                description=description or title or objective[:120],
                directory=directory,
                title=title or description or "",
                job_id=job_id,
                status=(
                    "cancelled"
                    if result.get("status") == "cancelled"
                    else ("completed" if code == 0 and not _looks_idle(text, tools) else "failed")
                ),
                last_prompt=objective[:2000],
                last_summary=(text or "")[:800],
                permissions=perm,
            )
        )

    summary_parts: List[str] = []
    if text:
        summary_parts.append(text[:1200])
    if tools:
        summary_parts.append("Tools: " + "; ".join(tools[:8]))
    if _looks_idle(text, tools):
        summary_parts.insert(
            0,
            "OpenCode did not run tools (idle reply). "
            f"Task file was at {task_path}. Try again or check OpenCode auth/model.",
        )
        result["status"] = "error"
    if not summary_parts:
        summary_parts.append(result.get("stderr_tail") or f"opencode exit={code}")

    if "rejected permission" in (result.get("_full_out") or "").lower() or "auto-rejecting" in (
        result.get("stderr_tail") or ""
    ).lower():
        if not tools:
            result["status"] = "error"
            summary_parts.insert(
                0,
                "Permission denied for tools. Ensure permissions=auto "
                "(OPENCODE_PERMISSION allow + --auto).",
            )

    return {
        "status": result.get("status") or "error",
        "content": "\n".join(summary_parts),
        "summary": (text[:600] if text else summary_parts[0][:600]),
        "session_id": sid,
        "directory": directory,
        "exit_code": code,
        "tools": tools,
        "events_count": result.get("events_count") or len(result.get("events") or []),
        "stderr_tail": result.get("stderr_tail") or "",
        "task_file": task_path,
    }


def sessions_prompt_block(limit: int = 8) -> str:
    """Compact list for system prompt / status."""
    store = get_session_store()
    rows = store.list_recent(limit)
    if not rows:
        # fall back to native list
        native = list_native_sessions(limit)
        if not native:
            return "(none)"
        lines = []
        for s in native[:limit]:
            lines.append(
                f"- {s.get('id')}: {s.get('title') or '(untitled)'} "
                f"dir={s.get('directory') or '?'}"
            )
        return "OPENCODE SESSIONS (native):\n" + "\n".join(lines)

    lines = ["OPENCODE SESSIONS (held — continue with session_id):"]
    for s in rows:
        lines.append(
            f"- {s.session_id}: {s.description or s.title or '(no desc)'} "
            f"[{s.status}] dir={s.directory or '?'} "
            f"job={s.job_id or '-'}"
        )
    return "\n".join(lines)
