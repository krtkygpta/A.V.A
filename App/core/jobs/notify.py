"""
notify.py - Shared completion / service-event routing to desktop + inbox + wake.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from core.services.base import ServiceEvent


def desktop_notify(title: str, body: str, *, sound: bool = False) -> None:
    """
    Desktop toast. Default sound=False so background completions do not beep;
    the main agent calls ``ping`` / ``send_notification`` when audio is wanted.
    """
    try:
        from functions.system.notifiers import send_notification, show_toast_only

        if sound:
            send_notification(str(title)[:80], str(body)[:160])
        else:
            try:
                show_toast_only(str(title)[:80], str(body)[:160])
            except Exception:
                # Fallback: toast path without forcing a loud ping if available
                send_notification(str(title)[:80], str(body)[:160])
    except Exception as e:
        print(f"[Notify] Desktop notify failed: {e}")


def format_done_inject(task: Any) -> str:
    """High-priority DONE text for the main agent — keep under ~600 chars."""
    try:
        compact = task.to_compact_done()
    except Exception:
        compact = f"Finished: {getattr(task, 'name', '?')} (id={getattr(task, 'id', '?')})"

    ttype = getattr(task, "task_type", "") or ""
    kind = getattr(task, "kind", "") or ""
    short = kind == "short" or ttype == "timer"

    # Minimal instruction footer (token-efficient; rules already in system prompt)
    if ttype in ("opencode", "coding"):
        tail = "Reply with the outcome only. No greeting."
    elif short:
        tail = "Handle once (tools if REQUIRED ACTION), then done forever. No greeting."
    else:
        tail = "Speak brief outcome. No greeting. No full report dump."

    return f"[BACKGROUND TASK DONE]\n{compact}\n{tail}"



def alert_for_completed_task(task: Any) -> None:
    """
    Apply the *LLM-chosen* alert_mode stored on the task at spawn time.

    - continuous → ring until user responds (or max timeout)
    - short → brief ping
    - notify → toast only (no beep); set at spawn if you want OS toast
    - silent (default) → nothing; main agent may call ping/send_notification after DONE

    Does NOT invent urgency. Real alarms must set alert_mode at spawn.
    """
    try:
        from core.TaskManager import normalize_alert_mode
        from functions.system.notifiers import continuous_ping, ping
    except Exception as e:
        print(f"[Notify] alert_for_completed_task import failed: {e}")
        return

    mode = normalize_alert_mode(getattr(task, "alert_mode", None))
    task_id = getattr(task, "id", "?")

    try:
        if mode == "continuous":
            continuous_ping(max_seconds=180, chunk_seconds=12)
            print(f"[Notify] Continuous ping for task {task_id} (alert_mode=continuous)")
        elif mode == "short":
            ping(duration_seconds=3)
            print(f"[Notify] Short ping for task {task_id} (alert_mode=short)")
        elif mode == "notify":
            print(f"[Notify] Toast-only for task {task_id} (alert_mode=notify, no beep)")
        else:
            print(
                f"[Notify] Silent for task {task_id} (alert_mode={mode}); "
                "main agent decides ping/notify"
            )
    except Exception as e:
        print(f"[Notify] Audible alert failed: {e}")


def route_service_event(event: "ServiceEvent") -> None:
    """
    ServiceEvent → desktop notify (by severity) + optional sticky inbox +
    optional wake inject for speak_now / critical.
    """
    if event is None:
        return

    severity = (event.severity or "normal").lower()
    title = event.title or f"{event.service}: {event.event_type}"
    summary = event.summary or ""

    # Desktop: everything except pure low heartbeats
    if severity != "low":
        prefix = {
            "critical": "AVA — Alert",
            "important": "AVA — Important",
            "normal": "AVA — Update",
        }.get(severity, "AVA")
        desktop_notify(prefix, f"{title}: {summary}"[:160])

    # Sticky inbox
    if event.create_inbox and severity != "low":
        try:
            from core.jobs.inbox import get_inbox

            get_inbox().add(
                id=event.id,
                source="service",
                title=title,
                summary=(summary or "")[:500],
                artifact_path=event.artifact_path,
                severity=severity if severity in (
                    "critical", "important", "normal", "low"
                ) else "normal",
            )
        except Exception as e:
            print(f"[Notify] Inbox add failed: {e}")

    # Wake main when chat active and event wants speech
    speak = bool(event.speak_now) or severity == "critical"
    if not speak:
        return

    try:
        from core.TaskManager import ConversationStateManager
        from core.messageHandler import add_message

        state = ConversationStateManager()
        if not state.is_conversation_active:
            # Idle: toast + inbox only; next conversation start injects inbox.
            return
        if state.is_speaking or state.is_listening:
            return

        lines = [
            f"[SERVICE EVENT]",
            f"id={event.id} service={event.service} type={event.event_type}",
            f"severity={severity}",
            f"title={title}",
        ]
        if summary:
            lines.append(f"summary={summary[:200]}")
        if event.create_inbox:
            lines.append("hint=also in unread inbox until result/dismiss")
        add_message(
            role="system",
            content="\n".join(lines),
            tool_id="",
            trigger_response=True,
        )
    except Exception as e:
        print(f"[Notify] Service wake failed: {e}")


def flush_pending_announcements(*, trigger_response: bool = False) -> int:
    """
    Drain deferred + idle completion queues into the current chat as
    [BACKGROUND TASK DONE] injects.

    Default ``trigger_response=False``: attach to an *existing* user turn
    (response_loop already woke). Never call with True from conversation
    start — that made AVA monologue about old research with no user command.

    Returns number of tasks flushed.
    """
    try:
        from core.TaskManager import CompletionQueue, TaskRegistry
    except Exception:
        return 0

    queue = CompletionQueue()
    registry = TaskRegistry()
    tasks = []
    try:
        tasks.extend(queue.get_deferred_notifications())
    except Exception:
        pass
    try:
        tasks.extend(queue.get_idle_notifications())
    except Exception:
        pass

    # Registry rows that completed but never got a spoken DONE inject
    # (e.g. process hiccup). Cap to recent few so we don't dump history.
    try:
        from datetime import datetime, timedelta

        cutoff = datetime.now() - timedelta(hours=6)
        extras = []
        for t in registry.get_pending_notifications():
            if any(x.id == t.id for x in tasks):
                continue
            completed = getattr(t, "completed_at", None) or getattr(t, "created_at", None)
            if completed and completed < cutoff:
                # Stale: mark notified so they stop haunting flushes; inbox still holds long results.
                try:
                    registry.mark_as_notified(t.id)
                except Exception:
                    pass
                continue
            extras.append(t)
        # Newest first, max 3
        extras.sort(
            key=lambda t: (t.completed_at or t.created_at),
            reverse=True,
        )
        tasks.extend(extras[:3])
    except Exception:
        pass

    if not tasks:
        return 0

    try:
        from core.messageHandler import add_message
    except Exception:
        return 0

    n = 0
    try:
        tasks.sort(
            key=lambda t: (t.completed_at or t.created_at),
            reverse=True,
        )
    except Exception:
        pass

    for i, task in enumerate(tasks):
        try:
            # Skip already announced / handled (prevents multi-speak of same DONE)
            meta = getattr(task, "metadata", None) or {}
            if meta.get("handled") or meta.get("announced") or task.notified_at:
                continue
            try:
                from core.jobs.announce_queue import get_announce_queue

                if get_announce_queue().was_announced(task.id):
                    continue
            except Exception:
                pass

            wake = bool(trigger_response) and n == 0
            if wake:
                alert_for_completed_task(task)
            add_message(
                role="system",
                content=format_done_inject(task),
                tool_id="",
                trigger_response=wake,
            )
            registry.mark_as_notified(task.id)
            try:
                with registry._tasks_lock:
                    live = registry._tasks.get(task.id)
                    if live is not None:
                        live.metadata = dict(live.metadata or {})
                        live.metadata["announced"] = True
                        registry._persist_locked()
                from core.jobs.announce_queue import get_announce_queue

                get_announce_queue().mark_announced(task.id)
            except Exception:
                pass
            n += 1
        except Exception as e:
            print(f"[Notify] Flush announce failed for {getattr(task, 'id', '?')}: {e}")
    return n
