"""
AVA Terminal UI — Textual-based TUI inspired by Claude Code / OpenCode.

Provides:
  - Rich scrollable chat log with user / assistant / tool messages
  - Animated tool-call panels (spinner while waiting, check when done)
  - Generating / thinking indicator while the model streams a response
  - Left history sidebar (past conversations, read-only)
  - Right tasks sidebar (live background jobs + timer countdowns)
  - Input bar, mode switcher, status bar, themes
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

_APP_DIR = str(Path(__file__).resolve().parent.parent)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _patch_terminal_cell_widths() -> None:
    """Ensure complex scripts (Indic, Arabic, Thai, Hebrew, etc.) whose combining
    marks take positive visual space in terminal fonts are measured
    as width 1 rather than width 0. This prevents Textual/Rich from undercounting cell
    widths, which generates phantom padding spaces and misaligns vertical borders and lines.
    """
    try:
        import functools
        import rich.cells

        orig_get_cell_size = rich.cells.get_character_cell_size

        @functools.lru_cache(maxsize=4096)
        def patched_get_character_cell_size(character: str, unicode_version: str = "auto") -> int:
            cp = ord(character)
            # Indic & complex script blocks where terminal monospace fonts render
            # combining marks / vowels / matras as discrete 1-cell glyphs:
            # Hebrew (0590-05FF), Arabic (0600-08FF),
            # Devanagari, Bengali, Gurmukhi, Gujarati, Oriya, Tamil, Telugu, Kannada,
            # Malayalam, Sinhala (0900-0DFF),
            # Thai, Lao, Tibetan, Myanmar (0E00-109F),
            # Vedic Extensions (1CD0-1CFF), Devanagari Extended (A8E0-A8FF)
            if (
                (0x0590 <= cp <= 0x109F)
                or (0x1CD0 <= cp <= 0x1CFF)
                or (0xA8E0 <= cp <= 0xA8FF)
            ):
                return 1
            return orig_get_cell_size(character, unicode_version)

        rich.cells.get_character_cell_size = patched_get_character_cell_size
        try:
            rich.cells.get_character_cell_size.cache_clear()
        except Exception:
            pass
        try:
            rich.cells.cached_cell_len.cache_clear()
        except Exception:
            pass
    except Exception as e:
        print(f"[TUI] Terminal cell width patch failed: {e}")


_patch_terminal_cell_widths()

from rich.text import Text


def escape(markup: str) -> str:
    """Safely escape arbitrary text so Textual and Rich won't interpret it as markup.

    Textual's markup tokenizer treats ANY unescaped '[' as a tag opening (including uppercase
    tags with attributes, such as '[TASK DOSSIER id=... status=...]'). Standard rich/textual
    escape() only regex-escapes lowercase tags '[a-z#/@]', which leaves bracketed expressions
    unescaped and causes Textual to crash with MarkupError when rendered.
    """
    if not markup:
        return ""
    escaped = re.sub(r"(\\*)(\[)", lambda m: f"{m.group(1)}{m.group(1)}\\[", str(markup))
    if escaped.endswith("\\") and not escaped.endswith("\\\\"):
        return escaped + "\\"
    return escaped
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Collapsible,
    Footer,
    Header,
    Input,
    Label,
    OptionList,
    Rule,
    Static,
)
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    pass

# ── Import AVA internals (same ones __main__.py uses) ───────────────────────
from config import ASSISTANT_NAME, USER_NAME
from core.AppStates import (
    main_runner,
    stop_chat_flag,
    stop_event,
    voice_stop_event,
    wakeword_stop_event,
)
from utils import chime

_mode_switch_event = threading.Event()
from core.messageHandler import (
    add_message,
    reset_messages,
    register_user_message_listener,
    unregister_user_message_listener,
    register_conversation_reset_listener,
    unregister_conversation_reset_listener,
    register_slash_command_listener,
    unregister_slash_command_listener,
    register_clear_chat_listener,
    unregister_clear_chat_listener,
    register_system_message_listener,
    unregister_system_message_listener,
)
from core.response_loop import ResponseLoopHandler, main_running, run_response_loop
from knowledge.ConversationManager import (
    save_current_conversation,
    start_new_conversation,
)
# Import for background task notifications
from core.TaskManager import BackgroundTask

# ── Theme definitions ─────────────────────────────────────────────────────────
"""
All Textual built-in themes + AVA custom themes, ordered by name in the picker.
Custom themes must use names that do not collide with Textual's BUILTIN_THEMES
(registering a custom with the same name would shadow the built-in).
"""

from textual.theme import BUILTIN_THEMES, Theme

# ── Custom themes (not provided by Textual) ───────────────────────────────────
# Palettes tuned for readable contrast: bg < surface < panel, vivid accents.

AVA_THEME = Theme(
    name="ava",
    primary="#8b7cf7",
    secondary="#5b9cf5",
    background="#0b0d14",
    surface="#12151f",
    panel="#1a1f2e",
    boost="#a99af9",
    foreground="#e8ecf4",
    success="#34d399",
    warning="#fbbf24",
    error="#f87171",
    accent="#22d3ee",
    dark=True,
)

EVERFOREST = Theme(
    name="everforest",
    primary="#a7c080",
    secondary="#7fbbb3",
    background="#1e2326",
    surface="#272e33",
    panel="#2f383e",
    boost="#83c092",
    foreground="#d3c6aa",
    success="#a7c080",
    warning="#dbbc7f",
    error="#e67e80",
    accent="#e69875",
    dark=True,
)

# ── Monochromatic themes — single accent, restrained palette ───────────────

CHATGPT = Theme(
    name="chatgpt",
    primary="#ececec",
    secondary="#b4b4b4",
    background="#000000",
    surface="#171717",
    panel="#212121",
    boost="#2f2f2f",
    foreground="#ececec",
    success="#34d399",
    warning="#b4b4b4",
    error="#ef4444",
    accent="#ececec",
    dark=True,
)

SLATE = Theme(
    name="slate",
    primary="#7e99b8",
    secondary="#5e7a96",
    background="#0f1117",
    surface="#161822",
    panel="#1e2030",
    boost="#94b0cc",
    foreground="#c8cdd6",
    success="#6a9e7a",
    warning="#b8a06a",
    error="#b86a6a",
    accent="#94b0cc",
    dark=True,
)

PINE = Theme(
    name="pine",
    primary="#6b9e7a",
    secondary="#4d7a5c",
    background="#0f1310",
    surface="#151a16",
    panel="#1c2420",
    boost="#8fbb9a",
    foreground="#c4d0c6",
    success="#6b9e7a",
    warning="#b0a060",
    error="#b07060",
    accent="#8fbb9a",
    dark=True,
)

EMBER = Theme(
    name="ember",
    primary="#c49a5c",
    secondary="#9a7a4a",
    background="#131110",
    surface="#1a1714",
    panel="#23201b",
    boost="#d4ad6e",
    foreground="#d0c8bc",
    success="#7a9e6a",
    warning="#c49a5c",
    error="#b06a5a",
    accent="#d4ad6e",
    dark=True,
)

FROST = Theme(
    name="frost",
    primary="#5e9fd4",
    secondary="#4a7faa",
    background="#0b0e14",
    surface="#111520",
    panel="#192030",
    boost="#7ebcf0",
    foreground="#c4d0e0",
    success="#6a9e7a",
    warning="#b0a060",
    error="#b06a6a",
    accent="#7ebcf0",
    dark=True,
)

INK = Theme(
    name="ink",
    primary="#4ecdc4",
    secondary="#3a9e97",
    background="#0e0e0e",
    surface="#161616",
    panel="#1e1e1e",
    boost="#6ee0d9",
    foreground="#c8c8c8",
    success="#6a9e7a",
    warning="#b0a060",
    error="#b06a6a",
    accent="#6ee0d9",
    dark=True,
)

PAPER = Theme(
    name="paper",
    primary="#5a7d9a",
    secondary="#7a9ab5",
    background="#faf8f5",
    surface="#f0eee8",
    panel="#e6e3dc",
    boost="#4a6d8a",
    foreground="#2a2a2a",
    success="#5a8a5a",
    warning="#a08040",
    error="#a05050",
    accent="#4a6d8a",
    dark=False,
)

STONE = Theme(
    name="stone",
    primary="#c07856",
    secondary="#a06040",
    background="#121110",
    surface="#1a1918",
    panel="#232220",
    boost="#d89070",
    foreground="#ccc4b8",
    success="#7a9e6a",
    warning="#b0a060",
    error="#b06a5a",
    accent="#d89070",
    dark=True,
)

# Only register themes whose names are NOT already in Textual's builtins.
_ALL_CUSTOM_THEMES: list[Theme] = [
    CHATGPT,
    AVA_THEME,
    EVERFOREST,
    SLATE,
    PINE,
    EMBER,
    FROST,
    INK,
    PAPER,
    STONE,
]
_ALL_CUSTOM_THEMES = [t for t in _ALL_CUSTOM_THEMES if t.name not in BUILTIN_THEMES]

# Curated built-in list: drop near-duplicates (extra catppuccins, extra rose-pines, etc.)
_KEEP_BUILTINS = {
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
}
AVAILABLE_BUILTINS: list[str] = sorted(
    name for name in BUILTIN_THEMES if name in _KEEP_BUILTINS
)

# Full catalog: curated builtins + customs, sorted A–Z by name
AVAILABLE_THEMES: list[str] = sorted(
    set(AVAILABLE_BUILTINS) | {t.name for t in _ALL_CUSTOM_THEMES}
)

# Old settings keys → current canonical theme names
THEME_ALIASES: dict[str, str] = {
    "minimal": "chatgpt",
    "monochrome": "chatgpt",
    "flexoki-dark": "flexoki",
    "gruvbox-dark": "gruvbox",
    # Retired custom themes → nearest replacement
    "ava-light": "paper",
    "flexoki-light": "paper",
    "midnight": "frost",
    "oranges": "ember",
    "synthwave": "ink",
}

# Descriptions for every built-in + custom (picker shows these)
THEME_DESCRIPTIONS: dict[str, str] = {
    # Custom
    "chatgpt": "pure monochromatic dark (ChatGPT minimal)",
    "ava": "violet + cyan on deep navy",
    "everforest": "moss greens, soft contrast",
    "slate": "cool grays, steel blue accent",
    "pine": "dark greens, sage accent",
    "ember": "warm charcoal, amber accent",
    "frost": "near-black, ice blue accent",
    "ink": "true monochrome, teal accent",
    "paper": "warm white, muted blue accent",
    "stone": "dark warm gray, terracotta accent",
    # Curated built-ins
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


def _resolve_theme_name(name: str | None) -> str:
    """Map alias / missing names to a valid theme; default chatgpt."""
    if not name:
        return "chatgpt"
    key = str(name).strip().lower()
    key = THEME_ALIASES.get(key, key)
    if key in AVAILABLE_THEMES:
        return key
    return "chatgpt"


def _theme_description(name: str) -> str:
    return THEME_DESCRIPTIONS.get(name, "Textual theme")
# ── Constants ────────────────────────────────────────────────────────────────
# Always resolve against the App package dir so cwd (project root vs App/)
# cannot point settings at a different/duplicate file.
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "settings.json")

CONFIG_DESCRIPTIONS = {
    "GROQ_API_KEY": "Groq API key for LLM inference",
    "USER_NAME": "Your name (used in prompts)",
    "ASSISTANT_NAME": "Assistant's name",
    "AVA_SERVER_URL": "AVA WebSocket server URL",
    "AVA_START_MODE": "Startup mode: tui / continuous / wakeword",
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
    "text": "",
    "continuous": "∞",
    "wakeword": "",
}
MODE_DESCRIPTIONS = {
    "text": "keyboard",
    "continuous": "mic always on",
    "wakeword": "say wake word",
}


@dataclass(frozen=True)
class SlashCommand:
    """A /command entry for the autocomplete palette."""

    command: str
    description: str
    aliases: tuple[str, ...] = ()
    # If True, selecting fills "/cmd " and leaves you to type args
    needs_args: bool = False
    arg_hint: str = ""

    def all_names(self) -> tuple[str, ...]:
        return (self.command,) + self.aliases

    def display_cmd(self) -> str:
        if self.needs_args and self.arg_hint:
            return f"{self.command} {self.arg_hint}"
        return self.command


# Canonical slash commands (palette + help source of truth)
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
    SlashCommand(
        "/watch",
        "Watch background task live process stream",
        needs_args=False,
        arg_hint="[<id>]",
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
    SlashCommand("/skills", "List installed Claude Code skills", ("/plugins",)),
    SlashCommand("/quit", "Exit AVA"),
)
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

# ── Global reference to the running TUI app (for callbacks) ──────────────────
_app_instance: "AVAApp | None" = None
# main_running is imported from core.response_loop (shared with CLI)


# ═══════════════════════════════════════════════════════════════════════════════
# Custom Widgets
# ═══════════════════════════════════════════════════════════════════════════════


class ChatMessage(Static):
    """A single message bubble in the chat log."""

    DEFAULT_CSS = """
    /* Flat on $background — accent rail, airy vertical rhythm */
    ChatMessage {
        width: 100%;
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
        background: transparent;
    }
    ChatMessage.user-msg {
        background: transparent;
        border-left: solid $secondary;
        padding: 0 1;
        color: $foreground;
        margin: 0 0 1 0;
    }
    ChatMessage.assistant-msg {
        background: transparent;
        border-left: solid $primary;
        padding: 0 1;
        color: $foreground;
        margin: 0 0 1 0;
    }
    ChatMessage.tool-msg {
        background: transparent;
        border-left: solid $accent;
        padding: 0 1;
        color: $foreground;
        margin: 0 0 0 0;
    }
    /* Ephemeral UI noise (theme, online, tips) — very faded, hide with /quiet */
    ChatMessage.system-msg {
        background: transparent;
        color: $foreground 35%;
        text-style: none;
        padding: 0 1;
        margin: 0;
        border-left: none;
        height: auto;
    }
    """


# Braille / block spinners used across tool + generating indicators
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

# Real mic-level bar glyphs (status bar only — never a fake animation)
_LEVEL_GLYPHS = "▁▂▃▄▅▆▇█"


def _level_bar(samples: list[float], width: int = 12) -> str:
    """Render a short meter from recent RMS samples in ~0..1 space."""
    if not samples:
        return _LEVEL_GLYPHS[0] * width
    # use last `width` samples; pad left with silence
    tail = list(samples[-width:])
    if len(tail) < width:
        tail = [0.0] * (width - len(tail)) + tail
    # normalize against recent peak so quiet rooms still move
    peak = max(tail) if tail else 0.0
    scale = peak if peak > 1e-4 else 1e-4
    out = []
    for v in tail:
        # mild log-ish curve so soft speech isn't stuck on ▁
        n = min(1.0, max(0.0, (v / scale) ** 0.55))
        idx = min(7, int(n * 7.999))
        out.append(_LEVEL_GLYPHS[idx])
    return "".join(out)


class ToolCallPanel(Static):
    """Tool call with animated loader on the name while waiting for a result."""

    DEFAULT_CSS = """
    ToolCallPanel {
        width: 100%;
        height: auto;
        margin: 0 0 1 0;
        background: transparent;
        border: none;
        border-left: solid $accent;
        padding: 0 0;
    }
    ToolCallPanel.tool-pending {
        border-left: solid $warning;
    }
    ToolCallPanel.tool-done {
        border-left: solid $success;
    }
    ToolCallPanel.tool-error {
        border-left: solid $error;
    }
    ToolCallPanel Collapsible {
        background: transparent;
        border: none;
        padding: 0;
        margin: 0;
        height: auto;
    }
    ToolCallPanel CollapsibleTitle {
        color: $warning;
        background: transparent;
        padding: 0 1;
        text-style: bold;
        height: 1;
    }
    ToolCallPanel.tool-done CollapsibleTitle {
        color: $success;
    }
    ToolCallPanel CollapsibleTitle:hover {
        background: transparent;
        color: $foreground;
        text-style: bold underline;
    }
    ToolCallPanel .tool-args {
        color: $secondary;
        padding: 0 1 0 2;
    }
    ToolCallPanel .tool-result {
        color: $accent;
        padding: 0 1 0 2;
    }
    ToolCallPanel .tool-wait {
        color: $warning;
        padding: 0 1 0 2;
        text-style: none;
    }
    """

    def __init__(self, func_name: str, args: dict | str, result: str = "", **kwargs):
        super().__init__(**kwargs)
        self._func_name = func_name
        self._args = args
        self._result = result
        self._pending = not bool(result)
        self._spin_idx = 0
        self._spin_timer = None
        self._started = time.time()

    def compose(self) -> ComposeResult:
        if isinstance(self._args, dict):
            try:
                args_str = json.dumps(self._args, indent=2)
            except Exception:
                args_str = str(self._args)
        else:
            args_str = str(self._args)

        title = self._title_for_state()
        with Collapsible(title=title, collapsed=True, id="tool-collapse"):
            if args_str and args_str != "{}":
                display_args = args_str[:500] + ("..." if len(args_str) > 500 else "")
                # markup=False: tool JSON often contains [brackets] that break Rich
                yield Static(display_args, classes="tool-args", markup=False)
            if self._pending:
                yield Static(
                    "waiting for result...",
                    classes="tool-wait",
                    id="tool-wait",
                    markup=False,
                )
            if self._result:
                display_result = self._result[:300] + (
                    "..." if len(self._result) > 300 else ""
                )
                yield Static(
                    f"↳ {display_result}",
                    classes="tool-result",
                    markup=False,
                )

    def on_mount(self) -> None:
        if self._pending:
            self.add_class("tool-pending")
            self._spin_timer = self.set_interval(0.15, self._tick_spinner)
        else:
            self.add_class("tool-done")

    def on_unmount(self) -> None:
        self._stop_spinner()

    def _title_for_state(self) -> str:
        if self._pending:
            glyph = _BRAILLE_SPINNER[self._spin_idx % len(_BRAILLE_SPINNER)]
            elapsed = max(0, int(time.time() - self._started))
            suffix = f"  ·  {elapsed}s" if elapsed > 0 else ""
            return f"{glyph}  {self._func_name}{suffix}"
        return f"✓  {self._func_name}"

    def _tick_spinner(self) -> None:
        if not self._pending:
            return
        self._spin_idx = (self._spin_idx + 1) % len(_BRAILLE_SPINNER)
        try:
            collapse = self.query_one("#tool-collapse", Collapsible)
            collapse.title = self._title_for_state()
        except NoMatches:
            pass

    def _stop_spinner(self) -> None:
        if self._spin_timer is not None:
            try:
                self._spin_timer.stop()
            except Exception:
                pass
            self._spin_timer = None

    def set_result(self, result: str, *, error: bool = False) -> None:
        """Mark the tool finished and attach the result body."""
        self._result = result or "done"
        self._pending = False
        self._stop_spinner()
        self.remove_class("tool-pending")
        self.add_class("tool-error" if error else "tool-done")
        try:
            collapse = self.query_one("#tool-collapse", Collapsible)
            collapse.title = self._title_for_state()
        except NoMatches:
            pass
        try:
            wait = self.query_one("#tool-wait", Static)
            wait.remove()
        except NoMatches:
            pass
        display_result = self._result[:300] + (
            "..." if len(self._result) > 300 else ""
        )
        result_widget = Static(
            f"↳ {display_result}",
            classes="tool-result",
            markup=False,
        )
        try:
            contents = self.query_one("Collapsible > Contents")
            contents.mount(result_widget)
        except NoMatches:
            self.mount(result_widget)


class ModeDropdown(Static):
    """Compact mode selector button placed right of the input bar.

    Shows the current mode icon + label. Clicking opens a modal to pick
    from all three modes.
    """

    DEFAULT_CSS = """
    ModeDropdown {
        height: 3;
        width: auto;
        min-width: 10;
        background: $panel;
        border: tall $secondary 50%;
        padding: 0 1;
        margin: 0 0 0 0;
        layout: horizontal;
        align-vertical: middle;
    }
    ModeDropdown:hover {
        background: $boost;
        border: tall $primary 50%;
    }
    ModeDropdown:focus {
        border: tall $primary;
    }
    ModeDropdown #mode-current {
        max-width: 12;
    }
    ModeDropdown #mode-current {
        height: 1;
        width: auto;
        color: $secondary;
        text-style: bold;
        content-align: center middle;
    }
    ModeDropdown:hover #mode-current {
        color: $foreground;
    }
    """

    current_mode: reactive[str] = reactive("text")

    def compose(self) -> ComposeResult:
        yield Static(self._label(), id="mode-current")

    def _label(self) -> str:
        icon = MODE_ICONS.get(self.current_mode, "")
        label = MODE_LABELS.get(self.current_mode, "TEXT")
        return f"{icon} {label}"

    def watch_current_mode(self, mode: str) -> None:
        try:
            self.query_one("#mode-current", Static).update(self._label())
        except NoMatches:
            pass

    def on_click(self) -> None:
        self.post_message(self.OpenRequested())

    def set_mode(self, mode: str) -> None:
        if mode in MODES:
            self.current_mode = mode

    class OpenRequested(Message):
        """Posted when the user clicks the mode dropdown button."""


class ModeSelectorPopup(ModalScreen[str | None]):
    """Compact mode selector popup — shows all three modes as clickable rows."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("q", "cancel", "Cancel", show=False),
    ]

    CSS = """
    ModeSelectorPopup {
        align: center middle;
        background: $background 40%;
    }
    #mode-popup {
        width: 40;
        max-width: 90%;
        height: auto;
        background: $panel;
        border: solid $primary;
        padding: 0 1;
        layout: vertical;
    }
    #mode-popup .popup-title {
        width: 100%;
        height: 1;
        color: $primary;
        text-style: bold;
        margin: 0 0 0 0;
    }
    #mode-popup .popup-hint {
        width: 100%;
        height: 1;
        color: $secondary;
        text-style: none;
        margin: 0 0 0 0;
    }
    #mode-popup Button {
        width: 100%;
        height: 1;
        min-height: 1;
        padding: 0 1;
        margin: 0;
        background: $surface;
        border: none;
        color: $foreground;
        text-style: none;
        text-align: left;
        content-align: left middle;
    }
    #mode-popup Button:hover {
        background: $boost;
        border: none;
        color: $foreground;
    }
    #mode-popup Button.-primary {
        background: $primary 25%;
        border: none;
        color: $foreground;
        text-style: bold;
    }
    #mode-popup Button.-primary:hover {
        background: $primary 40%;
        border: none;
        color: $foreground;
    }
    """

    def __init__(self, current_mode: str = "text", **kwargs):
        super().__init__(**kwargs)
        self._current = current_mode

    def compose(self) -> ComposeResult:
        with Vertical(id="mode-popup"):
            yield Static("select mode", classes="popup-title")
            yield Static("click to select  ·  esc cancel", classes="popup-hint")
            for mode in MODES:
                icon = MODE_ICONS[mode]
                label = MODE_LABELS[mode]
                desc = MODE_DESCRIPTIONS[mode]
                yield Button(
                    f" {icon}  {label}  ·  {desc}",
                    id=f"opt-{mode}",
                    variant="primary" if mode == self._current else "default",
                    flat=True,
                )

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid.startswith("opt-"):
            mode = bid[4:]
            if mode in MODES:
                self.dismiss(mode)

    def action_cancel(self) -> None:
        self.dismiss(None)


