"""
AVA Terminal UI — Textual-based TUI inspired by Claude Code.

Provides:
  - Rich scrollable chat log with user / assistant / tool messages
  - Input bar at the bottom
  - Mode switcher (continuous, text, wakeword)
  - Tool-call display with collapsible panels
  - Status bar showing current state
  - Theme switching (AVA dark + Textual built-in themes)
"""

from __future__ import annotations

import os
import json
import random
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.reactive import reactive
from textual.theme import Theme
from textual.widgets import (
    Collapsible,
    Button,
    Footer,
    Header,
    Input,
    Label,
    Rule,
    Static,
)

if TYPE_CHECKING:
    pass

# ── Import AVA internals (same ones __main__.py uses) ───────────────────────
from config import USER_NAME, ASSISTANT_NAME
from core.AppStates import main_runner, stop_event, voice_stop_event, wakeword_stop_event
_mode_switch_event = threading.Event()
from core.FuncHandler import handle_tool_call
from core.generate import generate_response
from core.messageHandler import add_message, reset_messages
from core.TaskManager import CompletionQueue, check_and_format_completions
from knowledge.ConversationManager import start_new_conversation, save_current_conversation

# ── Import theme definitions ──────────────────────────────────────────────────
"""
AVA Theme definitions.
Provides custom "AVA" theme and configuration for other Textual themes.
"""

from textual.theme import Theme

# Custom AVA theme - preserves original color scheme
AVA_THEME = Theme(
    name="ava",
    primary="#a78bfa",
    secondary="#4f9cf9",
    background="#0d1117",
    surface="#0a0f1a",
    panel="#131929",
    boost="#bd93f9",
    foreground="#e2e8f0",
    success="#4ade80",
    warning="#facc15",
    error="#ef4444",
    accent="#38bdf8",
)

FLEXOKI_DARK = Theme(
    name="flexoki-dark",
    primary="#205EA6",
    secondary="#3AA99F",
    background="#1C1B1A",
    surface="#282726",
    panel="#343331",
    boost="#4385BE",
    foreground="#CECDC3",
    success="#66800B",
    warning="#AD8301",
    error="#AF3029",
    accent="#DA702C",
)

FLEXOKI_LIGHT = Theme(
    name="flexoki-light",
    primary="#205EA6",
    secondary="#3AA99F",
    background="#FFFCF0",
    surface="#F2F0E5",
    panel="#E6E4D9",
    boost="#4385BE",
    foreground="#403E3C",
    success="#66800B",
    warning="#AD8301",
    error="#AF3029",
    accent="#DA702C",
)

EVERFOREST = Theme(
    name="everforest",
    primary="#83C092",
    secondary="#7FBBB3",
    background="#2D353B",
    surface="#343F44",
    panel="#3D484D",
    boost="#A7C080",
    foreground="#D3C6AA",
    success="#A7C080",
    warning="#DBBC7F",
    error="#E67E80",
    accent="#E69875",
)

CATPPUCCIN_MOCHA = Theme(
    name="catppuccin-mocha",
    primary="#cba6f7",
    secondary="#89b4fa",
    background="#1e1e2e",
    surface="#313244",
    panel="#45475a",
    boost="#b4befe",
    foreground="#cdd6f4",
    success="#a6e3a1",
    warning="#f9e2af",
    error="#f38ba8",
    accent="#94e2d5",
)

CATPPUCCIN_LATTE = Theme(
    name="catppuccin-latte",
    primary="#8839ef",
    secondary="#04a5e5",
    background="#eff1f5",
    surface="#ccd0da",
    panel="#bcc0cc",
    boost="#7287fd",
    foreground="#4c4f69",
    success="#40a02b",
    warning="#df8e1d",
    error="#d20f39",
    accent="#179299",
)

GRUVBOX_DARK = Theme(
    name="gruvbox-dark",
    primary="#fabd2f",
    secondary="#83a598",
    background="#282828",
    surface="#3c3836",
    panel="#504945",
    boost="#fabd2f",
    foreground="#ebdbb2",
    success="#b8bb26",
    warning="#fabd2f",
    error="#fb4934",
    accent="#8ec07c",
)

MONOKAI = Theme(
    name="monokai",
    primary="#f92672",
    secondary="#66d9ef",
    background="#272822",
    surface="#3e3d32",
    panel="#49483e",
    boost="#fd5ff3",
    foreground="#f8f8f2",
    success="#a6e22e",
    warning="#e6db74",
    error="#f92672",
    accent="#a6e22e",
)

ORANGES = Theme(
    name="oranges",
    primary="#FF8C00",
    secondary="#FF6B00",
    background="#000000",
    surface="#0A0A0A",
    panel="#1A1A1A",
    boost="#FFA500",
    foreground="#F5E6D3",
    success="#9ACD32",
    warning="#FFD700",
    error="#FF6347",
    accent="#FF7F50",
)

