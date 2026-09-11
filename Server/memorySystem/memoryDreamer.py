"""
memoryDreamer.py -- Interval-based offline memory consolidation ("dreaming").

Runs on a fixed wall-clock interval (default 1 hour). Does NOT dream on every
chat end, and does NOT reset the timer on user activity.

Flow:
  1. Sleep AVA_DREAM_INTERVAL_SECONDS (default 3600).
  2. Load conversations newer than last_checked.
  3. If any: LLM merge -> memories.txt / behaviours.txt; only then advance cursor.
  4. Repeat forever.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

# Server package root: .../Server  (this file lives in Server/memorySystem/)
_SERVER_ROOT = Path(__file__).resolve().parent.parent
_DATA_ROOT = _SERVER_ROOT / "data"
_MEMORIES_DIR = _DATA_ROOT / "memories"
_CONVERSATIONS = Path(
    os.getenv("AVA_SERVER_CONVERSATIONS_DIR", str(_DATA_ROOT / "conversations"))
)
_LAST_CHECK_FILE = _MEMORIES_DIR / "last_checked.txt"
_MEMORIES_FILE = _MEMORIES_DIR / "memories.txt"
_BEHAVIOURS_FILE = _MEMORIES_DIR / "behaviours.txt"

# Wall-clock interval between dream attempts (not chat-end, not idle-gated).
DEFAULT_INTERVAL_SECONDS = 3600
# Cap how many new conversation files to process in one dream (token safety).
MAX_CONVOS_PER_DREAM = int(os.getenv("AVA_DREAM_MAX_CONVOS", "12"))
# Truncate long message bodies (research dumps, tool monologues).
MAX_MSG_CHARS = int(os.getenv("AVA_DREAM_MAX_MSG_CHARS", "800"))
# Soft cap on total conversation JSON chars sent to the model.
MAX_BATCH_CHARS = int(os.getenv("AVA_DREAM_MAX_BATCH_CHARS", "60000"))

# Optional: other code may set this to wake the sleeper early (e.g. tests / admin).
# Activity on /generate does NOT reset the interval -- dreaming stays periodic.
dreamerEvent = threading.Event()
dreamerEvent.clear()

_dream_lock = threading.Lock()


def _ensure_memory_files() -> None:
    _MEMORIES_DIR.mkdir(parents=True, exist_ok=True)
    if not _MEMORIES_FILE.exists():
        _MEMORIES_FILE.write_text("[THINGS I REMEMBER]\n(none)\n", encoding="utf-8")
    if not _BEHAVIOURS_FILE.exists():
        _BEHAVIOURS_FILE.write_text("(none)\n", encoding="utf-8")
    if not _LAST_CHECK_FILE.exists():
        _LAST_CHECK_FILE.write_text("00000000_000000", encoding="utf-8")


def load_last_timestamp() -> str:
    _ensure_memory_files()
    try:
        return _LAST_CHECK_FILE.read_text(encoding="utf-8").strip() or "00000000_000000"
    except OSError:
        return "00000000_000000"


def save_last_timestamp(timestamp: str) -> None:
    _ensure_memory_files()
    _LAST_CHECK_FILE.write_text(str(timestamp).strip(), encoding="utf-8")


def _truncate_content(content: Any) -> str:
    text = "" if content is None else str(content)
    text = text.strip()
    if not text:
        return ""
    # Drop obvious task/noise blobs that drown personal signal.
    lower = text.lower()
    if lower.startswith("task ") and "status" in lower[:80]:
        return text[:200] + ("..." if len(text) > 200 else "")
    if len(text) > MAX_MSG_CHARS:
        return text[:MAX_MSG_CHARS] + "..."
    return text


def clean_conversation(data: dict[str, Any]) -> dict[str, Any]:
    """Keep user + assistant text only; truncate long bodies."""
    cleaned_messages = []
    for msg in data.get("messages") or []:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        content = _truncate_content(msg.get("content"))
        if not content:
            continue
        cleaned_messages.append(
            {
                "role": role,
                "content": content,
                "timestamp": msg.get("timestamp"),
            }
        )
    return {"id": data.get("id"), "messages": cleaned_messages}


def collect_new_conversations() -> tuple[list[dict[str, Any]], str]:
    """
    Load conversation files newer than last_checked.
    Does NOT advance the cursor -- caller advances only after a successful dream.
    Returns (cleaned_list, newest_timestamp_in_batch).
    """
    last_timestamp = load_last_timestamp()
    if not _CONVERSATIONS.exists():
        return [], last_timestamp

    files = sorted(
        f for f in _CONVERSATIONS.glob("*.json") if f.name != "index.json"
    )
    cleaned: list[dict[str, Any]] = []
    newest = last_timestamp

    for file in files:
        file_timestamp = file.stem
        if file_timestamp <= last_timestamp:
            continue
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            cleaned_one = clean_conversation(data)
            if not cleaned_one.get("messages"):
                # Empty shell -- still advance past it later so we don't stuck-loop.
                cleaned.append(cleaned_one)
            else:
                cleaned.append(cleaned_one)
            if file_timestamp > newest:
                newest = file_timestamp
            if len(cleaned) >= MAX_CONVOS_PER_DREAM:
                break
        except Exception as e:
            print(f"[Dreamer] Error reading {file.name}: {e}")

    return cleaned, newest


def get_memories_and_behaviours() -> tuple[str, str]:
    _ensure_memory_files()
    memories = _MEMORIES_FILE.read_text(encoding="utf-8")
    behaviours = _BEHAVIOURS_FILE.read_text(encoding="utf-8")
    return memories, behaviours


def write_memories_and_behaviours(memories: str, behaviours: str) -> None:
    _ensure_memory_files()
    _MEMORIES_FILE.write_text(memories.strip() + "\n", encoding="utf-8")
    _BEHAVIOURS_FILE.write_text(behaviours.strip() + "\n", encoding="utf-8")


def _parse_dream_output(response: str) -> Optional[tuple[str, str]]:
    """
    Parse model output into (memories, behaviours).
    Returns None if the response is unusable (caller must NOT wipe store).
    """
    if not response or not response.strip():
        return None

    text = response.strip()
    # Prefer labelled sections; accept case-insensitive headers.
    mem_match = re.search(
        r"(?is)\bmemories\s*:\s*(.*?)(?:\bbehaviours?\s*:|$)",
        text,
    )
    beh_match = re.search(r"(?is)\bbehaviours?\s*:\s*(.*)\Z", text)

    if mem_match and beh_match:
        memories = mem_match.group(1).strip()
        behaviours = beh_match.group(1).strip()
    else:
        # Legacy: split on first "behaviours:"
        parts = re.split(r"(?i)\bbehaviours?\s*:", text, maxsplit=1)
        if len(parts) != 2:
            return None
        memories = re.sub(r"(?i)^\s*memories\s*:\s*", "", parts[0]).strip()
        behaviours = parts[1].strip()

    if not memories:
        return None
    # Guard: model sometimes returns only prose with no list structure.
    if len(memories) < 3:
        return None
    if not behaviours:
        behaviours = "(none)"
    return memories, behaviours


def _call_dream_llm(prompt: str) -> str:
    import sys

    model = os.getenv("MODEL_NAME") or os.getenv("AVA_DREAM_MODEL") or ""
    if not model:
        raise RuntimeError("MODEL_NAME / AVA_DREAM_MODEL not set")

    # Ensure Server root is importable (dreamer thread may not have it)
    if str(_SERVER_ROOT) not in sys.path:
        sys.path.insert(0, str(_SERVER_ROOT))

    # Same OPENAI_API_KEYS pool as main LLM + research (round-robin + 429 retry).
    try:
        from services.key_rotator import chat_completions_create

        resp = chat_completions_create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            label="Dreamer",
            temperature=0.3,
        )
        content = resp.choices[0].message.content
        return (content or "").strip()
    except Exception as chat_err:
        # Fall back to single-key responses API (rare providers)
        print(f"[Dreamer] chat.completions failed ({chat_err}); trying responses API")
        from services.key_rotator import (
            collect_openai_keys,
            get_openai_client_for_key,
            normalize_openai_base_url,
        )

        keys = collect_openai_keys(env=True)
        if not keys:
            raise
        client = get_openai_client_for_key(
            keys[0],
            normalize_openai_base_url(os.getenv("OPENAI_BASE_URL")),
        )
        if not hasattr(client, "responses"):
            raise chat_err
        resp = client.responses.create(model=model, input=prompt)
        return (getattr(resp, "output_text", None) or str(resp)).strip()


def periodicMemory(
    existing_memories: str,
    previous_behaviours: str,
    conversations: str,
) -> bool:
    """
    Run one dream LLM pass and write results.
    Returns True on successful parse+write; False if nothing should advance the cursor.
    """
    prompt = f"""You are AVA's dreaming module. Goal: help AVA REMEMBER ALMOST EVERYTHING durable about the USER.