class StatusBar(Static):
    """Bottom status bar showing current state (+ optional live mic meter) and Hub connection status."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        min-height: 1;
        max-height: 1;
        width: 100%;
        padding: 0 1;
        background: $panel;
        color: $secondary;
    }
    """

    status_text: reactive[str] = reactive("")
    conn_status: reactive[str] = reactive("connecting")
    # Recent RMS samples from the mic (status bar meter only)
    level_samples: reactive[list] = reactive(list, always_update=True)
    mic_active: reactive[bool] = reactive(False)

    def render(self):
        from rich.table import Table
        from rich.text import Text

        text = self.status_text or ""
        low = text.lower()
        meter = ""
        if self.mic_active and self.level_samples:
            meter = f"  [bold cyan]{_level_bar(list(self.level_samples))}[/]"
        if "hearing" in low or (self.mic_active and "record" in low):
            left_str = f"[bold green]●[/]  {text}{meter}"
        elif "transcrib" in low:
            left_str = f"[bold yellow]●[/]  {text}"
        elif "thinking" in low or "generat" in low:
            left_str = f"[bold magenta]●[/]  {text}"
        elif "wake" in low:
            left_str = f"[bold cyan]○[/]  {text}"
        elif "ready" in low:
            left_str = f"[dim]○[/]  {text}"
        elif self.mic_active:
            left_str = f"[bold green]●[/]  {text}{meter}"
        else:
            left_str = text or "[dim]○  ready[/]"

        conn = (self.conn_status or "").lower()
        if conn == "connected":
            right_str = "[bold green]●[/] [dim]HUB ONLINE[/]"
        elif conn in ("connecting", "reconnecting"):
            right_str = "[bold yellow]○[/] [yellow]HUB RECONNECTING...[/]"
        else:
            right_str = "[bold red]●[/] [dim red]HUB OFFLINE[/]"

        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="right")
        grid.add_row(Text.from_markup(left_str), Text.from_markup(right_str))
        return grid


class MicStrip(Static):
    """Listening / recording strip directly above the input bar (not in chat)."""

    DEFAULT_CSS = """
    MicStrip {
        width: 100%;
        height: 1;
        min-height: 1;
        padding: 0 1;
        background: $success;
        color: $background;
        text-style: bold;
    }
    MicStrip.transcribing {
        background: $warning;
        color: $background;
    }
    """

    def __init__(self, **kwargs):
        super().__init__("", **kwargs)
        self._state = "idle"  # idle | recording | hearing | transcribing
        self._partial = ""
        self._levels: list[float] = []

    def on_mount(self) -> None:
        self.display = False

    def set_idle(self) -> None:
        self._state = "idle"
        self._partial = ""
        self._levels = []
        self.remove_class("visible")
        self.remove_class("recording")
        self.remove_class("hearing")
        self.remove_class("transcribing")
        self.display = False
        self.update("")

    def set_recording(self) -> None:
        self._state = "recording"
        self.remove_class("hearing")
        self.remove_class("transcribing")
        self.add_class("visible")
        self.add_class("recording")
        self.display = True
        self._paint()

    def set_hearing(self, partial: str = "") -> None:
        self._state = "hearing"
        self._partial = (partial or "").strip()
        self.remove_class("recording")
        self.remove_class("transcribing")
        self.add_class("visible")
        self.add_class("hearing")
        self.display = True
        self._paint()

    def set_transcribing(self) -> None:
        self._state = "transcribing"
        self._levels = []
        self.remove_class("recording")
        self.remove_class("hearing")
        self.add_class("visible")
        self.add_class("transcribing")
        self.display = True
        self._paint()

    def push_level(self, level: float) -> None:
        if self._state not in ("recording", "hearing"):
            self.set_recording()
        self._levels.append(max(0.0, float(level)))
        if len(self._levels) > 16:
            self._levels = self._levels[-16:]
        self._paint()

    def _paint(self) -> None:
        meter = _level_bar(self._levels, width=14)
        # Keep colors simple — strip background carries the state color
        if self._state == "transcribing":
            self.update("●  transcribing  ·  whisper…")
            return
        if self._state == "hearing":
            snippet = self._partial
            if len(snippet) > 60:
                snippet = snippet[:57] + "…"
            body = f"  ·  {snippet}" if snippet else ""
            self.update(f"●  hearing  {meter}{body}")
            return
        self.update(f"●  recording  {meter}  ·  speak…")


class GeneratingIndicator(Static):
    """OpenCode-style generating animation shown while the model is working."""

    DEFAULT_CSS = """
    GeneratingIndicator {
        height: 1;
        width: 100%;
        padding: 0 1;
        margin: 0 0 0 0;
        color: $primary;
        text-style: none;
        background: transparent;
        border: none;
        border-left: solid $primary;
    }
    """

    PHASES = (
        "thinking",
        "reasoning",
        "generating",
        "composing",
    )

    def __init__(self, label: str = "thinking", **kwargs):
        super().__init__(**kwargs)
        self._label = label
        self._spin_idx = 0
        self._phase_idx = 0
        self._bar_idx = 0
        self._timer = None
        self._started = time.time()

    def on_mount(self) -> None:
        self._tick()
        self._timer = self.set_interval(0.15, self._tick)

    def on_unmount(self) -> None:
        if self._timer is not None:
            try:
                self._timer.stop()
            except Exception:
                pass
            self._timer = None

    def set_label(self, label: str) -> None:
        self._label = label

    def _tick(self) -> None:
        self._spin_idx = (self._spin_idx + 1) % len(_BRAILLE_SPINNER)
        self._bar_idx = (self._bar_idx + 1) % len(_PULSE_BAR)
        # Slowly rotate soft phase labels for a living feel
        if self._spin_idx == 0:
            self._phase_idx = (self._phase_idx + 1) % len(self.PHASES)
        glyph = _BRAILLE_SPINNER[self._spin_idx]
        bar = _PULSE_BAR[self._bar_idx]
        phase = self._label or self.PHASES[self._phase_idx]
        elapsed = max(0, int(time.time() - self._started))
        time_bit = f"  ·  {elapsed}s" if elapsed >= 1 else ""
        self.update(f" {glyph}  {phase}  {bar}{time_bit}")


# Back-compat alias
TypingIndicator = GeneratingIndicator


# Setting categories for the modal form (key → section)
SETTING_SECTIONS: list[tuple[str, list[str]]] = [
    (
        "Profile",
        ["USER_NAME", "ASSISTANT_NAME", "AVA_START_MODE"],
    ),
    (
        "Connection",
        ["AVA_SERVER_URL"],
    ),
    (
        "API keys",
        ["GROQ_API_KEY", "GOOGLE_AI_API_KEY", "WEATHER_API_KEY", "TAVILY_API_KEY", "pat"],
    ),
    (
        "Devices",
        ["lg_device_id", "country_code", "main_ac_alias"],
    ),
    (
        "Appearance",
        ["THEME"],
    ),
]

# Fixed choices for a few keys (no broken Select popup)
SETTING_CHOICES: dict[str, list[str]] = {
    "AVA_START_MODE": [
        "tui",
        "tui_text",
        "tui_continuous",
        "tui_wakeword",
        "rich",
        "rich_text",
        "rich_continuous",
        "rich_wakeword",
        "continuous",
        "wakeword",
        "text",
    ],
}


