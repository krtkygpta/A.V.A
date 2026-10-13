"""
layer1_avatar.py - The Executive Avatar (System 1 AI Reflex & Conversational Front).

Owns:
- AI-driven Intent Triage: Conversational reflex vs Delegation to Layer 2 Chief of Staff
- Dynamic natural human assistant persona for Kartikey (zero canned / hardcoded responses)
- Instant spoken/text kickoff phrasing formulated by AI for delegated tasks
- Direct status reporting for in-flight tasks
- Dispatching execution tickets to Layer 2
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
from typing import Any, Dict, List, Optional

from config import get_settings
from services.blackboard import blackboard
from services.layer2_orchestrator import layer2_orchestrator
from services.llm_service import LLMService, get_base_behavior_prompt

logger = logging.getLogger("AvaLayer1Avatar")

DELEGATE_TOOL = {
    "type": "function",
    "function": {
        "name": "delegate_to_layer2",
        "description": (
            "Call this tool ONLY when the user request requires time, effort, tools, device queries "
            "(e.g. checking battery levels across devices, pinging nodes, hardware actions), "
            "script execution, coding, deep research, or multi-step background work. "
            "Do NOT call this tool for conversational queries, greetings, banter, chit-chat, explanations, "
            "opinions, or advice."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "The exact objective for Layer 2 Chief of Staff to accomplish."
                },
                "kickoff_response": {
                    "type": "string",
                    "description": (
                        "A natural, immediate spoken/text acknowledgment crafted specifically for Kartikey to hear "
                        "while Layer 2 works in the background (e.g., 'Checking battery levels across all your devices right now, give me a moment.')."
                    )
                }
            },
            "required": ["goal", "kickoff_response"]
        }
    }
}


def get_layer1_system_prompt(user_name: str = "Kartikey") -> str:
    """
    Builds Layer 1 prompt using the exact same base behavior prompt as Layer 2.
    """
    base = get_base_behavior_prompt(user_name=user_name)
    return f"""{base}

    DUAL-LAYER ROUTING LOGIC:
    1. CONVERSATIONAL & SIMPLE QUERIES:
       - Greetings (e.g. "wassup", "hey", "good morning"), banter, jokes, chit-chat, personal remarks (e.g. "That was fast"), opinions, quick explanations, advice, current time/date, or general conversation:
       - Respond DIRECTLY to {user_name} as AVA with natural wit, charm, and human assistant presence.
       - Do NOT call `delegate_to_layer2`.

    2. TASKS REQUIRING TIME & EFFORT:
       - Any query requiring real tools, device queries (e.g. checking battery levels across devices, pinging nodes, device hardware actions), running shell scripts, writing code, multi-step actions, or external research:
       - Call `delegate_to_layer2` with:
         * `goal`: The precise objective for Layer 2 Chief of Staff.
         * `kickoff_response`: A natural, spontaneous spoken/text acknowledgment crafted for {user_name} to hear while Layer 2 works (e.g., "Checking battery levels across all your devices right now — give me just a second.").

    3. STATUS INQUIRIES:
       - If {user_name} asks about the status or progress of an ongoing or recent background task, answer directly using the active jobs context provided below.
