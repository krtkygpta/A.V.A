"""
messageHandler.py - Message management for AVA conversations.

Manages the message list (conversation history sent to the LLM),
handles adding messages, resetting conversations, and injecting
dynamic context (memories, conversation history, pending tasks).

System prompt and tool definitions have been extracted to:
    core/system_prompt.py     - get_system_prompt()
    core/tool_definitions.py   - tools
"""

import os
import threading

import requests
from core.AppStates import main_runner
from core.server_api import add_remote_message
from core.system_prompt import get_system_prompt
from core.TaskManager import (
    get_pending_completions_summary,
    get_pending_clarifications_summary,
)
from core.tool_definitions import tools

# RLock: near-duplicate checks may nest under add_message's lock
messages_lock = threading.RLock()

# Registered UI listeners
_user_message_listeners = []
_conversation_reset_listeners = []
_slash_command_listeners = []
_clear_chat_listeners = []
_system_message_listeners = []


def register_user_message_listener(callback):
    """Register a callback fn(content: str) -> None for user messages."""
    if callback not in _user_message_listeners:
        _user_message_listeners.append(callback)


def unregister_user_message_listener(callback):
    """Unregister a user message callback."""
    if callback in _user_message_listeners:
        _user_message_listeners.remove(callback)


def register_conversation_reset_listener(callback):
    """Register a callback fn() -> None for conversation resets."""
    if callback not in _conversation_reset_listeners:
        _conversation_reset_listeners.append(callback)


def unregister_conversation_reset_listener(callback):
    if callback in _conversation_reset_listeners:
        _conversation_reset_listeners.remove(callback)


def notify_conversation_reset():
    """Notify all listeners that a new conversation has started."""
    for cb in list(_conversation_reset_listeners):
        try:
            cb()
        except Exception as e:
            print(f"[MsgHandler] Error in conversation reset listener: {e}")


def register_slash_command_listener(callback):
    """Register a callback fn(command: str, origin: str) -> None for slash commands."""
    if callback not in _slash_command_listeners:
        _slash_command_listeners.append(callback)


def unregister_slash_command_listener(callback):
    if callback in _slash_command_listeners:
        _slash_command_listeners.remove(callback)


def notify_slash_command(command: str, origin: str = "remote"):
    """Notify all listeners of a slash command execution."""
    for cb in list(_slash_command_listeners):
        try:
            cb(command, origin)
        except Exception as e:
            print(f"[MsgHandler] Error in slash command listener: {e}")


def register_clear_chat_listener(callback):
    """Register a callback fn() -> None for chat clear events."""
    if callback not in _clear_chat_listeners:
        _clear_chat_listeners.append(callback)


def unregister_clear_chat_listener(callback):
    if callback in _clear_chat_listeners:
        _clear_chat_listeners.remove(callback)


def notify_clear_chat():
    """Notify all listeners that the chat display was cleared."""
    for cb in list(_clear_chat_listeners):
        try:
            cb()
        except Exception as e:
            print(f"[MsgHandler] Error in clear chat listener: {e}")


def register_system_message_listener(callback):
    """Register a callback fn(text: str, is_alert: bool) -> None for system notices."""
    if callback not in _system_message_listeners:
        _system_message_listeners.append(callback)


def unregister_system_message_listener(callback):
    if callback in _system_message_listeners:
        _system_message_listeners.remove(callback)


def notify_system_message(text: str, is_alert: bool = False):
    """Notify all listeners of a system message/notice."""
    for cb in list(_system_message_listeners):
        try:
            cb(text, is_alert)
        except Exception as e:
            print(f"[MsgHandler] Error in system message listener: {e}")


# Import conversation manager (lazy to avoid circular imports)
_conversation_manager = None


def _get_conversation_manager():
    global _conversation_manager
    if _conversation_manager is None:
        from knowledge.ConversationManager import get_manager

        _conversation_manager = get_manager()
    return _conversation_manager


def _normalize_assistant_text(text: str) -> str:
    if not text:
        return ""
    return (
        text.replace("[stop_chat]", "")
        .replace("[STOP_CHAT]", "")
        .strip()
        .lower()
    )


def _assistant_fingerprint(text: str) -> set:
    """Token set for near-duplicate detection (DONE paraphrases)."""
    import re

    t = _normalize_assistant_text(text)
    # Drop filler / status verbs that differ between paraphrases
    stop = {
        "a",
        "an",
        "the",
        "is",
        "are",
        "to",
        "it",
        "with",
        "now",
        "sir",
        "please",
        "just",
        "done",
        "updated",
        "update",
        "run",
        "program",
        "file",
        "into",
        "as",
        "for",
        "and",
        "or",
        "on",
        "your",
        "my",
        "this",
        "that",
    }
    # Strip punctuation so "world." == "world"
    tokens = re.findall(r"[a-z0-9_]+(?:\.[a-z0-9_]+)*", t)
    return {tok.strip(".") for tok in tokens if tok.strip(".") not in stop and len(tok) > 1}