class ThemePickerModal(ModalScreen[str | None]):
    """Centered theme list with live preview — does not touch main layout."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("q", "cancel", "Cancel", show=False),
    ]

    CSS = """
    ThemePickerModal {
        align: center middle;
        background: $background 40%;
    }
    #theme-dialog {
        width: 70;
        max-width: 95%;
        height: auto;
        max-height: 90%;
        background: $panel;
        border: solid $primary;
        padding: 1 2;
        layout: vertical;
    }
    #theme-dialog .dialog-title {
        width: 100%;
        height: 1;
        color: $primary;
        text-style: bold;
        margin: 0;
    }
    #theme-dialog .dialog-hint {
        width: 100%;
        height: 1;
        color: $secondary;
        text-style: none;
        margin: 0 0 1 0;
    }
    #theme-dialog #theme-preview {
        width: 100%;
        height: 2;
        padding: 0 1;
        margin: 0 0 1 0;
        background: $surface;
        border: solid $secondary;
        color: $foreground;
    }
    #theme-dialog #theme-list {
        width: 100%;
        height: 16;
        min-height: 10;
        max-height: 20;
        border: solid $secondary;
        background: $surface;
        padding: 0 0;
    }
    #theme-dialog #theme-list:focus {
        border: solid $primary;
    }
    #theme-dialog .dialog-footer {
        width: 100%;
        height: 1;
        margin: 1 0 0 0;
        color: $secondary;
        text-style: none;
    }
    #theme-dialog #theme-actions {
        width: 100%;
        height: 1;
        layout: horizontal;
        align-horizontal: right;
        margin: 1 0 0 0;
    }
    #theme-dialog #theme-actions Button {
        width: auto;
        min-width: 10;
        height: 1;
        min-height: 1;
        max-height: 1;
        margin: 0 0 0 1;
        padding: 0 2;
        border: none;
        background: $panel;
        color: $foreground;
    }
    #theme-dialog #theme-actions Button.-primary {
        background: $primary;
        color: $background;
        text-style: bold;
        border: none;
    }
    #theme-dialog #theme-actions Button:hover {
        background: $boost;
        color: $background;
        border: none;
    }
    """

    def __init__(self, current_theme: str = "ava", **kwargs):
        super().__init__(**kwargs)
        self._original = _resolve_theme_name(current_theme)
        self._preview = self._original

    def compose(self) -> ComposeResult:
        with Vertical(id="theme-dialog"):
            yield Static(
                f"themes  ({len(AVAILABLE_THEMES)})  ·  A–Z",
                classes="dialog-title",
            )
            yield Static(
                "Up/Down to browse  -  Enter to apply",
                classes="dialog-hint",
            )
            yield Static(self._preview_text(self._original), id="theme-preview")
            options: list[Option] = []
            highlighted = 0
            # AVAILABLE_THEMES is already sorted by name
            for i, name in enumerate(AVAILABLE_THEMES):
                desc = _theme_description(name)
                if len(desc) > 36:
                    desc = desc[:33] + "..."
                mark = "●" if name == self._original else "○"
                options.append(
                    Option(f"{mark}  {name}   {desc}", id=name)
                )
                if name == self._original:
                    highlighted = i
            yield OptionList(*options, id="theme-list")
            yield Static(
                "esc cancels and restores your previous theme",
                classes="dialog-footer",
            )
            with Horizontal(id="theme-actions"):
                yield Button("apply", id="btn-theme-apply", variant="primary", flat=True)
                yield Button("cancel", id="btn-theme-cancel", flat=True)
        # Store highlight index for on_mount
        self._initial_index = highlighted

    def on_mount(self) -> None:
        try:
            ol = self.query_one("#theme-list", OptionList)
            ol.highlighted = self._initial_index
            self.set_focus(ol)
        except NoMatches:
            pass

    def _preview_text(self, name: str) -> str:
        desc = _theme_description(name)
        return f"[bold]{name}[/]  ·  {desc}\nprimary / accent / surface update live below"

    def _apply_preview(self, name: str) -> None:
        if name not in AVAILABLE_THEMES:
            return
        self._preview = name
        try:
            self.app.theme = name
        except Exception:
            pass
        try:
            self.query_one("#theme-preview", Static).update(self._preview_text(name))
        except NoMatches:
            pass

    def _selected_name(self) -> str | None:
        try:
            ol = self.query_one("#theme-list", OptionList)
            if ol.highlighted is None:
                return self._preview
            opt = ol.get_option_at_index(ol.highlighted)
            return str(opt.id) if opt and opt.id is not None else self._preview
        except Exception:
            return self._preview

    @on(OptionList.OptionHighlighted, "#theme-list")
    def on_theme_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_id:
            self._apply_preview(str(event.option_id))

    @on(OptionList.OptionSelected, "#theme-list")
    def on_theme_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            self.dismiss(str(event.option_id))

    @on(Button.Pressed, "#btn-theme-apply")
    def on_apply_pressed(self) -> None:
        name = self._selected_name() or self._preview
        self.dismiss(name if name in AVAILABLE_THEMES else self._original)

    @on(Button.Pressed, "#btn-theme-cancel")
    def on_cancel_pressed(self) -> None:
        self.action_cancel()

    def action_cancel(self) -> None:
        # Always restore the theme the picker was opened with
        try:
            self.app.theme = self._original
            if hasattr(self.app, "app_theme"):
                # Don't rewrite persisted app_theme — only visual restore
                pass
        except Exception:
            pass
        self.dismiss(None)


class SettingsModal(ModalScreen[dict | None]):
    """Modal settings form — layers over the TUI without dismantling layout."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    CSS = """
    SettingsModal {
        align: center middle;
        background: $background 40%;
    }
    #settings-dialog {
        width: 86;
        max-width: 100%;
        height: 32;
        max-height: 90%;
        background: $panel;
        border: solid $primary;
        padding: 1 2;
        layout: vertical;
    }
    #settings-dialog .dialog-title {
        width: 100%;
        height: 1;
        color: $primary;
        text-style: bold;
        margin: 0 0 0 0;
    }
    #settings-dialog .dialog-hint {
        width: 100%;
        height: 1;
        color: $secondary;
        text-style: none;
        margin: 0 0 1 0;
    }
    #settings-body {
        width: 100%;
        height: 1fr;
        layout: horizontal;
    }
    #settings-nav {
        width: 16;
        min-width: 14;
        height: 1fr;
        border: solid $secondary;
        background: $surface;
        padding: 0 0;
    }
    #settings-nav:focus {
        border: solid $primary;
    }
    #settings-form-wrap {
        width: 1fr;
        height: 1fr;
        border: solid $secondary;
        background: $surface;
        margin: 0 0 0 1;
        padding: 0 1;
    }
    #settings-form {
        height: 1fr;
        padding: 0 0;
    }
    #settings-form .section-title {
        color: $secondary;
        text-style: bold;
        height: 1;
        margin: 0 0 1 0;
    }
    #settings-form .setting-block {
        width: 100%;
        height: auto;
        margin: 0 0 1 0;
        padding: 0 0;
        layout: vertical;
    }
    #settings-form .setting-key {
        color: $primary;
        text-style: bold;
        height: 1;
        margin: 0;
        padding: 0;
    }
    #settings-form .setting-desc {
        color: $secondary;
        text-style: none;
        height: 1;
        margin: 0;
        padding: 0;
    }
    #settings-form .setting-input {
        width: 100%;
        height: 3;
        min-height: 3;
        border: tall $secondary;
        background: $panel;
        color: $foreground;
        margin: 0;
        padding: 0 1;
    }
    #settings-form .setting-input:focus {
        border: tall $primary;
    }
    #settings-form .theme-row {
        height: 1;
        layout: horizontal;
        align-vertical: middle;
    }
    #settings-form .theme-row Button {
        width: auto;
        min-width: 16;
        height: 1;
        min-height: 1;
        margin: 0 1 0 0;
        padding: 0 1;
        border: none;
        background: $primary;
        color: $background;
        text-style: bold;
    }
    #settings-form .theme-row Button:hover {
        background: $boost;
        border: none;
    }
    #settings-form .theme-current {
        color: $secondary;
        content-align: left middle;
        height: 1;
        width: 1fr;
    }
    #settings-form .choice-row {
        height: 1;
        layout: horizontal;
        align-vertical: middle;
    }
    #settings-form .choice-btn {
        width: auto;
        min-width: 10;
        height: 1;
        min-height: 1;
        margin: 0 1 0 0;
        padding: 0 1;
        border: none;
        background: $panel;
        color: $secondary;
    }
    #settings-form .choice-btn.-primary {
        background: $primary;
        color: $background;
        text-style: bold;
        border: none;
    }
    #settings-form .choice-btn:hover {
        color: $foreground;
        border: none;
    }
    #settings-actions {
        height: 1;
        layout: horizontal;
        align-horizontal: right;
        align-vertical: middle;
        margin: 1 0 0 0;
        padding: 0;
    }
    #settings-actions Button {
        width: auto;
        min-width: 10;
        height: 1;
        min-height: 1;
        margin: 0 0 0 1;
        padding: 0 2;
        border: none;
        background: $panel;
        color: $foreground;
        text-style: none;
        content-align: center middle;
    }
    #settings-actions Button.-primary {
        background: $primary;
        color: $background;
        text-style: bold;
        border: none;
    }
    #settings-actions Button:hover {
        background: $boost;
        color: $background;
        border: none;
    }
    #settings-actions Button:focus {
        border: none;
    }
    #settings-status {
        height: 1;
        color: $accent;
        text-style: italic;
        margin: 0;
        padding: 0;
    }
    """

    def __init__(self, config: dict, current_theme: str = "ava", **kwargs):
        super().__init__(**kwargs)
        self._config = dict(config)
        self._theme = _resolve_theme_name(
            str(config.get("THEME") or current_theme or "ava")
        )
        self._inputs: dict[str, Input] = {}
        self._choice_values: dict[str, str] = {}
        self._section = SETTING_SECTIONS[0][0]
        # Keys not listed in sections still appear under "Other"
        known = {k for _, keys in SETTING_SECTIONS for k in keys}
        extra = [k for k in self._config.keys() if k not in known]
        self._sections: list[tuple[str, list[str]]] = list(SETTING_SECTIONS)
        if extra:
            self._sections.append(("Other", extra))

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-dialog"):
            yield Static("settings", classes="dialog-title")
            yield Static(
                "Esc cancel  -  Ctrl+S save  -  theme button opens list",
                classes="dialog-hint",
            )
            with Horizontal(id="settings-body"):
                nav_opts = [
                    Option(f"  {name}", id=name) for name, _ in self._sections
                ]
                yield OptionList(*nav_opts, id="settings-nav")
                with Vertical(id="settings-form-wrap"):
                    yield VerticalScroll(id="settings-form")
            yield Static("", id="settings-status")
            with Horizontal(id="settings-actions"):
                yield Button("save", id="btn-save", variant="primary", flat=True)
                yield Button("cancel", id="btn-cancel", flat=True)

    def on_mount(self) -> None:
        self._render_section(self._section)
        try:
            self.set_focus(self.query_one("#settings-nav", OptionList))
        except NoMatches:
            pass

    def _set_status(self, text: str) -> None:
        try:
            self.query_one("#settings-status", Static).update(text)
        except NoMatches:
            pass

    def _render_section(self, section: str) -> None:
        self._section = section
        # Persist current field edits before rebuilding the form
        self._harvest_inputs()
        # Drop dead Input refs; values already live in _config / _choice_values
        self._inputs.clear()
        keys: list[str] = []
        for name, section_keys in self._sections:
            if name == section:
                keys = section_keys
                break
        try:
            form = self.query_one("#settings-form", VerticalScroll)
        except NoMatches:
            return
        form.remove_children()
        form.mount(Static(f"[bold]{section}[/]", classes="section-title"))

        for key in keys:
            val = self._config.get(key, "")
            desc = CONFIG_DESCRIPTIONS.get(key, "")
            block = Vertical(classes="setting-block")
            form.mount(block)
            block.mount(Static(key, classes="setting-key"))
            if desc:
                block.mount(Static(desc, classes="setting-desc"))

            if key == "THEME":
                row = Horizontal(classes="theme-row")
                block.mount(row)
                row.mount(
                    Button(
                        "choose theme…",
                        id="btn-open-theme",
                        variant="primary",
                        flat=True,
                    )
                )
                row.mount(
                    Static(
                        f"current: [bold]{self._theme}[/]",
                        classes="theme-current",
                        id="theme-current-label",
                    )
                )
            elif key in SETTING_CHOICES:
                choices = SETTING_CHOICES[key]
                current = str(val or choices[0])
                self._choice_values[key] = current
                row = Horizontal(classes="choice-row")
                block.mount(row)
                for choice in choices:
                    btn = Button(
                        choice,
                        id=f"choice-{key}-{choice}",
                        classes="choice-btn",
                        flat=True,
                        variant="primary" if choice == current else "default",
                    )
                    row.mount(btn)
            else:
                is_secret = key in CONFIG_SECRET_KEYS
                if is_secret:
                    inp = Input(
                        value="",
                        placeholder="••••••••  (leave blank to keep)" if val else "not set",
                        password=True,
                        classes="setting-input",
                        id=f"cfg-{key}",
                    )
                else:
                    inp = Input(
                        value=str(val) if val is not None else "",
                        placeholder="not set",
                        classes="setting-input",
                        id=f"cfg-{key}",
                    )
                self._inputs[key] = inp
                block.mount(inp)

    def _harvest_inputs(self) -> None:
        """Copy visible Input values into the working config dict."""
        for key, inp in list(self._inputs.items()):
            try:
                new_val = inp.value.strip()
            except Exception:
                continue
            if key in CONFIG_SECRET_KEYS and not new_val:
                continue  # keep existing secret
            self._config[key] = new_val
        for key, val in self._choice_values.items():
            self._config[key] = val
        self._config["THEME"] = self._theme

    def collect_values(self) -> dict:
        self._harvest_inputs()
        # Drop empty non-secret keys only if they were empty before? Keep as-is.
        return dict(self._config)

    @on(OptionList.OptionSelected, "#settings-nav")
    def on_nav_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            self._render_section(str(event.option_id))

    @on(OptionList.OptionHighlighted, "#settings-nav")
    def on_nav_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        # Switch section as user arrows through the nav list
        if event.option_id and str(event.option_id) != self._section:
            self._render_section(str(event.option_id))

    @on(Button.Pressed, "#btn-open-theme")
    def on_open_theme(self) -> None:
        self._harvest_inputs()

        def _after_theme(result: str | None) -> None:
            if result and result in AVAILABLE_THEMES:
                self._theme = result
                self._config["THEME"] = result
                try:
                    self.app.theme = result
                except Exception:
                    pass
                self._set_status(f"theme → {result}  (save to persist)")
                # Refresh appearance section label if visible
                if self._section == "Appearance":
                    self._render_section("Appearance")
            else:
                # Picker restores original on cancel; re-sync to working theme
                try:
                    self.app.theme = self._theme
                except Exception:
                    pass

        self.app.push_screen(ThemePickerModal(current_theme=self._theme), _after_theme)

    @on(Button.Pressed)
    def on_any_button(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid.startswith("choice-"):
            # choice-{key}-{value}  — value may contain hyphens
            rest = bid[len("choice-") :]
            # Match against known choice keys
            for key, choices in SETTING_CHOICES.items():
                prefix = f"{key}-"
                if rest.startswith(prefix):
                    value = rest[len(prefix) :]
                    if value in choices:
                        self._choice_values[key] = value
                        self._config[key] = value
                        # Update button variants in this row
                        for choice in choices:
                            try:
                                btn = self.query_one(
                                    f"#choice-{key}-{choice}", Button
                                )
                                btn.variant = (
                                    "primary" if choice == value else "default"
                                )
                            except NoMatches:
                                pass
                        self._set_status(f"{key} → {value}")
                    break

    @on(Button.Pressed, "#btn-save")
    def on_save_pressed(self) -> None:
        self.action_save()

    @on(Button.Pressed, "#btn-cancel")
    def on_cancel_pressed(self) -> None:
        self.action_cancel()

    def action_save(self) -> None:
        self.dismiss(self.collect_values())

    def action_cancel(self) -> None:
        self.dismiss(None)


class HelpOverlay(Static):
    """Keyboard shortcuts overlay."""

    DEFAULT_CSS = """
    HelpOverlay {
        width: 100%;
        height: 100%;
        background: $panel;
        opacity: 0.95;
        padding: 2 4;
    }
    HelpOverlay .help-title {
        color: $primary;
        text-style: bold;
        margin-bottom: 1;
    }
    HelpOverlay .help-section {
        color: $secondary;
        text-style: bold;
        margin-top: 1;
    }
    HelpOverlay .help-row {
        color: $foreground;
        width: 100%;
    }
    HelpOverlay .help-key {
        color: $primary;
        text-style: bold;
        width: 18;
    }
    HelpOverlay .help-desc {
        color: $foreground;
    }
    HelpOverlay .commands-title {
        color: $accent;
        text-style: bold;
        margin-top: 1;
    }
    HelpOverlay .help-cmd {
        color: $foreground;
    }
    HelpOverlay #help-close {
        width: 100%;
        align-horizontal: right;
        margin-top: 2;
    }
    """

    BINDINGS = [
        Binding("escape", "close_help", "Close", show=False),
        Binding("q", "close_help", "Close", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Static("[bold]keyboard shortcuts[/]", classes="help-title")
        yield Static("General:", classes="help-section")
        yield Static("[bold]?[/]  Show this help", classes="help-row")
        yield Static("[bold]ctrl+q[/]  Quit", classes="help-row")
        yield Static("[bold]ctrl+n[/]  New conversation", classes="help-row")
        yield Static("[bold]ctrl+i[/]  Toggle settings", classes="help-row")
        yield Static("[bold]ctrl+f[/]  Search in chat", classes="help-row")
        yield Static("[bold]ctrl+d[/]  Full task dashboard", classes="help-row")
        yield Static("[bold]ctrl+r[/]  Remote mobile QR access", classes="help-row")
        yield Static("[bold]ctrl+b[/]  Toggle tasks sidebar", classes="help-row")
        yield Static("[bold]ctrl+h[/]  Toggle history sidebar", classes="help-row")
        yield Static("[bold]ctrl+t[/]  Theme list", classes="help-row")
        yield Static("[bold]ctrl+m[/]  Cycle mode", classes="help-row")
        yield Static("[bold]escape[/]  Focus input / close overlay", classes="help-row")
        yield Static(":", classes="help-section")
        yield Static("[bold]/help[/]  Show commands", classes="help-row")
        yield Static("[bold]/remote[/]  Show QR code & URL for mobile access", classes="help-row")
        yield Static("[bold]/theme[/]  Open theme picker", classes="help-row")
        yield Static("[bold]/theme name[/]  Set theme by name", classes="help-row")
        yield Static("[bold]/themes[/]  Open theme picker", classes="help-row")
        yield Static(
            "[bold]/mode text|continuous|wakeword[/]  Switch mode", classes="help-row"
        )
        yield Static("[bold]/clear[/]  Clear chat", classes="help-row")
        yield Static("[bold]/tasks[/]  Full task dashboard", classes="help-row")
        yield Static("[bold]/task <id>[/]  Show task result", classes="help-row")
        yield Static("[bold]/history[/]  Toggle history sidebar", classes="help-row")
        yield Static(
            "[bold]/quiet[/]  Hide/show system log (theme tips, etc.)",
            classes="help-row",
        )
        yield Static("[bold]/settings[/]  Open settings", classes="help-row")
        yield Static("[bold]/new[/]  New conversation", classes="help-row")
        yield Static("[bold]/quit[/]  Exit", classes="help-row")
        yield Static("Press [bold]?[/] or [bold]escape[/] to close", classes="help-row")

    def action_close_help(self) -> None:
        self.remove()


def _all_slash_commands() -> list[SlashCommand]:
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
    # Once the user is typing arguments (any space), hide the palette
    if " " in raw:
        return []
    q = raw.strip().lower()
    if not q.startswith("/"):
        return []
    catalog = _all_slash_commands()
    # Bare "/" shows everything
    if q == "/":
        return list(catalog)

    scored: list[tuple[int, SlashCommand]] = []
    seen: set[tuple[str, str, bool]] = set()
    for cmd in catalog:
        key = (cmd.command, cmd.description, cmd.needs_args)
        if key in seen:
            continue
        names = [n.lower() for n in cmd.all_names()]
        # Prefer prefix matches on command name, then substring, then description
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


class SlashCommandPalette(Static):
    """Floating /command autocomplete above the input bar."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._matches: list[SlashCommand] = []
        self._index = 0
        self._open = False

    def compose(self) -> ComposeResult:
        yield Static(" / commands", classes="slash-title")
        yield Static(
            "Up/Down move  -  Tab/Enter select  -  Esc close",
            classes="slash-hint",
        )
        yield OptionList(id="slash-list")
        yield Static("", id="slash-empty", classes="slash-empty")

    def is_open(self) -> bool:
        return self._open

    def show_for(self, query: str) -> None:
        matches = filter_slash_commands(query)
        if not matches:
            self.hide()
            return
        # Keep highlight if the same entry still exists; else snap to top
        prev = self.selected() if self._open else None
        self._matches = matches
        self._index = 0
        if prev is not None:
            for i, m in enumerate(matches):
                if (
                    m.command == prev.command
                    and m.description == prev.description
                    and m.needs_args == prev.needs_args
                ):
                    self._index = i
                    break
        self._open = True
        self.add_class("visible")
        self.display = True
        self._render_list()

    def hide(self) -> None:
        self._open = False
        self._matches = []
        self._index = 0
        self.remove_class("visible")
        self.display = False

    def move(self, delta: int) -> None:
        if not self._matches:
            return
        self._index = (self._index + delta) % len(self._matches)
        self._render_list()

    def selected(self) -> SlashCommand | None:
        if not self._matches:
            return None
        if 0 <= self._index < len(self._matches):
            return self._matches[self._index]
        return self._matches[0]

    def _render_list(self) -> None:
        try:
            ol = self.query_one("#slash-list", OptionList)
            empty = self.query_one("#slash-empty", Static)
        except NoMatches:
            return
        ol.clear_options()
        if not self._matches:
            empty.update("no matching commands")
            empty.display = True
            return
        empty.update("")
        empty.display = False
        for i, cmd in enumerate(self._matches):
            mark = "›" if i == self._index else " "
            label = (
                f"{mark} [bold]{cmd.display_cmd()}[/]  "
                f"[dim]{cmd.description}[/]"
            )
            ol.add_option(Option(label, id=f"slash-{i}"))
        try:
            ol.highlighted = self._index
        except Exception:
            pass

    @on(OptionList.OptionSelected, "#slash-list")
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        # Click to pick
        if event.option_id and str(event.option_id).startswith("slash-"):
            try:
                idx = int(str(event.option_id).split("-", 1)[1])
                self._index = idx
            except ValueError:
                pass
        self.post_message(self.Accepted(self.selected()))

    @on(OptionList.OptionHighlighted, "#slash-list")
    def on_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_index is not None:
            self._index = event.option_index

    class Accepted(Message):
        def __init__(self, command: SlashCommand | None) -> None:
            super().__init__()
            self.command = command


class ChatInput(Input):
    """Chat input that routes palette navigation keys while /commands are open."""

    BINDINGS = [
        Binding("up", "slash_up", "Slash up", show=False, priority=True),
        Binding("down", "slash_down", "Slash down", show=False, priority=True),
        Binding("tab", "slash_tab", "Slash tab", show=False, priority=True),
        Binding("escape", "slash_escape", "Slash esc", show=False, priority=True),
    ]

    def _palette(self) -> "SlashCommandPalette | None":
        try:
            return self.app.query_one("#slash-palette", SlashCommandPalette)
        except Exception:
            return None

    def action_slash_up(self) -> None:
        pal = self._palette()
        if pal and pal.is_open():
            pal.move(-1)
        # else: single-line input — ignore

    def action_slash_down(self) -> None:
        pal = self._palette()
        if pal and pal.is_open():
            pal.move(1)

    def action_slash_tab(self) -> None:
        pal = self._palette()
        if pal and pal.is_open() and hasattr(self.app, "_accept_slash_command"):
            self.app._accept_slash_command(pal.selected())  # type: ignore[attr-defined]

    def action_slash_escape(self) -> None:
        pal = self._palette()
        if pal and pal.is_open():
            pal.hide()
            return
        # Fall through to app-level focus/close behavior
        if hasattr(self.app, "action_focus_input"):
            self.app.action_focus_input()  # type: ignore[attr-defined]

    def on_paste(self, event: events.Paste) -> None:
        """If clipboard contains a file or image/screenshot, upload it to the server and paste its server path."""
        try:
            from utils.clipboard_image import upload_clipboard_files_to_server

            records = upload_clipboard_files_to_server()
            if records:
                event.stop()
                event.prevent_default()
                parts = []
                for rec in records:
                    sp = rec.get("server_path") or rec.get("filename")
                    parts.append(f"[Attached: {sp}]")
                inserted = " ".join(parts) + " "
                self.insert_text_at_cursor(inserted)
                try:
                    if hasattr(self.app, "notify"):
                        self.app.notify(
                            f"Attached {len(records)} file(s) to server",
                            title="File Uploaded",
                            severity="information",
                        )
                except Exception:
                    pass
                return
        except Exception:
            pass