# ── Register custom themes ────────────────────────────────────────────────────
_ALL_CUSTOM_THEMES = [
    AVA_THEME,
    FLEXOKI_DARK,
    FLEXOKI_LIGHT,
    EVERFOREST,
    CATPPUCCIN_MOCHA,
    CATPPUCCIN_LATTE,
    GRUVBOX_DARK,
    MONOKAI,
    ORANGES,
]

# Available built-in Textual themes
AVAILABLE_THEMES = [
    "ava",
    "nord",
    "dracula",
    "tokyo-night",
    "flexoki-dark",
    "flexoki-light",
    "everforest",
    "catppuccin-mocha",
    "catppuccin-latte",
    "gruvbox-dark",
    "monokai",
    "oranges",
]

THEME_DESCRIPTIONS = {
    "ava": "Custom AVA dark theme with purple accents",
    "nord": "Nord color palette",
    "dracula": "Dracula dark theme",
    "tokyo-night": "Tokyo Night color scheme",
    "flexoki-dark": "Flexoki dark - inky dark theme",
    "flexoki-light": "Flexoki light - warm paper theme",
    "everforest": "Everforest - green forest tones",
    "catppuccin-mocha": "Catppuccin Mocha - cozy dark",
    "catppuccin-latte": "Catppuccin Latte - light pastel",
    "gruvbox-dark": "Gruvbox - retro earthy tones",
    "monokai": "Monokai - classic syntax colors",
    "oranges": "Orange - warm citrus tones",
}
# ── Constants ────────────────────────────────────────────────────────────────
CONFIG_PATH = "settings.json"

CONFIG_DESCRIPTIONS = {
    "GROQ_API_KEY":       "Groq API key for LLM inference",
    "USER_NAME":          "Your name (used in prompts)",
    "ASSISTANT_NAME":     "Assistant's name",
    "AVA_SERVER_URL":     "AVA WebSocket server URL",
    "AVA_START_MODE":     "Startup mode: tui / continuous / wakeword",
    "GOOGLE_AI_API_KEY":  "Google AI (Gemini) API key",
    "WEATHER_API_KEY":    "OpenWeatherMap API key",
    "THEME":              "UI theme: ava, nord, dracula, tokyo-night",
}

CONFIG_SECRET_KEYS = {"GROQ_API_KEY", "GOOGLE_AI_API_KEY", "WEATHER_API_KEY"}

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

main_running = threading.Event()


# ═══════════════════════════════════════════════════════════════════════════════
# Custom Widgets
# ═══════════════════════════════════════════════════════════════════════════════

class ChatMessage(Static):
    """A single message bubble in the chat log."""

    DEFAULT_CSS = """
    ChatMessage {
        width: 100%;
        padding: 1 2;
        margin: 0 0 1 0;
    }
    ChatMessage.user-msg {
        background: $surface;
        border-left: thick $secondary;
        padding: 1 2 1 3;
        color: $foreground;
    }
    ChatMessage.assistant-msg {
        background: $surface;
        border-left: thick $primary;
        padding: 1 2 1 3;
        color: $foreground;
    }
    ChatMessage.tool-msg {
        background: $surface;
        border-left: thick $accent;
        padding: 1 2 1 3;
        color: $foreground;
    }
    ChatMessage.system-msg {
        background: transparent;
        color: $foreground;
        text-style: italic;
        padding: 0 2;
        margin: 0;
    }
    """


class ToolCallPanel(Static):
    """Shows a tool call with name, args, and result inside a Collapsible."""

    DEFAULT_CSS = """
    ToolCallPanel {
        width: 100%;
        margin: 0 0 1 0;
        background: $surface;
        border: none;
        opacity: 0.9;
    }
    ToolCallPanel Collapsible {
        background: $surface;
        border: none;
        padding: 0;
        margin: 0;
    }
    ToolCallPanel CollapsibleTitle {
        color: $warning;
        background: $panel;
        padding: 0 1;
        text-style: bold;
    }
    ToolCallPanel CollapsibleTitle:hover {
        background: $accent;
        color: $background;
    }
    ToolCallPanel .tool-header {
        color: $warning;
        text-style: bold;
        padding: 0 1;
    }
    ToolCallPanel .tool-args {
        color: $foreground;
        padding: 0 2 0 3;
        opacity: 0.8;
    }
    ToolCallPanel .tool-result {
        color: $accent;
        padding: 0 2 1 3;
    }
    """

    def __init__(self, func_name: str, args: dict | str, result: str = "", **kwargs):
        super().__init__(**kwargs)
        self._func_name = func_name
        self._args = args
        self._result = result

    def compose(self) -> ComposeResult:
        if isinstance(self._args, dict):
            try:
                args_str = json.dumps(self._args, indent=2)
            except Exception:
                args_str = str(self._args)
        else:
            args_str = str(self._args)

        with Collapsible(title=f"⚡ {self._func_name}", collapsed=True):
            if args_str and args_str != "{}":
                display_args = args_str[:500] + ("..." if len(args_str) > 500 else "")
                yield Static(f"{display_args}", classes="tool-args")
            if self._result:
                display_result = self._result[:300] + ("..." if len(self._result) > 300 else "")
                yield Static(f"↳ {display_result}", classes="tool-result")