def _is_near_duplicate_assistant(content: str, lookback: int = 6) -> bool:
    """
    True if content is exact or substantially the same as a recent assistant
    line (catches 'Done. test.py is Hello World' vs 'Updated test.py to Hello World').
    """
    if not content or not str(content).strip():
        return False
    norm = _normalize_assistant_text(content)
    fp = _assistant_fingerprint(content)
    if not fp:
        return False

    def _check(recent: list) -> bool:
        for msg in recent:
            if msg.get("role") != "assistant":
                continue
            prev = str(msg.get("content") or "")
            if not prev.strip():
                continue
            prev_norm = _normalize_assistant_text(prev)
            if norm == prev_norm:
                return True
            prev_fp = _assistant_fingerprint(prev)
            if not prev_fp:
                continue
            inter = len(fp & prev_fp)
            union = len(fp | prev_fp) or 1
            jaccard = inter / union
            # Same file + same topic (hello/world/python) even if short
            if inter >= 3 and jaccard >= 0.45:
                return True
            if inter >= 2 and jaccard >= 0.6:
                return True
            # Shared distinctive anchors: filename + product word
            anchors = fp & prev_fp
            has_file = any("." in a or a.endswith("py") for a in anchors)
            if has_file and inter >= 2 and jaccard >= 0.35:
                return True
            if len(norm) > 20 and (norm in prev_norm or prev_norm in norm):
                return True
        return False

    try:
        with messages_lock:
            recent = list(messages)[-lookback:]
            return _check(recent)
    except Exception:
        return False


def _last_assistant_content_locked() -> str:
    """Caller must hold messages_lock."""
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            return str(msg.get("content") or "")
    return ""


def redact_done_injects(task_ids: list | set | None = None) -> int:
    """
    Rewrite live [BACKGROUND TASK DONE] system messages so the model stops
    re-reading REQUIRED ACTION / timer noise after a job was handled.

    If task_ids is given, only those ids; otherwise all DONE injects in history.
    Returns number of messages rewritten.
    """
    import re

    ids = None
    if task_ids is not None:
        ids = {str(x) for x in task_ids if x}

    n = 0
    with messages_lock:
        for msg in messages:
            if msg.get("role") != "system":
                continue
            content = str(msg.get("content") or "")
            if "BACKGROUND TASK DONE" not in content:
                continue
            if "BACKGROUND TASK HANDLED" in content and "already delivered" in content:
                continue
            found_ids = set(re.findall(r"\bid=([A-Za-z0-9_\-:]+)", content))
            # also main:parent markers
            if ids is not None:
                if not found_ids.intersection(ids) and not any(
                    f"id={i}" in content or f"({i})" in content for i in ids
                ):
                    # Match by substring id=
                    hit = False
                    for i in ids:
                        if i and i in content:
                            hit = True
                            break
                    if not hit:
                        continue
                label = ",".join(sorted(found_ids.intersection(ids) or found_ids or ids))
            else:
                label = ",".join(sorted(found_ids)) if found_ids else "task"
            msg["content"] = (
                f"[BACKGROUND TASK HANDLED — already delivered]\n"
                f"ids={label}\n"
                "Do NOT re-mention, re-do, or announce this job. "
                "It is finished and acknowledged."
            )
            n += 1
    if n:
        print(f"[MsgHandler] Redacted {n} DONE inject(s) as handled")
    return n


def _content_for_storage(content):
    """
    Compact content for disk / remote store.

    Multimodal tool results (list of parts with base64 image_url) must not be
    written into conversation JSON — keep a short text summary + path only.
    """
    if not isinstance(content, list):
        return content
    texts = []
    has_image = False
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text":
            texts.append(str(part.get("text") or ""))
        elif ptype == "image_url":
            has_image = True
    summary = " ".join(t for t in texts if t).strip() or "[multimodal tool result]"
    if has_image and "[image]" not in summary.lower():
        summary = f"{summary} [image attached for model]"
    return summary