class SearchOverlay(Static):
    """Search in chat overlay."""

    DEFAULT_CSS = """
    SearchOverlay {
        width: 100%;
        height: auto;
        background: $surface;
        padding: 1 2;
        border-bottom: solid $secondary;
    }
    SearchOverlay #search-label {
        color: $secondary;
        width: 10;
        height: 1;
    }
    SearchOverlay #search-input {
        width: 30;
        height: 1;
        border: round $secondary;
    }
    SearchOverlay #search-input:focus {
        border: round $primary;
    }
    SearchOverlay #search-count {
        color: $foreground;
        width: 20;
        height: 1;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._search_term = ""
        self._matches: list = []
        self._current_idx = 0

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static("search:", id="search-label")
            yield Input(placeholder="type to search...", id="search-input")
            yield Static("", id="search-count")

    @on(Input.Changed, "#search-input")
    def on_search_changed(self, event: Input.Changed) -> None:
        self._search_term = event.value.strip().lower()
        self._current_idx = 0
        if not self._search_term:
            self.query_one("#search-count", Static).update("")
            return
        self._find_matches()
        count = len(self._matches)
        self.query_one("#search-count", Static).update(
            f"{self._current_idx + 1}/{count}" if count > 0 else "no matches"
        )

    @on(Input.Submitted, "#search-input")
    def on_search_submit(self, event: Input.Submitted) -> None:
        if not self._matches:
            return
        if event.key == "enter":
            self._current_idx = (self._current_idx + 1) % len(self._matches)
            self._scroll_to_match()

    def _find_matches(self) -> None:
        self._matches = []
        try:
            chat_log = self.query_one("#chat-log", Vertical)
            for msg in chat_log.children:
                if isinstance(msg, ChatMessage):
                    if self._search_term in str(msg.renderable).lower():
                        self._matches.append(msg)
        except NoMatches:
            pass

    def _scroll_to_match(self) -> None:
        if self._matches and 0 <= self._current_idx < len(self._matches):
            try:
                scroll = self.query_one("#chat-scroll", VerticalScroll)
                match = self._matches[self._current_idx]
                scroll.scroll_to_widget(match, animate=True)
            except NoMatches:
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# Sidebars + task dashboard
# ═══════════════════════════════════════════════════════════════════════════════


def _format_countdown(seconds: float) -> str:
    """Format remaining seconds as mm:ss or hh:mm:ss."""
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def _timer_remaining(task: dict) -> float | None:
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


def _status_glyph(status: str, spin_idx: int = 0) -> str:
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


class HistoryEntry(Static):
    """One roomy chat row — name only, click to open."""

    DEFAULT_CSS = """
    HistoryEntry {
        height: 2;
        min-height: 2;
        max-height: 2;
        width: 100%;
        padding: 0 1;
        margin: 0 0 1 0;
        color: $foreground;
        background: transparent;
        content-align: left middle;
        overflow-x: hidden;
    }
    HistoryEntry:hover {
        background: $panel;
        color: $primary;
    }
    HistoryEntry.-active {
        background: $panel;
        color: $primary;
        text-style: bold;
    }
    """

    def __init__(self, conversation_id: str, name: str, **kwargs):
        super().__init__(name, markup=False, **kwargs)
        self.conversation_id = conversation_id
        self.chat_name = name

    def on_click(self) -> None:
        self.post_message(
            HistorySidebar.ConversationSelected(self.conversation_id, self.chat_name)
        )


class HistorySidebar(Static):
    """Left sidebar: past conversations as spaced name rows → read-only modal."""

    DEFAULT_CSS = """
    HistorySidebar {
        width: 18;
        min-width: 14;
        height: 1fr;
        background: $background;
        border-right: solid $panel;
        layout: vertical;
        padding: 1 0 0 0;
        overflow-x: hidden;
    }
    HistorySidebar .sidebar-header {
        height: 1;
        width: 100%;
        padding: 0;
        margin: 0 0 1 0;
        layout: horizontal;
        align-vertical: middle;
    }
    HistorySidebar .sidebar-title {
        height: 1;
        width: 1fr;
        padding: 0 1;
        color: $secondary;
        text-style: bold;
        background: transparent;
        content-align: left middle;
    }
    HistorySidebar .sidebar-close {
        height: 1;
        width: 3;
        min-width: 3;
        padding: 0;
        margin: 0;
        background: transparent;
        border: none;
        color: $secondary;
        content-align: center middle;
    }
    HistorySidebar .sidebar-close:hover {
        color: $error;
        background: $panel;
    }
    HistorySidebar #history-list {
        height: 1fr;
        background: $background;
        padding: 0 0 1 0;
        scrollbar-size: 1 1;
        scrollbar-background: $background;
        scrollbar-color: $secondary 25%;
        overflow-x: hidden;
    }
    HistorySidebar .history-empty {
        height: 2;
        padding: 0 1;
        color: $secondary;
        content-align: left middle;
    }
    """

    class ConversationSelected(Message):
        def __init__(self, conversation_id: str, name: str) -> None:
            super().__init__()
            self.conversation_id = conversation_id
            self.name = name

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._refresh_timer = None
        self._fingerprint: tuple = ()
        self._names: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(classes="sidebar-header"):
            yield Static("Chats", classes="sidebar-title")
            yield Button("×", id="btn-close-history", classes="sidebar-close", flat=True)
        yield VerticalScroll(id="history-list")

    def on_mount(self) -> None:
        self.refresh_list()
        self._trigger_background_sync()
        self._refresh_timer = self.set_interval(30.0, self.refresh_list)

    def on_unmount(self) -> None:
        if self._refresh_timer is not None:
            try:
                self._refresh_timer.stop()
            except Exception:
                pass

    def _trigger_background_sync(self) -> None:
        def _bg():
            try:
                from knowledge.ConversationManager import get_manager
                mgr = get_manager()
                count = mgr.sync_with_server()
                if count > 0:
                    self.app.call_from_thread(self.refresh_list)
            except Exception:
                pass
        threading.Thread(target=_bg, daemon=True).start()

    def _load_recent(self) -> list[dict]:
        try:
            from knowledge.ConversationManager import get_manager

            mgr = get_manager()
            # In-memory fast fetch — never block UI with synchronous HTTP or disk scans
            recent = mgr.get_recent_conversations(limit=40)
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
                        key=lambda x: x[1].get(
                            "updated_at", x[1].get("created_at", "")
                        ),
                        reverse=True,
                    )[:40]
                ]
            return recent
        except Exception as e:
            return []

    def refresh_list(self) -> None:
        recent = self._load_recent()
        fingerprint = tuple(
            (c.get("id"), (c.get("name") or "").strip()) for c in recent
        )
        if fingerprint == self._fingerprint:
            return
        self._fingerprint = fingerprint
        self._names = {
            str(c.get("id")): (c.get("name") or "Unnamed")
            for c in recent
            if c.get("id")
        }

        try:
            scroller = self.query_one("#history-list", VerticalScroll)
        except NoMatches:
            return
        scroller.remove_children()
        if not recent:
            scroller.mount(Static("No chats yet", classes="history-empty"))
            return

        for conv in recent:
            cid = str(conv.get("id") or "")
            if not cid:
                continue
            name = (conv.get("name") or "Unnamed").strip().replace("\n", " ")
            if len(name) > 26:
                name = name[:25] + "…"
            scroller.mount(HistoryEntry(cid, name))

    def action_open_highlighted(self) -> None:
        # Click-driven list; keep for binding compatibility
        pass


class TaskLiveEntry(Static):
    """One live task row in the tasks sidebar / dashboard — click to watch live process."""

    DEFAULT_CSS = """
    TaskLiveEntry {
        width: 100%;
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
        background: transparent;
        border-left: solid $accent;
        color: $foreground;
        overflow-x: hidden;
    }
    TaskLiveEntry:hover {
        background: $panel;
        color: $primary;
    }
    TaskLiveEntry.running {
        border-left: solid $warning;
    }
    TaskLiveEntry.pending {
        border-left: solid $warning;
    }
    TaskLiveEntry.timer {
        border-left: solid $primary;
    }
    TaskLiveEntry.needs_input {
        border-left: solid $error;
    }
    TaskLiveEntry.completed {
        border-left: solid $success;
    }
    TaskLiveEntry.failed {
        border-left: solid $error;
    }
    """

    def __init__(self, task_id: str, formatted_content: str, classes: str = "", **kwargs):
        super().__init__(formatted_content, classes=classes, **kwargs)
        self.task_id = task_id

    def on_click(self) -> None:
        self.post_message(TasksSidebar.TaskSelected(self.task_id))


class TasksSidebar(Static):
    """Right sidebar: truly live background tasks + timer countdowns."""

    class TaskSelected(Message):
        def __init__(self, task_id: str) -> None:
            super().__init__()
            self.task_id = task_id

    DEFAULT_CSS = """
    TasksSidebar {
        width: 26;
        min-width: 20;
        height: 1fr;
        background: $background;
        border-left: solid $panel;
        layout: vertical;
        padding: 0;
        overflow-x: hidden;
    }
    TasksSidebar .sidebar-header {
        height: 1;
        width: 100%;
        padding: 0;
        margin: 0;
        layout: horizontal;
        align-vertical: middle;
    }
    TasksSidebar .sidebar-title {
        height: 1;
        width: 1fr;
        padding: 0 1;
        color: $secondary;
        text-style: bold;
        background: $background;
        content-align: left middle;
    }
    TasksSidebar .sidebar-close {
        height: 1;
        width: 3;
        min-width: 3;
        padding: 0;
        margin: 0;
        background: transparent;
        border: none;
        color: $secondary;
        content-align: center middle;
    }
    TasksSidebar .sidebar-close:hover {
        color: $error;
        background: $panel;
    }
    TasksSidebar #tasks-live-list {
        height: 1fr;
        padding: 0;
        margin: 0;
        background: $background;
        scrollbar-size: 1 1;
        scrollbar-background: $background;
        scrollbar-color: $secondary 30%;
        overflow-x: hidden;
    }
    TasksSidebar .task-live {
        width: 100%;
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
        background: transparent;
        border-left: solid $accent;
        color: $foreground;
        overflow-x: hidden;
    }
    TasksSidebar .task-live.running {
        border-left: solid $warning;
    }
    TasksSidebar .task-live.pending {
        border-left: solid $warning;
    }
    TasksSidebar .task-live.timer {
        border-left: solid $primary;
    }
    TasksSidebar .task-live.needs_input {
        border-left: solid $error;
    }
    TasksSidebar .task-live.completed {
        border-left: solid $success;
    }
    TasksSidebar .task-live.failed {
        border-left: solid $error;
    }
    TasksSidebar .task-empty {
        height: 3;
        padding: 0 1;
        color: $secondary;
        text-style: none;
        content-align: left middle;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._refresh_timer = None
        self._spin_idx = 0
        self._last_render_key: tuple = ()

    def compose(self) -> ComposeResult:
        with Horizontal(classes="sidebar-header"):
            yield Static("TASKS", classes="sidebar-title")
            yield Button("×", id="btn-close-tasks", classes="sidebar-close", flat=True)
        yield VerticalScroll(id="tasks-live-list")

    def on_mount(self) -> None:
        self.refresh_list()
        # 1 Hz is smooth for live countdowns and prevents input lag / DOM thrashing
        self._refresh_timer = self.set_interval(1.0, self.refresh_list)

    def on_unmount(self) -> None:
        if self._refresh_timer is not None:
            try:
                self._refresh_timer.stop()
            except Exception:
                pass

    def _is_live_task(self, task: dict) -> bool:
        """Only real in-process work — not stale pending ghosts from disk."""
        status = task.get("status", "")
        if status in ("running", "needs_input"):
            return True
        # Fresh pending only (just spawned, thread not flipped to running yet)
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

    def refresh_list(self) -> None:
        self._spin_idx = (self._spin_idx + 1) % len(_BRAILLE_SPINNER)
        try:
            from core.TaskManager import list_all_tasks

            tasks = list_all_tasks()
        except Exception:
            tasks = []

        active = [t for t in tasks if self._is_live_task(t)]
        # Recent completions (last 2 minutes only — less noise)
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
        display = active + recent_done[:4]

        try:
            scroller = self.query_one("#tasks-live-list", VerticalScroll)
        except NoMatches:
            return

        if not display:
            self._last_render_key = ()
            if not any(not isinstance(w, TaskLiveEntry) for w in scroller.children):
                scroller.remove_children()
                scroller.mount(Static("No active tasks", classes="task-empty"))
            return

        # Check if same task IDs are already mounted — update in place to avoid lag
        existing_entries = [w for w in scroller.children if isinstance(w, TaskLiveEntry)]
        existing_ids = [w.task_id for w in existing_entries]
        display_ids = [str(t.get("id") or "") for t in display]

        if existing_ids == display_ids:
            for w, task in zip(existing_entries, display):
                w.update(self._format_row(task))
                w.set_classes(self._row_classes(task))
            return

        # Task list membership changed — remount entries
        scroller.remove_children()
        for task in display:
            tid = str(task.get("id") or "")
            scroller.mount(
                TaskLiveEntry(
                    tid,
                    self._format_row(task),
                    classes=self._row_classes(task),
                )
            )

    def _row_classes(self, task: dict) -> str:
        status = task.get("status", "")
        classes = f"task-live {status}"
        if task.get("task_type") == "timer" and status in ("running", "pending"):
            classes += " timer"
        return classes

    def _format_row(self, task: dict) -> str:
        status = task.get("status", "")
        glyph = _status_glyph(status, self._spin_idx)
        raw_name = str(task.get("name") or "task").strip()
        if len(raw_name) > 24:
            raw_name = raw_name[:23] + "..."
        name = escape(raw_name)
        remaining = _timer_remaining(task)
        ttype = str(task.get("task_type") or "general")
        # Two lines max for clean density
        lines = [f"{glyph} {name}"]
        if remaining is not None and status in ("running", "pending"):
            bar = self._smooth_progress(task, remaining)
            countdown = _format_countdown(remaining)
            if bar:
                lines.append(f"{countdown} [cyan]{bar}[/]")
            else:
                lines.append(countdown)
        else:
            status_label = status.replace("_", " ")
            if status in ("completed", "notified"):
                result = str(task.get("result") or "").replace("\n", " ").strip()
                if result:
                    if len(result) > 28:
                        result = result[:27] + "..."
                    lines.append(f"[dim]{escape(result)}[/]")
                else:
                    lines.append(f"[dim]{status_label}[/]")
            elif status == "failed":
                err = str(task.get("error") or "failed").replace("\n", " ").strip()
                if len(err) > 28:
                    err = err[:27] + "..."
                lines.append(f"[red]{escape(err)}[/]")
            elif status == "needs_input":
                lines.append("[yellow]needs input[/]")
            else:
                lines.append(f"[dim]{escape(status_label)} / {escape(ttype)}[/]")
        return "\n".join(lines)

    # 8 partial block glyphs → sub-cell smooth fill (classic terminal meter)
    _BAR_PARTIAL = "▏▎▍▌▋▊▉█"
    _BAR_EMPTY = "░"
    _BAR_FULL = "█"

    def _smooth_progress(self, task: dict, remaining: float, width: int = 14) -> str:
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
        # Map remainder 0..1 onto partial glyphs (skip empty partial)
        partial_i = min(len(self._BAR_PARTIAL) - 1, int(rem * len(self._BAR_PARTIAL)))
        parts: list[str] = [self._BAR_FULL] * full
        if full < width:
            if rem > 0.02:
                parts.append(self._BAR_PARTIAL[partial_i])
                parts.append(self._BAR_EMPTY * (width - full - 1))
            else:
                parts.append(self._BAR_EMPTY * (width - full))
        return "".join(parts)


class ConversationViewerModal(ModalScreen[str | None]):
    """Modal to preview and optionally resume an old conversation."""

    BINDINGS = [
        Binding("escape", "close_viewer", "Close", show=True),
        Binding("q", "close_viewer", "Close", show=False),
        Binding("r", "resume_viewer", "Resume", show=True),
    ]

    CSS = """
    ConversationViewerModal {
        align: center middle;
        background: $background 55%;
    }
    #viewer-dialog {
        width: 92;
        max-width: 100%;
        height: 36;
        max-height: 92%;
        background: $surface;
        border: solid $primary 60%;
        padding: 1 2;
        layout: vertical;
    }
    #viewer-dialog .viewer-title {
        width: 100%;
        height: 1;
        color: $primary;
        text-style: bold;
    }
    #viewer-dialog .viewer-hint {
        width: 100%;
        height: 1;
        color: $secondary;
        text-style: none;
        margin: 0 0 1 0;
    }
    #viewer-dialog #viewer-scroll {
        width: 100%;
        height: 1fr;
        border: none;
        background: $surface;
        padding: 0 0;
        scrollbar-size: 1 1;
        scrollbar-color: $primary 40%;
    }
    #viewer-dialog .viewer-msg {
        width: 100%;
        height: auto;
        padding: 1 1 1 2;
        margin: 0 0 1 0;
        background: $panel;
        border-left: thick $secondary;
    }
    #viewer-dialog .viewer-msg.user {
        border-left: thick $secondary;
    }
    #viewer-dialog .viewer-msg.assistant {
        border-left: thick $primary;
    }
    #viewer-dialog .viewer-msg.tool {
        border-left: thick $accent;
    }
    #viewer-dialog .viewer-msg.system {
        border-left: thick $secondary;
        text-style: none;
    }
    #viewer-dialog #viewer-actions {
        width: 100%;
        height: 1;
        layout: horizontal;
        align-horizontal: right;
        margin: 1 0 0 0;
    }
    #viewer-dialog #viewer-actions Button {
        width: auto;
        min-width: 10;
        height: 1;
        min-height: 1;
        max-height: 1;
        margin: 0 0 0 1;
        padding: 0 2;
        border: none;
        text-style: bold;
    }
    #viewer-dialog #viewer-actions #btn-viewer-resume {
        background: $primary;
        color: $background;
    }
    #viewer-dialog #viewer-actions #btn-viewer-close {
        background: $panel;
        color: $foreground;
    }
    """

    def __init__(self, conversation_id: str, name: str = "", **kwargs):
        super().__init__(**kwargs)
        self._conversation_id = conversation_id
        self._name = name or conversation_id

    def compose(self) -> ComposeResult:
        with Vertical(id="viewer-dialog"):
            yield Static(
                f"{escape(self._name)}",
                classes="viewer-title",
            )
            yield Static(
                f"{escape(self._conversation_id)}  ·  [r] or 'continue chat' to resume  ·  Esc closes",
                classes="viewer-hint",
            )
            with VerticalScroll(id="viewer-scroll"):
                for role, text in self._load_messages():
                    # User/tool content may include [brackets] — never parse as markup
                    yield Static(text, classes=f"viewer-msg {role}", markup=False)
            with Horizontal(id="viewer-actions"):
                yield Button(
                    "continue chat (r)", id="btn-viewer-resume", variant="primary", flat=True
                )
                yield Button(
                    "close", id="btn-viewer-close", variant="default", flat=True
                )

    def on_mount(self) -> None:
        try:
            self.set_focus(self.query_one("#viewer-scroll", VerticalScroll))
        except NoMatches:
            pass

    def _load_messages(self) -> list[tuple[str, str]]:
        try:
            from knowledge.ConversationManager import get_manager

            conv = get_manager().load_conversation(self._conversation_id)
        except Exception as e:
            return [("system", f"could not load conversation: {e}")]
        if not conv:
            return [
                (
                    "system",
                    f"conversation file not found for id {self._conversation_id}",
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
            head = f"[bold]{label}[/]" + (f"  [dim]{ts}[/]" if ts else "")
            safe_role = (
                role if role in ("user", "assistant", "tool", "system") else "system"
            )
            out.append((safe_role, f"{head}\n{content}"))
        if not out:
            out.append(("system", "empty conversation"))
        return out

    @on(Button.Pressed, "#btn-viewer-resume")
    def on_resume_pressed(self) -> None:
        self.action_resume_viewer()

    @on(Button.Pressed, "#btn-viewer-close")
    def on_close_pressed(self) -> None:
        self.action_close_viewer()

    def action_resume_viewer(self) -> None:
        self.dismiss(self._conversation_id)

    def action_close_viewer(self) -> None:
        self.dismiss(None)


class TaskDashboardModal(ModalScreen[None]):
    """Full task list modal — does not tear down the main layout."""

    BINDINGS = [
        Binding("escape", "close_tasks", "Close", show=True),
        Binding("q", "close_tasks", "Close", show=False),
    ]

    CSS = """
    TaskDashboardModal {
        align: center middle;
        background: $background 55%;
    }
    #tasks-dialog {
        width: 94;
        max-width: 100%;
        height: 34;
        max-height: 92%;
        background: $surface;
        border: solid $primary 60%;
        padding: 1 2;
        layout: vertical;
    }
    #tasks-dialog .tasks-title {
        width: 100%;
        height: 1;
        color: $primary;
        text-style: bold;
        padding: 0;
        margin: 0 0 1 0;
    }
    #tasks-dialog .tasks-hint {
        width: 100%;
        height: 1;
        color: $secondary;
        text-style: none;
        margin: 0 0 1 0;
    }
    #tasks-dialog #tasks-scroll {
        width: 100%;
        height: 1fr;
        border: none;
        background: $surface;
        padding: 0;
        scrollbar-size: 1 1;
        scrollbar-color: $primary 40%;
    }
    #tasks-dialog .task-row {
        width: 100%;
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
        background: transparent;
        border-left: solid $accent;
        color: $foreground;
    }
    #tasks-dialog .task-row.running {
        border-left: solid $warning;
    }
    #tasks-dialog .task-row.pending {
        border-left: solid $warning;
    }
    #tasks-dialog .task-row.completed {
        border-left: solid $success;
    }
    #tasks-dialog .task-row.notified {
        border-left: solid $success;
    }
    #tasks-dialog .task-row.failed {
        border-left: solid $error;
    }
    #tasks-dialog .task-row.cancelled {
        border-left: solid $secondary;
    }
    #tasks-dialog .task-row.needs_input {
        border-left: solid $error;
    }
    #tasks-dialog .tasks-empty {
        width: 100%;
        height: 3;
        padding: 0 1;
        color: $secondary;
        text-style: none;
        content-align: left middle;
    }
    #tasks-dialog #tasks-actions {
        width: 100%;
        height: 1;
        layout: horizontal;
        align-horizontal: right;
        margin: 1 0 0 0;
    }
    #tasks-dialog #tasks-actions Button {
        width: auto;
        min-width: 10;
        height: 1;
        min-height: 1;
        max-height: 1;
        margin: 0;
        padding: 0 2;
        border: none;
        background: $primary;
        color: $background;
        text-style: bold;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._refresh_timer = None
        self._spin_idx = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="tasks-dialog"):
            yield Static("Background tasks", classes="tasks-title")
            yield Static(
                "Esc to close  ·  /task <id> for full result",
                classes="tasks-hint",
            )
            yield VerticalScroll(id="tasks-scroll")
            with Horizontal(id="tasks-actions"):
                yield Button(
                    "close", id="btn-tasks-close", variant="primary", flat=True
                )

    def on_mount(self) -> None:
        self._reload()
        self._refresh_timer = self.set_interval(1.0, self._reload)
        try:
            self.set_focus(self.query_one("#tasks-scroll", VerticalScroll))
        except NoMatches:
            pass

    def on_unmount(self) -> None:
        if self._refresh_timer is not None:
            try:
                self._refresh_timer.stop()
            except Exception:
                pass

    def _reload(self) -> None:
        self._spin_idx = (self._spin_idx + 1) % len(_BRAILLE_SPINNER)
        try:
            scroller = self.query_one("#tasks-scroll", VerticalScroll)
        except NoMatches:
            return
        tasks = self._load_tasks()
        if not tasks:
            if not any(not isinstance(w, TaskLiveEntry) for w in scroller.children):
                scroller.remove_children()
                scroller.mount(
                    Static("No background tasks yet.", classes="tasks-empty")
                )
            return

        # Check if same task IDs are mounted — update in place to eliminate layout thrashing
        existing_entries = [w for w in scroller.children if isinstance(w, TaskLiveEntry)]
        existing_ids = [w.task_id for w in existing_entries]
        display_ids = [str(t.get("id") or "") for t in tasks]

        if existing_ids == display_ids:
            for w, task in zip(existing_entries, tasks):
                status = str(task.get("status") or "")
                w.update(self._format_task(task))
                w.set_classes(f"task-row {status}")
            return

        scroller.remove_children()
        for task in tasks:
            tid = str(task.get("id") or "")
            status = str(task.get("status") or "")
            scroller.mount(
                TaskLiveEntry(
                    tid,
                    self._format_task(task),
                    classes=f"task-row {status}",
                )
            )

    def on_tasks_sidebar_task_selected(self, message: TasksSidebar.TaskSelected) -> None:
        self.dismiss(None)
        if hasattr(self.app, "_open_task_watcher"):
            self.app._open_task_watcher(message.task_id)

    def _load_tasks(self) -> list[dict]:
        try:
            from core.TaskManager import list_all_tasks

            tasks = list_all_tasks()
            return sorted(tasks, key=lambda t: t.get("created_at", ""), reverse=True)
        except Exception:
            return []

    def _format_task(self, task: dict) -> str:
        status = task.get("status", "")
        glyph = _status_glyph(status, self._spin_idx)
        remaining = _timer_remaining(task)
        result = task.get("result") or task.get("error") or ""
        result = str(result).replace("\n", " ").strip()
        if len(result) > 180:
            result = result[:177] + "…"
        name = str(task.get("name") or "task").strip()
        tid = str(task.get("id") or "")
        ttype = str(task.get("task_type") or "general")
        status_label = str(status).replace("_", " ")
        conv = str(task.get("conversation_name") or "no conversation")
        created = str(task.get("created_at", ""))[11:19]
        completed = (
            str(task.get("completed_at", ""))[11:19]
            if task.get("completed_at")
            else ""
        )
        timing = created + (f"  →  {completed}" if completed else "")

        lines = [
            f"{glyph} {escape(name)}",
            f"[dim]{escape(status_label)} / {escape(ttype)} / {escape(tid)}[/]",
        ]
        if remaining is not None and status in ("running", "pending"):
            lines.append(f"{_format_countdown(remaining)} remaining")
        if timing:
            lines.append(f"[dim]{escape(timing)}  -  {escape(conv)}[/]")
        elif conv:
            lines.append(f"[dim]{escape(conv)}[/]")
        if result:
            lines.append(escape(result))
        return "\n".join(lines)

    @on(Button.Pressed, "#btn-tasks-close")
    def on_close_pressed(self) -> None:
        self.action_close_tasks()

    def action_close_tasks(self) -> None:
        self.dismiss(None)


# Back-compat name used in type hints / older call sites
TaskDashboard = TaskDashboardModal


class TaskWatcherModal(ModalScreen[None]):
    """Live modal to watch a background delegate agent or task process in real time."""

    BINDINGS = [
        Binding("escape", "close_watcher", "Close", show=True),
        Binding("q", "close_watcher", "Close", show=False),
        Binding("r", "refresh_watcher", "Refresh", show=True),
    ]

    CSS = """
    TaskWatcherModal {
        align: center middle;
        background: $background 65%;
    }
    #watcher-dialog {
        width: 96;
        max-width: 100%;
        height: 38;
        max-height: 94%;
        background: $surface;
        border: solid $primary 70%;
        padding: 1 2;
        layout: vertical;
    }
    #watcher-dialog .watcher-header {
        width: 100%;
        height: 1;
        min-height: 1;
        max-height: 1;
        layout: horizontal;
        align-vertical: middle;
        margin: 0 0 1 0;
    }
    #watcher-dialog .watcher-title {
        color: $primary;
        text-style: bold;
        width: 1fr;
        height: 1;
    }
    #watcher-dialog .watcher-badge {
        width: auto;
        height: 1;
        padding: 0 1;
        text-style: bold;
        background: $panel;
        color: $foreground;
    }
    #watcher-dialog .watcher-badge.running {
        color: $warning;
    }
    #watcher-dialog .watcher-badge.completed {
        color: $success;
    }
    #watcher-dialog .watcher-badge.failed {
        color: $error;
    }
    #watcher-dialog .watcher-hint {
        width: 100%;
        height: 1;
        color: $secondary;
        text-style: none;
        margin: 0 0 1 0;
    }
    #watcher-dialog #watcher-scroll {
        width: 100%;
        height: 1fr;
        border: none;
        background: $surface;
        padding: 0 0;
        scrollbar-size: 1 1;
        scrollbar-color: $primary 40%;
    }
    #watcher-dialog .watcher-msg {
        width: 100%;
        height: auto;
        padding: 1 1 1 2;
        margin: 0 0 1 0;
        background: $panel;
        border-left: thick $secondary;
    }
    #watcher-dialog .watcher-msg.user {
        border-left: thick $secondary;
    }
    #watcher-dialog .watcher-msg.assistant {
        border-left: thick $primary;
    }
    #watcher-dialog .watcher-msg.tool {
        border-left: thick $accent;
        background: $background;
    }
    #watcher-dialog .watcher-msg.system {
        border-left: thick $secondary;
        text-style: none;
    }
    #watcher-dialog #watcher-footer {
        width: 100%;
        height: 1;
        min-height: 1;
        max-height: 1;
        layout: horizontal;
        align-vertical: middle;
        margin: 1 0 0 0;
    }
    #watcher-dialog .watcher-activity {
        width: 1fr;
        height: 1;
        color: $secondary;
        text-style: none;
    }
    #watcher-dialog #watcher-actions {
        width: auto;
        height: 1;
        layout: horizontal;
    }
    #watcher-dialog #watcher-actions Button {
        width: auto;
        min-width: 8;
        height: 1;
        min-height: 1;
        max-height: 1;
        margin-left: 1;
        padding: 0 2;
        border: none;
    }
    """

    def __init__(self, task_id: str, **kwargs):
        super().__init__(**kwargs)
        self.task_id = task_id
        self._poll_timer = None
        self._last_msg_count = -1
        self._last_status = ""
        self._spin_idx = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="watcher-dialog"):
            with Horizontal(classes="watcher-header"):
                yield Static(f"WATCHING: {self.task_id}", id="watcher-title-text", classes="watcher-title", markup=False)
                yield Static("RUNNING", id="watcher-status-tag", classes="watcher-badge running", markup=False)
            yield Static("Live process stream  ·  Esc / Q closes  ·  R refreshes", classes="watcher-hint", markup=False)
            yield VerticalScroll(id="watcher-scroll")
            with Horizontal(id="watcher-footer"):
                yield Static("Agent active...", id="watcher-activity-text", classes="watcher-activity", markup=False)
                with Horizontal(id="watcher-actions"):
                    yield Button("refresh", id="btn-watcher-refresh", variant="default", flat=True)
                    yield Button("close", id="btn-watcher-close", variant="primary", flat=True)

    def on_mount(self) -> None:
        self.refresh_stream()
        self._poll_timer = self.set_interval(1.0, self.refresh_stream)

    def on_unmount(self) -> None:
        if self._poll_timer:
            try:
                self._poll_timer.stop()
            except Exception:
                pass

    def refresh_stream(self) -> None:
        self._spin_idx = (self._spin_idx + 1) % len(_BRAILLE_SPINNER)
        try:
            from core.TaskManager import TaskRegistry
            from core.jobs.delegate_session import get_delegate_session_store

            registry = TaskRegistry()
            task = registry.get_task(self.task_id)
            if not task:
                return

            meta = getattr(task, "metadata", {}) or {}
            session_id = meta.get("session_id") or meta.get("delegate_session_id") or ""
            specialist = meta.get("specialist") or ""
            current_activity = meta.get("current_activity") or ""
            status = getattr(task.status, "value", str(task.status))
            is_running = status in ("running", "pending")

            messages = []
            if session_id:
                store = get_delegate_session_store()
                s_rec = store.get(session_id)
                if s_rec:
                    messages = list(s_rec.messages or [])
                    if not specialist:
                        specialist = s_rec.specialist

            if not messages and "messages" in meta and isinstance(meta["messages"], list):
                messages = list(meta["messages"])

            # Fallback to tool_history if session has no message history yet
            tool_history = meta.get("tool_history") or []
            if not messages and tool_history:
                obj_text = getattr(task, "description", None) or meta.get("objective") or getattr(task, "original_query", None) or ""
                if obj_text:
                    messages.append({"role": "user", "content": obj_text})
                for th in tool_history:
                    messages.append({
                        "role": "tool",
                        "name": th.get("tool", "tool"),
                        "content": f"[Round {th.get('round', '?')}] {th.get('summary', '')}",
                    })

            if not messages:
                if getattr(task, "description", None):
                    messages.append({"role": "user", "content": task.description})
                if getattr(task, "result", None):
                    messages.append({"role": "assistant", "content": str(task.result)})
                elif is_running:
                    messages.append({
                        "role": "system",
                        "content": current_activity or "Agent is executing in the background...",
                    })

            # Update header & status
            try:
                title_widget = self.query_one("#watcher-title-text", Static)
                spec_label = f" [{specialist}]" if specialist else ""
                title_widget.update(f"{task.name or self.task_id}{spec_label}")

                tag_widget = self.query_one("#watcher-status-tag", Static)
                spin = _BRAILLE_SPINNER[self._spin_idx] if is_running else ""
                tag_widget.update(f"{spin} {status.upper()}".strip())
                tag_widget.set_classes(f"watcher-badge {status}")

                act_widget = self.query_one("#watcher-activity-text", Static)
                act_widget.update(current_activity or ("In progress..." if is_running else "Completed"))
            except NoMatches:
                return

            # Re-mount messages if count or status changed
            if len(messages) != self._last_msg_count or status != self._last_status:
                self._last_msg_count = len(messages)
                self._last_status = status
                scroller = self.query_one("#watcher-scroll", VerticalScroll)
                scroller.remove_children()

                for msg in messages:
                    role = msg.get("role") or "system"
                    content = str(msg.get("content") or "")
                    tool_calls = msg.get("tool_calls") or []
                    tool_name = msg.get("name") or ""

                    t = Text()
                    if role == "system":
                        t.append("SYSTEM / SPECIALIST\n", style="bold")
                        snippet = content[:350] + ("…" if len(content) > 350 else "")
                        t.append(snippet)
                    elif role == "user":
                        t.append("OBJECTIVE\n", style="bold cyan")
                        t.append(content)
                    elif role == "assistant":
                        t.append("DELEGATE AGENT", style="bold green")
                        if content:
                            t.append(f"\n{content}")
                        if tool_calls:
                            for tc in tool_calls:
                                fn = tc.get("function", {}).get("name", "tool") if isinstance(tc, dict) else getattr(getattr(tc, "function", None), "name", "tool")
                                args = tc.get("function", {}).get("arguments", "") if isinstance(tc, dict) else getattr(getattr(tc, "function", None), "arguments", "")
                                t.append("\n▶ Invoking Tool: ", style="yellow")
                                t.append(str(fn), style="bold")
                                t.append(f"\n{str(args)[:500]}")
                    elif role == "tool":
                        t.append("TOOL OUTPUT", style="bold yellow")
                        if tool_name:
                            t.append(f" ({tool_name})", style="bold yellow")
                        t.append("\n")
                        snippet = content[:1500] + ("…" if len(content) > 1500 else "")
                        t.append(snippet)
                    else:
                        t.append(f"{role.upper()}\n", style="bold")
                        t.append(content)

                    scroller.mount(Static(t, classes=f"watcher-msg {role}"))

                scroller.scroll_end(animate=False)
        except Exception as e:
            print(f"[TaskWatcherModal] refresh error: {e}")

    @on(Button.Pressed, "#btn-watcher-close")
    def on_close_pressed(self) -> None:
        self.action_close_watcher()

    @on(Button.Pressed, "#btn-watcher-refresh")
    def on_refresh_pressed(self) -> None:
        self.action_refresh_watcher()

    def action_refresh_watcher(self) -> None:
        self.refresh_stream()

    def action_close_watcher(self) -> None:
        self.dismiss(None)


class RemoteAccessModal(ModalScreen[None]):
    """Modal showing QR Code & URL for mobile/web remote access."""

    BINDINGS = [
        Binding("escape", "close_remote", "Close", show=True),
        Binding("q", "close_remote", "Close", show=False),
        Binding("enter", "close_remote", "Close", show=False),
    ]

    CSS = """
    RemoteAccessModal {
        align: center middle;
        background: $background 65%;
    }
    #remote-dialog {
        width: 58;
        max-width: 95%;
        height: auto;
        max-height: 92%;
        background: $surface;
        border: solid $primary 70%;
        padding: 1 2;
        layout: vertical;
        align-horizontal: center;
    }
    #remote-dialog .remote-title {
        width: 100%;
        height: 1;
        color: $primary;
        text-style: bold;
        text-align: center;
        margin: 0 0 1 0;
    }
    #remote-dialog .remote-qr-box {
        width: auto;
        height: auto;
        color: #ffffff;
        background: #000000;
        padding: 0 1;
        border: solid $secondary 40%;
        margin: 0 0 1 0;
        align: center middle;
        text-align: center;
    }
    #remote-dialog .remote-url {
        width: 100%;
        height: 1;
        color: $secondary;
        text-style: bold underline;
        text-align: center;
        margin: 0 0 1 0;
    }
    #remote-dialog .remote-hint {
        width: 100%;
        height: 2;
        color: $text-muted;
        text-align: center;
        margin: 0 0 1 0;
    }
    #remote-dialog #remote-actions {
        width: 100%;
        height: 3;
        align: center middle;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 1))
            ip = s.getsockname()[0]
            s.close()
        except Exception:
            ip = "127.0.0.1"

        port = 8766
        url = f"http://{ip}:{port}"

        qr_text = ""
        try:
            import qrcode
            qr = qrcode.QRCode(border=1)
            qr.add_data(url)
            qr.make(fit=True)
            m = qr.get_matrix()
            lines = []
            for y in range(0, len(m), 2):
                row = []
                for x in range(len(m[0])):
                    top = m[y][x]
                    bottom = m[y + 1][x] if y + 1 < len(m) else False
                    if top and bottom:
                        row.append("█")
                    elif top and not bottom:
                        row.append("▀")
                    elif not top and bottom:
                        row.append("▄")
                    else:
                        row.append(" ")
                lines.append("".join(row))
            qr_text = "\n".join(lines)
        except Exception as e:
            qr_text = f"(QR Code generation error: {e})"

        with Vertical(id="remote-dialog"):
            yield Static("A.V.A Remote Mobile & Web Access", classes="remote-title")
            yield Static(qr_text, classes="remote-qr-box")
            yield Static(f"[b]{url}[/b]", classes="remote-url")
            yield Static("Scan with your phone's camera\nor open link in any browser on your Wi-Fi", classes="remote-hint")
            with Horizontal(id="remote-actions"):
                yield Button("close (Esc)", id="btn-remote-close", variant="primary")

    @on(Button.Pressed, "#btn-remote-close")
    def on_close_pressed(self) -> None:
        self.action_close_remote()

    def action_close_remote(self) -> None:
        self.dismiss(None)


