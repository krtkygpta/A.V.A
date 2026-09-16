"""
coding_agent.py - Native Pi-inspired delegate coding agent for AVA.

Provides a self-contained, autonomous software engineering worker that:
1. Operates on local project directories or AVA's own repository (self-editing).
2. Uses focused precision tools: read_file, edit_file, write_file, search_code, list_dir, bash, code_executor.
3. Automatically validates python syntax on modified files to protect AVA during self-edits.
4. Exposes full tool-call history and activity steps to the main agent and TaskManager.
5. Supports multi-turn session persistence and continuation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import ROOT_DIR


CODING_WORKER_CORE_PROMPT = """You are AVA's elite autonomous coding delegate agent.
Your mission is to inspect, implement, debug, test, and verify software changes directly in the workspace.

RULES & WORKFLOW (Pi-inspired):
1. INSPECT FIRST:
   - Before modifying any file, inspect it using read_file or search_code.
   - Never guess file paths, function names, or existing line numbers.
   - Use list_dir to discover directory structure and search_code to locate references.

2. PRECISION EDITING:
   - Use edit_file for surgical find-and-replace. Provide exact target_content and replacement_content.
   - Make the minimal necessary changes. Match existing codebase style and formatting.
   - Use write_file only when creating brand new files or rewriting small generated configs.

3. EXECUTE & VERIFY:
   - After modifying files, use bash to verify your changes (e.g. `python -m py_compile <file>`, running unit tests, or checking syntax).
   - If tests or syntax checks fail, read the error output, fix the problem, and verify again.
   - When self-editing AVA: you must NEVER leave broken syntax or broken imports.

4. COLLABORATION:
   - If you encounter a high-risk destructive action or ambiguous user preference, call request_permission or request_clarification.
   - Do not stop until the objective is genuinely achieved and verified.

OUTPUT FORMAT (To AVA main agent):
1. Summary of what was modified, created, or debugged.
2. Verification outcome (syntax checks or test runs).
3. Exact file paths changed.
"""


def _detect_ava_self_edit(target_dir: str, objective: str) -> bool:
    """Check whether the coding task is targeting AVA's own repository."""
    try:
        ava_root = Path(ROOT_DIR).resolve()
        target = Path(target_dir).resolve() if target_dir else None
        if target and (target == ava_root or ava_root in target.parents):
            return True
    except Exception:
        pass

    obj_lower = (objective or "").lower()
    self_signals = (
        "edit yourself",
        "update yourself",
        "fix yourself",
        "modify ava",
        "ava repo",
        "in your code",
        "in your own code",
        "update your prompt",
        "add a tool to ava",
    )
    return any(s in obj_lower for s in self_signals)