def add_message(content, tool_id="", role="user", trigger_response=None, broadcast=True, attachments=None):
    """Add a message to both the current messages list and the conversation history."""
    if trigger_response is None:
        trigger_response = role in {"user", "tool"}

    if role == "tool":
        # Live LLM history keeps full multimodal content (image data URLs).
        with messages_lock:
            messages.append(
                {"role": "tool", "content": content, "tool_call_id": tool_id}
            )
        # Disk / remote: store text-only summary (no base64 bloat).
        stored = _content_for_storage(content)
        mgr = _get_conversation_manager()
        conv = mgr.ensure_current_conversation()
        conv.add_message("tool", stored, tool_id)
        try:
            add_remote_message(role="tool", content=stored, tool_id=tool_id)
        except Exception:
            pass
        if trigger_response and not main_runner.is_set():
            main_runner.set()
    elif role == "user":
        # User is present — silence continuous timer rings immediately.
        try:
            from functions.system.notifiers import stop_ping

            stop_ping()
        except Exception:
            pass
        # Ensure a ConversationManager thread exists *before* append so the first
        # TUI session (no /new yet) is still persisted when the user later /news.
        mgr = _get_conversation_manager()
        conv = mgr.ensure_current_conversation()
        msg_payload = {"role": role, "content": content}
        if attachments:
            msg_payload["attachments"] = attachments
        with messages_lock:
            messages.append(msg_payload)
        conv.add_message("user", content, attachments=attachments)
        add_remote_message(role="user", content=content)

        # Notify registered UI listeners (e.g. Textual TUI)
        for listener in list(_user_message_listeners):
            try:
                listener(content)
            except Exception as e:
                print(f"[MsgHandler] Listener error: {e}")

        # Broadcast user message to connected remote clients (Web & Android) if requested
        if broadcast:
            try:
                from core.gateway import broadcast_user_message
                from config import USER_NAME
                clean_text = content
                user_prefix = f"{USER_NAME}: "
                if clean_text.startswith(user_prefix):
                    clean_text = clean_text[len(user_prefix):]
                broadcast_user_message(clean_text, user=USER_NAME, attachments=attachments)
            except Exception:
                pass

        if trigger_response and not main_runner.is_set():
            main_runner.set()
    elif role == "assistant":
        # Skip consecutive duplicate / near-duplicate assistant text.
        # DONE wakes often re-paraphrase the same outcome 2–4 times ("Done. test.py
        # is Hello World" / "Updated test.py to Hello World") — drop those.
        mgr = _get_conversation_manager()
        conv = mgr.ensure_current_conversation()
        with messages_lock:
            if content and _is_near_duplicate_assistant(content):
                print("[MsgHandler] Skipping duplicate/near-duplicate assistant message")
                return
            messages.append({"role": role, "content": content})
        conv.add_message("assistant", content)
        add_remote_message(role="assistant", content=content)
        try:
            from core.gateway import broadcast_assistant_message
            from config import ASSISTANT_NAME
            broadcast_assistant_message(content, assistant=ASSISTANT_NAME)
        except Exception:
            pass
    elif role == "system":
        with messages_lock:
            messages.append({"role": role, "content": content})
        if trigger_response and not main_runner.is_set():
            main_runner.set()
    else:
        print("[MsgHandler] Invalid role")


def add_assistant_message(content=None, tool_calls=None):
    """
    Preserve assistant text and tool calls in a single history item so one
    turn can say what it is doing and then continue after tool execution.
    """
    payload = {"role": "assistant"}
    clean = None
    if content is not None:
        clean = content.replace("[stop_chat]", "").replace("[STOP_CHAT]", "").strip()
        payload["content"] = clean
    if tool_calls:
        payload["tool_calls"] = tool_calls

    mgr = _get_conversation_manager()
    conv = mgr.ensure_current_conversation()

    with messages_lock:
        # Dedup pure-text repeats / DONE paraphrases (same body, no new tool calls).
        if clean and not tool_calls:
            if _normalize_assistant_text(clean) == _normalize_assistant_text(
                _last_assistant_content_locked()
            ) or _is_near_duplicate_assistant(clean):
                print(
                    "[MsgHandler] Skipping duplicate assistant message (add_assistant_message)"
                )
                return False
        messages.append(payload)

    if clean:
        conv.add_message("assistant", clean)
        add_remote_message(role="assistant", content=clean)
        try:
            from core.gateway import broadcast_assistant_message
            from config import ASSISTANT_NAME
            broadcast_assistant_message(clean, assistant=ASSISTANT_NAME)
        except Exception:
            pass
    return True


