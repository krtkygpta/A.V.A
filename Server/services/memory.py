"""
memory.py - Semantic Memory System for AVA (server-side).

Port of App/knowledge/memory.py. Stores agent-saved memories as natural-language
sentences in Server/data/memories/agent_memories.json. Reuses the server's shared
OPENAI_API_KEYS pool (key_rotator) for format/dedup/search LLM calls.

The dreamer's interval-consolidated store (memories.txt / behaviours.txt) lives
beside this file and is merged in at read time by get_memory_context().
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_SERVER_ROOT = Path(__file__).resolve().parent.parent
_DATA_ROOT = _SERVER_ROOT / "data"
_MEMORIES_DIR = _DATA_ROOT / "memories"
_AGENT_FILE = _MEMORIES_DIR / "agent_memories.json"
_DREAMER_MEMORIES_FILE = _MEMORIES_DIR / "memories.txt"
_DREAMER_BEHAVIOURS_FILE = _MEMORIES_DIR / "behaviours.txt"

USER_NAME = os.getenv("USER_NAME", "User")

# Soft caps for prompt injection (same env hooks as the TUI module).
MAX_CONTEXT_ITEMS = int(os.getenv("AVA_MEMORY_CONTEXT_MAX_ITEMS", "120"))
MAX_CONTEXT_CHARS = int(os.getenv("AVA_MEMORY_CONTEXT_MAX_CHARS", "14000"))

# Model used for format/dedup/search calls (lightweight + cheap).
_LLM_MODEL = os.getenv("AVA_MEMORY_MODEL", "") or os.getenv("MODEL_NAME", "")


def _ensure_dir() -> None:
    _MEMORIES_DIR.mkdir(parents=True, exist_ok=True)


def _load_memories() -> Dict[str, Any]:
    _ensure_dir()
    if not _AGENT_FILE.exists():
        initial: Dict[str, Any] = {"memories": [], "last_updated": None}
        _save_memories(initial)
        return initial
    try:
        with open(_AGENT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "memories" not in data:
            data["memories"] = []
        return data
    except (json.JSONDecodeError, IOError):
        return {"memories": [], "last_updated": None}


def _save_memories(data: Dict[str, Any]) -> None:
    _ensure_dir()
    data["last_updated"] = datetime.now().isoformat()
    with open(_AGENT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _llm_chat(prompt: str, max_tokens: int = 200, temperature: float = 0.3) -> str:
    """Single-turn chat completion via the server's shared key pool."""
    from services.key_rotator import chat_completions_create

    model = _LLM_MODEL
    resp = chat_completions_create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        label="Memory",
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return (resp.choices[0].message.content or "").strip()


def _format_and_check_duplicate(key: str, value: str, memories: List[Dict]) -> tuple:
    """
    Single LLM call to format a key-value pair as a natural sentence AND find a
    duplicate to update. Returns (formatted_text, duplicate_index) where
    duplicate_index is -1 when there is no match.
    """
    existing = ""
    if memories:
        existing = "\n".join(f"{i}. {m['text']}" for i, m in enumerate(memories[-20:]))

    try:
        prompt = f"""Do two things:
1. Convert this key-value pair into a simple, natural sentence about {USER_NAME}.
   Key: {key}
   Value: {value}
   Example: Key: favorite_song, Value: Believer → "{USER_NAME}'s favorite song is Believer."

2. Check if the sentence updates or replaces any EXISTING memory below. If yes, respond with the number. If not, respond with NEW.
"""
        if existing:
            prompt += f"\nEXISTING MEMORIES:\n{existing}\n"
        else:
            prompt += "\nNo existing memories yet.\n"

        prompt += '\nRespond in EXACTLY this format (two lines):\nSENTENCE: <the natural language sentence>\nDUPLICATE: <number or NEW>'

        result_text = _llm_chat(prompt, max_tokens=150, temperature=0.3)

        sentence = ""
        duplicate_idx = -1
        for line in result_text.split("\n"):
            line = line.strip()
            if line.upper().startswith("SENTENCE:"):
                sentence = line[len("SENTENCE:"):].strip().strip('"\'')
            elif line.upper().startswith("DUPLICATE:"):
                dup_val = line[len("DUPLICATE:"):].strip()
                if dup_val.upper() != "NEW":
                    try:
                        idx = int(dup_val)
                        if 0 <= idx < len(memories):
                            duplicate_idx = idx
                    except ValueError:
                        pass

        if sentence and USER_NAME.lower() not in sentence.lower():
            sentence = f"{USER_NAME}: {sentence}"

        if not sentence:
            sentence = f"{USER_NAME}'s {key.replace('_', ' ')} is {value}"
        return sentence, duplicate_idx

    except Exception as e:
        print(f"[ServerMemory] Format/duplicate check error: {e}")
        return f"{USER_NAME}'s {key.replace('_', ' ')} is {value}", -1


def save_memory(category: str, key: str, value: str) -> str:
    if not key or not value:
        return "Error: Both key and value are required"

    data = _load_memories()
    memories = data.get("memories", [])

    memory_text, duplicate_idx = _format_and_check_duplicate(key, value, memories)
    memory_entry = {
        "text": memory_text,
        "category": category or "general",
        "timestamp": datetime.now().isoformat(),
        "original_key": key,
        "original_value": value,
    }

    if duplicate_idx >= 0:
        memories[duplicate_idx] = memory_entry
        action = "Updated"
    else:
        memories.append(memory_entry)
        action = "Saved"

    data["memories"] = memories
    _save_memories(data)
    return f"{action}! I'll remember that."