"""


class Layer1Avatar:
    """Executive AI front-of-house managing conversational presence and fast reflexes."""

    def __init__(self) -> None:
        self._llm: Optional[LLMService] = None

    def _get_llm(self) -> LLMService:
        if self._llm is None:
            settings = get_settings()
            model = settings.layer1_model or settings.model_name or "gpt-5.5"
            self._llm = LLMService(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                model_name=model,
                api_keys=list(settings.openai_api_keys) if settings.openai_api_keys else None,
            )
        return self._llm

    async def route_turn(
        self,
        user_text: str,
        client_id: str = "default",
        conversation_id: str = "",
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Main entry point for incoming user queries under Dual-Layer mode.
        Evaluated by Layer 1 AI model:
        Returns:
        {
          "type": "reflex" | "delegation",
          "response": str,
          "ticket_id": Optional[str],
          "goal": Optional[str]
        }
        """
        text = user_text.strip()
        if not text:
            return {
                "type": "reflex",
                "response": "I'm right here, Kartikey. How can I help?",
                "ticket_id": None,
            }

        # Sub-10ms Blackboard check: If Kartikey specifically inquires about in-flight tasks
        lower = text.lower()
        if any(k in lower for k in ("status of", "how is", "how's", "progress of", "where are we on")):
            active = blackboard.find_active_job_by_query(text)
            if active:
                status_text = self.handle_status_query(text)
                return {
                    "type": "reflex",
                    "response": status_text,
                    "ticket_id": None,
                }

        # Real-time Clock & System Environment Context
        now_dt = datetime.datetime.now().astimezone()
        tz_name = time.tzname[0] if time.tzname else "Local"
        env_ctx = (
            f"\n[SYSTEM ENVIRONMENT & REAL-TIME STATUS]\n"
            f"- Current Real-World Time: {now_dt.strftime('%I:%M:%S %p')} ({tz_name}, {now_dt.strftime('%z')})\n"
            f"- Current Date: {now_dt.strftime('%A, %B %d, %Y')}\n"
            f"- Host Machine: Windows PC (Server active on 127.0.0.1)\n"
            f"- User: Kartikey\n"
            f"- Connected Client: {client_id}\n"
        )

        # Device presence context
        try:
            from services.device_registry import device_registry
            dev_summary = device_registry.get_presence_summary(caller_id=client_id)
            if dev_summary:
                env_ctx += f"\n{dev_summary}\n"
        except Exception:
            pass

        # User memory context
        try:
            from services.memory import get_memory_context
            mem_ctx = get_memory_context()
            if mem_ctx:
                env_ctx += f"\n{mem_ctx}\n"
        except Exception:
            pass

        # Build prompt with active background jobs context
        active_tickets = blackboard.list_tickets(limit=5)
        running_jobs = []
        for t in active_tickets:
            stat = t.get("status") if isinstance(t, dict) else getattr(t, "status", "")
            if stat in ("in_progress", "scoping", "review"):
                running_jobs.append(t)

        if running_jobs:
            active_lines = []
            for t in running_jobs:
                tid = t.get("ticket_id") if isinstance(t, dict) else getattr(t, "ticket_id", "")
                goal = t.get("goal") if isinstance(t, dict) else getattr(t, "goal", "")
                stat = t.get("status") if isinstance(t, dict) else getattr(t, "status", "")
                pct = t.get("progress_pct") if isinstance(t, dict) else getattr(t, "progress_pct", 0)
                active_lines.append(f"- [{tid}] Goal: '{goal}', Status: {stat}, Progress: {pct}%")
            active_ctx = "\nCURRENT ACTIVE BACKGROUND TASKS:\n" + "\n".join(active_lines)
        else:
            active_ctx = "\nCURRENT ACTIVE BACKGROUND TASKS: None currently running."

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": f"{get_layer1_system_prompt('Kartikey')}\n\n{env_ctx}\n\n{active_ctx}"}
        ]

        # Inject recent conversation history for contextual conversational awareness
        if conversation_history:
            for msg in conversation_history[-6:]:
                role = msg.get("role")
                content = msg.get("content")
                if role in ("user", "assistant") and isinstance(content, str):
                    messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": text})

        try:
            llm = self._get_llm()
            gen_res = await asyncio.to_thread(
                llm.generate,
                messages=messages,
                tools=[DELEGATE_TOOL],
            )

            tool_calls = gen_res.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    fn_name = tc.get("function", {}).get("name")
                    if fn_name == "delegate_to_layer2":
                        raw_args = tc.get("function", {}).get("arguments", "{}")
                        try:
                            args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                        except Exception:
                            args = {}

                        goal = args.get("goal") or text
                        kickoff_ack = args.get("kickoff_response") or f"On it. Working on '{goal}' right now."

                        ticket = blackboard.create_ticket(
                            goal=goal,
                            client_id=client_id,
                            conversation_id=conversation_id,
                        )
                        layer2_orchestrator.submit_ticket(ticket)

                        return {
                            "type": "delegation",
                            "response": kickoff_ack,
                            "ticket_id": ticket.ticket_id,
                            "goal": goal,
                            "status": "in_progress",
                        }

            # Conversational response (Reflex)
            reply = (gen_res.get("content") or "").strip()
            if not reply:
                reply = "I'm right here, Kartikey. What are we working on?"

            return {
                "type": "reflex",
                "response": reply,
                "ticket_id": None,
            }

        except Exception as exc:
            logger.error(f"[Layer1Avatar] AI routing error: {exc}", exc_info=True)
            # Fallback for unexpected failures
            return {
                "type": "reflex",
                "response": "I'm with you, Kartikey. Standing by.",
                "ticket_id": None,
            }

    def route_turn_sync(
        self,
        user_text: str,
        client_id: str = "default",
        conversation_id: str = "",
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Synchronous wrapper for route_turn, safe to call from sync functions and tests."""
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        asyncio.run,
                        self.route_turn(
                            user_text=user_text,
                            client_id=client_id,
                            conversation_id=conversation_id,
                            conversation_history=conversation_history,
                        ),
                    )
                    return future.result()
            else:
                return asyncio.run(
                    self.route_turn(
                        user_text=user_text,
                        client_id=client_id,
                        conversation_id=conversation_id,
                        conversation_history=conversation_history,
                    )
                )
        except Exception as e:
            logger.error(f"[Layer1Avatar] route_turn_sync error: {e}", exc_info=True)
            return {
                "type": "reflex",
                "response": "I'm right here, Kartikey. Standing by.",
                "ticket_id": None,
            }

    def handle_status_query(self, query: str) -> str:
        """Sub-10ms Blackboard lookup for in-flight tasks."""
        start = time.perf_counter()
        job = blackboard.find_active_job_by_query(query)
        lookup_ms = round((time.perf_counter() - start) * 1000, 2)

        if not job:
            return "There are no active or recent background jobs running at the moment, sir."

        if job.status == "completed":
            return f"Job '{job.goal}' is complete (100%). {job.executive_summary} (retrieved in {lookup_ms}ms)"
        elif job.status == "failed":
            return f"Job '{job.goal}' encountered an issue: {job.active_blocker or 'Unknown error'}. (retrieved in {lookup_ms}ms)"
        elif job.status == "cancelled":
            return f"Job '{job.goal}' was cancelled. (retrieved in {lookup_ms}ms)"
        else:
            active_step_title = "Initializing"
            for s in reversed(job.steps):
                if s.status == "running":
                    active_step_title = s.title
                    break
            return (
                f"We are at {job.progress_pct}%, sir. Currently on milestone: '{active_step_title}'. "
                f"(Retrieved from Blackboard in {lookup_ms}ms)"
            )

    def format_sentinel_report(self, alert: dict, diagnosis: dict) -> str:
        """Formats a Sentinel warning into a natural human assistant briefing."""
        title = alert.get("title") or alert.get("alert_title") or "System Alert"
        message = alert.get("message") or ""
        severity = (alert.get("severity") or "WARN").upper()

        diag_text = ""
        if isinstance(diagnosis, dict):
            diag_text = diagnosis.get("diagnosis") or diagnosis.get("summary") or ""
        elif isinstance(diagnosis, str):
            diag_text = diagnosis

        if severity == "CRITICAL":
            greeting = "Kartikey, urgent heads-up on the system:"
        else:
            greeting = "Hey Kartikey, quick heads-up:"

        report = f"{greeting} **{title}**.\n\n"
        if message:
            report += f"{message}\n\n"
        if diag_text:
            report += f"**What's going on**: {diag_text}\n\n"
        report += "Do you want me to outline the steps to fix this, or inspect the running processes for you?"
        return report

    def deliver_delegation_outcome(
        self,
        user_goal: str,
        ticket_id: str,
        layer2_result: str,
        client_id: str = "default",
    ) -> str:
        """
        Receives the outcome of a delegated task from Layer 2 Chief of Staff.
        Layer 1 (The Executive Avatar) acts as the sole communicator to Kartikey.
        """
        cleaned = (layer2_result or "").strip()
        if not cleaned:
            return "Task completed, Kartikey."
        return cleaned


# Global singleton access
layer1_avatar = Layer1Avatar()