def reset_messages():
    """Reset messages to just the system prompt for a new conversation."""
    global messages
    with messages_lock:
        system_prompt = messages[0] if messages else None

        messages.clear()
        if system_prompt:
            sp = system_prompt["content"]
            sp = sp.replace("<replaceMemoriesHere>", get_memory_context())

            # Prior conversations are NOT stuffed into the system prompt — they are
            # injected as a separate message on each /generate call (easier for the model).
            sp = sp.replace(
                "<replaceConversationContextHere>",
                "(prior chats injected as a separate context message each turn)",
            )

            # Compact unread inbox (long jobs only). Timers are one-shot and
            # never appear here. get_pending_completions_summary is non-destructive.
            pending_inbox = get_pending_completions_summary()
            sp = sp.replace(
                "<replacePendingCompletionsHere>",
                pending_inbox or "(none)",
            )

            # Open clarification questions (jobs blocked on needs_input).
            pending_clarify = get_pending_clarifications_summary()
            sp = sp.replace(
                "<replacePendingClarificationsHere>",
                pending_clarify or "(none)",
            )

            # Held finished-task dossiers (run/open/continue after jobs complete)
            try:
                from core.jobs.dossier import recent_completed_prompt

                dossiers = recent_completed_prompt()
            except Exception as e:
                print(f"[MsgHandler] Recent dossiers failed: {e}")
                dossiers = "(none)"
            # Append OpenCode session index (session tokens for continue)
            try:
                from functions.system.opencode_client import sessions_prompt_block

                oc = sessions_prompt_block(6)
                if oc and "(none)" not in oc:
                    dossiers = (dossiers or "") + "\n" + oc
            except Exception:
                pass
            sp = sp.replace(
                "<replaceRecentTaskDossiersHere>",
                dossiers or "(none)",
            )

            promptFinal = {"role": "system", "content": sp}
            messages.append(promptFinal)


def load_conversation_messages(conv_messages: list[dict]):
    """Reset messages and populate with the given conversation history."""
    reset_messages()
    with messages_lock:
        for msg in conv_messages:
            role = msg.get("role")
            content = msg.get("content", "")
            if not role or role not in ("user", "assistant", "tool"):
                continue
            entry = {"role": role, "content": content}
            if role == "tool":
                tool_id = msg.get("tool_call_id") or msg.get("tool_id") or ""
                if tool_id:
                    entry["tool_call_id"] = tool_id
            elif role == "user":
                if msg.get("attachments"):
                    entry["attachments"] = msg["attachments"]
            elif role == "assistant":
                if msg.get("tool_calls"):
                    entry["tool_calls"] = msg["tool_calls"]
            messages.append(entry)


def _normalize_memory_line(line: str) -> str:
    """Strip bullets/tsv confidence for rough dedupe."""
    s = (line or "").strip()
    if not s or s.lower() in ("(none)", "none"):
        return ""
    if s.upper().startswith("[THINGS I REMEMBER"):
        return ""
    s = s.lstrip("-•*").strip()
    # Dreamer TSV: text \t type \t conf
    if "\t" in s:
        s = s.split("\t", 1)[0].strip()
    return " ".join(s.lower().split())


def _lines_from_memory_blob(blob: str) -> list[str]:
    """Extract bullet/tsv memory lines from a free-text store."""
    if not blob or not str(blob).strip():
        return []
    out: list[str] = []
    for raw in str(blob).splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.upper().startswith("[THINGS I REMEMBER"):
            continue
        if line.lower() in ("(none)", "none", "memories:", "behaviours:", "behaviors:"):
            continue
        # Keep as display line (bullet-normalized)
        if "\t" in line:
            text = line.split("\t", 1)[0].strip()
            if text:
                out.append(text)
        else:
            out.append(line.lstrip("-•* ").strip())
    return [x for x in out if x]