def retrieve_memories(category: Optional[str] = None, search: Optional[str] = None) -> str:
    data = _load_memories()
    memories = data.get("memories", [])
    if not memories:
        return "No saved memories yet."

    if category:
        memories = [m for m in memories if m.get("category") == category]

    if search:
        try:
            all_texts = "\n".join(f"- {m['text']}" for m in memories)
            prompt = f"""Find memories relevant to this query.

ALL MEMORIES:
{all_texts}

QUERY: "{search}"

Return ONLY the relevant memories, one per line. If none are relevant, say "No relevant memories found."
"""
            return _llm_chat(prompt, max_tokens=300, temperature=0.2)
        except Exception as e:
            print(f"[ServerMemory] Search error: {e}")

    return "Here's what I remember:\n" + "\n".join(f"- {m['text']}" for m in memories)


def delete_memory(category: str = None, key: str = None) -> str:
    if not key:
        return "Please specify what memory to delete"

    data = _load_memories()
    memories = data.get("memories", [])
    if not memories:
        return "No memories to delete"

    search_term = key.lower()
    for i, m in enumerate(memories):
        if search_term in m.get("text", "").lower() or search_term in m.get("original_key", "").lower():
            deleted = memories.pop(i)
            data["memories"] = memories
            _save_memories(data)
            return f"Deleted: {deleted['text']}"

    return f"No memories found matching '{key}'"


def list_categories() -> str:
    data = _load_memories()
    memories = data.get("memories", [])
    if not memories:
        return "No memories stored yet."

    categories: Dict[str, int] = {}
    for m in memories:
        cat = m.get("category", "general")
        categories[cat] = categories.get(cat, 0) + 1

    lines = [f"I have {len(memories)} memories about you:"]
    for cat, count in categories.items():
        lines.append(f"  - {cat}: {count} memories")
    return "\n".join(lines)


def list_memory_texts(limit: int | None = None) -> List[str]:
    """Plain memory sentences (newest last)."""
    data = _load_memories()
    memories = data.get("memories", []) or []
    texts = [str(m.get("text", "")).strip() for m in memories if m.get("text")]
    texts = [t for t in texts if t]
    if limit is not None and limit > 0 and len(texts) > limit:
        texts = texts[-limit:]
    return texts


def _read_dreamer_block() -> str:
    """Read the dreamer's interval-consolidated memories + behaviours, if present."""
    parts: List[str] = []
    if _DREAMER_MEMORIES_FILE.exists():
        try:
            text = _DREAMER_MEMORIES_FILE.read_text(encoding="utf-8").strip()
            if text and text.lower() != "(none)":
                parts.append(f"[LONG-TERM MEMORY — Dreamer consolidation]\n{text}")
        except OSError:
            pass
    if _DREAMER_BEHAVIOURS_FILE.exists():
        try:
            text = _DREAMER_BEHAVIOURS_FILE.read_text(encoding="utf-8").strip()
            if text and text.lower() != "(none)":
                parts.append(f"[OBSERVED BEHAVIOURS]\n{text}")
        except OSError:
            pass
    return "\n\n".join(parts)


def get_memory_context() -> str:
    """
    Build the merged memory block injected into the agent prompt. Combines:
      1. Dreamer store (memories.txt + behaviours.txt) — durable long-term facts
      2. Agent-saved semantic memories from agent_memories.json

    Returns empty string if there is nothing to inject.
    """
    sections: List[str] = []

    dreamer_block = _read_dreamer_block()
    if dreamer_block:
        sections.append(dreamer_block)

    texts = list_memory_texts(limit=MAX_CONTEXT_ITEMS)
    if texts:
        lines = [f"[THINGS I REMEMBER ABOUT {USER_NAME.upper()}]"]
        used = len(lines[0])
        selected: List[str] = []
        for text in reversed(texts):
            line = f"- {text}"
            if used + len(line) + 1 > MAX_CONTEXT_CHARS and selected:
                break
            selected.append(line)
            used += len(line) + 1
        selected.reverse()
        lines.extend(selected)
        agent_count = len(list_memory_texts())
        if len(selected) < agent_count:
            lines.append(f"(+{agent_count - len(selected)} older saved memories not shown — use memory_manager retrieve)")
        sections.append("\n".join(lines))

    if not sections:
        return ""

    return "\n\n".join(sections)


def handle_memory_manager(
    action: str,
    category: str = None,
    key: str = None,
    value: str = None,
    search: str = None,
) -> str:
    """Main dispatch — mirrors App/knowledge/memory.py handle_memory_manager."""
    try:
        action = (action or "").lower().strip()
        if action == "save":
            if not key or not value:
                return "Please provide what to remember (key) and the information (value)"
            return save_memory(category or "general", key, value)
        if action in ("retrieve", "get", "recall", "search"):
            return retrieve_memories(category=category, search=search or key)
        if action in ("delete", "remove", "forget"):
            return delete_memory(category, key)
        if action in ("categories", "list", "stats"):
            return list_categories()
        return f"Unknown action: {action}. Use: save, retrieve, delete, or categories"
    except Exception as e:
        print(f"[ServerMemory] Error: {e}")
        return f"Memory error: {e}"