MAIN_CSS = """
/* ── Base ─────────────────────────────────────────────────────────────── */
/* NOTE: In Textual, borders consume rows inside the widget height.
   Never put height:1 on a widget that also has a vertical border. */

Screen {
    background: $background;
}

/* Flat compact buttons (no tall default chrome). Parents must give space. */
Button {
    height: 1;
    min-height: 1;
    min-width: 8;
    width: auto;
    border: none;
    background: $panel;
    color: $foreground;
    text-style: none;
    padding: 0 1;
    margin: 0;
    content-align: center middle;
}
Button:hover {
    background: $boost;
    color: $foreground;
    border: none;
}
Button:focus {
    border: none;
    text-style: bold;
}
Button.-primary {
    background: $primary;
    color: $background;
    text-style: bold;
    border: none;
}
Button.-primary:hover {
    background: $boost;
    color: $background;
    border: none;
}
Button.-primary:focus {
    background: $primary;
    color: $background;
    border: none;
}
Button:disabled {
    color: $secondary;
    background: $panel;
}

Header {
    background: $surface;
    color: $foreground;
    height: 1;
    padding: 0 1;
}
Header .header--title {
    color: $primary;
    text-style: bold;
}
Header .header--sub-title {
    color: $secondary;
}
Header .header--clock {
    color: $secondary;
}
Footer {
    background: $surface;
    color: $secondary;
    height: 1;
}
Footer .footer--key {
    color: $primary;
    background: $surface;
}
Footer .footer--description {
    color: $secondary;
    background: $surface;
}

#app-grid {
    width: 100%;
    height: 1fr;
    background: $background;
}
#main-body {
    width: 100%;
    height: 1fr;
    background: $background;
}
#center-pane {
    width: 1fr;
    height: 1fr;
    background: $background;
    layout: vertical;
}
#history-sidebar {
    display: block;
}
#tasks-sidebar {
    display: block;
}
#history-sidebar.hidden {
    display: none;
    width: 0;
    min-width: 0;
}
#tasks-sidebar.hidden {
    display: none;
    width: 0;
    min-width: 0;
}
#chat-scroll {
    width: 100%;
    height: 1fr;
    padding: 0 0;
    background: $background;
    scrollbar-size: 1 1;
    scrollbar-background: $background;
    scrollbar-color: $primary 30%;
}
#chat-log {
    width: 100%;
    height: auto;
    padding: 0 1;
}
/* Splash banner stays; system log lines can be muted via /quiet */
#chat-log .splash {
    color: $foreground 45%;
    margin: 0;
    padding: 0 1;
}
#chat-log.-quiet ChatMessage.system-msg {
    display: none;
}

/* Mic strip: full-width bar just above the text input */
#mic-strip {
    width: 100%;
}

/* Input bar: tall border Input needs 3 rows; bar gets 4 so border-top + padding fit.
   Same $background as chat so it does not form a second "band" under messages. */
#input-bar {
    height: 4;
    min-height: 4;
    padding: 0 3;
    background: $background;
    border-top: solid $surface 50%;
    layout: horizontal;
    align-vertical: middle;
}
#user-input {
    width: 1fr;
    height: 3;
    min-height: 3;
    background: $panel;
    color: $foreground;
    border: tall $secondary 50%;
    padding: 0 1;
}
#user-input:focus {
    background: $panel;
    border: tall $primary;
}
#user-input > .input--placeholder {
    color: $secondary;
}

/* Slash command palette */
#slash-palette {
    display: none;
    height: auto;
    max-height: 8;
    width: 100%;
    background: $surface;
    border-top: solid $panel;
    padding: 0 1;
}
#slash-palette.visible {
    display: block;
}
#slash-palette .slash-title {
    height: 1;
    color: $primary;
    text-style: bold;
    padding: 0 1;
    margin: 0;
}
#slash-palette .slash-hint {
    height: 1;
    color: $secondary;
    text-style: none;
    padding: 0 1;
    margin: 0;
}
#slash-palette #slash-list {
    height: auto;
    max-height: 9;
    background: $surface;
    border: none;
    padding: 0;
    margin: 0;
}
#slash-palette #slash-list:focus {
    border: none;
}
#slash-palette #slash-list > .option-list--option {
    padding: 0 1;
    margin: 0;
}
#slash-palette #slash-list > .option-list--option-highlighted {
    background: $primary 22%;
    color: $foreground;
}
#slash-palette .slash-empty {
    height: 1;
    color: $secondary;
    text-style: none;
    padding: 0 1;
    margin: 0;
}

Rule {
    color: transparent;
    margin: 0;
    height: 1;
    padding: 0;
}
"""


