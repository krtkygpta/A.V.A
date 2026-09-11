"""
Shared UI catalog — used by the Textual TUI and the Rich UI.

No widget / framework imports. Safe to import from either frontend.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any


# Always resolve against the App package dir so cwd cannot point at a duplicate.
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "settings.json")

CONFIG_DESCRIPTIONS = {
    "GROQ_API_KEY": "Groq API key for LLM inference",
    "USER_NAME": "Your name (used in prompts)",
    "ASSISTANT_NAME": "Assistant's name",
    "AVA_SERVER_URL": "AVA WebSocket server URL",
    "AVA_START_MODE": "Startup mode: tui_* / continuous / wakeword / text",
    "GOOGLE_AI_API_KEY": "Google AI (Gemini) API key",
    "WEATHER_API_KEY": "OpenWeatherMap API key",
    "THEME": "UI theme name (ctrl+t for full A–Z list)",
    "HA_URL": "Home Assistant URL (e.g., http://homeassistant.local:8123)",
    "HA_TOKEN": "Home Assistant Long-Lived Access Token",
    "HA_TIMEOUT": "Home Assistant timeout in seconds (default: 5)",
}

CONFIG_SECRET_KEYS = {
    "GROQ_API_KEY",
    "GOOGLE_AI_API_KEY",
    "WEATHER_API_KEY",
    "TAVILY_API_KEY",
    "pat",
    "HA_TOKEN",
}

AVA_ASCII = r"""
_____ ___  _______
\__  \\  \/ /\__  \
 / __ \\   /  / __ \_