class ModeSwitcher(Static):
    """Horizontal mode selector using Textual Button widgets."""

    DEFAULT_CSS = """
    ModeSwitcher {
        dock: top;
        height: 5;
        padding: 0 1;
        background: $surface;
        border-bottom: solid $panel;
    }
    ModeSwitcher Horizontal {
        height: 5;
        align-horizontal: left;
        align-vertical: middle;
    }
    ModeSwitcher Button {
        min-width: 16;
        height: 3;
        margin: 0 1 0 0;
        border: none;
    }
    ModeSwitcher Button:focus {
        border: none;
        color: $foreground;
    }
    ModeSwitcher Button.-primary {
        background: $primary;
        color: $background;
        text-style: bold;
        border: none;
    }
    """

    current_mode: reactive[str] = reactive("text")

    def compose(self) -> ComposeResult:
        with Horizontal():
            for mode in MODES:
                label = f"{MODE_ICONS[mode]}  {MODE_LABELS[mode]}"
                is_active = mode == self.current_mode
                yield Button(
                    label,
                    id=f"mode-{mode}",
                    variant="primary" if is_active else "default",
                )

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("mode-"):
            new_mode = button_id[5:]
            if new_mode in MODES:
                self.set_mode(new_mode)

    def set_mode(self, mode: str) -> None:
        self.current_mode = mode
        for m in MODES:
            try:
                btn = self.query_one(f"#mode-{m}", Button)
                btn.variant = "primary" if m == mode else "default"
            except NoMatches:
                pass
        self.post_message(self.ModeChanged(mode))

    class ModeChanged(Message):
        """Posted when user switches mode."""

        def __init__(self, mode: str) -> None:
            super().__init__()
            self.mode = mode