# ── TUI Response Loop Handler ────────────────────────────────────────────

class _TUIResponseLoopHandler(ResponseLoopHandler):
    """Response loop handler for TUI mode."""

    def __init__(self, app: "AVAApp"):
        self.app = app

    def speak(self, text: str):
        # Mark TTS busy on *this* thread before scheduling UI work, so the
        # response loop's post-turn wait cannot race ahead of the UI thread
        # and start the next announce mid-utterance.
        gen = None
        try:
            if getattr(self.app, "current_mode", "text") != "text":
                from utils import tts_piper

                gen = tts_piper.note_speech_started()
        except Exception:
            gen = None
        self.app.call_from_thread(self.app._speak_to_ui, text, gen)
        try:
            from core.gateway import broadcast_assistant_message
            from config import ASSISTANT_NAME
            broadcast_assistant_message(text, assistant=ASSISTANT_NAME)
        except Exception:
            pass

    def on_response_chunk(self, delta: str):
        super().on_response_chunk(delta)
        self.app.call_from_thread(self.app._on_stream_chunk, delta)

    def on_processing_start(self):
        super().on_processing_start()
        self.app.call_from_thread(self.app._set_processing, True)
        self.app.call_from_thread(self.app._update_status, "thinking...")

    def on_tool_call_start(self, func_name: str, args: dict, tool_id: str = ""):
        super().on_tool_call_start(func_name, args, tool_id=tool_id)
        self.app.call_from_thread(
            self.app._update_status, f"tool  ·  {func_name}"
        )
        self.app.call_from_thread(
            self.app._on_tool_running, func_name, args
        )

    def on_tool_call_result(self, result: str, func_name: str = "", tool_id: str = ""):
        super().on_tool_call_result(result, func_name=func_name, tool_id=tool_id)
        self.app.call_from_thread(
            self.app._update_last_tool_result, result
        )

    def on_processing_end(self):
        super().on_processing_end()
        self.app.call_from_thread(self.app._set_processing, False)

    def on_background_task_notify(self, summary: str):
        super().on_background_task_notify(summary)
        # Prefer a one-line UI string; callers may still pass the old multi-line
        # compact blob — collapse whitespace and hard-cap length for the chat log.
        text = " ".join(str(summary or "").split())
        if len(text) > 220:
            text = text[:217].rsplit(" ", 1)[0] + "..."
        self.app.call_from_thread(
            self.app._add_system_message,
            f"task update · {text}",
        )

    def on_immediate_notification(self, text: str):
        """Called for immediate notifications (high/urgent priority tasks)."""
        self.app.call_from_thread(
            self.app._add_system_message,
            f"[IMMEDIATE] {text}",
        )

    def on_idle_notification(self, task):
        """Called when in idle/wake-word mode and a task completes."""
        self.app.call_from_thread(
            self.app._add_system_message,
            f"[IDLE NOTIFICATION] {task.to_idle_notification()}",
        )