(____  /\_/  (____  /
     \/           \/ """

MODES = ["text", "continuous", "wakeword"]
MODE_LABELS = {
    "text": "Text",
    "continuous": "Continuous",
    "wakeword": "Wakeword",
}
MODE_ICONS = {
    "text": "💬",
    "continuous": "∞",
    "wakeword": "🎧",
}
MODE_DESCRIPTIONS = {
    "text": "keyboard",
    "continuous": "mic always on",
    "wakeword": "say wake word",
}

SHUTUP_COMMANDS = {"shutup", "shut up", "exit", "quiet", "stop", "bye", "goodbye"}
EXIT_RESPONSES = [
    "Goodbye, sir.",
    "Alright, I'll be quiet.",
    "Got it, stepping back!",
    "I'll give you some space.",
    "Okay, I'll stop talking now.",
    "Message received, going silent.",
    "As you wish, sir!",
    "No problem, I'll leave you alone.",
    "Alright, I'll go away now.",
    "Sorry if I bothered you, I'll stop.",
]


@dataclass(frozen=True)
class SlashCommand:
    """A /command entry for the autocomplete palette."""

    command: str
    description: str
    aliases: tuple[str, ...] = ()
    needs_args: bool = False
    arg_hint: str = ""

    def all_names(self) -> tuple[str, ...]:
        return (self.command,) + self.aliases

    def display_cmd(self) -> str:
        if self.needs_args and self.arg_hint:
            return f"{self.command} {self.arg_hint}"
        return self.command


SLASH_COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("/help", "Show help and command list", ("/commands",)),
    SlashCommand("/theme", "Open the theme picker", ("/themes",)),
    SlashCommand(
        "/theme",
        "Set a theme by name",
        needs_args=True,
        arg_hint="<name>",
    ),
    SlashCommand(
        "/mode",
        "Switch input mode",
        needs_args=True,
        arg_hint="text|continuous|wakeword",
    ),
    SlashCommand("/clear", "Clear the chat log"),
    SlashCommand("/tasks", "Open full background task dashboard"),
    SlashCommand(
        "/task",
        "Show a background task result",
        needs_args=True,
        arg_hint="<id>",
    ),
    SlashCommand("/history", "Toggle history sidebar", ("/chats", "/conversations")),
    SlashCommand("/taskside", "Toggle tasks sidebar", ("/sidebar",)),
    SlashCommand(
        "/quiet",
        "Hide or show system log lines (theme, tips, etc.)",
        ("/logs", "/sys"),
    ),
    SlashCommand("/settings", "Open settings", ("/config", "/set")),
    SlashCommand("/new", "Start a new conversation"),
    SlashCommand("/remote", "Show QR code & URL for mobile/web remote access", ("/remote-access", "/qr", "/mobile", "/web")),
    SlashCommand("/skills", "List installed Claude Code skills", ("/plugins",)),
    SlashCommand("/quit", "Exit AVA"),
)

SETTING_SECTIONS: list[tuple[str, list[str]]] = [
    ("Profile", ["USER_NAME", "ASSISTANT_NAME", "AVA_START_MODE"]),
    ("Connection", ["AVA_SERVER_URL"]),
    (
        "API keys",
        ["GROQ_API_KEY", "GOOGLE_AI_API_KEY", "WEATHER_API_KEY", "TAVILY_API_KEY", "pat"],
    ),
    ("Devices", ["lg_device_id", "country_code", "main_ac_alias"]),
    ("Appearance", ["THEME"]),
]

SETTING_CHOICES: dict[str, list[str]] = {
    "AVA_START_MODE": [
        "tui",
        "tui_text",
        "tui_continuous",
        "tui_wakeword",
        "continuous",
        "wakeword",
        "text",
    ],
}

# Theme names shared by both UIs (Textual builtins + AVA customs).
CUSTOM_THEME_NAMES = (
    "ava",
    "everforest",
    "slate",
    "pine",
    "ember",
    "frost",
    "ink",
    "paper",
    "stone",
)
BUILTIN_THEME_NAMES = (
    "atom-one-dark",
    "atom-one-light",
    "catppuccin-mocha",
    "dracula",
    "flexoki",
    "gruvbox",
    "monokai",
    "nord",
    "rose-pine",
    "solarized-dark",
    "solarized-light",
    "textual-dark",
    "tokyo-night",
)
AVAILABLE_THEMES: list[str] = sorted(set(CUSTOM_THEME_NAMES) | set(BUILTIN_THEME_NAMES))

THEME_ALIASES: dict[str, str] = {
    "flexoki-dark": "flexoki",
    "gruvbox-dark": "gruvbox",
    "ava-light": "paper",
    "flexoki-light": "paper",
    "midnight": "frost",
    "oranges": "ember",
    "synthwave": "ink",
}

THEME_DESCRIPTIONS: dict[str, str] = {
    "ava": "violet + cyan on deep navy",
    "everforest": "moss greens, soft contrast",
    "slate": "cool grays, steel blue accent",
    "pine": "dark greens, sage accent",
    "ember": "warm charcoal, amber accent",
    "frost": "near-black, ice blue accent",
    "ink": "true monochrome, teal accent",
    "paper": "warm white, muted blue accent",
    "stone": "dark warm gray, terracotta accent",
    "atom-one-dark": "Atom One Dark",
    "atom-one-light": "Atom One Light",
    "catppuccin-mocha": "Catppuccin Mocha — cozy dark",
    "dracula": "purple dark",
    "flexoki": "inky dark",
    "gruvbox": "retro earthy",
    "monokai": "classic syntax colors",
    "nord": "arctic blues",
    "rose-pine": "soft dark",
    "solarized-dark": "Solarized Dark",
    "solarized-light": "Solarized Light",
    "textual-dark": "default dark",
    "tokyo-night": "Tokyo Night",
}

_BRAILLE_SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_PULSE_BAR = [
    "▮▯▯▯▯",
    "▮▮▯▯▯",
    "▮▮▮▯▯",
    "▯▮▮▮▯",
    "▯▯▮▮▮",
    "▯▯▯▮▮",
    "▯▯▯▯▮",
    "▯▯▯▮▮",
    "▯▯▮▮▮",
    "▯▮▮▮▯",
    "▮▮▮▯▯",
    "▮▮▯▯▯",
]
_LEVEL_GLYPHS = "▁▂▃▄▅▆▇█"
_BAR_PARTIAL = "▏▎▍▌▋▊▉█"
_BAR_EMPTY = "░"
_BAR_FULL = "█"


def resolve_theme_name(name: str | None) -> str:
    """Map alias / missing names to a valid theme; default ava."""
    if not name:
        return "ava"
    key = str(name).strip().lower()
    key = THEME_ALIASES.get(key, key)
    if key in AVAILABLE_THEMES:
        return key
    return "ava"


def theme_description(name: str) -> str:
    return THEME_DESCRIPTIONS.get(name, "theme")


def all_slash_commands() -> list[SlashCommand]:
    """Built-in /commands plus installed Claude Code skills."""
    out = list(SLASH_COMMANDS)
    try:
        from core.cc_plugins.skills_host import get_host

        host = get_host()
        host.load()
        for skill in host.slash_entries():
            out.append(
                SlashCommand(
                    skill.slash,
                    (skill.description or "Claude Code skill")[:80],
                    needs_args=bool(skill.argument_hint or skill.arguments),
                    arg_hint=skill.argument_hint or ("<args>" if skill.arguments else ""),
                )
            )
    except Exception:
        pass
    return out


def filter_slash_commands(query: str) -> list[SlashCommand]:
    """Return slash commands matching the current input (prefix filter)."""
    raw = query or ""
    if " " in raw:
        return []
    q = raw.strip().lower()
    if not q.startswith("/"):
        return []
    catalog = all_slash_commands()
    if q == "/":
        return list(catalog)

    scored: list[tuple[int, SlashCommand]] = []
    seen: set[tuple[str, str, bool]] = set()
    for cmd in catalog:
        key = (cmd.command, cmd.description, cmd.needs_args)
        if key in seen:
            continue
        names = [n.lower() for n in cmd.all_names()]
        rank = None
        for n in names:
            if n.startswith(q):
                rank = 0
                break
            if q[1:] and q[1:] in n[1:]:
                rank = 1
        if rank is None and q[1:] and q[1:] in cmd.description.lower():
            rank = 2
        if rank is not None:
            seen.add(key)
            scored.append((rank, cmd))
    scored.sort(key=lambda x: (x[0], x[1].command, x[1].description))
    return [c for _, c in scored]


def load_config() -> dict[str, Any]:
    try:
        import json

        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {k: "" for k in CONFIG_DESCRIPTIONS}


def save_config(data: dict) -> tuple[bool, str]:
    try:
        import json

        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True, ""
    except Exception as e:
        return False, str(e)


def format_countdown(seconds: float) -> str:
    """Format remaining seconds as mm:ss or hh:mm:ss."""
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def timer_remaining(task: dict) -> float | None:
    """Return seconds remaining for a timer task, or None if not a live timer."""
    if task.get("task_type") != "timer":
        return None
    status = task.get("status", "")
    if status not in ("running", "pending"):
        return None
    meta = task.get("metadata") or {}
    ends_at = meta.get("ends_at")
    if ends_at is not None:
        try:
            return max(0.0, float(ends_at) - time.time())
        except (TypeError, ValueError):
            pass
    duration = meta.get("duration_seconds")
    if duration is None:
        m = re.search(r"Timer\s*\((\d+)s\)", str(task.get("name", "")), re.I)
        if m:
            duration = int(m.group(1))
    if duration is None:
        return None
    created = task.get("created_at")
    if not created:
        return float(duration)
    try:
        if isinstance(created, str):
            started = datetime.fromisoformat(created)
        else:
            started = created
        elapsed = (datetime.now() - started).total_seconds()
        return max(0.0, float(duration) - elapsed)
    except Exception:
        return float(duration)


def status_glyph(status: str, spin_idx: int = 0) -> str:
    if status in ("running", "pending"):
        return _BRAILLE_SPINNER[spin_idx % len(_BRAILLE_SPINNER)]
    if status == "needs_input":
        return "?"
    if status in ("completed", "notified"):
        return "✓"
    if status == "failed":
        return "✗"
    if status == "cancelled":
        return "—"
    return "·"


def level_bar(samples: list[float], width: int = 12) -> str:
    """Render a short meter from recent RMS samples in ~0..1 space."""
    if not samples:
        return _LEVEL_GLYPHS[0] * width
    tail = list(samples[-width:])
    if len(tail) < width:
        tail = [0.0] * (width - len(tail)) + tail
    peak = max(tail) if tail else 0.0
    scale = peak if peak > 1e-4 else 1e-4
    out = []
    for v in tail:
        n = min(1.0, max(0.0, (v / scale) ** 0.55))
        idx = min(7, int(n * 7.999))
        out.append(_LEVEL_GLYPHS[idx])
    return "".join(out)


def smooth_progress(task: dict, remaining: float, width: int = 14) -> str:
    """Fractional block progress bar that advances smoothly between seconds."""
    meta = task.get("metadata") or {}
    total = meta.get("duration_seconds")
    if total is None:
        m = re.search(r"Timer\s*\((\d+)s\)", str(task.get("name", "")), re.I)
        total = int(m.group(1)) if m else None
    if not total or float(total) <= 0:
        return ""
    frac = 1.0 - (remaining / float(total))
    frac = min(1.0, max(0.0, frac))
    exact = frac * width
    full = int(exact)
    rem = exact - full
    partial_i = min(len(_BAR_PARTIAL) - 1, int(rem * len(_BAR_PARTIAL)))
    parts: list[str] = [_BAR_FULL] * full
    if full < width:
        if rem > 0.02:
            parts.append(_BAR_PARTIAL[partial_i])
            parts.append(_BAR_EMPTY * (width - full - 1))
        else:
            parts.append(_BAR_EMPTY * (width - full))
    return "".join(parts)


def is_live_task(task: dict) -> bool:
    """Only real in-process work — not stale pending ghosts from disk."""
    status = task.get("status", "")
    if status in ("running", "needs_input"):
        return True
    if status == "pending":
        created = task.get("created_at")
        if not created:
            return False
        try:
            when = (
                datetime.fromisoformat(created)
                if isinstance(created, str)
                else created
            )
            return (datetime.now() - when).total_seconds() < 45
        except Exception:
            return False
    return False


def collect_sidebar_tasks(tasks: list[dict]) -> list[dict]:
    active = [t for t in tasks if is_live_task(t)]
    recent_done = []
    for t in tasks:
        if t.get("status") not in ("completed", "failed", "notified"):
            continue
        completed = t.get("completed_at")
        if not completed:
            continue
        try:
            when = datetime.fromisoformat(completed)
            if (datetime.now() - when).total_seconds() < 120:
                recent_done.append(t)
        except Exception:
            pass
    active.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    recent_done.sort(key=lambda t: t.get("completed_at", ""), reverse=True)
    return active + recent_done[:4]


def load_recent_conversations(limit: int = 50) -> list[dict]:
    try:
        from knowledge.ConversationManager import get_manager

        mgr = get_manager()
        try:
            mgr._load_index()
        except Exception:
            pass
        recent = mgr.get_recent_conversations(limit=limit)
        if not recent and mgr.conversations_index:
            recent = [
                {
                    "id": cid,
                    "name": info.get("name", "Unnamed"),
                    "summary": info.get("summary", ""),
                    "date": str(info.get("created_at", ""))[:10],
                }
                for cid, info in sorted(
                    mgr.conversations_index.items(),
                    key=lambda x: x[1].get("updated_at", x[1].get("created_at", "")),
                    reverse=True,
                )[:limit]
            ]
        return recent
    except Exception:
        return []


def load_conversation_messages(conversation_id: str) -> list[tuple[str, str]]:
    try:
        from knowledge.ConversationManager import get_manager

        conv = get_manager().load_conversation(conversation_id)
    except Exception as e:
        return [("system", f"could not load conversation: {e}")]
    if not conv:
        return [
            (
                "system",
                f"conversation file not found for id {conversation_id}",
            )
        ]
    out: list[tuple[str, str]] = []
    for msg in conv.messages:
        role = msg.get("role") or "system"
        content = str(msg.get("content") or "")
        if not content.strip():
            continue
        if role == "tool" and len(content) > 600:
            content = content[:600] + "…"
        elif len(content) > 2000:
            content = content[:2000] + "…"
        label = role.upper()
        ts = str(msg.get("timestamp") or "")[11:16]
        head = f"{label}" + (f"  {ts}" if ts else "")
        safe_role = (
            role if role in ("user", "assistant", "tool", "system") else "system"
        )
        out.append((safe_role, f"{head}\n{content}"))
    if not out:
        out.append(("system", "empty conversation"))
    return out


def parse_start_mode(raw: str) -> tuple[str, str]:
    """Return (ui_kind, voice_mode) from AVA_START_MODE / override.

    ui_kind is 'textual' or 'cli'. voice_mode is text|continuous|wakeword.
    """
    key = (raw or "tui_text").strip().lower()
    if key in ("vosk",):
        return "cli", "wakeword"
    if key in ("continuous", "wakeword", "text"):
        return "cli", key
    if key.startswith("tui"):
        rest = key[3:].lstrip("_") or "text"
        if rest not in MODES:
            rest = "text"
        return "textual", rest
    return "textual", "text"
