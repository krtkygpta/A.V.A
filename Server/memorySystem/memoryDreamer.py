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
from datetime import datetime, timedelta
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
# Synced to wall-clock :00 and :30 marks (every 30 minutes).
DEFAULT_INTERVAL_SECONDS = 1800
# Cap how many new conversation files to process in one dream (token safety).
MAX_CONVOS_PER_DREAM = int(os.getenv("AVA_DREAM_MAX_CONVOS", "12"))
# Truncate long message bodies (research dumps, tool monologues).
MAX_MSG_CHARS = int(os.getenv("AVA_DREAM_MAX_MSG_CHARS", "800"))
# Soft cap on total conversation JSON chars sent to the model.
MAX_BATCH_CHARS = int(os.getenv("AVA_DREAM_MAX_BATCH_CHARS", "60000"))

# Small chat combination settings
COMBINE_SMALL_CHATS_ENABLED = os.getenv("AVA_DREAM_COMBINE_SMALL_CHATS", "1").lower() in ("1", "true", "yes")
MIN_INACTIVE_AGE_SECONDS = int(os.getenv("AVA_DREAM_MIN_INACTIVE_AGE_SECONDS", "900"))  # 15 minutes
MAX_SMALL_CHAT_MESSAGES = int(os.getenv("AVA_DREAM_MAX_SMALL_CHAT_MESSAGES", "4"))
MAX_SMALL_CHAT_CHARS = int(os.getenv("AVA_DREAM_MAX_SMALL_CHAT_CHARS", "1500"))

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
    Load conversation files updated newer than last_checked.
    Does NOT advance the cursor -- caller advances only after a successful dream.
    Returns (cleaned_list, newest_timestamp_in_batch).
    """
    last_timestamp = load_last_timestamp()
    if not _CONVERSATIONS.exists():
        return [], last_timestamp

    files = sorted(
        [f for f in _CONVERSATIONS.glob("*.json") if f.name != "index.json"],
        key=lambda f: f.stat().st_mtime,
    )
    cleaned: list[dict[str, Any]] = []
    newest = last_timestamp

    for file in files:
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            updated_at = str(data.get("updated_at") or data.get("created_at") or file.stem)

            # Check if this chat was created or updated after the last dream cursor
            if updated_at <= last_timestamp:
                continue

            cleaned_one = clean_conversation(data)
            msgs = cleaned_one.get("messages") or []
            if msgs:
                # Include messages added after last dream cursor if timestamps exist
                fresh_msgs = [
                    m for m in msgs
                    if not m.get("timestamp") or str(m.get("timestamp")) > last_timestamp
                ]
                if fresh_msgs:
                    cleaned_one["messages"] = fresh_msgs
                    cleaned.append(cleaned_one)
            else:
                # Empty shell -- still record to advance past it
                cleaned.append(cleaned_one)

            if updated_at > newest:
                newest = updated_at
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


def is_large_or_important(data: dict[str, Any]) -> bool:
    """
    Check if a conversation should be protected from combination/compression.
    Large or important conversations are strictly preserved as independent threads.
    """
    messages = data.get("messages") or []
    # 1. Message count threshold: > MAX_SMALL_CHAT_MESSAGES
    if len(messages) > MAX_SMALL_CHAT_MESSAGES:
        return True

    # 2. Total character threshold: > MAX_SMALL_CHAT_CHARS
    total_chars = sum(len(str(m.get("content") or "")) for m in messages)
    if total_chars > MAX_SMALL_CHAT_CHARS:
        return True

    # 3. Content heuristics for importance/depth:
    for m in messages:
        content = str(m.get("content") or "")
        # Code blocks with significant lines of code
        if "```" in content:
            code_parts = content.split("```")
            for idx in range(1, len(code_parts), 2):
                if code_parts[idx].count("\n") >= 3:
                    return True

        # Substantial research reports or deep analysis
        lower = content.lower()
        if any(marker in lower for marker in (
            "# research report", "### executive summary", "## key findings", "analysis report"
        )):
            return True

    # 4. Topics or tags indicating importance
    topics = data.get("topics") or []
    for t in topics:
        if str(t).lower() in ("important", "keep", "project", "pinned", "architecture"):
            return True

    return False


def is_small_chat(
    data: dict[str, Any],
    active_ids: set[str],
    now_dt: datetime,
    min_inactive_seconds: int = MIN_INACTIVE_AGE_SECONDS,
) -> bool:
    """
    Determine if a conversation is a small, simple chat eligible for combination.
    """
    cid = str(data.get("id") or "").strip()
    if not cid:
        return False

    # Never combine active threads
    if cid in active_ids:
        return False

    # Never combine already consolidated threads
    if cid.startswith("consolidated_") or cid.startswith("daily_"):
        return False

    messages = data.get("messages") or []
    if not (1 <= len(messages) <= MAX_SMALL_CHAT_MESSAGES):
        return False

    # Check if large or important
    if is_large_or_important(data):
        return False

    # Inactivity check: ensure user isn't in an active session
    latest_ts_str = str(data.get("updated_at") or data.get("created_at") or "")
    if not latest_ts_str and messages:
        latest_ts_str = str(messages[-1].get("timestamp") or "")

    if latest_ts_str:
        try:
            clean_ts = latest_ts_str.replace("Z", "+00:00")
            ts_dt = datetime.fromisoformat(clean_ts)
            if ts_dt.tzinfo is not None and now_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=None)
            elif ts_dt.tzinfo is None and now_dt.tzinfo is not None:
                now_dt = now_dt.replace(tzinfo=None)
            age_seconds = (now_dt - ts_dt).total_seconds()
            if age_seconds < min_inactive_seconds:
                return False
        except Exception:
            pass

    return True


def _extract_date_str(data: dict[str, Any], fallback_stem: str) -> str:
    """
    Extract YYYYMMDD date string from conversation metadata, messages, or ID.
    """
    for field in ("created_at", "updated_at"):
        val = str(data.get(field) or "")
        match = re.search(r"(\d{4})-?(\d{2})-?(\d{2})", val)
        if match:
            return f"{match.group(1)}{match.group(2)}{match.group(3)}"

    messages = data.get("messages") or []
    if messages:
        ts = str(messages[0].get("timestamp") or "")
        match = re.search(r"(\d{4})-?(\d{2})-?(\d{2})", ts)
        if match:
            return f"{match.group(1)}{match.group(2)}{match.group(3)}"

    match = re.search(r"(\d{4})(\d{2})(\d{2})", fallback_stem)
    if match:
        return f"{match.group(1)}{match.group(2)}{match.group(3)}"

    return datetime.now().strftime("%Y%m%d")


def _format_date_human(date_str: str) -> str:
    try:
        dt = datetime.strptime(date_str, "%Y%m%d")
        return dt.strftime("%b %d, %Y")
    except Exception:
        return date_str


def _generate_compressed_summary(messages: list[dict[str, Any]], human_date: str, count: int) -> str:
    """
    Generate a compressed, dense 1-2 sentence summary of multiple quick actions.
    Uses LLM if available, falling back to smart query extraction.
    """
    user_queries = []
    for m in messages:
        if m.get("role") == "user":
            c = str(m.get("content", "")).strip().replace("\n", " ")
            if c:
                c = re.sub(r"^[A-Za-z0-9_\-\s]+:\s*", "", c).strip()
                if c:
                    user_queries.append(c[:60])
        elif m.get("role") == "assistant" and not user_queries:
            c = str(m.get("content", "")).strip().replace("\n", " ")
            if c:
                user_queries.append(c[:60])

    if not user_queries:
        return f"Quick daily interactions for {human_date} ({len(messages)} messages)."

    unique_queries: list[str] = []
    for q in user_queries:
        if q not in unique_queries:
            unique_queries.append(q)

    fallback_summary = f"Quick actions ({count}): " + "; ".join(unique_queries[:5])
    if len(unique_queries) > 5:
        fallback_summary += f" (+{len(unique_queries) - 5} more)"
    fallback_summary = fallback_summary[:180]

    if len(unique_queries) >= 2:
        try:
            prompt = (
                f"Compress these {len(unique_queries)} quick commands and queries from {human_date} "
                f"into a single concise 1-sentence log summary (maximum 160 characters). "
                f"Keep it brief and factual. Do not include markdown or conversational filler.\n\n"
                + "\n".join(f"- {q}" for q in unique_queries[:12])
            )
            llm_summary = _call_dream_llm(prompt).strip().replace("\n", " ")
            if llm_summary and len(llm_summary) > 10 and "error" not in llm_summary.lower():
                llm_summary = llm_summary.strip("\"' -")
                return f"Quick actions ({count}): {llm_summary[:160]}"
        except Exception:
            pass

    return fallback_summary


def compress_and_combine_small_chats(
    conv_store: Optional[Any] = None,
    min_inactive_seconds: Optional[int] = None,
    all_chats: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Find small, trivial chats (e.g. quick 1-2 turn queries like 'what is the time' or
    device toggles) that have been inactive, combine them chronologically by date into
    'consolidated_YYYYMMDD' threads with compressed summaries, and prune the individual
    micro-files.

    If all_chats=True, scans all historical chats, defaults min_inactive_seconds=0,
    and consolidates all isolated small chats across all dates.
    """
    conv_dir = Path(conv_store.conversations_dir) if (conv_store is not None and getattr(conv_store, "conversations_dir", None)) else _CONVERSATIONS
    if not conv_dir.exists():
        return {"status": "no_dir", "combined_count": 0, "consolidated_threads": 0}

    if min_inactive_seconds is None:
        min_inactive_seconds = 0 if all_chats else MIN_INACTIVE_AGE_SECONDS

    active_ids = set()
    if conv_store is not None and hasattr(conv_store, "get_active_ids"):
        try:
            active_ids = conv_store.get_active_ids()
        except Exception:
            active_ids = set()

    now_dt = datetime.now()
    today_str = now_dt.strftime("%Y%m%d")

    index_file = conv_dir / "index.json"
    index_data: dict[str, dict[str, Any]] = {}
    if index_file.exists():
        try:
            index_data = json.loads(index_file.read_text(encoding="utf-8"))
        except Exception:
            index_data = {}

    all_files = [f for f in conv_dir.glob("*.json") if f.name != "index.json"]
    grouped_by_date: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    empty_pruned = 0

    for file in all_files:
        cid = file.stem
        if cid in active_ids or cid.startswith("consolidated_"):
            continue

        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except Exception:
            continue

        messages = data.get("messages") or []
        if len(messages) == 0:
            # Abandoned empty chat shell
            age_ok = False
            try:
                mtime = file.stat().st_mtime
                if all_chats or (time.time() - mtime) >= min_inactive_seconds:
                    age_ok = True
            except OSError:
                pass
            if age_ok and not dry_run:
                try:
                    file.unlink()
                    index_data.pop(cid, None)
                    empty_pruned += 1
                except OSError:
                    pass
            continue

        if is_large_or_important(data):
            continue

        if not is_small_chat(data, active_ids=active_ids, now_dt=now_dt, min_inactive_seconds=min_inactive_seconds):
            continue

        date_key = _extract_date_str(data, file.stem)
        if date_key not in grouped_by_date:
            grouped_by_date[date_key] = []
        grouped_by_date[date_key].append((file, data))

    total_combined = 0
    consolidated_created = 0
    updated_consolidated: list[str] = []

    for date_key, items in grouped_by_date.items():
        target_id = f"consolidated_{date_key}"
        target_file = conv_dir / f"{target_id}.json"
        already_exists = target_file.exists()

        # Combine if >= 2 items, OR already existing consolidated thread, OR past date, OR all_chats
        if not (len(items) >= 2 or already_exists or date_key < today_str or all_chats):
            continue

        if dry_run:
            total_combined += len(items)
            consolidated_created += 1
            continue

        existing_messages: list[dict[str, Any]] = []
        if already_exists:
            try:
                existing_data = json.loads(target_file.read_text(encoding="utf-8"))
                existing_messages = existing_data.get("messages") or []
            except Exception:
                existing_messages = []

        all_messages = list(existing_messages)
        for _, cdata in items:
            for msg in cdata.get("messages") or []:
                if isinstance(msg, dict) and msg.get("content"):
                    all_messages.append(msg)

        all_messages.sort(key=lambda m: str(m.get("timestamp") or ""))

        # Deduplicate consecutive identical messages
        deduped: list[dict[str, Any]] = []
        for m in all_messages:
            if deduped:
                last = deduped[-1]
                if (
                    last.get("role") == m.get("role")
                    and str(last.get("content", "")).strip() == str(m.get("content", "")).strip()
                ):
                    continue
            deduped.append(m)
        all_messages = deduped

        human_date = _format_date_human(date_key)
        target_name = f"Quick Queries & Actions - {human_date}"

        timestamps = [str(m.get("timestamp")) for m in all_messages if m.get("timestamp")]
        earliest_ts = timestamps[0] if timestamps else f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:]}T00:00:00"
        latest_ts = timestamps[-1] if timestamps else f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:]}T23:59:59"

        compressed_summary = _generate_compressed_summary(
            all_messages, human_date=human_date, count=len(items) + (1 if already_exists else 0)
        )

        ids_to_remove = [
            (d.get("id") or f.stem) for f, d in items if (d.get("id") or f.stem) != target_id
        ]

        applied_via_store = False
        if conv_store is not None:
            try:
                from services.conversation_store import ConversationThread
                cons_thread = ConversationThread(
                    id=target_id,
                    name=target_name,
                    created_at=earliest_ts,
                    updated_at=latest_ts,
                    summary=compressed_summary,
                    messages=all_messages,
                    topics=["quick_interactions", "daily_log"],
                )
                conv_store.prune_and_replace(removed_ids=ids_to_remove, consolidated=cons_thread)
                applied_via_store = True
            except Exception as ex:
                print(f"[Dreamer] prune_and_replace error: {ex}")

        if not applied_via_store:
            thread_dict = {
                "id": target_id,
                "name": target_name,
                "created_at": earliest_ts,
                "updated_at": latest_ts,
                "summary": compressed_summary,
                "messages": all_messages,
                "topics": ["quick_interactions", "daily_log"],
            }
            target_file.write_text(
                json.dumps(thread_dict, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            for f, d in items:
                if f != target_file and f.exists():
                    try:
                        f.unlink()
                    except OSError:
                        pass
                src_id = d.get("id") or f.stem
                index_data.pop(src_id, None)

            index_data[target_id] = {
                "name": target_name,
                "created_at": earliest_ts,
                "updated_at": latest_ts,
                "summary": compressed_summary,
                "message_count": len(all_messages),
            }
            index_file.write_text(
                json.dumps(index_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            if conv_store and hasattr(conv_store, "reload_index"):
                conv_store.reload_index()

        total_combined += len(items)
        consolidated_created += 1
        updated_consolidated.append(target_id)
        print(
            f"[Dreamer] Combined {len(items)} small chat(s) into {target_id} "
            f"({target_name})"
        )

    if empty_pruned > 0 and not applied_via_store:
        try:
            index_file.write_text(
                json.dumps(index_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            if conv_store and hasattr(conv_store, "reload_index"):
                conv_store.reload_index()
        except Exception:
            pass

    return {
        "status": "consolidated" if total_combined > 0 else "no_small_chats",
        "combined_count": total_combined,
        "consolidated_threads": consolidated_created,
        "threads": updated_consolidated,
        "empty_pruned": empty_pruned,
    }


def run_dream_once(conv_store: Optional[Any] = None, all_chats: bool = False) -> dict[str, Any]:
    """
    One interval tick. Safe to call from tests or admin.
    Consolidates facts into long-term memories AND combines small chats.
    Returns a status dict.
    """
    with _dream_lock:
        _ensure_memory_files()
        batch, newest = collect_new_conversations()
        last = load_last_timestamp()

        dream_status = "idle"
        new_convos_count = len(batch)

        if not batch:
            print(f"[Dreamer] Interval tick -- no new conversations (last_checked={last})")
        elif all(not c.get("messages") for c in batch):
            save_last_timestamp(newest)
            print(
                f"[Dreamer] Interval tick -- {len(batch)} empty convo(s), "
                f"advanced cursor to {newest}"
            )
            dream_status = "skipped_empty"
        else:
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
                if ok:
                    save_last_timestamp(newest)
                    print(f"[Dreamer] Done -- last_checked -> {newest}")
                    dream_status = "dreamed"
                else:
                    dream_status = "parse_failed"
            except Exception as e:
                print(f"[Dreamer] LLM/dream failed: {e}")
                dream_status = "error"

        # Chat combination and compression step
        combine_stats = {}
        if COMBINE_SMALL_CHATS_ENABLED or all_chats:
            try:
                combine_stats = compress_and_combine_small_chats(
                    conv_store=conv_store,
                    all_chats=all_chats,
                )
            except Exception as e:
                print(f"[Dreamer] Error during chat combination: {e}")
                combine_stats = {"status": "error", "error": str(e)}

        return {
            "status": dream_status,
            "new_conversations": new_convos_count,
            "last_checked": load_last_timestamp(),
            "chat_combination": combine_stats,
        }


def _interval_seconds() -> float:
    raw = os.getenv("AVA_DREAM_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS))
    try:
        val = float(raw)
    except (TypeError, ValueError):
        val = float(DEFAULT_INTERVAL_SECONDS)
    # Floor at 60s so a bad env value cannot hot-loop the LLM.
    return max(60.0, val)


def _seconds_until_next_half_hour() -> float:
    """Calculate seconds until next :00 or :30 mark on the wall clock."""
    now = datetime.now()
    minute = now.minute
    second = now.second
    microsecond = now.microsecond

    if minute < 30:
        # Next mark is :30 of current hour
        target_minute = 30
    else:
        # Next mark is :00 of next hour
        target_minute = 60

    delta_minutes = target_minute - minute
    # Subtract elapsed seconds/microseconds in current minute
    elapsed_seconds = second + microsecond / 1_000_000
    wait_seconds = (delta_minutes * 60) - elapsed_seconds

    return max(1.0, wait_seconds)


def DreamerStart(conv_store: Optional[Any] = None) -> None:
    """
    Background thread: dream on wall-clock synced to :00 and :30 marks.

    - Consolidates long-term user facts and memories.
    - Combines and compresses small, trivial chats into daily threads.
    - Not triggered by chat end.
    - User activity does not change the schedule.
    - Syncs to real-world clock: runs at XX:00 and XX:30.
    """
    interval = _interval_seconds()
    print(
        f"[Dreamer] Started -- synced to wall-clock :00 and :30 marks "
        f"(interval={interval:.0f}s), conversations={_CONVERSATIONS}"
    )
    _ensure_memory_files()

    while True:
        # Sleep until next :00 or :30 mark on the wall clock
        wait_seconds = _seconds_until_next_half_hour()
        now = datetime.now()
        next_mark = now + timedelta(seconds=wait_seconds)
        print(f"[Dreamer] Next dream at {next_mark.strftime('%H:%M:%S')} (in {wait_seconds:.0f}s)")

        # Chunked sleep so an admin early-wake can interrupt
        remaining = wait_seconds
        chunk = 30.0
        while remaining > 0:
            dreamerEvent.clear()
            woke = dreamerEvent.wait(timeout=min(chunk, remaining))
            if woke:
                # Explicit early wake (tests/admin). Drain and break to dream now.
                dreamerEvent.clear()
                break
            remaining -= chunk

        try:
            run_dream_once(conv_store=conv_store)
        except Exception as e:
            print(f"[Dreamer] Unhandled error on interval tick: {e}")
            # Brief backoff so a hard bug does not tight-loop.
            time.sleep(5)


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="AVA Memory Dreamer & Chat Consolidator")
    parser.add_argument("--all", dest="all_chats", action="store_true", help="Consolidate and compress all small chats across all history")
    parser.add_argument("--dream", action="store_true", help="Run full memory consolidation pass as well")
    parser.add_argument("--dry-run", action="store_true", help="Preview consolidation without modifying files")
    parser.add_argument("--min-age", type=int, default=None, help="Minimum inactive age in seconds (default 900, or 0 if --all)")
    args = parser.parse_args()

    # Ensure Server root is importable
    if str(_SERVER_ROOT) not in sys.path:
        sys.path.insert(0, str(_SERVER_ROOT))

    print(f"[Dreamer CLI] Initializing with conversations dir: {_CONVERSATIONS}")
    try:
        from services.conversation_store import ConversationStore
        store = ConversationStore(_CONVERSATIONS)
    except Exception as ex:
        print(f"[Dreamer CLI] Note: ConversationStore init ({ex}), running direct file-mode")
        store = None

    if args.dream:
        print("[Dreamer CLI] Running dream pass (memories + consolidation)...")
        res = run_dream_once(conv_store=store, all_chats=args.all_chats)
        print(f"[Dreamer CLI] Result: {json.dumps(res, indent=2)}")
    else:
        print(f"[Dreamer CLI] Consolidating small chats (all_chats={args.all_chats}, dry_run={args.dry_run})...")
        res = compress_and_combine_small_chats(
            conv_store=store,
            min_inactive_seconds=args.min_age,
            all_chats=args.all_chats,
            dry_run=args.dry_run,
        )
        print(f"[Dreamer CLI] Result: {json.dumps(res, indent=2)}")