class AVAApp(App):
    """AVA Terminal UI — Claude Code / OpenCode inspired with Textual theming."""

    CSS = MAIN_CSS
    # ASSISTANT_NAME is usually "AVA" — don't prefix another AVA
    TITLE = str(ASSISTANT_NAME or "AVA")
    SUB_TITLE = "personal intelligence"
    # Disable Textual's built-in command palette (we use / slash commands instead)
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+n", "new_conversation", "New Chat", show=True),
        Binding("ctrl+m", "cycle_mode", "Mode", show=True),
        Binding("escape", "focus_input", "Focus Input", show=False),
        Binding("ctrl+i", "toggle_settings", "Settings", show=True),
        Binding("ctrl+t", "open_theme_picker", "Themes", show=True),
        Binding("?", "toggle_help", "Help", show=True),
        Binding("ctrl+f", "toggle_search", "Search", show=False),
        Binding("ctrl+d", "toggle_tasks", "Tasks", show=True),
        Binding("ctrl+r", "show_remote_access", "Remote", show=True),
        Binding("ctrl+b", "toggle_tasks_sidebar", "Task Side", show=False),
        Binding("ctrl+h", "toggle_history_sidebar", "History", show=True),
        Binding("ctrl+l", "toggle_system_log", "Logs", show=False),
    ]
    settings_open: reactive[bool] = reactive(False)
    theme_picker_open: reactive[bool] = reactive(False)
    help_open: reactive[bool] = reactive(False)
    search_open: reactive[bool] = reactive(False)
    tasks_open: reactive[bool] = reactive(False)
    viewer_open: reactive[bool] = reactive(False)
    task_watcher_open: reactive[bool] = reactive(False)
    history_sidebar_visible: reactive[bool] = reactive(True)
    tasks_sidebar_visible: reactive[bool] = reactive(True)
    # Off by default — theme/online tips stay mounted but hidden until /quiet
    system_log_visible: reactive[bool] = reactive(False)
    _tasks_widget: "TaskDashboard | None" = None
    current_mode: reactive[str] = reactive("text")
    app_theme: reactive[str] = reactive("ava")
    is_processing: reactive[bool] = reactive(False)
    conversation_active: reactive[bool] = reactive(False)

    def __init__(self, start_mode: str = "text", start_theme: str = "ava", **kwargs):
        super().__init__(**kwargs)
        self.current_mode = start_mode
        self.app_theme = start_theme
        self._voice_thread: threading.Thread | None = None
        self._last_local_dispatched_text: str | None = None
        self._last_mounted_user_text: str | None = None
        self._last_mounted_user_time: float = 0.0
        self._streaming_widget: "ChatMessage | None" = None
        self._streaming_text: str = ""
        self._streaming_ts: str = ""

    # ── Theme management ──────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except Exception:
            return {k: "" for k in CONFIG_DESCRIPTIONS}

    def _save_config(self, data: dict) -> bool:
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            self._add_system_message(f"save failed: {e}")
            return False

    def _register_themes(self) -> None:
        """Register custom themes (builtins are already known) and set initial theme."""
        for theme in _ALL_CUSTOM_THEMES:
            self.register_theme(theme)

        config = self._load_config()
        theme = _resolve_theme_name(config.get("THEME", "ava"))
        # Migrate old alias names in settings if needed
        if str(config.get("THEME", "")).strip().lower() in THEME_ALIASES:
            config["THEME"] = theme
            self._save_config(config)
        self.app_theme = theme
        try:
            self.theme = theme
        except Exception:
            self.app_theme = "ava"
            self.theme = "ava"

    def action_cycle_theme(self) -> None:
        """Open the theme list (kept for /theme compatibility)."""
        self.action_open_theme_picker()

    def action_open_theme_picker(self) -> None:
        """Show the centered theme list with live preview."""
        if self.theme_picker_open or self.settings_open:
            return
        self.theme_picker_open = True
        current = _resolve_theme_name(self.app_theme)

        original = current

        def _on_theme(result: str | None) -> None:
            self.theme_picker_open = False
            if result and result in AVAILABLE_THEMES:
                self._set_theme(result, announce=True)
            else:
                # Restore visual theme after live preview
                self.app_theme = original
                try:
                    self.theme = original
                except Exception:
                    pass
            try:
                self.query_one("#user-input", Input).focus()
            except NoMatches:
                pass

        self.push_screen(ThemePickerModal(current_theme=current), _on_theme)

    def _set_theme(self, theme_name: str, *, announce: bool = True, persist: bool = True) -> None:
        """Change the application theme (and optionally persist to settings.json)."""
        raw = str(theme_name or "").strip().lower()
        resolved = _resolve_theme_name(raw)
        # Unknown names (not even aliases) → error unless they map to default via empty
        if raw and raw not in AVAILABLE_THEMES and raw not in THEME_ALIASES:
            if announce:
                self._add_system_message(
                    f"theme '{theme_name}' not found  ·  /theme for the full list"
                )
            return

        self.app_theme = resolved
        try:
            self.theme = resolved
        except Exception:
            self.app_theme = "ava"
            self.theme = "ava"
            if announce:
                self._add_system_message("theme not available, using ava")
            return
        if persist:
            config = self._load_config()
            config["THEME"] = resolved
            self._save_config(config)
        if announce:
            desc = _theme_description(resolved)
            # Status bar is primary; chat gets a faded one-liner only if logs visible
            self._update_status(f"theme → {resolved}")
            self._add_system_message(f"theme → {resolved}  ·  {desc}")

    # ── Settings management ───────────────────────────────────────────────────

    def action_toggle_settings(self) -> None:
        if self.settings_open:
            # Pop the settings modal if it's the top screen
            try:
                if isinstance(self.screen, SettingsModal):
                    self.screen.dismiss(None)
                    return
            except Exception:
                pass
            self.settings_open = False
            return
        self._open_settings()

    def _open_settings(self) -> None:
        if self.settings_open or self.theme_picker_open:
            return
        self.settings_open = True
        config = self._load_config()
        # Snapshot theme so cancel can restore if user previewed from settings
        theme_before = self.app_theme

        def _on_settings(result: dict | None) -> None:
            self.settings_open = False
            if result is None:
                # Restore theme if they previewed but cancelled
                try:
                    self.theme = theme_before
                    self.app_theme = theme_before
                except Exception:
                    pass
            else:
                theme = _resolve_theme_name(result.get("THEME") or theme_before)
                result["THEME"] = theme
                # Persist full config first
                if self._save_config(result):
                    # Apply theme immediately without double-writing weird state
                    self._set_theme(theme, announce=True, persist=False)
                    self._add_system_message(
                        "settings saved  ·  theme applied now  ·  "
                        "API keys / names may need a restart"
                    )
                else:
                    try:
                        self.theme = theme_before
                        self.app_theme = theme_before
                    except Exception:
                        pass
            try:
                self.query_one("#user-input", Input).focus()
            except NoMatches:
                pass

        self.push_screen(
            SettingsModal(config, current_theme=self.app_theme),
            _on_settings,
        )

    def _close_settings(self) -> None:
        """Dismiss settings modal if open (used by escape / overlay manager)."""
        if not self.settings_open:
            return
        try:
            if isinstance(self.screen, SettingsModal):
                self.screen.dismiss(None)
                return
        except Exception:
            pass
        self.settings_open = False

    def action_toggle_help(self) -> None:
        if self.help_open:
            self._close_help()
        else:
            self._open_help()

    def _open_help(self) -> None:
        if self.help_open:
            return
        self.help_open = True
        try:
            chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
            chat_scroll.display = False
        except NoMatches:
            pass
        try:
            input_bar = self.query_one("#input-bar", Horizontal)
            input_bar.display = False
        except NoMatches:
            pass
        center = self.query_one("#center-pane", Vertical)
        center.mount(HelpOverlay(id="help-overlay"), before=self.query_one("#input-bar"))

    def _close_help(self) -> None:
        if not self.help_open:
            return
        self.help_open = False
        try:
            overlay = self.query_one("#help-overlay", HelpOverlay)
            overlay.remove()
        except NoMatches:
            pass
        try:
            chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
            chat_scroll.display = True
        except NoMatches:
            pass
        try:
            input_bar = self.query_one("#input-bar", Horizontal)
            input_bar.display = True
        except NoMatches:
            pass
        self.query_one("#user-input", Input).focus()

    def action_toggle_search(self) -> None:
        if self.search_open:
            self._close_search()
        else:
            self._open_search()

    def _open_search(self) -> None:
        if self.search_open:
            return
        self.search_open = True
        try:
            input_bar = self.query_one("#input-bar", Horizontal)
        except NoMatches:
            return
        search = SearchOverlay(id="search-overlay")
        input_bar.mount(search, before=self.query_one("#user-input"))
        self.set_focus(search.query_one("#search-input", Input))

    def _close_search(self) -> None:
        if not self.search_open:
            return
        self.search_open = False
        try:
            overlay = self.query_one("#search-overlay", SearchOverlay)
            overlay.remove()
        except NoMatches:
            pass
        self.query_one("#user-input", Input).focus()

    def action_toggle_tasks(self) -> None:
        if self.tasks_open:
            self._close_tasks()
        else:
            self._open_tasks()

    def _open_tasks(self) -> None:
        if self.tasks_open or self.settings_open or self.theme_picker_open:
            return
        self.tasks_open = True

        def _on_close(_result: None) -> None:
            self.tasks_open = False
            self._tasks_widget = None
            try:
                self.query_one("#user-input", Input).focus()
            except NoMatches:
                pass

        modal = TaskDashboardModal()
        self._tasks_widget = modal
        self.push_screen(modal, _on_close)

    def _close_tasks(self) -> None:
        if not self.tasks_open:
            return
        try:
            if isinstance(self.screen, TaskDashboardModal):
                self.screen.dismiss(None)
                return
        except Exception:
            pass
        self.tasks_open = False
        self._tasks_widget = None

    def action_show_remote_access(self) -> None:
        """Show the QR code and URL for mobile/web remote access."""
        def _on_close(_result: None) -> None:
            try:
                self.query_one("#user-input", Input).focus()
            except NoMatches:
                pass

        self.push_screen(RemoteAccessModal(), _on_close)

    # ── Sidebars ──────────────────────────────────────────────────────────────

    @on(Button.Pressed, "#btn-close-history")
    def on_close_history(self) -> None:
        self.action_toggle_history_sidebar()

    @on(Button.Pressed, "#btn-close-tasks")
    def on_close_tasks(self) -> None:
        self.action_toggle_tasks_sidebar()

    def action_toggle_history_sidebar(self) -> None:
        self.history_sidebar_visible = not self.history_sidebar_visible
        self._apply_sidebar_visibility()

    def action_toggle_tasks_sidebar(self) -> None:
        self.tasks_sidebar_visible = not self.tasks_sidebar_visible
        self._apply_sidebar_visibility()

    def _apply_sidebar_visibility(self) -> None:
        try:
            hist = self.query_one("#history-sidebar", HistorySidebar)
            if self.history_sidebar_visible:
                hist.remove_class("hidden")
                hist.display = True
            else:
                hist.add_class("hidden")
                hist.display = False
        except NoMatches:
            pass
        try:
            tasks = self.query_one("#tasks-sidebar", TasksSidebar)
            if self.tasks_sidebar_visible:
                tasks.remove_class("hidden")
                tasks.display = True
            else:
                tasks.add_class("hidden")
                tasks.display = False
        except NoMatches:
            pass
        side = []
        if self.history_sidebar_visible:
            side.append("history")
        if self.tasks_sidebar_visible:
            side.append("tasks")
        if side:
            self._update_status(f"sidebars · {' + '.join(side)}")
        else:
            self._update_status("sidebars hidden · ctrl+h / ctrl+b to restore")

    def on_history_sidebar_conversation_selected(
        self, message: HistorySidebar.ConversationSelected
    ) -> None:
        self._open_conversation_viewer(message.conversation_id, message.name)

    def _open_conversation_viewer(self, conversation_id: str, name: str = "") -> None:
        if not conversation_id:
            return
        if self.viewer_open:
            return
        # Don't stack on top of settings/theme modals awkwardly
        if self.settings_open or self.theme_picker_open:
            return
        self.viewer_open = True

        def _on_close(result: str | None = None) -> None:
            self.viewer_open = False
            if result:
                self.action_resume_conversation(result)
            else:
                try:
                    self.query_one("#user-input", Input).focus()
                except NoMatches:
                    pass

        self.push_screen(
            ConversationViewerModal(conversation_id, name),
            _on_close,
        )

    def _close_conversation_viewer(self) -> None:
        if not self.viewer_open:
            return
        try:
            if isinstance(self.screen, ConversationViewerModal):
                self.screen.dismiss(None)
                return
        except Exception:
            pass
        self.viewer_open = False

    def on_tasks_sidebar_task_selected(
        self, message: TasksSidebar.TaskSelected
    ) -> None:
        self._open_task_watcher(message.task_id)

    def _open_task_watcher(self, task_id: str) -> None:
        if not task_id:
            return
        if self.task_watcher_open or self.settings_open or self.theme_picker_open:
            return
        self.task_watcher_open = True

        def _on_close(_result: None) -> None:
            self.task_watcher_open = False
            try:
                self.query_one("#user-input", Input).focus()
            except NoMatches:
                pass

        self.push_screen(
            TaskWatcherModal(task_id),
            _on_close,
        )

    def _close_task_watcher(self) -> None:
        if not self.task_watcher_open:
            return
        try:
            if isinstance(self.screen, TaskWatcherModal):
                self.screen.dismiss(None)
                return
        except Exception:
            pass
        self.task_watcher_open = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="app-grid"):
            with Horizontal(id="main-body"):
                yield HistorySidebar(id="history-sidebar")
                with Vertical(id="center-pane"):
                    with VerticalScroll(id="chat-scroll"):
                        yield Vertical(id="chat-log")
                    yield SlashCommandPalette(id="slash-palette")
                    yield MicStrip(id="mic-strip")
                    with Horizontal(id="input-bar"):
                        yield ChatInput(
                            placeholder=f"message {ASSISTANT_NAME.lower()}...  ·  / for commands",
                            id="user-input",
                        )
                        yield ModeDropdown(id="mode-dropdown")
                    yield StatusBar(id="status-bar")
                yield TasksSidebar(id="tasks-sidebar")

    # ── Main response loop ────────────────────────────────────────────────────

    def _start_response_loop(self) -> None:
        """Start the shared response loop with a TUI handler."""
        handler = _TUIResponseLoopHandler(self)
        threading.Thread(target=run_response_loop, args=(handler,), daemon=True).start()

    def on_mount(self) -> None:
        global _app_instance
        _app_instance = self

        # Register themes and set initial theme
        self._register_themes()

        # ASCII splash (not part of hideable system log)
        log = self.query_one("#chat-log", Vertical)
        log.mount(
            Static(
                f"[bold]{AVA_ASCII}[/]\n[dim]personal intelligence  -  {ASSISTANT_NAME}[/]",
                classes="splash",
            )
        )

        try:
            dropdown = self.query_one(ModeDropdown)
            dropdown.set_mode(self.current_mode)
        except NoMatches:
            pass
        mode_label = MODE_LABELS.get(self.current_mode, self.current_mode).lower()
        self._update_status(f"ready  ·  {mode_label} mode")
        # Bootstrap tips go to the fadeable system log (toggle: /quiet or ctrl+l)
        self._add_system_message(f"online  ·  {USER_NAME}  ·  {mode_label} mode")
        self._add_system_message(f"theme: {self.app_theme}")
        self._add_system_message("ctrl+h history  ·  ctrl+b tasks  ·  /quiet hides this log")
        self._apply_system_log_visibility()
        self._apply_sidebar_visibility()

        # Hub Device Client connection & status tracking
        try:
            from core.device_client import (
                get_connection_status,
                add_status_listener,
                start_device_client,
            )
            start_device_client(device_id="pc_main")

            def _on_hub_conn_change(new_status: str) -> None:
                try:
                    bar = self.query_one("#status-bar", StatusBar)
                    bar.conn_status = new_status
                except Exception:
                    pass

            add_status_listener(_on_hub_conn_change)
            bar = self.query_one("#status-bar", StatusBar)
            bar.conn_status = get_connection_status()
            self.set_interval(3.0, self._sync_hub_connection)
        except Exception as e:
            logger.debug(f"[TUI] Hub status setup: {e}")

        # Always-on services (idempotent if CLI already started them)
        try:
            from core.services import start_ava_services

            start_ava_services()
        except Exception as e:
            print(f"[Services] TUI start failed: {e}")

        # Drop stale pending/running jobs left on disk from a previous process
        try:
            from core.jobs.executor import get_executor
            from core.TaskManager import get_registry

            get_executor().reclaim_orphans(get_registry())
        except Exception as e:
            print(f"[TUI] Task reclaim skipped: {e}")

        self._start_response_loop()

        # Populate sidebars after registry is clean
        try:
            self.query_one("#tasks-sidebar", TasksSidebar).refresh_list()
        except NoMatches:
            pass
        try:
            self.query_one("#history-sidebar", HistorySidebar).refresh_list()
        except NoMatches:
            pass

        # Listen for events arriving from outside (e.g. phone gateway, web portal)
        register_user_message_listener(self._on_incoming_user_message)
        register_conversation_reset_listener(self._on_remote_new_conversation)
        register_slash_command_listener(self._on_remote_slash_command)
        register_clear_chat_listener(self._on_remote_clear_chat)
        register_system_message_listener(self._on_incoming_system_message)

        self.query_one("#user-input", Input).focus()

    def on_unmount(self) -> None:
        unregister_user_message_listener(self._on_incoming_user_message)
        unregister_conversation_reset_listener(self._on_remote_new_conversation)
        unregister_slash_command_listener(self._on_remote_slash_command)
        unregister_clear_chat_listener(self._on_remote_clear_chat)
        unregister_system_message_listener(self._on_incoming_system_message)

    def _on_remote_new_conversation(self) -> None:
        """Called when a new conversation is started remotely (Web / Android)."""
        def _apply():
            self._add_separator()
            self._add_system_message("new conversation started remotely")
            self.conversation_active = True
            self._update_status(
                f"ready  ·  {MODE_LABELS.get(self.current_mode, self.current_mode).lower()} mode"
            )
            try:
                hist = self.query_one("#history-sidebar", HistorySidebar)
                hist.refresh_list()
            except NoMatches:
                pass
        try:
            self.call_from_thread(_apply)
        except Exception:
            pass

    def _on_remote_slash_command(self, command: str, origin: str) -> None:
        """Called when a slash command is sent from a remote client."""
        def _apply():
            self._add_system_message(f"[{origin}] {command}")
        try:
            self.call_from_thread(_apply)
        except Exception:
            pass

    def _on_remote_clear_chat(self) -> None:
        """Called when chat clear is requested remotely."""
        def _apply():
            try:
                log = self.query_one("#chat-log", Vertical)
                for child in log.children:
                    child.remove()
            except NoMatches:
                pass
        try:
            self.call_from_thread(_apply)
        except Exception:
            pass

    def _on_incoming_system_message(self, text: str, is_alert: bool = False) -> None:
        """Called when a system notification is broadcast."""
        def _apply():
            self._add_system_message(text)
        try:
            self.call_from_thread(_apply)
        except Exception:
            pass

    def _on_incoming_user_message(self, content: str) -> None:
        """Called when a user message arrives from any source (e.g. phone gateway)."""
        clean_text = content
        user_prefix = f"{USER_NAME}: "
        if clean_text.startswith(user_prefix):
            clean_text = clean_text[len(user_prefix):]

        if self._last_local_dispatched_text and clean_text.strip() == self._last_local_dispatched_text.strip():
            self._last_local_dispatched_text = None
            return

        now = time.time()
        if (
            self._last_mounted_user_text
            and clean_text.strip() == self._last_mounted_user_text.strip()
            and (now - self._last_mounted_user_time) < 2.0
        ):
            return

        try:
            self.call_from_thread(self._add_user_message, clean_text)
            self.call_from_thread(self._update_status, "thinking...")
            self.call_from_thread(self._set_processing, True)
        except Exception as e:
            print(f"[TUI] Error mounting incoming user message: {e}")

    def on_ready(self) -> None:
        if self.current_mode != "text":
            self._start_voice_mode(self.current_mode)

    # ── Mode switching ────────────────────────────────────────────────────────

    @on(ModeDropdown.OpenRequested)
    def on_mode_dropdown_open_requested(self, message: ModeDropdown.OpenRequested) -> None:
        self._open_mode_selector()

    def _open_mode_selector(self) -> None:
        current = self.current_mode

        def _on_select(mode: str | None) -> None:
            if mode and mode in MODES:
                self._switch_mode(mode)
            try:
                self.query_one("#user-input", Input).focus()
            except NoMatches:
                pass

        self.push_screen(ModeSelectorPopup(current_mode=current), _on_select)

    def action_cycle_mode(self) -> None:
        idx = MODES.index(self.current_mode) if self.current_mode in MODES else 0
        new_mode = MODES[(idx + 1) % len(MODES)]
        self._switch_mode(new_mode)

    def _switch_mode(self, new_mode: str) -> None:
        old_mode = self.current_mode
        if new_mode == old_mode:
            return
        self.current_mode = new_mode
        try:
            from core.TaskManager import set_mode
            set_mode(new_mode)
        except Exception:
            pass
        # Update the mode dropdown button
        try:
            dropdown = self.query_one(ModeDropdown)
            dropdown.set_mode(new_mode)
        except NoMatches:
            pass
        self._add_system_message(
            f"mode → {MODE_LABELS.get(new_mode, new_mode).lower()}"
        )
        self._update_status(
            f"ready  ·  {MODE_LABELS.get(new_mode, new_mode).lower()} mode"
        )

        if self._voice_thread and self._voice_thread.is_alive():
            if old_mode == "continuous":
                voice_stop_event.set()
            elif old_mode == "wakeword":
                wakeword_stop_event.set()
            _mode_switch_event.set()
            self._voice_thread.join(timeout=3.0)
            _mode_switch_event.clear()

        if new_mode != "text":
            self._start_voice_mode(new_mode)

    def _start_voice_mode(self, mode: str) -> None:
        def _run():
            try:
                if mode == "continuous":
                    self._voice_continuous()
                elif mode == "wakeword":
                    self._voice_wakeword()
            except Exception as e:
                self.call_from_thread(self._add_system_message, f"voice error: {e}")

        self._voice_thread = threading.Thread(target=_run, daemon=True)
        self._voice_thread.start()

    # ── Input handling ────────────────────────────────────────────────────────

    @on(Input.Changed, "#user-input")
    def on_user_input_changed(self, event: Input.Changed) -> None:
        self._sync_slash_palette(event.value)

    def _sync_slash_palette(self, value: str) -> None:
        try:
            pal = self.query_one("#slash-palette", SlashCommandPalette)
        except NoMatches:
            return
        text = value or ""
        # Only when the line is a slash-command draft (starts with /, no args yet)
        if text.startswith("/") and " " not in text:
            pal.show_for(text)
        else:
            if pal.is_open():
                pal.hide()

    def _hide_slash_palette(self) -> None:
        try:
            self.query_one("#slash-palette", SlashCommandPalette).hide()
        except NoMatches:
            pass

    def _accept_slash_command(self, cmd: SlashCommand | None) -> None:
        """Apply a palette selection into the input (and run if complete)."""
        if cmd is None:
            self._hide_slash_palette()
            return
        try:
            inp = self.query_one("#user-input", ChatInput)
        except NoMatches:
            try:
                inp = self.query_one("#user-input", Input)
            except NoMatches:
                return

        if cmd.needs_args:
            # Fill "/cmd " and leave user to type the rest
            filled = f"{cmd.command} "
            inp.value = filled
            try:
                inp.cursor_position = len(filled)
            except Exception:
                pass
            self._hide_slash_palette()
            inp.focus()
            return

        # Complete command → run immediately
        self._hide_slash_palette()
        inp.value = ""
        self._dispatch_user_text(cmd.command)

    def on_slash_command_palette_accepted(
        self, message: SlashCommandPalette.Accepted
    ) -> None:
        self._accept_slash_command(message.command)

    @on(Input.Submitted, "#user-input")
    def handle_input(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return

        # If the slash palette is open, Enter selects the highlighted command
        try:
            pal = self.query_one("#slash-palette", SlashCommandPalette)
            if pal.is_open():
                selected = pal.selected()
                if selected is not None:
                    event.input.clear()
                    # needs_args: fill; else run
                    if selected.needs_args:
                        filled = f"{selected.command} "
                        event.input.value = filled
                        try:
                            event.input.cursor_position = len(filled)
                        except Exception:
                            pass
                        pal.hide()
                        return
                    pal.hide()
                    self._dispatch_user_text(selected.command)
                    return
        except NoMatches:
            pass

        event.input.clear()
        self._hide_slash_palette()
        self._dispatch_user_text(text)

    def _dispatch_user_text(self, text: str) -> None:
        """Route a submitted line (slash command or chat message)."""
        text = (text or "").strip()
        if not text:
            return

        # Claude Code skill: /skill-name [args]
        try:
            from core.cc_plugins.skills_host import get_host
            from core.messageHandler import add_message

            host = get_host()
            host.load()
            hit = host.match_slash(text)
        except Exception:
            hit = None
        if hit:
            skill, args = hit
            try:
                body = get_host().render(skill.name, args)
                add_message(content=body, role="system", trigger_response=False)
                add_message(
                    content=text,
                    role="user",
                    trigger_response=True,
                )
                self._add_system_message(f"loaded skill {skill.slash}")
            except Exception as e:
                self._add_system_message(f"Skill {skill.slash} failed: {e}")
            return

        # Settings command
        if (
            text.lower().startswith("/settings")
            or text.lower().startswith("/config")
            or text.lower().startswith("/set")
        ):
            self.action_toggle_settings()
            return

        # Help command
        if text.lower() in {"/help", "/commands"}:
            self._add_system_message("available commands  ·  type / for the palette:")
            for cmd in _all_slash_commands():
                self._add_system_message(f"{cmd.display_cmd()}  —  {cmd.description}")
            return

        if text.lower() in {"/skills", "/plugins"}:
            try:
                from core.cc_plugins.skills_host import get_host

                host = get_host()
                host.load()
                self._add_system_message(host.summary())
            except Exception as e:
                self._add_system_message(f"Skills failed to load: {e}")
            return

        # New conversation command
        if text.lower() == "/new":
            self.action_new_conversation()
            return

        # Quit command
        if text.lower() == "/quit":
            self.action_quit()
            return

        # Print messages command
        if text.lower() == "print messages":
            from core.messageHandler import messages

            self._add_system_message(str(messages))
            return
        if text.lower() == "print memories":
            from knowledge.memory import retrieve_memories

            self._add_system_message(str(retrieve_memories()))
            return

        # Theme command — opens the list picker (or sets by name)
        if text.lower().startswith("/theme"):
            if text.lower() in {"/theme", "/themes"}:
                self.action_open_theme_picker()
            elif text.lower().startswith("/theme "):
                theme_name = text[7:].strip().lower()
                if theme_name in AVAILABLE_THEMES or theme_name in THEME_ALIASES:
                    self._set_theme(theme_name)
                else:
                    self._add_system_message(
                        f"unknown theme: {theme_name}  ·  /theme for the A–Z list "
                        f"({len(AVAILABLE_THEMES)} themes)"
                    )
            return

        if text.lower() == "/themes":
            self.action_open_theme_picker()
            return

        # Mode command
        if text.lower().startswith("/mode"):
            if text.lower().startswith("/mode "):
                mode_arg = text[6:].strip().lower()
                if mode_arg in MODES:
                    self._switch_mode(mode_arg)
                else:
                    self._add_system_message(
                        f"invalid mode: {mode_arg}  ·  text/continuous/wakeword"
                    )
            return

        # Clear command
        if text.lower() == "/clear":
            try:
                log = self.query_one("#chat-log", Vertical)
                for child in log.children:
                    child.remove()
            except NoMatches:
                pass
            try:
                from core.gateway import get_broadcaster
                get_broadcaster().broadcast_sync("event:clear_chat", {})
            except Exception:
                pass
            return

        if text.lower() == "/tasks":
            self.action_toggle_tasks()
            return

        if text.lower() in {"/history", "/chats", "/conversations"}:
            self.action_toggle_history_sidebar()
            return

        if text.lower() in {"/taskside", "/sidebar"}:
            self.action_toggle_tasks_sidebar()
            return

        if text.lower() in {"/quiet", "/logs", "/sys"}:
            self.action_toggle_system_log()
            return

        if text.lower().startswith("/task "):
            task_id = text[6:].strip()
            from core.TaskManager import manage_background_tasks

            result = manage_background_tasks("result", task_id)
            self._add_system_message(result)
            return

        if text.lower().startswith("/watch"):
            target_id = text[6:].strip()
            if not target_id:
                try:
                    from core.TaskManager import list_all_tasks

                    running = [
                        t for t in list_all_tasks()
                        if t.get("status") in ("running", "pending")
                    ]
                    if running:
                        target_id = str(running[0].get("id") or "")
                except Exception:
                    pass
            if target_id:
                self._open_task_watcher(target_id)
            else:
                self._add_system_message("No active task to watch. Usage: /watch <task_id>")
            return

        # Remote / QR Code Access command
        if text.lower() in {"/remote", "/remote-access", "/qr", "/mobile", "/web"}:
            self.action_show_remote_access()
            return

        # New conversation command
        if text.lower() == "/new":
            self.action_new_conversation()
            return

        if text.lower().startswith("/resume ") or text.lower().startswith("/switch "):
            conv_id = text.split(" ", 1)[1].strip()
            if conv_id:
                self.action_resume_conversation(conv_id)
            else:
                self._add_system_message("Usage: /resume <conversation_id>")
            return

        if text.lower() == "/sync":
            try:
                from knowledge.ConversationManager import get_manager
                count = get_manager().sync_with_server()
                self._add_system_message(f"conversations synced with server ({count} updated)")
                try:
                    hist = self.query_one("#history-sidebar", HistorySidebar)
                    hist.refresh_list()
                except NoMatches:
                    pass
            except Exception as e:
                self._add_system_message(f"sync error: {e}")
            return

        # Quit command
        if text.lower() == "/quit":
            self.action_quit()
            return

        # Print messages command
        if text.lower() == "print messages":
            from core.messageHandler import messages

            self._add_system_message(str(messages))
            return
        if text.lower() == "print memories":
            from knowledge.memory import retrieve_memories

            self._add_system_message(str(retrieve_memories()))
            return

        if any(cmd in text.lower() for cmd in SHUTUP_COMMANDS):
            response = random.choice(EXIT_RESPONSES)
            self._add_user_message(text)
            self._add_assistant_message(response)
            self._end_conversation()
            return

        user_msg = f"{USER_NAME}: {text}"
        self._add_user_message(text)
        self._update_status("thinking...")
        self._set_processing(True)
        # First text message opens a real conversation thread (so /new can save it).
        if not self.conversation_active:
            self.conversation_active = True

        self._last_local_dispatched_text = text
        add_message(role="user", content=user_msg, tool_id="")

    # ── Conversation management ───────────────────────────────────────────────

    def action_new_conversation(self) -> None:
        # Always persist the previous chat — including the first TUI session,
        # which used to never set conversation_active / start a ConversationManager
        # thread, so /new discarded it.
        had_dialogue = self._has_saveable_dialogue()
        if had_dialogue or self.conversation_active:
            save_current_conversation()
            self.conversation_active = False
            if had_dialogue:
                self._add_system_message("conversation saved")
            try:
                hist = self.query_one("#history-sidebar", HistorySidebar)
                hist.refresh_list()
            except NoMatches:
                pass

        self._add_separator()
        self._add_system_message("new conversation")
        start_new_conversation()
        reset_messages()
        self.conversation_active = True
        self._update_status(
            f"ready  ·  {MODE_LABELS.get(self.current_mode, self.current_mode).lower()} mode"
        )
        try:
            hist = self.query_one("#history-sidebar", HistorySidebar)
            hist.refresh_list()
        except NoMatches:
            pass
        self.query_one("#user-input", Input).focus()

        # Notify Gateway & remote clients (Web & Android)
        try:
            from core.gateway import get_broadcaster, broadcast_system_message
            get_broadcaster().broadcast_sync("event:new_conversation", {})
            broadcast_system_message("New conversation started on PC")
        except Exception:
            pass

    def action_resume_conversation(self, conversation_id: str) -> None:
        """Switch active session and LLM context to an existing conversation thread."""
        had_dialogue = self._has_saveable_dialogue()
        if had_dialogue or self.conversation_active:
            save_current_conversation()

        from knowledge.ConversationManager import get_manager
        from core.messageHandler import load_conversation_messages

        mgr = get_manager()
        conv = mgr.switch_to_conversation(conversation_id)
        if not conv:
            self._add_system_message(f"could not resume conversation {conversation_id}")
            return

        # Reload live LLM message history
        load_conversation_messages(conv.messages)

        # Clear UI chat log and mount historical messages
        try:
            log = self.query_one("#chat-log", Vertical)
            for child in list(log.children):
                child.remove()
        except NoMatches:
            pass

        self._add_system_message(f"resumed: {conv.name} ({conv.id})")
        for msg in conv.messages:
            role = msg.get("role") or ""
            content = str(msg.get("content") or "").strip()
            if not content:
                continue
            if role == "user":
                clean = content
                user_prefix = f"{USER_NAME}: "
                if clean.startswith(user_prefix):
                    clean = clean[len(user_prefix):]
                self._add_user_message(clean)
            elif role == "assistant":
                self._add_assistant_message(content)
            elif role == "tool":
                short = content if len(content) <= 140 else content[:137] + "…"
                self._add_system_message(f"[tool] {short}")
            elif role == "system":
                self._add_system_message(content)

        self.conversation_active = True
        self._update_status(f"ready  ·  {conv.name}")
        try:
            hist = self.query_one("#history-sidebar", HistorySidebar)
            hist.refresh_list()
        except NoMatches:
            pass
        try:
            self.query_one("#user-input", Input).focus()
        except NoMatches:
            pass

        # Notify remote clients
        try:
            from core.gateway import get_broadcaster, broadcast_system_message
            get_broadcaster().broadcast_sync("event:resume_conversation", {"conversation_id": conv.id, "name": conv.name})
            broadcast_system_message(f"Switched conversation to {conv.name}")
        except Exception:
            pass

    def _has_saveable_dialogue(self) -> bool:
        """True if the live LLM history or ConversationManager has user/assistant turns."""
        try:
            from core.messageHandler import messages, messages_lock
            from knowledge.ConversationManager import get_manager

            mgr = get_manager()
            if mgr.current_conversation and mgr.current_conversation.messages:
                return True
            with messages_lock:
                return any(
                    isinstance(m, dict) and m.get("role") in ("user", "assistant")
                    for m in messages
                )
        except Exception:
            return self.conversation_active

    def _end_conversation(self) -> None:
        # Save even if the UI flag was never flipped (first session before /new).
        had_dialogue = self._has_saveable_dialogue()
        if not self.conversation_active and not had_dialogue:
            return
        save_current_conversation()
        self.conversation_active = False
        if had_dialogue:
            self._add_system_message("conversation saved")
        self._update_status(
            f"ready  ·  {MODE_LABELS.get(self.current_mode, self.current_mode).lower()} mode"
        )
        try:
            hist = self.query_one("#history-sidebar", HistorySidebar)
            hist.refresh_list()
        except NoMatches:
            pass

    def action_focus_input(self) -> None:
        # Escape: modal screens handle their own escape; then close inline overlays
        try:
            if isinstance(
                self.screen,
                (
                    SettingsModal,
                    ThemePickerModal,
                    ConversationViewerModal,
                    TaskDashboardModal,
                ),
            ):
                # Let the modal binding win; don't steal focus underneath
                return
        except Exception:
            pass
        if self.help_open:
            self._close_help()
            return
        if self.search_open:
            self._close_search()
            return
        self.query_one("#user-input", Input).focus()

    def _set_processing(self, val: bool) -> None:
        self.is_processing = val
        if val:
            self._show_generating_indicator("thinking")
        else:
            self._hide_generating_indicator()
            self._update_status(
                f"ready  ·  {MODE_LABELS.get(self.current_mode, self.current_mode).lower()} mode"
            )

    def _show_generating_indicator(self, label: str = "thinking") -> None:
        try:
            existing = self.query_one("#typing-indicator", GeneratingIndicator)
            existing.set_label(label)
            return
        except NoMatches:
            pass
        try:
            log = self.query_one("#chat-log", Vertical)
            log.mount(GeneratingIndicator(label=label, id="typing-indicator"))
            self._scroll_to_bottom()
        except NoMatches:
            pass

    def _hide_generating_indicator(self) -> None:
        try:
            indicator = self.query_one("#typing-indicator", GeneratingIndicator)
            indicator.remove()
        except NoMatches:
            pass

    # Back-compat wrappers
    def _show_typing_indicator(self) -> None:
        self._show_generating_indicator("thinking")

    def _hide_typing_indicator(self) -> None:
        self._hide_generating_indicator()

    # ── Voice modes ───────────────────────────────────────────────────────────

    def _voice_continuous(self) -> None:
        from utils import stt_hybrid as stt
        from utils import tts_piper

        tts_piper.init_tts()
        self.call_from_thread(self._add_system_message, "continuous listening active")

        recorder = stt.VoiceRecorder(
            status_callback=lambda txt: self.call_from_thread(
                self._handle_status_update, txt
            )
        )
        while self.current_mode == "continuous" and not _mode_switch_event.is_set():
            success, filename = recorder.record()
            if _mode_switch_event.is_set():
                break
            if success:
                prompt = recorder.get_whisper_result(timeout=15)
                transcription = prompt if prompt is not None else ""

                if any(
                    word in transcription.lower()
                    for word in ["ava", "assistant", "eva", "ayva", "evaa"]
                ):
                    self.call_from_thread(
                        self._add_system_message, "wake word detected"
                    )
                    start_new_conversation()
                    reset_messages()
                    self.call_from_thread(self._set_conversation_active, True)

                    while (
                        self.current_mode == "continuous"
                        and not _mode_switch_event.is_set()
                    ):
                        while (
                            main_runner.is_set() or main_running.is_set()
                        ) and not _mode_switch_event.is_set():
                            time.sleep(0.01)
                        if _mode_switch_event.is_set():
                            break

                        transcription = transcription.strip()
                        if not transcription:
                            break

                        self.call_from_thread(self._add_user_message, transcription)
                        self.call_from_thread(self._update_status, "thinking...")
                        self.call_from_thread(self._set_processing, True)

                        if any(cmd in transcription.lower() for cmd in SHUTUP_COMMANDS):
                            resp = random.choice(EXIT_RESPONSES)
                            self.call_from_thread(self._add_assistant_message, resp)
                            break

                        self._last_local_dispatched_text = transcription.strip()
                        user_msg = f"{USER_NAME}: {transcription}"
                        add_message(role="user", content=user_msg, tool_id="")

                        main_running.wait(timeout=5.0)
                        if _mode_switch_event.is_set():
                            break
                        while main_running.is_set() and not _mode_switch_event.is_set():
                            time.sleep(0.01)
                        if _mode_switch_event.is_set():
                            break

                        # Check if we're expecting follow-up from the model (work in progress)
                        from core.AppStates import expecting_followup, work_in_progress
                        if expecting_followup.is_set() or work_in_progress.is_set():
                            # Extend timeout significantly for work in progress
                            timeout = max(tts_piper.get_last_duration() + 7, 45)  # Minimum 45 seconds
                            print(f"[TUI VoiceMode] Extended timeout to {timeout}s (work in progress)")

                            # Clear the flag now that we're waiting
                            expecting_followup.clear()
                        else:
                            timeout = tts_piper.get_last_duration() + 7

                        continued, _ = recorder.record(
                            timeout=timeout, silence_duration=2.5
                        )
                        if stop_chat_flag.is_set():
                            stop_chat_flag.clear()
                            break

                        if continued:
                            stop_event.clear()
                            transcription = recorder.get_whisper_result(timeout=15) or ""
                            if transcription.strip():
                                continue
                            else:
                                break
                        else:
                            # If we were expecting follow-up but timed out, try one more time
                            if work_in_progress.is_set():
                                work_in_progress.clear()
                                print("[TUI VoiceMode] Work in progress timed out, giving extra grace period...")
                                timeout = 30  # Additional grace period
                                continued, _ = recorder.record(
                                    timeout=timeout, silence_duration=2.5
                                )
                                if continued:
                                    stop_event.clear()
                                    transcription = recorder.get_whisper_result(timeout=15) or ""
                                    if transcription.strip():
                                        continue
                                    else:
                                        break
                            stop_event.clear()
                            break

                    save_current_conversation()
                    self.call_from_thread(self._set_conversation_active, False)
                    self.call_from_thread(
                        self._add_system_message, "conversation ended  ·  listening..."
                    )

    def _voice_wakeword(self) -> None:
        from utils import stt_hybrid as stt
        from utils import tts_piper
        from utils.wakeword import WakeWordDetector

        tts_piper.init_tts()

        try:
            detector = WakeWordDetector()
        except FileNotFoundError as e:
            self.call_from_thread(
                self._add_system_message,
                f"wake word model not found: {e}  ·  falling back to continuous",
            )
            self._voice_continuous()
            return

        recorder = stt.VoiceRecorder(
            status_callback=lambda txt: self.call_from_thread(
                self._handle_status_update, txt
            )
        )
        self.call_from_thread(
            self._add_system_message,
            f"wake word mode active  ·  say '{ASSISTANT_NAME}' to activate",
        )

        while self.current_mode == "wakeword" and not _mode_switch_event.is_set():
            self.call_from_thread(self._clear_mic_status)
            self.call_from_thread(self._update_status, "wake word  ·  say AVA")
            detector.listen_for_wakeword()

            chime.play()
            start_new_conversation()
            reset_messages()
            self.call_from_thread(self._set_conversation_active, True)
            self.call_from_thread(self._add_system_message, "yes, sir?")

            success, filename = recorder.record(timeout=5)
            if not success:
                self.call_from_thread(self._clear_mic_status)
                self.call_from_thread(
                    self._add_system_message,
                    "didn't catch that  ·  going back to sleep",
                )
                save_current_conversation()
                detector.reset()
                self.call_from_thread(self._set_conversation_active, False)
                self.call_from_thread(
                    self._update_status, "wake word  ·  say AVA"
                )
                continue

            transcription = recorder.get_whisper_result(timeout=15) or ""
            if not transcription.strip():
                self.call_from_thread(self._clear_mic_status)
                self.call_from_thread(
                    self._add_system_message,
                    "didn't hear anything  ·  going back to sleep",
                )
                save_current_conversation()
                detector.reset()
                self.call_from_thread(self._set_conversation_active, False)
                self.call_from_thread(
                    self._update_status, "wake word  ·  say AVA"
                )
                continue

            while self.current_mode == "wakeword" and not _mode_switch_event.is_set():
                if not main_runner.is_set():
                    transcription = transcription.strip()
                    if not transcription:
                        break

                    self.call_from_thread(self._add_user_message, transcription)
                    self.call_from_thread(self._update_status, "thinking...")
                    self.call_from_thread(self._set_processing, True)

                    if any(cmd in transcription.lower() for cmd in SHUTUP_COMMANDS):
                        resp = random.choice(EXIT_RESPONSES)
                        self.call_from_thread(self._speak_to_ui, resp)
                        break

                    self._last_local_dispatched_text = transcription.strip()
                    full = f"{USER_NAME}: {transcription}"
                    add_message(role="user", content=full, tool_id="")

                    # Freeze noise floor while TTS plays - prevents speaker bleed
                    # from inflating the threshold and killing second-utterance sensitivity
                    recorder.freeze_noise_floor = True
                    main_running.wait(timeout=5.0)
                    if _mode_switch_event.is_set():
                        recorder.freeze_noise_floor = False
                        break
                    while main_running.is_set() and not _mode_switch_event.is_set():
                        time.sleep(0.01)
                    if _mode_switch_event.is_set():
                        recorder.freeze_noise_floor = False
                        break
                    recorder.freeze_noise_floor = False

                    # Check if we're expecting follow-up from the model (work in progress)
                    from core.AppStates import expecting_followup, work_in_progress
                    if expecting_followup.is_set() or work_in_progress.is_set():
                        # Extend timeout significantly for work in progress
                        timeout = max(tts_piper.get_last_duration() + 7, 45)  # Minimum 45 seconds
                        print(f"[TUI WakewordMode] Extended timeout to {timeout}s (work in progress)")

                        # Clear the flag now that we're waiting
                        expecting_followup.clear()
                    else:
                        timeout = tts_piper.get_last_duration() + 7

                    continued, _ = recorder.record(timeout=timeout)
                    if continued:
                        stop_event.clear()
                        transcription = recorder.get_whisper_result(timeout=15) or ""
                        if transcription.strip():
                            continue
                        else:
                            break
                    else:
                        # If we were expecting follow-up but timed out, try one more time
                        if work_in_progress.is_set():
                            work_in_progress.clear()
                            print("[TUI WakewordMode] Work in progress timed out, giving extra grace period...")
                            timeout = 30  # Additional grace period
                            continued, _ = recorder.record(timeout=timeout)
                            if continued:
                                stop_event.clear()
                                transcription = recorder.get_whisper_result(timeout=15) or ""
                                if transcription.strip():
                                    continue
                        stop_event.clear()
                        break

            save_current_conversation()
            detector.reset()
            self.call_from_thread(self._set_conversation_active, False)
            self.call_from_thread(self._add_system_message, "going back to sleep")

    def _set_conversation_active(self, val: bool) -> None:
        self.conversation_active = val

    # ── UI helpers ────────────────────────────────────────────────────────────

    def _on_stream_chunk(self, delta: str) -> None:
        if not delta:
            return
        if self._streaming_widget is None:
            self._hide_generating_indicator()
            self._streaming_text = ""
            self._streaming_ts = datetime.now().strftime("%H:%M")
            self._streaming_widget = ChatMessage(
                f"[bold]{ASSISTANT_NAME}[/]  [dim]{self._streaming_ts}[/]\n",
                classes="assistant-msg",
            )
            self._mount_chat_child(self._streaming_widget)

        self._streaming_text += delta
        body = escape(self._streaming_text)
        try:
            self._streaming_widget.update(
                f"[bold]{ASSISTANT_NAME}[/]  [dim]{self._streaming_ts}[/]\n{body}"
            )
        except Exception:
            pass
        self._scroll_to_bottom()

    def _finalize_streaming(self) -> None:
        self._streaming_widget = None
        self._streaming_text = ""
        self._streaming_ts = ""

    def _speak_to_ui(self, text: str, gen: int | None = None) -> None:
        # Interrupt any currently playing utterance. stop_event alone can race
        # with clear(); force the audio device stop as well.
        stop_event.set()
        try:
            import sounddevice as sd

            sd.stop()
        except Exception:
            pass
        try:
            from utils import tts_piper

            # If a newer gen was already reserved by speak(), waiting on
            # play_done would block on the *new* session — only wait when we
            # did not pre-reserve.
            if gen is None:
                tts_piper.wait_until_done(timeout=1.5)
            else:
                time.sleep(0.05)
        except Exception:
            pass

        if self._streaming_widget is not None:
            ts = self._streaming_ts or datetime.now().strftime("%H:%M")
            body = escape(text or "")
            try:
                self._streaming_widget.update(
                    f"[bold]{ASSISTANT_NAME}[/]  [dim]{ts}[/]\n{body}"
                )
            except Exception:
                pass
            self._finalize_streaming()
            self._scroll_to_bottom()
        else:
            self._add_assistant_message(text)
        stop_event.clear()

        if self.current_mode != "text":
            from utils import tts_piper

            # gen may already be reserved by _TUIResponseLoopHandler.speak
            if gen is None:
                gen = tts_piper.note_speech_started()
            threading.Thread(
                target=tts_piper.speak,
                args=(text, stop_event, gen),
                daemon=True,
            ).start()

    def _mount_chat_child(self, widget) -> None:
        """Mount into the chat log, keeping the generating strip at the bottom."""
        log = self.query_one("#chat-log", Vertical)
        try:
            indicator = self.query_one("#typing-indicator", GeneratingIndicator)
            log.mount(widget, before=indicator)
        except NoMatches:
            log.mount(widget)

        # Cap chat widgets in active DOM to prevent unbounded layout lag
        children = [c for c in log.children if not isinstance(c, GeneratingIndicator)]
        if len(children) > 100:
            for old in children[:15]:
                try:
                    old.remove()
                except Exception:
                    pass

    def _add_user_message(self, text: str) -> None:
        self._finalize_streaming()
        self._last_mounted_user_text = (text or "").strip()
        self._last_mounted_user_time = time.time()
        self._clear_mic_status()
        ts = datetime.now().strftime("%H:%M")
        # Escape body only — labels use intentional Rich markup
        body = escape(text or "")
        self._mount_chat_child(
            ChatMessage(
                f"[bold]{USER_NAME}[/]  [dim]{ts}[/]\n{body}",
                classes="user-msg",
            )
        )
        self._scroll_to_bottom()

    def _add_assistant_message(self, text: str) -> None:
        ts = datetime.now().strftime("%H:%M")
        body = escape(text or "")
        self._mount_chat_child(
            ChatMessage(
                f"[bold]{ASSISTANT_NAME}[/]  [dim]{ts}[/]\n{body}",
                classes="assistant-msg",
            )
        )
        self._scroll_to_bottom()

    def _on_tool_running(self, func_name: str, args: dict | str) -> None:
        """UI hook when a tool starts: spinner panel + generating label."""
        self._finalize_streaming()
        # Keep the generating strip but retarget it to the tool name
        self._show_generating_indicator(f"tool · {func_name}")
        self._add_tool_call(func_name, args, "")

    def _add_tool_call(
        self, func_name: str, args: dict | str, result: str = ""
    ) -> None:
        panel = ToolCallPanel(func_name, args, result)
        self._mount_chat_child(panel)
        self._scroll_to_bottom()

    def _update_last_tool_result(self, result: str) -> None:
        log = self.query_one("#chat-log", Vertical)
        panels = log.query(ToolCallPanel)
        if panels:
            last_panel = list(panels)[-1]
            res_str = str(result or "").strip().lower()
            err = bool(
                res_str
                and (
                    res_str.startswith(("error", "[error]", "failed", "[failed]"))
                    or "traceback" in res_str
                )
            )
            if hasattr(last_panel, "set_result"):
                last_panel.set_result(str(result) if result else "done", error=err)
            self._show_generating_indicator("generating")
            self._update_status("generating...")
            self._scroll_to_bottom()
        # Nudge tasks sidebar after tool that may have spawned work
        try:
            self.query_one("#tasks-sidebar", TasksSidebar).refresh_list()
        except NoMatches:
            pass

    def _add_system_message(self, text: str) -> None:
        """Faded ephemeral line (theme, tips, status). Hide with /quiet or ctrl+l."""
        clean = " ".join(str(text or "").split())
        clean = escape(clean)
        self._mount_chat_child(
            ChatMessage(f"[dim]· {clean}[/]", classes="system-msg")
        )
        # Only auto-scroll system noise when the log is visible
        if self.system_log_visible:
            self._scroll_to_bottom()

    def action_toggle_system_log(self) -> None:
        """Show or hide faded system log lines in the chat."""
        self.system_log_visible = not self.system_log_visible
        self._apply_system_log_visibility()
        state = "shown" if self.system_log_visible else "hidden"
        self._update_status(f"system log {state}  ·  /quiet to toggle")

    def _apply_system_log_visibility(self) -> None:
        try:
            log = self.query_one("#chat-log", Vertical)
        except NoMatches:
            return
        if self.system_log_visible:
            log.remove_class("-quiet")
        else:
            log.add_class("-quiet")

    def _add_separator(self) -> None:
        self._mount_chat_child(Rule())
        self._scroll_to_bottom()

    def _mic_strip(self) -> MicStrip | None:
        try:
            return self.query_one("#mic-strip", MicStrip)
        except NoMatches:
            return None

    def _handle_status_update(self, txt: str) -> None:
        """Voice pipeline → mic strip above input (+ status bar). Never chat."""
        if not txt:
            return

        mic = self._mic_strip()

        # Real mic RMS — drives the meter above the input
        if txt.startswith("Level: "):
            try:
                level = float(txt[7:].strip())
            except ValueError:
                return
            if mic is not None:
                mic.push_level(level)
            self._push_status_level(level)
            return

        if txt.startswith("Hearing: "):
            partial = txt[9:].strip()
            self._live_partial = partial
            if mic is not None:
                mic.set_hearing(partial)
            snippet = partial[:56] + ("…" if len(partial) > 56 else "")
            self._update_status(
                f"hearing  ·  {snippet}" if snippet else "hearing"
            )
            return

        if txt == "Recording...":
            self._live_partial = ""
            if mic is not None:
                mic.set_recording()
            self._update_status("recording")
            return

        if txt in ("Waiting for voice...", "Idle"):
            self._clear_mic_status()
            mode = MODE_LABELS.get(self.current_mode, self.current_mode).lower()
            if self.conversation_active:
                self._update_status(f"your turn  ·  {mode}")
            elif self.current_mode == "wakeword":
                self._update_status("wake word  ·  say AVA")
            else:
                self._update_status(f"ready  ·  {mode} mode")
            return

        # Legacy: confirmed speech → recording
        if txt == "[Listening] Speech detected...":
            if mic is not None:
                mic.set_recording()
            self._update_status("recording")
            return

        if txt == "Transcribing with Whisper...":
            if mic is not None:
                mic.set_transcribing()
            self._update_status("transcribing  ·  whisper")
            return

        self._update_status(txt)

    def _push_status_level(self, level: float) -> None:
        try:
            bar = self.query_one("#status-bar", StatusBar)
        except NoMatches:
            return
        samples = list(bar.level_samples or [])
        samples.append(max(0.0, float(level)))
        if len(samples) > 12:
            samples = samples[-12:]
        bar.level_samples = samples
        bar.mic_active = True
        if not (bar.status_text or "").startswith(("hearing", "recording", "transcrib")):
            bar.status_text = "recording"

    def _clear_mic_status(self) -> None:
        self._live_partial = ""
        mic = self._mic_strip()
        if mic is not None:
            mic.set_idle()
        try:
            bar = self.query_one("#status-bar", StatusBar)
            bar.mic_active = False
            bar.level_samples = []
        except NoMatches:
            pass

    def _update_status(self, text: str) -> None:
        try:
            bar = self.query_one("#status-bar", StatusBar)
            bar.status_text = text
        except NoMatches:
            pass

    def _sync_hub_connection(self) -> None:
        try:
            from core.device_client import get_connection_status
            bar = self.query_one("#status-bar", StatusBar)
            bar.conn_status = get_connection_status()
        except Exception:
            pass

    def _scroll_to_bottom(self) -> None:
        try:
            scroll = self.query_one("#chat-scroll", VerticalScroll)
            self.call_after_refresh(scroll.scroll_end, animate=False)
        except NoMatches:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════


def run_tui(start_mode: str = "text", start_theme: str = "chatgpt") -> None:
    """Launch the AVA TUI."""
    try:
        from core.server_manager import ensure_server_running

        ensure_server_running()
    except Exception as e:
        print(f"[Server] Check failed: {e}")

    app = AVAApp(start_mode=start_mode, start_theme=start_theme)
    app.run()


if __name__ == "__main__":
    run_tui()

