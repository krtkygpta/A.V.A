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
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

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
from core.messageHandler import add_message, reset_messages
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

FLEXOKI_LIGHT = Theme(
    name="flexoki-light",
    primary="#205ea6",
    secondary="#24837b",
    background="#fffcf0",
    surface="#f2f0e5",
    panel="#e6e4d9",
    boost="#4385be",
    foreground="#100f0f",
    success="#66800b",
    warning="#ad8301",
    error="#af3029",
    accent="#bc5215",
    dark=False,
)

ORANGES = Theme(
    name="oranges",
    primary="#ff9f1c",
    secondary="#ff6b35",
    background="#0c0a08",
    surface="#161210",
    panel="#221c16",
    boost="#ffb347",
    foreground="#fff1e0",
    success="#8fce5b",
    warning="#ffd166",
    error="#ff5a5f",
    accent="#ff7a45",
    dark=True,
)

# Soft blue night — unique to AVA
MIDNIGHT = Theme(
    name="midnight",
    primary="#7aa2f7",
    secondary="#bb9af7",
    background="#070a12",
    surface="#0e1422",
    panel="#171f33",
    boost="#89b4fa",
    foreground="#c8d3f5",
    success="#9ece6a",
    warning="#e0af68",
    error="#f7768e",
    accent="#7dcfff",
    dark=True,
)

# Warm paper + ink (light companion to ava)
AVA_LIGHT = Theme(
    name="ava-light",
    primary="#6d28d9",
    secondary="#1d4ed8",
    background="#fbfcfe",
    surface="#f1f5fb",
    panel="#e4eaf3",
    boost="#8b5cf6",
    foreground="#0f172a",
    success="#15803d",
    warning="#a16207",
    error="#b91c1c",
    accent="#0e7490",
    dark=False,
)

# Synthwave / cyberpunk neon
SYNTHWAVE = Theme(
    name="synthwave",
    primary="#ff7edb",
    secondary="#36f9f6",
    background="#12091f",
    surface="#1c1033",
    panel="#2a1848",
    boost="#f97e72",
    foreground="#f5ebff",
    success="#72f1b8",
    warning="#fede5d",
    error="#fe4450",
    accent="#36f9f6",
    dark=True,
)

# Only register themes whose names are NOT already in Textual's builtins.
_ALL_CUSTOM_THEMES: list[Theme] = [
    AVA_THEME,
    AVA_LIGHT,
    EVERFOREST,
    FLEXOKI_LIGHT,
    MIDNIGHT,
    ORANGES,
    SYNTHWAVE,
]
_ALL_CUSTOM_THEMES = [t for t in _ALL_CUSTOM_THEMES if t.name not in BUILTIN_THEMES]

# Full catalog: all Textual builtins + customs, sorted A–Z by name
AVAILABLE_THEMES: list[str] = sorted(
    set(BUILTIN_THEMES.keys()) | {t.name for t in _ALL_CUSTOM_THEMES}
)

# Old settings keys → current canonical theme names
THEME_ALIASES: dict[str, str] = {
    "flexoki-dark": "flexoki",
    "gruvbox-dark": "gruvbox",
}

# Descriptions for every built-in + custom (picker shows these)
THEME_DESCRIPTIONS: dict[str, str] = {
    # Custom
    "ava": "AVA default — violet + cyan on deep navy",
    "ava-light": "AVA light — violet ink on cool paper",
    "everforest": "Everforest — moss greens, soft contrast",
    "flexoki-light": "Flexoki light — warm paper, ink blue",
    "midnight": "Midnight — Tokyo-night blues",
    "oranges": "Oranges — amber fire on charcoal",
    "synthwave": "Synthwave — hot pink / neon cyan",
    # Textual built-ins
    "atom-one-dark": "Atom One Dark",
    "atom-one-light": "Atom One Light",
    "catppuccin-frappe": "Catppuccin Frappé — soft dark",
    "catppuccin-latte": "Catppuccin Latte — light pastel",
    "catppuccin-macchiato": "Catppuccin Macchiato — medium dark",
    "catppuccin-mocha": "Catppuccin Mocha — cozy dark",
    "dracula": "Dracula",
    "flexoki": "Flexoki — inky dark (built-in)",
    "gruvbox": "Gruvbox — retro earthy",
    "monokai": "Monokai — classic syntax colors",
    "nord": "Nord — arctic blues",
    "rose-pine": "Rosé Pine — main dark",
    "rose-pine-dawn": "Rosé Pine Dawn — light",
    "rose-pine-moon": "Rosé Pine Moon — moonlit dark",
    "solarized-dark": "Solarized Dark",
    "solarized-light": "Solarized Light",
    "textual-ansi": "Textual ANSI — terminal palette",
    "textual-dark": "Textual Dark — default dark",
    "textual-light": "Textual Light — default light",
    "tokyo-night": "Tokyo Night",
}