You consolidate chats into long-term memory. Prefer retaining user facts over forgetting.
When unsure whether a user detail is useful later, KEEP IT as a memory (high recall).

MEMORY FORMAT (TSV - Tab Separated):
[THINGS I REMEMBER]
memory text here	fact	0.85

Fields: text | type | confidence (0.0-1.0)
Types: fact, preference, style, habit, relationship, plan, project, opinion, other

---

IGNORE ONLY:
- Pure greetings/goodbyes with no content
- How the user addresses AVA (sir, buddy)
- AVA's own behaviour / tool dumps / research report bodies / timer noise / stack traces
- One-off ephemeral UI chatter with zero personal signal ("ok", "thanks", "hmm")

REMEMBER AGGRESSIVELY (user-centric):
- Preferences, likes/dislikes, favorites
- People (friends, family, colleagues) and relationships
- Work, school, projects, goals, deadlines the user mentioned
- Habits, schedule, routines, health notes they shared
- Opinions, plans, decisions, recurring requests
- Locations, devices, setup details they treat as stable
- Corrections to previous facts (update, do not keep contradictions)

---

YOUR TASK:
Produce COMPLETE updated lists. Start from EXISTING MEMORIES and only drop items that are
clearly wrong, contradicted, or pure noise. Do NOT wipe the list to (none) because a batch
was mostly tools/tests -- keep prior user facts.