class StatusBar(Static):
    """Bottom status bar showing current state."""

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        padding: 0 2;
        background: $surface;
        color: $text-muted;
        border-top: solid $panel;
    }
    """

    status_text: reactive[str] = reactive("")

    def render(self) -> str:
        return self.status_text


class TypingIndicator(Static):
    """Animated typing indicator."""

    DEFAULT_CSS = """
    TypingIndicator {
        height: 2;
        padding: 0 2;
        color: $secondary;
    }
    """

    _dots = ["", ".", "..", "..."]
    _idx = 0

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._timer = None

    def on_mount(self) -> None:
        self._update_dots()

    def _update_dots(self) -> None:
        self._idx = (self._idx + 1) % 4
        self.update(f"typing{self._dots[self._idx]}")
        self._timer = self.set_timer(0.4, self._update_dots)

    def on_unmount(self) -> None:
        if self._timer:
            self._timer.stop()


class SettingsScreen(Static):
    """Inline settings panel that replaces chat-log content."""

    DEFAULT_CSS = """
    SettingsScreen {
        width: 100%;
        height: 1fr;
        layout: vertical;
        overflow: hidden;
        padding: 1 2;
    }
    SettingsScreen .settings-title {
        color: $primary;
        text-style: bold;
        margin-bottom: 1;
    }
    SettingsScreen .settings-hint {
        color: $secondary;
        text-style: italic;
        margin-bottom: 1;
    }
    SettingsScreen .setting-row {
        width: 100%;
        height: 3;
        margin-bottom: 1;
    }
    SettingsScreen .setting-label {
        width: 24;
        height: 3;
        content-align: left middle;
        color: $primary;
        text-style: bold;
    }
    SettingsScreen .setting-desc {
        width: 1fr;
        height: 3;
        content-align: left middle;
        color: $foreground;
        text-style: italic;
        padding: 0 1;
    }
    SettingsScreen .setting-input {
        width: 40;
        height: 3;
        border: round $secondary;
        background: $surface;
        color: $foreground;
        padding: 0 1;
    }
    SettingsScreen .setting-input:focus {
        border: round $primary;
    }
    SettingsScreen .settings-footer {
        color: $secondary;
        margin-top: 1;
        text-style: italic;
    }
    SettingsScreen .save-hint {
        color: $accent;
        margin-top: 0;
    }
    SettingsScreen #settings-buttons {
        width: 100%;
        align-horizontal: right;
        height: 3;
        margin-top: 1;
    }
    SettingsScreen #settings-buttons Button {
        width: 14;
        height: 3;
    }
    SettingsScreen #settings-scroll {
        width: 100%;
        height: 1fr;
    }
    """

    def __init__(self, config: dict, **kwargs):
        super().__init__(**kwargs)
        self._config = config
        self._inputs: dict[str, Input] = {}

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="settings-scroll"):
            yield Static("[bold]⚙  settings[/]", classes="settings-title")
            yield Static(
                "[italic]edit values below  ·  /settings or ctrl+i to close[/]",
                classes="settings-hint",
            )
            yield Rule()

            for key, val in self._config.items():
                desc = CONFIG_DESCRIPTIONS.get(key, "")
                is_secret = key in CONFIG_SECRET_KEYS
                placeholder = "••••••••" if (is_secret and val) else (str(val) if val else "not set")
                with Horizontal(classes="setting-row"):
                    yield Static(f"[bold]{key}[/]", classes="setting-label")
                    yield Static(f"{desc}", classes="setting-desc")
                    inp = Input(
                        value="" if (is_secret and val) else str(val),
                        placeholder=placeholder,
                        password=is_secret,
                        classes="setting-input",
                        id=f"cfg-{key}",
                    )
                    self._inputs[key] = inp
                    yield inp

            yield Rule()
            yield Static(
                "[italic]changes take effect after restart for most settings[/]",
                classes="settings-footer",
            )
        yield Horizontal(
            Button("save", id="btn-save", variant="primary"),
            Button("cancel", id="btn-cancel"),
            id="settings-buttons",
        )

    def collect_values(self) -> dict:
        """Read current input values, preserving old secrets if left blank."""
        result = dict(self._config)
        for key in self._config:
            try:
                inp = self.query_one(f"#cfg-{key}", Input)
                new_val = inp.value.strip()
                if key in CONFIG_SECRET_KEYS and not new_val:
                    pass  # keep existing secret
                else:
                    result[key] = new_val
            except NoMatches:
                pass
        return result

    @on(Button.Pressed, "#btn-save")
    def on_save(self) -> None:
        self.post_message(self.SaveRequested(self.collect_values()))

    @on(Button.Pressed, "#btn-cancel")
    def on_cancel(self) -> None:
        self.post_message(self.CancelRequested())

    class SaveRequested(Message):
        def __init__(self, config: dict) -> None:
            super().__init__()
            self.config = config

    class CancelRequested(Message):
        pass


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
        yield Static("[bold]ctrl+t[/]  Cycle theme", classes="help-row")
        yield Static("[bold]ctrl+m[/]  Cycle mode", classes="help-row")
        yield Static("[bold]escape[/]  Focus input", classes="help-row")
        yield Static(":", classes="help-section")
        yield Static("[bold]/help[/]  Show commands", classes="help-row")
        yield Static("[bold]/theme[/]  Next theme", classes="help-row")
        yield Static("[bold]/themes[/]  List themes", classes="help-row")
        yield Static("[bold]/mode text|continuous|wakeword[/]  Switch mode", classes="help-row")
        yield Static("[bold]/clear[/]  Clear chat", classes="help-row")
        yield Static("[bold]/settings[/]  Open settings", classes="help-row")
        yield Static("[bold]/new[/]  New conversation", classes="help-row")
        yield Static("[bold]/quit[/]  Exit", classes="help-row")
        yield Static("Press [bold]?[/] or [bold]escape[/] to close", classes="help-row")

    def action_close_help(self) -> None:
        self.remove()


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
# Main Application
# ═══════════════════════════════════════════════════════════════════════════════

MAIN_CSS = """
Screen {
    background: $background;
}
Header {
    background: $surface;
    color: $foreground;
    border-bottom: solid $secondary;
    height: 1;
}
Header .header--title {
    color: $primary;
    text-style: bold;
}
Header .header--sub-title {
    color: $foreground;
}
Header .header--clock {
    color: $foreground;
}
Footer {
    background: $surface;
    color: $foreground;
    opacity: 0.55;
}
Button.-primary {
    background: $primary;
    color: $background;
    border: none;
}
Button.-primary:hover {
    background: $boost;
    color: $background;
}
Button.-primary:focus {
    background: $primary;
    color: $background;
    border: none;
}
Footer .footer--key {
    color: $secondary;
    background: $surface;
}
Footer .footer--description {
    color: $foreground;
    background: $surface;
}
#app-grid {
    width: 100%;
    height: 1fr;
    background: $background;
}

#chat-scroll {
    width: 100%;
    height: 1fr;
    padding: 1 0;
    background: $background;
}

#input-bar {
    height: 4;
    padding: 0 0;
    background: $surface;
    border-top: solid $secondary;
    align-vertical: middle;
}
#chat-log {
    width: 100%;
    height: auto;
    padding: 0 1;
}
#user-input {
    width: 100%;
    background: $surface;
    color: $foreground;
}
#user-input:focus {
    background: $surface;
    border: tall $primary;
}
#user-input>.input--placeholder {
    color: $foreground;
}