def _resolve_theme_name(name: str | None) -> str:
    """Map alias / missing names to a valid theme; default ava."""
    if not name:
        return "ava"
    key = str(name).strip().lower()
    key = THEME_ALIASES.get(key, key)
    if key in AVAILABLE_THEMES:
        return key
    return "ava"


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
}

CONFIG_SECRET_KEYS = {
    "GROQ_API_KEY",
    "GOOGLE_AI_API_KEY",
    "WEATHER_API_KEY",
    "TAVILY_API_KEY",
    "pat",
}

AVA_ASCII = r"""
_____ ___  _______
\__  \\  \/ /\__  \
 / __ \\   /  / __ \_
(____  /\_/  (____  /
     \/           \/ """
MODES = ["text", "continuous", "wakeword"]
MODE_LABELS = {
    "text": "TEXT",
    "continuous": "CONTINUOUS",
    "wakeword": "WAKE WORD",
}
MODE_ICONS = {
    "text": "⌨",
    "continuous": "◉",
    "wakeword": "◎",
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
    SlashCommand("/history", "Toggle history sidebar", ("/chats", "/conversations")),
    SlashCommand("/taskside", "Toggle tasks sidebar", ("/sidebar",)),
    SlashCommand(
        "/quiet",
        "Hide or show system log lines (theme, tips, etc.)",
        ("/logs", "/sys"),
    ),
    SlashCommand("/settings", "Open settings", ("/config", "/set")),
    SlashCommand("/new", "Start a new conversation"),
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
        margin: 0 0 1 0;
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
            self._spin_timer = self.set_interval(0.08, self._tick_spinner)
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


class ModeSwitcher(Static):
    """Mode pills + description. Height must leave room for borders + button text."""

    DEFAULT_CSS = """
    ModeSwitcher {
        height: 3;
        width: 100%;
        layout: horizontal;
        background: $surface;
        border-bottom: solid $primary 15%;
        padding: 0 2;
        align-vertical: middle;
    }
    ModeSwitcher .mode-tag {
        width: 6;
        height: 1;
        content-align: left middle;
        color: $secondary;
        text-style: bold;
    }
    ModeSwitcher #mode-segment {
        width: auto;
        height: 1;
        layout: horizontal;
        align-vertical: middle;
    }
    ModeSwitcher #mode-segment Button {
        width: auto;
        min-width: 14;
        height: 1;
        min-height: 1;
        max-height: 1;
        margin: 0 1 0 0;
        padding: 0 1;
        border: none;
        background: $panel;
        color: $secondary;
        text-style: none;
        content-align: center middle;
    }
    ModeSwitcher #mode-segment Button:hover {
        color: $foreground;
        background: $boost;
        border: none;
    }
    ModeSwitcher #mode-segment Button:focus {
        border: none;
        text-style: none;
        color: $foreground;
    }
    ModeSwitcher #mode-segment Button.-primary {
        background: $primary;
        color: $background;
        text-style: bold;
        border: none;
    }
    ModeSwitcher #mode-segment Button.-primary:hover {
        background: $boost;
        color: $background;
        border: none;
    }
    ModeSwitcher #mode-segment Button.-primary:focus {
        background: $primary;
        color: $background;
        border: none;
    }
    ModeSwitcher .mode-desc {
        width: 1fr;
        height: 1;
        content-align: right middle;
        color: $secondary;
        text-style: none;
        padding: 0 0 0 1;
    }
    """

    current_mode: reactive[str] = reactive("text")

    def compose(self) -> ComposeResult:
        yield Static("mode", classes="mode-tag")
        with Horizontal(id="mode-segment"):
            for mode in MODES:
                is_active = mode == self.current_mode
                label = f"{MODE_ICONS[mode]} {MODE_LABELS[mode]}"
                yield Button(
                    label,
                    id=f"mode-{mode}",
                    variant="primary" if is_active else "default",
                    flat=True,
                )
        yield Static(
            self._desc_for(self.current_mode),
            id="mode-desc",
            classes="mode-desc",
        )

    def _desc_for(self, mode: str) -> str:
        return MODE_DESCRIPTIONS.get(mode, mode)

    def watch_current_mode(self, mode: str) -> None:
        try:
            self.query_one("#mode-desc", Static).update(self._desc_for(mode))
        except NoMatches:
            pass

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("mode-"):
            new_mode = button_id[5:]
            if new_mode in MODES:
                self.set_mode(new_mode)

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            return
        prev = self.current_mode
        self.current_mode = mode
        for m in MODES:
            try:
                btn = self.query_one(f"#mode-{m}", Button)
                btn.variant = "primary" if m == mode else "default"
            except NoMatches:
                pass
        try:
            self.query_one("#mode-desc", Static).update(self._desc_for(mode))
        except NoMatches:
            pass
        if mode != prev:
            self.post_message(self.ModeChanged(mode))

    class ModeChanged(Message):
        """Posted when user switches mode."""

        def __init__(self, mode: str) -> None:
            super().__init__()
            self.mode = mode


class StatusBar(Static):
    """Bottom status bar showing current state (+ optional live mic meter)."""

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $secondary;
    }
    """

    status_text: reactive[str] = reactive("")
    # Recent RMS samples from the mic (status bar meter only)
    level_samples: reactive[list] = reactive(list, always_update=True)
    mic_active: reactive[bool] = reactive(False)

    def render(self) -> str:
        text = self.status_text or ""
        low = text.lower()
        meter = ""
        if self.mic_active and self.level_samples:
            meter = f"  [bold cyan]{_level_bar(list(self.level_samples))}[/]"
        if "hearing" in low or (self.mic_active and "record" in low):
            return f"[bold green]●[/]  {text}{meter}"
        if "transcrib" in low:
            return f"[bold yellow]●[/]  {text}"
        if "thinking" in low or "generat" in low:
            return f"[bold magenta]●[/]  {text}"
        if "wake" in low:
            return f"[bold cyan]○[/]  {text}"
        if "ready" in low:
            return f"[dim]○[/]  {text}"
        if self.mic_active:
            return f"[bold green]●[/]  {text}{meter}"
        return text


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
        self._timer = self.set_interval(0.09, self._tick)

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
        width: 62;
        height: auto;
        max-height: 90%;
        background: $panel;
        border: solid $primary;
        padding: 1 2;
        layout: vertical;
    }
    #theme-dialog .dialog-title {
        height: 1;
        color: $primary;
        text-style: bold;
        margin: 0;
    }
    #theme-dialog .dialog-hint {
        height: 1;
        color: $secondary;
        text-style: none;
        margin: 0 0 1 0;
    }
    #theme-dialog #theme-preview {
        height: 2;
        padding: 0 1;
        margin: 0 0 1 0;
        background: $surface;
        border: solid $secondary;
        color: $foreground;
    }
    #theme-dialog #theme-list {
        height: 18;
        min-height: 12;
        max-height: 24;
        border: solid $secondary;
        background: $surface;
        padding: 0 0;
    }
    #theme-dialog #theme-list:focus {
        border: solid $primary;
    }
    #theme-dialog .dialog-footer {
        height: 1;
        margin: 1 0 0 0;
        color: $secondary;
        text-style: none;
    }
    #theme-dialog #theme-actions {
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
                f"🎨  themes  ({len(AVAILABLE_THEMES)})  ·  A–Z",
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
        height: 1;
        color: $primary;
        text-style: bold;
        margin: 0 0 0 0;
    }
    #settings-dialog .dialog-hint {
        height: 1;
        color: $secondary;
        text-style: none;
        margin: 0 0 1 0;
    }
    #settings-body {
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
            yield Static("⚙  settings", classes="dialog-title")
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
        yield Static("[bold]⌨  keyboard shortcuts[/]", classes="help-title")
        yield Static("General:", classes="help-section")
        yield Static("[bold]?[/]  Show this help", classes="help-row")
        yield Static("[bold]ctrl+q[/]  Quit", classes="help-row")
        yield Static("[bold]ctrl+n[/]  New conversation", classes="help-row")
        yield Static("[bold]ctrl+i[/]  Toggle settings", classes="help-row")
        yield Static("[bold]ctrl+f[/]  Search in chat", classes="help-row")
        yield Static("[bold]ctrl+d[/]  Full task dashboard", classes="help-row")
        yield Static("[bold]ctrl+b[/]  Toggle tasks sidebar", classes="help-row")
        yield Static("[bold]ctrl+h[/]  Toggle history sidebar", classes="help-row")
        yield Static("[bold]ctrl+t[/]  Theme list", classes="help-row")
        yield Static("[bold]ctrl+m[/]  Cycle mode", classes="help-row")
        yield Static("[bold]escape[/]  Focus input / close overlay", classes="help-row")
        yield Static(":", classes="help-section")
        yield Static("[bold]/help[/]  Show commands", classes="help-row")
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


def filter_slash_commands(query: str) -> list[SlashCommand]:
    """Return slash commands matching the current input (prefix filter)."""
    raw = query or ""
    # Once the user is typing arguments (any space), hide the palette
    if " " in raw:
        return []
    q = raw.strip().lower()
    if not q.startswith("/"):
        return []
    # Bare "/" shows everything
    if q == "/":
        return list(SLASH_COMMANDS)

    scored: list[tuple[int, SlashCommand]] = []
    seen: set[tuple[str, str, bool]] = set()
    for cmd in SLASH_COMMANDS:
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
        super().__init__(name, **kwargs)
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
        width: 22;
        min-width: 16;
        height: 1fr;
        background: $background;
        border-right: solid $panel;
        layout: vertical;
        padding: 1 0 0 0;
    }
    HistorySidebar .sidebar-title {
        height: 1;
        padding: 0 1;
        margin: 0 0 1 0;
        color: $secondary;
        text-style: bold;
        background: transparent;
        content-align: left middle;
    }
    HistorySidebar #history-list {
        height: 1fr;
        background: $background;
        padding: 0 0 1 0;
        scrollbar-size: 1 1;
        scrollbar-background: $background;
        scrollbar-color: $secondary 25%;
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
        yield Static("Chats", classes="sidebar-title")
        yield VerticalScroll(id="history-list")

    def on_mount(self) -> None:
        self.refresh_list()
        self._refresh_timer = self.set_interval(12.0, self.refresh_list)

    def on_unmount(self) -> None:
        if self._refresh_timer is not None:
            try:
                self._refresh_timer.stop()
            except Exception:
                pass

    def _load_recent(self) -> list[dict]:
        try:
            from knowledge.ConversationManager import get_manager

            mgr = get_manager()
            try:
                mgr._load_index()
            except Exception:
                pass
            recent = mgr.get_recent_conversations(limit=50)
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
                    )[:50]
                ]
            return recent
        except Exception as e:
            print(f"[HistorySidebar] load failed: {e}")
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
            if len(name) > 20:
                name = name[:19] + "…"
            scroller.mount(HistoryEntry(cid, name))

    def action_open_highlighted(self) -> None:
        # Click-driven list; keep for binding compatibility
        pass


class TasksSidebar(Static):
    """Right sidebar: truly live background tasks + timer countdowns."""

    DEFAULT_CSS = """
    TasksSidebar {
        width: 30;
        min-width: 24;
        height: 1fr;
        background: $background;
        border-left: solid $panel;
        layout: vertical;
        padding: 0;
    }
    TasksSidebar .sidebar-title {
        height: 1;
        min-height: 1;
        max-height: 1;
        padding: 0 1;
        margin: 0;
        color: $secondary;
        text-style: bold;
        background: $background;
        content-align: left middle;
    }
    TasksSidebar #tasks-live-list {
        height: 1fr;
        padding: 0;
        margin: 0;
        background: $background;
        scrollbar-size: 1 1;
        scrollbar-background: $background;
        scrollbar-color: $secondary 30%;
    }
    TasksSidebar .task-live {
        width: 100%;
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
        background: transparent;
        border-left: solid $accent;
        color: $foreground;
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
        yield Static("TASKS", classes="sidebar-title")
        yield VerticalScroll(id="tasks-live-list")

    def on_mount(self) -> None:
        self.refresh_list()
        # 4 Hz so timer partial-blocks animate smoothly
        self._refresh_timer = self.set_interval(0.25, self.refresh_list)

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

        # Include ~4Hz timer quanta so the partial-block bar advances smoothly
        render_key = tuple(
            (
                t.get("id"),
                t.get("status"),
                # tenths of a second remaining for smooth bar motion
                int((_timer_remaining(t) or 0) * 10)
                if t.get("task_type") == "timer"
                and t.get("status") in ("running", "pending")
                else None,
                self._spin_idx if t.get("status") in ("running", "pending") else 0,
            )
            for t in display
        )
        if render_key == self._last_render_key:
            return
        self._last_render_key = render_key

        try:
            scroller = self.query_one("#tasks-live-list", VerticalScroll)
        except NoMatches:
            return
        scroller.remove_children()
        if not display:
            scroller.mount(Static("No active tasks", classes="task-empty"))
            return

        for task in display:
            scroller.mount(
                Static(self._format_row(task), classes=self._row_classes(task))
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
        name = str(task.get("name") or "task").strip()
        if len(name) > 24:
            name = name[:23] + "..."
        ttype = task.get("task_type") or "general"
        remaining = _timer_remaining(task)
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
                    lines.append(f"[dim]{result}[/]")
                else:
                    lines.append(f"[dim]{status_label}[/]")
            elif status == "failed":
                err = str(task.get("error") or "failed").replace("\n", " ").strip()
                if len(err) > 28:
                    err = err[:27] + "..."
                lines.append(f"[red]{err}[/]")
            elif status == "needs_input":
                lines.append("[yellow]needs input[/]")
            else:
                lines.append(f"[dim]{status_label} / {ttype}[/]")
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


class ConversationViewerModal(ModalScreen[None]):
    """Read-only modal to preview an old conversation (does not switch active chat)."""

    BINDINGS = [
        Binding("escape", "close_viewer", "Close", show=True),
        Binding("q", "close_viewer", "Close", show=False),
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
        color: $primary;
        text-style: bold;
    }
    #viewer-dialog .viewer-hint {
        height: 1;
        color: $secondary;
        text-style: none;
        margin: 0 0 1 0;
    }
    #viewer-dialog #viewer-scroll {
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
        margin: 0;
        padding: 0 2;
        border: none;
        background: $primary;
        color: $background;
        text-style: bold;
    }
    """

    def __init__(self, conversation_id: str, name: str = "", **kwargs):
        super().__init__(**kwargs)
        self._conversation_id = conversation_id
        self._name = name or conversation_id

    def compose(self) -> ComposeResult:
        with Vertical(id="viewer-dialog"):
            yield Static(
                f"📖  {self._name}",
                classes="viewer-title",
            )
            yield Static(
                f"Read only  -  {self._conversation_id}  -  Esc closes",
                classes="viewer-hint",
            )
            with VerticalScroll(id="viewer-scroll"):
                for role, text in self._load_messages():
                    # User/tool content may include [brackets] — never parse as markup
                    yield Static(text, classes=f"viewer-msg {role}", markup=False)
            with Horizontal(id="viewer-actions"):
                yield Button(
                    "close", id="btn-viewer-close", variant="primary", flat=True
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

    @on(Button.Pressed, "#btn-viewer-close")
    def on_close_pressed(self) -> None:
        self.action_close_viewer()

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
        height: 1;
        color: $primary;
        text-style: bold;
        padding: 0;
        margin: 0 0 1 0;
    }
    #tasks-dialog .tasks-hint {
        height: 1;
        color: $secondary;
        text-style: none;
        margin: 0 0 1 0;
    }
    #tasks-dialog #tasks-scroll {
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
        height: 3;
        padding: 0 1;
        color: $secondary;
        text-style: none;
        content-align: left middle;
    }
    #tasks-dialog #tasks-actions {
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
        scroller.remove_children()
        if not tasks:
            scroller.mount(
                Static("No background tasks yet.", classes="tasks-empty")
            )
            return
        for task in tasks:
            status = str(task.get("status") or "")
            scroller.mount(
                Static(
                    self._format_task(task),
                    classes=f"task-row {status}",
                )
            )

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
        ttype = task.get("task_type") or "general"
        status_label = str(status).replace("_", " ")
        conv = task.get("conversation_name") or "no conversation"
        created = str(task.get("created_at", ""))[11:19]
        completed = (
            str(task.get("completed_at", ""))[11:19]
            if task.get("completed_at")
            else ""
        )
        timing = created + (f"  →  {completed}" if completed else "")

        lines = [
            f"{glyph} {name}",
            f"[dim]{status_label} / {ttype} / {tid}[/]",
        ]
        if remaining is not None and status in ("running", "pending"):
            lines.append(f"{_format_countdown(remaining)} remaining")
        if timing:
            lines.append(f"[dim]{timing}  -  {conv}[/]")
        elif conv:
            lines.append(f"[dim]{conv}[/]")
        if result:
            lines.append(result)
        return "\n".join(lines)

    @on(Button.Pressed, "#btn-tasks-close")
    def on_close_pressed(self) -> None:
        self.action_close_tasks()

    def action_close_tasks(self) -> None:
        self.dismiss(None)


# Back-compat name used in type hints / older call sites
TaskDashboard = TaskDashboardModal


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
    padding: 1 0;
    background: $background;
    scrollbar-size: 1 1;
    scrollbar-background: $background;
    scrollbar-color: $primary 30%;
}
#chat-log {
    width: 100%;
    height: auto;
    padding: 0 2;
}
/* Splash banner stays; system log lines can be muted via /quiet */
#chat-log .splash {
    color: $foreground 45%;
    margin: 0 0 1 0;
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
    padding: 0 2;
    background: $background;
    border-top: solid $surface;
    layout: horizontal;
    align-vertical: middle;
}
#user-input {
    width: 100%;
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
    max-height: 12;
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

    def on_processing_start(self):
        self.app.call_from_thread(self.app._set_processing, True)
        self.app.call_from_thread(self.app._update_status, "thinking...")

    def on_tool_call_start(self, func_name: str, args: dict):
        self.app.call_from_thread(
            self.app._update_status, f"tool  ·  {func_name}"
        )
        self.app.call_from_thread(
            self.app._on_tool_running, func_name, args
        )

    def on_tool_call_result(self, result: str):
        self.app.call_from_thread(
            self.app._update_last_tool_result, result
        )

    def on_processing_end(self):
        self.app.call_from_thread(self.app._set_processing, False)

    def on_background_task_notify(self, summary: str):
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

    # ── Sidebars ──────────────────────────────────────────────────────────────

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

        def _on_close(_result: None) -> None:
            self.viewer_open = False
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

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield ModeSwitcher(id="mode-switcher")
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
                yield TasksSidebar(id="tasks-sidebar")
        yield StatusBar(id="status-bar")
        # No built-in "Open command palette" control — AVA uses / commands
        yield Footer(show_command_palette=False)

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
            switcher = self.query_one(ModeSwitcher)
            switcher.set_mode(self.current_mode)
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

        self.query_one("#user-input", Input).focus()

    def on_ready(self) -> None:
        if self.current_mode != "text":
            self._start_voice_mode(self.current_mode)

    # ── Mode switching ────────────────────────────────────────────────────────

    def on_mode_switcher_mode_changed(self, message: ModeSwitcher.ModeChanged) -> None:
        self._switch_mode(message.mode)

    def action_cycle_mode(self) -> None:
        idx = MODES.index(self.current_mode) if self.current_mode in MODES else 0
        new_mode = MODES[(idx + 1) % len(MODES)]
        try:
            switcher = self.query_one(ModeSwitcher)
            switcher.set_mode(new_mode)
        except NoMatches:
            self._switch_mode(new_mode)

    def _switch_mode(self, new_mode: str) -> None:
        old_mode = self.current_mode
        if new_mode == old_mode:
            return
        self.current_mode = new_mode
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
            for cmd in SLASH_COMMANDS:
                self._add_system_message(f"{cmd.display_cmd()}  —  {cmd.description}")
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
                    try:
                        switcher = self.query_one(ModeSwitcher)
                        switcher.set_mode(mode_arg)
                    except NoMatches:
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

                        self.call_from_thread(self._add_user_message, transcription)

                        if any(cmd in transcription.lower() for cmd in SHUTUP_COMMANDS):
                            resp = random.choice(EXIT_RESPONSES)
                            self.call_from_thread(self._add_assistant_message, resp)
                            break

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
                            transcription = str(recorder.get_whisper_result(timeout=15))
                            continue
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
                                    transcription = str(recorder.get_whisper_result(timeout=15))
                                    continue
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
                    self.call_from_thread(self._add_user_message, transcription)

                    if any(cmd in transcription.lower() for cmd in SHUTUP_COMMANDS):
                        resp = random.choice(EXIT_RESPONSES)
                        self.call_from_thread(self._speak_to_ui, resp)
                        break

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

    def _add_user_message(self, text: str) -> None:
        self._clear_mic_status()
        ts = datetime.now().strftime("%H:%M")
        # Escape body only — labels use intentional Rich markup
        try:
            from rich.markup import escape as _esc

            body = _esc(text or "")
        except Exception:
            body = text or ""
        self._mount_chat_child(
            ChatMessage(
                f"[bold]{USER_NAME}[/]  [dim]{ts}[/]\n{body}",
                classes="user-msg",
            )
        )
        self._scroll_to_bottom()

    def _add_assistant_message(self, text: str) -> None:
        ts = datetime.now().strftime("%H:%M")
        try:
            from rich.markup import escape as _esc

            body = _esc(text or "")
        except Exception:
            body = text or ""
        self._mount_chat_child(
            ChatMessage(
                f"[bold]{ASSISTANT_NAME}[/]  [dim]{ts}[/]\n{body}",
                classes="assistant-msg",
            )
        )
        self._scroll_to_bottom()

    def _on_tool_running(self, func_name: str, args: dict | str) -> None:
        """UI hook when a tool starts: spinner panel + generating label."""
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
            err = bool(
                result
                and (
                    str(result).lower().startswith("error")
                    or "traceback" in str(result).lower()
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
        try:
            from rich.markup import escape as _esc

            clean = _esc(clean)
        except Exception:
            pass
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

    def _scroll_to_bottom(self) -> None:
        try:
            scroll = self.query_one("#chat-scroll", VerticalScroll)
            self.call_after_refresh(scroll.scroll_end, animate=False)
        except NoMatches:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════


def run_tui(start_mode: str = "text", start_theme: str = "ava") -> None:
    """Launch the AVA TUI."""
    app = AVAApp(start_mode=start_mode, start_theme=start_theme)
    app.run()