def get_memory_context():
    """
    Build USER MEMORIES block for the system prompt.

    Merges:
      1) App tool-saved semantic memories (memory_manager → semantic_memories.json)
      2) Server dreamer store (interval consolidation → memories.txt)
      3) Optional weak patterns from behaviours.txt (if server returns them)

    This is what AVA sees every new conversation as durable context.
    """
    max_items = int(os.getenv("AVA_MEMORY_CONTEXT_MAX_ITEMS", "120"))
    max_chars = int(os.getenv("AVA_MEMORY_CONTEXT_MAX_CHARS", "14000"))

    display_lines: list[str] = []
    seen: set[str] = set()

    def _add_many(texts: list[str]) -> None:
        for t in texts:
            key = _normalize_memory_line(t)
            if not key or key in seen:
                continue
            seen.add(key)
            display_lines.append(t.strip())

    # 1) Local tool memories (explicit saves — usually highest signal)
    try:
        from knowledge.memory import list_memory_texts

        _add_many(list_memory_texts(limit=max_items))
    except Exception as e:
        print(f"[MsgHandler] Local memory load error: {e}")

    # 2) Server dreamer + behaviours
    server_blob = ""
    behaviours_blob = ""
    try:
        SERVER_URL = os.getenv("AVA_SERVER_URL")
        if SERVER_URL:
            response = requests.get(f"{SERVER_URL}/memories", timeout=5)
            response.raise_for_status()
            payload = response.json() or {}
            server_blob = payload.get("memories") or ""
            behaviours_blob = payload.get("behaviours") or ""
            _add_many(_lines_from_memory_blob(server_blob))
    except Exception as e:
        print(f"[MsgHandler] Memory server error: {e}")

    if not display_lines and not behaviours_blob:
        if not os.getenv("AVA_SERVER_URL"):
            return "(no memories yet — memory server not configured; use memory_manager to save)"
        return (
            "(no durable memories yet — tell me facts about yourself, or they will "
            "accumulate via memory_manager + interval dreaming)"
        )

    # Prefer newest-ish: local list is chronological; server appends are mixed.
    # Cap by count then chars.
    if len(display_lines) > max_items:
        display_lines = display_lines[-max_items:]

    header = "[THINGS I REMEMBER]"
    lines = [header]
    used = len(header)
    kept: list[str] = []
    for text in reversed(display_lines):
        line = f"- {text}"
        if used + len(line) + 1 > max_chars and kept:
            break
        kept.append(line)
        used += len(line) + 1
    kept.reverse()
    lines.extend(kept)
    omitted = len(display_lines) - len(kept)
    if omitted > 0:
        lines.append(
            f"(+{omitted} more on disk — use memory_manager retrieve/search when needed)"
        )

    # Weak patterns: short, optional, does not replace facts
    beh_lines = _lines_from_memory_blob(behaviours_blob)
    if beh_lines:
        lines.append("")
        lines.append("[WEAK PATTERNS / STILL FORMING]")
        for b in beh_lines[:15]:
            lines.append(f"- {b}")

    return "\n".join(lines)



def get_conversation_context():
    """Get past conversations context (clipped; for message-list inject)."""
    try:
        from knowledge.ConversationManager import get_past_conversations_context

        return get_past_conversations_context()
    except Exception as e:
        print(f"[MsgHandler] Conversation context error: {e}")
        return ""


def _sanitize_multimodal_message(msg: dict) -> dict:
    content = msg.get("content")
    if not isinstance(content, list):
        return msg
    clean_parts = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "image_url":
            url = str(part.get("image_url", {}).get("url") or "").strip()
            if url.startswith("data:"):
                hdr = url.split(";", 1)[0].lower()
                if not hdr.startswith("data:image/"):
                    clean_parts.append({
                        "type": "text",
                        "text": f"[Non-image attachment omitted: {hdr[5:]}]",
                    })
                    continue
        clean_parts.append(part)
    res = dict(msg)
    res["content"] = clean_parts
    return res


def messages_for_llm() -> list:
    """
    Copy of the live message list with prior-conversation context inserted as
    its own system message after the main system prompt (not inside it).

    Does not mutate the persistent ``messages`` history.
    """
    with messages_lock:
        base = [_sanitize_multimodal_message(dict(m)) for m in messages]

    try:
        ctx = (get_conversation_context() or "").strip()
    except Exception as e:
        print(f"[MsgHandler] messages_for_llm context error: {e}")
        ctx = ""

    extras = []
    if ctx and not ctx.startswith("(no prior") and not ctx.startswith("(prior chats injected"):
        extras.append(
            {
                "role": "system",
                "content": (
                    "[PRIOR CONVERSATION CONTEXT — for continuity only]\n"
                    "Use when the user continues earlier work. Do not monologue about this on greeting.\n\n"
                    + ctx
                ),
            }
        )

    try:
        from core.cc_plugins.skills_host import get_host

        catalog = (get_host().catalog_for_main() or "").strip()
    except Exception:
        catalog = ""
    if catalog:
        extras.append(
            {
                "role": "system",
                "content": (
                    "[SKILLS]\n"
                    "Installed Claude Code skills. Call use_skill when one matches.\n\n"
                    + catalog
                ),
            }
        )

    if not extras:
        return base
    if base and (base[0].get("role") or "") == "system":
        return [base[0], *extras] + base[1:]
    return extras + base


messages = [
    {
        "role": "system",
        "content": get_system_prompt(),
    }
]