Rule {
    color: transparent;
    margin: 0 0;
}
"""


class AVAApp(App):
    """AVA Terminal UI — Claude Code inspired with Textual theming."""

    CSS = MAIN_CSS
    TITLE = f"AVA  ·  {ASSISTANT_NAME}"
    SUB_TITLE = "personal intelligence"
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+n", "new_conversation", "New Chat", show=True),
        Binding("ctrl+m", "cycle_mode", "Cycle Mode", show=True),
        Binding("escape", "focus_input", "Focus Input", show=False),
        Binding("ctrl+i", "toggle_settings", "Settings", show=True),
        Binding("ctrl+t", "cycle_theme", "Cycle Theme", show=True),
        Binding("?", "toggle_help", "Help", show=True),
        Binding("ctrl+f", "toggle_search", "Search", show=True),
    ]
    settings_open: reactive[bool] = reactive(False)
    help_open: reactive[bool] = reactive(False)
    search_open: reactive[bool] = reactive(False)
    _settings_widget: "SettingsScreen | None" = None
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
        """Register all custom themes and set initial theme."""
        for theme in _ALL_CUSTOM_THEMES:
            self.register_theme(theme)

        config = self._load_config()
        theme = config.get("THEME", "ava")
        if theme not in AVAILABLE_THEMES:
            theme = "ava"
        self.app_theme = theme
        try:
            self.theme = theme
        except Exception:
            self.app_theme = "ava"
            self.theme = "ava"

    def action_cycle_theme(self) -> None:
        """Cycle through available themes."""
        current_idx = AVAILABLE_THEMES.index(self.app_theme) if self.app_theme in AVAILABLE_THEMES else 0
        next_idx = (current_idx + 1) % len(AVAILABLE_THEMES)
        next_theme = AVAILABLE_THEMES[next_idx]
        self._set_theme(next_theme)

    def _set_theme(self, theme_name: str) -> None:
        """Change the application theme."""
        if theme_name not in AVAILABLE_THEMES:
            self._add_system_message(f"theme '{theme_name}' not found")
            return
        
        self.app_theme = theme_name
        try:
            self.theme = theme_name
        except Exception:
            self.app_theme = "ava"
            self.theme = "ava"
            self._add_system_message("theme not available, using ava")
            return
        desc = THEME_DESCRIPTIONS.get(theme_name, "")
        self._add_system_message(f"theme → {theme_name}  ·  {desc}")
        config = self._load_config()
        config["THEME"] = theme_name
        self._save_config(config)

    # ── Settings management ───────────────────────────────────────────────────

    def action_toggle_settings(self) -> None:
        if self.settings_open:
            self._close_settings()
        else:
            self._open_settings()

    def _open_settings(self) -> None:
        if self.settings_open:
            return
        self.settings_open = True
        config = self._load_config()
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
        self._settings_widget = SettingsScreen(config, id="settings-panel")
        grid = self.query_one("#app-grid", Vertical)
        grid.mount(self._settings_widget, before=self.query_one("#input-bar"))
        self.set_focus(self._settings_widget)

    def _close_settings(self) -> None:
        if not self.settings_open:
            return
        self.settings_open = False
        try:
            panel = self.query_one("#settings-panel", SettingsScreen)
            panel.remove()
        except NoMatches:
            pass
        self._settings_widget = None
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
        grid = self.query_one("#app-grid", Vertical)
        grid.mount(HelpOverlay(id="help-overlay"), before=self.query_one("#input-bar"))

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

    def on_settings_screen_save_requested(self, event: SettingsScreen.SaveRequested) -> None:
        if self._save_config(event.config):
            self._add_system_message("settings saved  ·  restart to apply changes")
        self._close_settings()

    def on_settings_screen_cancel_requested(self, event: SettingsScreen.CancelRequested) -> None:
        self._close_settings()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield ModeSwitcher(id="mode-switcher")
        with Vertical(id="app-grid"):
            with VerticalScroll(id="chat-scroll"):
                yield Vertical(id="chat-log")
            with Horizontal(id="input-bar"):
                yield Input(
                    placeholder=f"message {ASSISTANT_NAME.lower()}...",
                    id="user-input",
                )
        yield StatusBar(id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        global _app_instance
        _app_instance = self

        # Register themes and set initial theme
        self._register_themes()

        # ASCII splash
        log = self.query_one("#chat-log", Vertical)
        log.mount(Static(
            f"[bold]{AVA_ASCII}[/]\n[italic]personal intelligence  ·  {ASSISTANT_NAME}[/]",
            classes="system-msg"
        ))

        try:
            switcher = self.query_one(ModeSwitcher)
            switcher.set_mode(self.current_mode)
        except NoMatches:
            pass
        self._update_status(f"ready  ·  {MODE_LABELS.get(self.current_mode, self.current_mode).lower()} mode")
        self._add_system_message(
            f"online  ·  {USER_NAME}  ·  {MODE_LABELS.get(self.current_mode, self.current_mode).lower()} mode"
        )
        self._add_system_message(f"theme: {self.app_theme}")

        threading.Thread(target=self._main_loop, daemon=True).start()

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
        self._add_system_message(f"mode → {MODE_LABELS.get(new_mode, new_mode).lower()}")
        self._update_status(f"ready  ·  {MODE_LABELS.get(new_mode, new_mode).lower()} mode")

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

    @on(Input.Submitted, "#user-input")
    def handle_input(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.clear()

        # Settings command
        if text.lower().startswith("/settings") or text.lower().startswith("/config") or text.lower().startswith("/set"):
            self.action_toggle_settings()
            return

        # Help command
        if text.lower() in {"/help", "/commands"}:
            self._add_system_message("available commands:")
            self._add_system_message("/help - show this help")
            self._add_system_message("/theme [name] - cycle/set theme")
            self._add_system_message("/themes - list themes")
            self._add_system_message("/mode text|continuous|wakeword - switch mode")
            self._add_system_message("/clear - clear chat log")
            self._add_system_message("/settings - open settings")
            self._add_system_message("/new - new conversation")
            self._add_system_message("/quit - exit")
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

        # Theme command
        if text.lower().startswith("/theme"):
            if text.lower() == "/theme":
                self.action_cycle_theme()
            elif text.lower().startswith("/theme "):
                theme_name = text[7:].strip().lower()
                if theme_name in AVAILABLE_THEMES:
                    self._set_theme(theme_name)
                else:
                    self._add_system_message(f"unknown theme: {theme_name}  ·  /themes for list")
            return

        # Themes list
        if text.lower() == "/themes":
            self._add_system_message("available themes:")
            for t in AVAILABLE_THEMES:
                desc = THEME_DESCRIPTIONS.get(t, "")
                marker = "◉" if t == self.app_theme else "○"
                self._add_system_message(f"  {marker} {t}  —  {desc}")
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
                    self._add_system_message(f"invalid mode: {mode_arg}  ·  text/continuous/wakeword")
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
        self.is_processing = True

        add_message(role="user", content=user_msg, tool_id="")

    # ── Conversation management ───────────────────────────────────────────────

    def action_new_conversation(self) -> None:
        if self.conversation_active:
            self._end_conversation()
        self._add_separator()
        self._add_system_message("new conversation")
        start_new_conversation()
        reset_messages()
        self.conversation_active = True
        self.query_one("#user-input", Input).focus()

    def _end_conversation(self) -> None:
        if not self.conversation_active:
            return
        save_current_conversation()
        self.conversation_active = False
        self._add_system_message("conversation saved")
        self._update_status(f"ready  ·  {MODE_LABELS.get(self.current_mode, self.current_mode).lower()} mode")

    def action_focus_input(self) -> None:
        self.query_one("#user-input", Input).focus()

    # ── Main response loop ────────────────────────────────────────────────────

    def _main_loop(self) -> None:
        while True:
            signaled = main_runner.wait(timeout=0.5)

            if not main_runner.is_set():
                completion_queue = CompletionQueue()
                if completion_queue.has_notifications():
                    completed_summary = check_and_format_completions()
                    if completed_summary:
                        self.call_from_thread(self._add_system_message, f"background task update: {completed_summary}")
                        add_message(
                            role="user",
                            content=f"[SYSTEM NOTIFICATION] {completed_summary}",
                            tool_id="",
                        )
                continue

            if main_runner.is_set():
                main_running.set()
                main_runner.clear()
                try:
                    response = generate_response()
                    tool_calls = response.get("tool_calls")
                    if tool_calls:
                        tool_call = tool_calls[0]
                        if isinstance(tool_call, dict):
                            tool_id = tool_call.get("id")
                            function_payload = tool_call.get("function", {}) or {}
                            args_raw = function_payload.get("arguments", "{}")
                            func_name = function_payload.get("name")
                        else:
                            tool_id = tool_call.id
                            args_raw = tool_call.function.arguments
                            func_name = tool_call.function.name

                        if isinstance(args_raw, str):
                            try:
                                args = json.loads(args_raw) if args_raw.strip() else {}
                            except json.JSONDecodeError:
                                args = args_raw
                        elif isinstance(args_raw, dict):
                            args = args_raw
                        else:
                            args = {}

                        if func_name == "inform_user_between_tool_calls":
                            if isinstance(args, dict):
                                arg_text = args.get("message", args.get("text", str(args)))
                            else:
                                arg_text = str(args)

                            self.call_from_thread(self._speak_to_ui, arg_text)
                            add_message(role="tool", content="informed the user", tool_id=tool_id)
                        else:
                            self.call_from_thread(
                                self._update_status, f"running  ·  {func_name}"
                            )
                            self.call_from_thread(
                                self._add_tool_call, func_name, args, ""
                            )
                            func_resp, t_id = handle_tool_call(tool_call)
                            self.call_from_thread(
                                self._update_last_tool_result, str(func_resp) if func_resp else "done"
                            )
                            add_message(role="tool", content=func_resp, tool_id=t_id)
                    else:
                        content = response.get("content")
                        if content:
                            self.call_from_thread(self._speak_to_ui, content)
                        else:
                            self.call_from_thread(
                                self._speak_to_ui, "I'm not sure how to respond to that."
                            )
                finally:
                    main_running.clear()
                    self.call_from_thread(self._set_processing, False)

    def _set_processing(self, val: bool) -> None:
        self.is_processing = val
        if val:
            self._show_typing_indicator()
        else:
            self._hide_typing_indicator()
        if not val:
            self._update_status(f"ready  ·  {MODE_LABELS.get(self.current_mode, self.current_mode).lower()} mode")

    def _show_typing_indicator(self) -> None:
        try:
            existing = self.query_one("#typing-indicator", TypingIndicator)
            return
        except NoMatches:
            pass
        try:
            log = self.query_one("#chat-log", Vertical)
            log.mount(TypingIndicator(id="typing-indicator"))
            self._scroll_to_bottom()
        except NoMatches:
            pass

    def _hide_typing_indicator(self) -> None:
        try:
            indicator = self.query_one("#typing-indicator", TypingIndicator)
            indicator.remove()
        except NoMatches:
            pass

    # ── Voice modes ───────────────────────────────────────────────────────────

    def _voice_continuous(self) -> None:
        from utils import stt_hybrid as stt, tts_piper
        tts_piper.init_tts()
        self.call_from_thread(self._add_system_message, "continuous listening active")

        recorder = stt.VoiceRecorder(status_callback=lambda txt: self.call_from_thread(self._handle_status_update, txt))
        while self.current_mode == "continuous" and not _mode_switch_event.is_set():
            success, filename = recorder.record()
            if _mode_switch_event.is_set():
                break
            if success:
                prompt = recorder.get_whisper_result(timeout=15)
                transcription = prompt if prompt is not None else ""

                if any(word in transcription.lower() for word in ["ava", "assistant", "eva", "ayva", "evaa"]):
                    self.call_from_thread(self._add_system_message, "wake word detected")
                    start_new_conversation()
                    reset_messages()
                    self.call_from_thread(self._set_conversation_active, True)

                    while self.current_mode == "continuous" and not _mode_switch_event.is_set():
                        while (main_runner.is_set() or main_running.is_set()) and not _mode_switch_event.is_set():
                            time.sleep(0.01)
                        if _mode_switch_event.is_set(): break

                        self.call_from_thread(self._add_user_message, transcription)

                        if any(cmd in transcription.lower() for cmd in SHUTUP_COMMANDS):
                            resp = random.choice(EXIT_RESPONSES)
                            self.call_from_thread(self._add_assistant_message, resp)
                            break

                        user_msg = f"{USER_NAME}: {transcription}"
                        add_message(role="user", content=user_msg, tool_id="")

                        main_running.wait(timeout=5.0)
                        if _mode_switch_event.is_set(): break
                        while main_running.is_set() and not _mode_switch_event.is_set():
                            time.sleep(0.01)
                        if _mode_switch_event.is_set(): break

                        timeout = tts_piper.get_last_duration() + 7
                        continued, _ = recorder.record(timeout=timeout, silence_duration=2.5)
                        if continued:
                            stop_event.clear()
                            transcription = str(recorder.get_whisper_result(timeout=15))
                            continue
                        else:
                            stop_event.clear()
                            break

                    save_current_conversation()
                    self.call_from_thread(self._set_conversation_active, False)
                    self.call_from_thread(self._add_system_message, "conversation ended  ·  listening...")

    def _voice_wakeword(self) -> None:
        from utils.wakeword import WakeWordDetector
        from utils import stt_hybrid as stt, tts_piper
        tts_piper.init_tts()

        try:
            detector = WakeWordDetector()
        except FileNotFoundError as e:
            self.call_from_thread(self._add_system_message, f"wake word model not found: {e}  ·  falling back to continuous")
            self._voice_continuous()
            return

        recorder = stt.VoiceRecorder(status_callback=lambda txt: self.call_from_thread(self._handle_status_update, txt))
        self.call_from_thread(self._add_system_message, f"wake word mode active  ·  say '{ASSISTANT_NAME}' to activate")

        while self.current_mode == "wakeword" and not _mode_switch_event.is_set():
            self.call_from_thread(self._update_status, "listening for wake word...")
            detector.listen_for_wakeword()

            start_new_conversation()
            reset_messages()
            self.call_from_thread(self._set_conversation_active, True)
            self.call_from_thread(self._add_system_message, "yes, sir?")

            success, filename = recorder.record(timeout=5)
            if not success:
                self.call_from_thread(self._add_system_message, "didn't catch that  ·  going back to sleep")
                save_current_conversation()
                detector.reset()
                self.call_from_thread(self._set_conversation_active, False)
                continue

            transcription = recorder.get_whisper_result(timeout=15) or ""
            if not transcription.strip():
                self.call_from_thread(self._add_system_message, "didn't hear anything  ·  going back to sleep")
                save_current_conversation()
                detector.reset()
                self.call_from_thread(self._set_conversation_active, False)
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
                        stop_event.clear()
                        break

            save_current_conversation()
            detector.reset()
            self.call_from_thread(self._set_conversation_active, False)
            self.call_from_thread(self._add_system_message, "going back to sleep")

    def _set_conversation_active(self, val: bool) -> None:
        self.conversation_active = val

    # ── UI helpers ────────────────────────────────────────────────────────────

    def _speak_to_ui(self, text: str) -> None:
        stop_event.set()
        self._add_assistant_message(text)
        stop_event.clear()

        if self.current_mode != "text":
            from utils import tts_piper
            threading.Thread(
                target=tts_piper.speak, args=(text, stop_event), daemon=True
            ).start()

    def _add_user_message(self, text: str) -> None:
        ts = datetime.now().strftime("%H:%M")
        log = self.query_one("#chat-log", Vertical)
        log.mount(
            ChatMessage(
                f"[bold]{USER_NAME}[/]  [{ts}]\n{text}",
                classes="user-msg",
            )
        )
        self._scroll_to_bottom()

    def _add_assistant_message(self, text: str) -> None:
        ts = datetime.now().strftime("%H:%M")
        log = self.query_one("#chat-log", Vertical)
        log.mount(
            ChatMessage(
                f"[bold]{ASSISTANT_NAME}[/]  [{ts}]\n{text}",
                classes="assistant-msg",
            )
        )
        self._scroll_to_bottom()

    def _add_tool_call(self, func_name: str, args: dict | str, result: str = "") -> None:
        log = self.query_one("#chat-log", Vertical)
        panel = ToolCallPanel(func_name, args, result, id=f"tool-{id(args)}")
        log.mount(panel)
        self._scroll_to_bottom()

    def _update_last_tool_result(self, result: str) -> None:
        log = self.query_one("#chat-log", Vertical)
        panels = log.query(ToolCallPanel)
        if panels:
            last_panel = list(panels)[-1]
            display_result = result[:300] + ("..." if len(result) > 300 else "")
            try:
                # Textual stores collapsible body content in a `Contents` widget
                from textual.widgets._collapsible import CollapsibleTitle
                contents = last_panel.query_one("Collapsible > Contents")
                contents.mount(
                    Static(f"↳ {display_result}", classes="tool-result")
                )
            except NoMatches:
                last_panel.mount(
                    Static(f"↳ {display_result}", classes="tool-result")
                )
            self._scroll_to_bottom()

    def _add_system_message(self, text: str) -> None:
        log = self.query_one("#chat-log", Vertical)
        log.mount(
            ChatMessage(f"─ [italic]{text}[/]", classes="system-msg")
        )
        self._scroll_to_bottom()

    def _add_separator(self) -> None:
        log = self.query_one("#chat-log", Vertical)
        log.mount(Rule())
        self._scroll_to_bottom()

    def _handle_status_update(self, txt: str) -> None:
        if txt.startswith("Hearing: "):
            partial_text = txt.replace("Hearing: ", "")
            self._update_live_transcription(partial_text)
        elif txt == "[Listening] Speech detected...":
            self._start_live_transcription()
        elif txt == "Transcribing with Whisper...":
            self._update_status("transcribing audio...")
            self._end_live_transcription()
        else:
            self._update_status(txt)

    def _start_live_transcription(self) -> None:
        if getattr(self, "_live_msg", None):
            return
        ts = datetime.now().strftime("%H:%M")
        self._live_msg = ChatMessage(
            f"[bold]{USER_NAME} [italic](listening...)[/][/]  [{ts}]\n",
            classes="user-msg",
        )
        self._live_text = ""
        log = self.query_one("#chat-log", Vertical)
        log.mount(self._live_msg)
        self._scroll_to_bottom()

    def _update_live_transcription(self, text: str) -> None:
        self._live_text = text
        if getattr(self, "_live_msg", None):
            ts = datetime.now().strftime("%H:%M")
            self._live_msg.update(f"[bold]{USER_NAME} [italic](listening...)[/][/]  [{ts}]\n{text}")
            self._scroll_to_bottom()

    def _end_live_transcription(self) -> None:
        if getattr(self, "_live_msg", None):
            try:
                self._live_msg.remove()
            except Exception:
                pass
            self._live_msg = None

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