def _validate_python_syntax(files: List[str]) -> Tuple[bool, str]:
    """Run py_compile syntax check on a list of python files."""
    failed = []
    for f in files:
        if not f.endswith(".py"):
            continue
        p = Path(f)
        if not p.exists():
            continue
        try:
            res = subprocess.run(
                [sys.executable, "-m", "py_compile", str(p)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if res.returncode != 0:
                failed.append(f"{p.name}: {res.stderr.strip() or res.stdout.strip()}")
        except Exception as e:
            failed.append(f"{p.name}: {e}")

    if failed:
        return False, "\n".join(failed)
    return True, "All modified Python files compiled cleanly."


def get_coding_tools_schema() -> List[Dict[str, Any]]:
    """Return tool definitions schema specifically for the coding worker."""
    from core.tool_definitions import tools
    from core.tool_registry import init_plugins, registry

    init_plugins()
    all_tools = list(tools) + list(registry.get_schema() or [])

    coding_tool_names = {
        "read_file",
        "edit_file",
        "write_file",
        "search_code",
        "list_dir",
        "bash",
        "code_executor",
        "request_clarification",
        "request_permission",
    }

    filtered = []
    seen = set()
    for t in all_tools:
        name = (t.get("function") or {}).get("name") or t.get("name")
        if name in coding_tool_names and name not in seen:
            seen.add(name)
            filtered.append(t)

    from core.TaskManager import REQUEST_CLARIFICATION_TOOL, REQUEST_PERMISSION_TOOL
    if "request_clarification" not in seen:
        filtered.append(REQUEST_CLARIFICATION_TOOL)
    if "request_permission" not in seen:
        filtered.append(REQUEST_PERMISSION_TOOL)

    return filtered


class CodingAgent:
    """
    SubAgent wrapper for native Pi-inspired coding sessions.
    Imported and instantiated inside TaskManager.py.
    """

    def __init__(
        self,
        objective: str = "",
        directory: str = "",
        session_id: str = "",
        model_tier: Optional[str] = None,
        continue_session: bool = False,
        title: str = "",
        description: str = "",
        context: str = "",
        conversation_id: str = "",
        conversation_name: str = "",
        priority: Any = None,
        kind: Optional[str] = None,
        permissions: str = "auto",
        **kwargs: Any,
    ):
        from core.TaskManager import SubAgent, TaskPriority

        self.objective = (objective or description or title or kwargs.get("prompt") or "").strip()
        if not self.objective:
            self.objective = "Inspect and improve code as requested."

        self.raw_directory = (directory or kwargs.get("dir") or kwargs.get("cwd") or "").strip()
        self.is_self_edit = _detect_ava_self_edit(self.raw_directory, self.objective)

        if not self.raw_directory:
            if self.is_self_edit:
                self.directory = str(ROOT_DIR)
            else:
                self.directory = str(Path.cwd())
        else:
            self.directory = str(Path(self.raw_directory).expanduser().resolve())

        self.session_id = (session_id or kwargs.get("session") or "").strip()
        self.continue_session = bool(continue_session or kwargs.get("continue", False))
        self.title = (title or description or self.objective[:60]).strip()
        self.context = (context or "").strip()
        self.permissions = (permissions or "auto").strip().lower()

        # Model tier: defaults to high for normal coding, very_high for self-editing
        if not model_tier or str(model_tier).lower() in ("none", "small", "nano"):
            self.model_tier = "very_high" if self.is_self_edit else "high"
        else:
            self.model_tier = str(model_tier).strip().lower()

        # Build underlying SubAgent instance
        self.subagent = SubAgent(
            task_name=f"Coding: {self.title[:35]}",
            task_description=self.objective,
            conversation_id=conversation_id,
            conversation_name=conversation_name,
            original_query=self.objective,
            task_type="coding",
            kind="long",
            priority=priority or TaskPriority.HIGH,
            model_tier=self.model_tier,
        )
        self.task = self.subagent.task
        if kwargs.get("origin"):
            self.task.metadata = dict(self.task.metadata or {})
            self.task.metadata["origin"] = kwargs["origin"]
            if kwargs.get("caller_id"):
                self.task.metadata["caller_id"] = kwargs["caller_id"]
        self.registry = self.subagent.registry

    def start(self):
        """Dispatch subagent execution thread via SubAgent.start()."""
        # Bind execute method to subagent
        self.subagent.execute = self.execute
        return self.subagent.start()

    def execute(self, **kwargs) -> str:
        """Main autonomous tool execution loop."""
        from core.server_api import generate_remote_response
        from core.FuncHandler import handle_tool_call
        from core.jobs.context import compact_worker_user_pack, clean_outcome_summary
        from core.jobs.delegate_session import (
            get_delegate_session_store,
            DelegateSessionRecord,
        )
        from core.TaskManager import set_current_worker

        store = get_delegate_session_store()
        sid = self.session_id
        session_rec: Optional[DelegateSessionRecord] = None
        is_continuing = False

        if not sid and self.continue_session:
            last = store.get_last_session()
            if last and ("code" in last.session_id or last.specialist == "coder"):
                sid = last.session_id

        if sid:
            session_rec = store.get(sid)
            if session_rec:
                is_continuing = True
                self.session_id = sid

        if not self.session_id:
            self.session_id = f"code_{uuid.uuid4().hex[:8]}"
            sid = self.session_id

        live = self.registry.get_task(self.task.id) or self.task
        try:
            live.metadata = dict(live.metadata or {})
            live.metadata["session_id"] = self.session_id
            live.metadata["coding_session_id"] = self.session_id
            live.metadata["directory"] = self.directory
            live.metadata["is_self_edit"] = self.is_self_edit
            live.metadata["model_tier"] = self.model_tier
            live.metadata["tool_history"] = []
            live.metadata["files_modified"] = []
            live.metadata["continue_hints"] = [
                f"Continue Coding: session_id={self.session_id}",
                f'background_task task_type=coding session_id={self.session_id} objective="..."',
            ]
        except Exception:
            pass

        # Build initial messages
        worker_tools = get_coding_tools_schema()
        system_prompt = (
            f"{CODING_WORKER_CORE_PROMPT}\n\n"
            f"CURRENT WORKING DIRECTORY: {self.directory}\n"
            f"TARGET REPO: {'AVA (Self-editing active)' if self.is_self_edit else self.directory}\n"
            f"MODEL TIER: {self.model_tier}\n"
        )

        user_content = (
            f"OBJECTIVE: {self.objective}\n"
            f"DIRECTORY: {self.directory}\n"
        )
        if self.context:
            user_content += f"ADDITIONAL CONTEXT:\n{self.context}\n"
        if self.is_self_edit:
            user_content += (
                "\nSAFETY NOTICE: You are editing AVA's codebase. "
                "Ensure every change preserves compatibility, and test with `python -m py_compile` before finishing.\n"
            )

        if is_continuing and session_rec and session_rec.messages:
            messages = list(session_rec.messages)
            # Update system prompt if first message
            if messages and messages[0].get("role") == "system":
                messages[0]["content"] = system_prompt
            messages.append({"role": "user", "content": f"Follow-up instruction:\n{user_content}"})
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]

        # Register running session record
        if not session_rec:
            session_rec = DelegateSessionRecord(
                session_id=self.session_id,
                title=self.objective[:80],
                specialist="coder",
                status="running",
                task_id=self.task.id,
                messages=list(messages),
                summary="Starting coding agent execution...",
                metadata={"directory": self.directory, "is_self_edit": self.is_self_edit},
            )
        else:
            session_rec.status = "running"
            session_rec.messages = list(messages)
            session_rec.task_id = self.task.id
        store.upsert(session_rec)

        max_rounds = int(os.getenv("AVA_CODING_MAX_ROUNDS", "20"))
        tool_history: List[Dict[str, Any]] = []
        files_modified: set[str] = set()
        final_content = ""

        set_current_worker(self.subagent)
        try:
            for _round in range(max_rounds):
                if self.subagent.is_cancelled():
                    final_content = "Coding agent was cancelled by user or main agent."
                    break

                if hasattr(self.task, "metadata") and isinstance(self.task.metadata, dict):
                    self.task.metadata["current_activity"] = f"Reasoning & planning (round {_round + 1}/{max_rounds})..."

                response = None
                for _attempt in range(3):
                    try:
                        response = generate_remote_response(
                            messages,
                            tools=worker_tools,
                            model_tier=self.model_tier,
                            include_server_context=False,
                        )
                        break
                    except Exception as e:
                        if self.subagent.is_cancelled():
                            break
                        print(f"[CodingAgent] generate attempt {_attempt + 1}/3 failed: {e}")
                        if _attempt < 2:
                            time.sleep(2.0 * (_attempt + 1))
                        else:
                            raise e

                if not response:
                    break

                content = response.get("content")
                tool_calls = response.get("tool_calls") or []

                assistant_msg = {"role": "assistant"}
                if content:
                    assistant_msg["content"] = content
                    final_content = content
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                messages.append(assistant_msg)

                # Persist live progress
                session_rec.messages = list(messages)
                session_rec.summary = f"Round {_round + 1}: {clean_outcome_summary(content, 140) if content else 'Executing tools...'}"
                store.upsert(session_rec)

                if not tool_calls:
                    # Agent completed without further tool calls
                    break

                # Execute tool calls
                for tool_call in tool_calls:
                    if self.subagent.is_cancelled():
                        final_content = "Coding agent was cancelled."
                        break

                    if isinstance(tool_call, dict):
                        function_payload = tool_call.get("function", {}) or {}
                        func_name = function_payload.get("name")
                        args_raw = function_payload.get("arguments", "{}")
                        tool_id = tool_call.get("id") or "unknown"
                    else:
                        func_name = tool_call.function.name
                        args_raw = tool_call.function.arguments
                        tool_id = getattr(tool_call, "id", None) or "unknown"

                    if isinstance(args_raw, str):
                        try:
                            args = json.loads(args_raw) if args_raw.strip() else {}
                        except json.JSONDecodeError:
                            args = {}
                    elif isinstance(args_raw, dict):
                        args = args_raw
                    else:
                        args = {}

                    # Ensure relative file paths resolve against coding agent's target directory
                    if func_name in ("read_file", "edit_file", "write_file", "search_code", "list_dir"):
                        target_p = args.get("path") or ""
                        if target_p and not Path(target_p).is_absolute():
                            args["path"] = str(Path(self.directory) / target_p)
                        if func_name in ("edit_file", "write_file"):
                            files_modified.add(args.get("path") or "")

                    if hasattr(self.task, "metadata") and isinstance(self.task.metadata, dict):
                        self.task.metadata["current_activity"] = f"Executing {func_name} ({args.get('path') or args.get('command') or ''})..."

                    # Special delegation asks
                    if func_name == "request_permission":
                        action = str(args.get("action", "")).strip()
                        reason = str(args.get("reason", "")).strip()
                        risk = str(args.get("risk_level", "medium")).strip().lower()
                        result = self.subagent.ask_main(
                            question=f"Permission requested: {action}. Reason: {reason}",
                            options=["grant", "deny"],
                            context_snippet=f"Action: {action}\nReason: {reason}\nRisk: {risk}",
                            default_if_timeout="grant" if risk == "low" else "deny",
                            require_user=False,
                            allow_main_self_answer=True,
                        )
                    elif func_name == "request_clarification":
                        result = self.subagent.ask_main(
                            question=str(args.get("question", "")),
                            options=args.get("options"),
                            context_snippet=str(args.get("context_snippet", "") or ""),
                            default_if_timeout=args.get("default_if_timeout"),
                            require_user=bool(args.get("require_user", False)),
                            allow_main_self_answer=True,
                        )
                    else:
                        result, tool_id = handle_tool_call({
                            "id": tool_id,
                            "type": "function",
                            "function": {"name": func_name, "arguments": json.dumps(args)},
                        })

                    # Log step to tool history
                    res_str = result if isinstance(result, str) else json.dumps(result)
                    step_entry = {
                        "round": _round + 1,
                        "tool": func_name,
                        "args": args,
                        "summary": res_str[:220] if len(res_str) > 220 else res_str,
                        "status": "error" if "error" in res_str.lower()[:30] else "ok",
                    }
                    tool_history.append(step_entry)

                    if hasattr(self.task, "metadata") and isinstance(self.task.metadata, dict):
                        self.task.metadata["tool_history"] = list(tool_history)
                        self.task.metadata["files_modified"] = list(files_modified)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id or "unknown",
                        "name": func_name,
                        "content": res_str,
                    })

                    session_rec.messages = list(messages)
                    store.upsert(session_rec)

                if self.subagent.is_cancelled():
                    break
        finally:
            set_current_worker(None)

        # Post-execution verification for self-editing safety
        syntax_ok, syntax_msg = _validate_python_syntax(list(files_modified))
        if not syntax_ok:
            final_content += f"\n\n[WARNING: Python Syntax Error Detected in Modified Files]:\n{syntax_msg}"

        if not final_content and not self.subagent.is_cancelled():
            mod_count = len(files_modified)
            final_content = f"Coding agent finished. Modified {mod_count} file(s) in {self.directory}. Syntax check: {syntax_msg}"

        # Final session update
        status = "cancelled" if self.subagent.is_cancelled() else "completed"
        session_rec.status = status
        session_rec.messages = list(messages)
        session_rec.summary = clean_outcome_summary(str(final_content), 280)
        session_rec.metadata = {
            "directory": self.directory,
            "is_self_edit": self.is_self_edit,
            "files_modified": list(files_modified),
            "tool_history_count": len(tool_history),
            "syntax_check": syntax_msg,
        }
        store.upsert(session_rec)

        if hasattr(self.task, "metadata") and isinstance(self.task.metadata, dict):
            self.task.metadata["status"] = status
            self.task.metadata["summary"] = session_rec.summary
            self.task.metadata["current_activity"] = "Completed"
            self.task.metadata["tool_history"] = list(tool_history)
            self.task.metadata["files_modified"] = list(files_modified)

        return final_content