ANALYSIS STEPS:
1. KEEP nearly all existing valid memories
2. ADD every new durable user fact from conversations (even single clear statements)
3. UPDATE when new info revises an old fact
4. REMOVE only when contradicted or clearly not about the user
5. BEHAVIOURS: weak / half-formed interaction patterns (not full memories yet)

RULES:
- Explicit user statements -> memory (confidence ≥ 0.75), even if said once
- Preferences and relationships -> memory
- Repeated requests -> preference memory
- Vague hints -> behaviour first; upgrade to memory when confirmed
- If new chats add nothing personal, output EXISTING memories largely unchanged
- Never invent facts not supported by conversations or existing store

---

OUTPUT FORMAT - ALWAYS OUTPUT COMPLETE LISTS:

memories:
[THINGS I REMEMBER]
memory text	fact	0.85
another memory	preference	0.70
(no memories? output just: (none))

behaviours:
- pattern still forming (0.3-0.6)
- another observation
(no behaviours? output just: (none))

---

CONTEXT:

## PREVIOUS BEHAVIOURS
{previous_behaviours}

## RECENT CONVERSATIONS
{conversations}

## EXISTING MEMORIES
{existing_memories}

---

OUTPUT COMPLETE UPDATED LISTS NOW:
"""

    response = _call_dream_llm(prompt)
    parsed = _parse_dream_output(response)
    if not parsed:
        print("[Dreamer] Unparseable LLM output -- keeping previous memories, cursor unchanged")
        preview = (response or "")[:300].replace("\n", " ")
        print(f"[Dreamer] Output preview: {preview}")
        return False

    memories, behaviours = parsed
    # Extra safety: never replace a non-empty store with a bare failure-looking wipe
    # when the model returned only whitespace junk (handled above). Explicit (none)
    # is allowed so stale memories can be cleared intentionally.
    write_memories_and_behaviours(memories, behaviours)
    return True


def run_dream_once() -> dict[str, Any]:
    """
    One interval tick. Safe to call from tests or admin.
    Returns a small status dict.
    """
    with _dream_lock:
        _ensure_memory_files()
        batch, newest = collect_new_conversations()
        last = load_last_timestamp()

        if not batch:
            print(f"[Dreamer] Interval tick -- no new conversations (last_checked={last})")
            return {"status": "idle", "new_conversations": 0, "last_checked": last}

        # All empty shells -> advance cursor without LLM spend.
        if all(not c.get("messages") for c in batch):
            save_last_timestamp(newest)
            print(
                f"[Dreamer] Interval tick -- {len(batch)} empty convo(s), "
                f"advanced cursor to {newest}"
            )
            return {
                "status": "skipped_empty",
                "new_conversations": len(batch),
                "last_checked": newest,
            }

        conversations_json = json.dumps(batch, ensure_ascii=False, indent=2)
        if len(conversations_json) > MAX_BATCH_CHARS:
            conversations_json = conversations_json[:MAX_BATCH_CHARS] + "\n...[truncated]"

        existing_memories, previous_behaviours = get_memories_and_behaviours()
        print(
            f"[Dreamer] Interval tick -- consolidating {len(batch)} conversation(s) "
            f"(through {newest})"
        )
        try:
            ok = periodicMemory(
                existing_memories, previous_behaviours, conversations_json
            )
        except Exception as e:
            print(f"[Dreamer] LLM/dream failed: {e}")
            return {
                "status": "error",
                "error": str(e),
                "new_conversations": len(batch),
                "last_checked": last,
            }

        if ok:
            save_last_timestamp(newest)
            print(f"[Dreamer] Done -- last_checked -> {newest}")
            return {
                "status": "dreamed",
                "new_conversations": len(batch),
                "last_checked": newest,
            }

        return {
            "status": "parse_failed",
            "new_conversations": len(batch),
            "last_checked": last,
        }


def _interval_seconds() -> float:
    raw = os.getenv("AVA_DREAM_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS))
    try:
        val = float(raw)
    except (TypeError, ValueError):
        val = float(DEFAULT_INTERVAL_SECONDS)
    # Floor at 60s so a bad env value cannot hot-loop the LLM.
    return max(60.0, val)


def DreamerStart() -> None:
    """
    Background thread: dream on a fixed wall-clock interval forever.

    - Not triggered by chat end.
    - User activity does not change the schedule (pure period).
    - dreamerEvent is kept for optional admin/tests (set -> run soon); the main
      loop still sleeps the full interval between ticks and does not treat
      normal /generate traffic as a dream signal.
    """
    interval = _interval_seconds()
    print(
        f"[Dreamer] Started -- interval={interval:.0f}s "
        f"(AVA_DREAM_INTERVAL_SECONDS), conversations={_CONVERSATIONS}"
    )
    _ensure_memory_files()

    while True:
        # Pure interval sleep. Chunked so an admin early-wake can interrupt.
        remaining = interval
        chunk = min(30.0, interval)
        while remaining > 0:
            dreamerEvent.clear()
            woke = dreamerEvent.wait(timeout=min(chunk, remaining))
            if woke:
                # Explicit early wake (tests/admin). Drain and break to dream now.
                dreamerEvent.clear()
                break
            remaining -= chunk

        try:
            run_dream_once()
        except Exception as e:
            print(f"[Dreamer] Unhandled error on interval tick: {e}")
            # Brief backoff so a hard bug does not tight-loop.
            time.sleep(5)
