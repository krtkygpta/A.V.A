"""
AVA Terminal UI — Textual-based TUI inspired by Claude Code.

Provides:
  - Rich scrollable chat log with user / assistant / tool messages
  - Input bar at the bottom
  - Mode switcher (continuous, text, wakeword)
  - Tool-call display with collapsible panels
  - Status bar showing current state
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
from textual.widgets import (
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
from core.AppStates import main_runner, stop_event
from core.FuncHandler import handle_tool_call
from core.generate import generate_response
from core.messageHandler import add_message, reset_messages
from core.TaskManager import CompletionQueue, check_and_format_completions
from knowledge.ConversationManager import start_new_conversation, save_current_conversation

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
        color: #e2e8f0;
        background: #1e2433;
        border-left: thick #4f9cf9;
        padding: 1 2 1 3;
    }
    ChatMessage.assistant-msg {
        color: #e2e8f0;
        background: #171c2a;
        border-left: thick #a78bfa;
        padding: 1 2 1 3;
    }
    ChatMessage.tool-msg {
        color: #94a3b8;
        background: #131929;
        border-left: thick #38bdf8;
        padding: 1 2 1 3;
    }
    ChatMessage.system-msg {
        color: #64748b;
        text-style: italic;
        padding: 0 2;
        margin: 0;
    }
    """


class ToolCallPanel(Static):
    """Shows a tool call with name, args, and result."""

    DEFAULT_CSS = """
    ToolCallPanel {
        width: 100%;
        padding: 1 2 1 3;
        margin: 0 0 1 0;
        background: #0f1520;
        border-left: thick #f59e0b;
        opacity: 0.85;
    }
    ToolCallPanel .tool-name {
        color: #fbbf24;
        text-style: bold;
    }
    ToolCallPanel .tool-args {
        color: #475569;
        margin-top: 0;
    }
    ToolCallPanel .tool-result {
        color: #38bdf8;
        margin-top: 0;
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

        yield Static(f"[bold #fbbf24]▸ {self._func_name}[/]", classes="tool-name")
        if args_str and args_str != "{}":
            display_args = args_str[:500] + ("..." if len(args_str) > 500 else "")
            yield Static(f"[#475569]{display_args}[/]", classes="tool-args")
        if self._result:
            display_result = self._result[:300] + ("..." if len(self._result) > 300 else "")
            yield Static(f"[#38bdf8]↳ {display_result}[/]", classes="tool-result")


class ModeSwitcher(Static):
    """Horizontal mode selector."""

    DEFAULT_CSS = """
    ModeSwitcher {
        dock: top;
        height: 3;
        padding: 0 2;
        background: #0a0f1a;
        border-bottom: solid #1e2433;
    }
    ModeSwitcher Horizontal {
        height: 3;
        align-horizontal: left;
        align-vertical: middle;
    }
    ModeSwitcher .mode-btn {
        width: auto;
        min-width: 14;
        height: 1;
        padding: 0 2;
        margin: 0 0 0 0;
        text-align: center;
        content-align: center middle;
        background: #0a0f1a;
        color: #334155;
        text-style: bold;
    }
    ModeSwitcher .mode-btn:hover {
        color: #94a3b8;
    }
    ModeSwitcher .mode-btn.active {
        color: #e2e8f0;
        background: #0a0f1a;
        text-style: bold;
    }
    ModeSwitcher .mode-divider {
        color: #1e2433;
        width: 1;
        content-align: center middle;
    }
    ModeSwitcher .mode-indicator {
        color: #4f9cf9;
        width: 2;
        content-align: center middle;
    }
    """

    current_mode: reactive[str] = reactive("text")

    def compose(self) -> ComposeResult:
        with Horizontal():
            for i, mode in enumerate(MODES):
                is_active = mode == self.current_mode
                label = f"{MODE_ICONS[mode]}  {MODE_LABELS[mode]}"
                btn = Static(
                    f"[bold {'#e2e8f0' if is_active else '#334155'}]{label}[/]",
                    classes="mode-btn" + (" active" if is_active else ""),
                    id=f"mode-{mode}",
                )
                yield btn
                if i < len(MODES) - 1:
                    yield Static("│", classes="mode-divider")

    def on_click(self, event) -> None:
        widget = event.widget if hasattr(event, 'widget') else None
        target = widget
        while target is not None:
            if isinstance(target, Static) and "mode-btn" in target.classes:
                break
            target = getattr(target, 'parent', None)

        if target is None:
            return

        target_id = target.id or ""
        if target_id.startswith("mode-"):
            new_mode = target_id[5:]
            if new_mode in MODES:
                self.set_mode(new_mode)

    def set_mode(self, mode: str) -> None:
        self.current_mode = mode
        for m in MODES:
            try:
                btn = self.query_one(f"#mode-{m}", Static)
                is_active = m == mode
                label = f"{MODE_ICONS[m]}  {MODE_LABELS[m]}"
                btn.update(f"[bold {'#e2e8f0' if is_active else '#334155'}]{label}[/]")
                if is_active:
                    btn.add_class("active")
                else:
                    btn.remove_class("active")
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
        background: #0a0f1a;
        color: #334155;
        border-top: solid #1e2433;
    }
    """

    status_text: reactive[str] = reactive("")

    def render(self) -> str:
        return self.status_text


class SettingsScreen(Static):
    """Inline settings panel that replaces chat-log content."""

    DEFAULT_CSS = """
    SettingsScreen {
        width: 100%;
        height: auto;
        padding: 1 2;
    }
    SettingsScreen .settings-title {
        color: #a78bfa;
        text-style: bold;
        margin-bottom: 1;
    }
    SettingsScreen .settings-hint {
        color: #334155;
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
        color: #94a3b8;
        text-style: bold;
    }
    SettingsScreen .setting-desc {
        width: 1fr;
        height: 3;
        content-align: left middle;
        color: #334155;
        text-style: italic;
        padding: 0 1;
    }
    SettingsScreen .setting-input {
        width: 40;
        height: 3;
        border: round #1e2433;
        background: #131929;
        color: #e2e8f0;
        padding: 0 1;
    }
    SettingsScreen .setting-input:focus {
        border: round #a78bfa;
    }
    SettingsScreen .settings-footer {
        color: #334155;
        margin-top: 1;
        text-style: italic;
    }
    SettingsScreen .save-hint {
        color: #38bdf8;
        margin-top: 0;
    }
    """

    def __init__(self, config: dict, **kwargs):
        super().__init__(**kwargs)
        self._config = config
        self._inputs: dict[str, Input] = {}

    def compose(self) -> ComposeResult:
        yield Static("[bold #a78bfa]⚙  settings[/]", classes="settings-title")
        yield Static(
            "[#334155 italic]edit values below  ·  ctrl+s to save  ·  /settings or ctrl+, to close[/]",
            classes="settings-hint",
        )
        yield Rule()

        for key, val in self._config.items():
            desc = CONFIG_DESCRIPTIONS.get(key, "")
            is_secret = key in CONFIG_SECRET_KEYS
            placeholder = "••••••••" if (is_secret and val) else (str(val) if val else "not set")
            with Horizontal(classes="setting-row"):
                yield Static(f"[bold #94a3b8]{key}[/]", classes="setting-label")
                yield Static(f"[#334155]{desc}[/]", classes="setting-desc")
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
            "[#334155 italic]changes take effect after restart for most settings[/]",
            classes="settings-footer",
        )
        yield Static(
            "[#38bdf8]ctrl+s[/] [#334155]save  ·[/] [#38bdf8]ctrl+,[/] [#334155]close[/]",
            classes="save-hint",
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
# ═══════════════════════════════════════════════════════════════════════════════
# Main Application
# ═══════════════════════════════════════════════════════════════════════════════

MAIN_CSS = """
Screen {
    background: #0d1117;
}
Header {
    background: #0a0f1a;
    color: #64748b;
    border-bottom: solid #1e2433;
    height: 1;
}
Header .header--title {
    color: #a78bfa;
    text-style: bold;
}
Header .header--sub-title {
    color: #334155;
}
Header .header--clock {
    color: #334155;
}
Footer {
    background: #0a0f1a;
    color: #334155;
    border-top: solid #1e2433;
    height: 1;
}
Footer .footer--key {
    color: #4f9cf9;
    background: #0a0f1a;
}
Footer .footer--description {
    color: #334155;
    background: #0a0f1a;
}
#app-grid {
    width: 100%;
    height: 1fr;
    background: #0d1117;
}

#chat-scroll {
    width: 100%;
    height: 1fr;      /* ← takes all remaining space inside app-grid */
    padding: 1 0;
    background: #0d1117;
    scrollbar-color: #1e2433;
    scrollbar-color-hover: #334155;
    scrollbar-background: #0d1117;
}

#input-bar {
    height: 4;        /* ← fixed height, no dock */
    padding: 0 2;
    background: #0a0f1a;
    border-top: solid #1e2433;
    align-vertical: middle;
}
#chat-log {
    width: 100%;
    height: auto;
    padding: 0 1;
}

#user-input {
    width: 100%;
    border: round #1e2433;
    background: #131929;
    color: #e2e8f0;
    padding: 0 1;
}
#user-input:focus {
    border: round #4f9cf9;
    background: #131929;
}
#user-input>.input--placeholder {
    color: #2d3748;
}
Rule {
    color: #1e2433;
    margin: 1 0;
}
"""


class AVAApp(App):
    """AVA Terminal UI — Claude Code inspired."""

    CSS = MAIN_CSS
    TITLE = f"AVA  ·  {ASSISTANT_NAME}"
    SUB_TITLE = "personal intelligence"
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+n", "new_conversation", "New Chat", show=True),
        Binding("ctrl+m", "cycle_mode", "Cycle Mode", show=True),
        Binding("escape", "focus_input", "Focus Input", show=False),
        Binding("ctrl+comma", "toggle_settings", "Settings", show=True),
        Binding("ctrl+s", "save_settings", "Save Settings", show=False),

    ]
    settings_open: reactive[bool] = reactive(False)
    _settings_widget: "SettingsScreen | None" = None
    current_mode: reactive[str] = reactive("text")
    is_processing: reactive[bool] = reactive(False)
    conversation_active: reactive[bool] = reactive(False)

    def __init__(self, start_mode: str = "text", **kwargs):
        super().__init__(**kwargs)
        self.current_mode = start_mode
        self._voice_thread: threading.Thread | None = None

    # ── Add these methods to AVAApp ───────────────────────────────────────────────

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
        log = self.query_one("#chat-log", Vertical)
        self._settings_widget = SettingsScreen(config, id="settings-panel")
        log.mount(self._settings_widget)
        self.call_after_refresh(
            self.query_one("#chat-scroll", VerticalScroll).scroll_end, animate=False
        )

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
        self._add_system_message("settings closed")

    def action_save_settings(self) -> None:
        if not self.settings_open or not self._settings_widget:
            return
        data = self._settings_widget.collect_values()
        if self._save_config(data):
            self._add_system_message("settings saved  ·  restart to apply changes")
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

        # ASCII splash
        log = self.query_one("#chat-log", Vertical)
        log.mount(Static(
            f"[bold #a78bfa]{AVA_ASCII}[/]\n[#334155 italic]personal intelligence  ·  {ASSISTANT_NAME}[/]",
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

        threading.Thread(target=self._main_loop, daemon=True).start()

        # if self.current_mode != "text":
        #     self._start_voice_mode(self.current_mode)

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
            stop_event.set()
            time.sleep(0.2)
            stop_event.clear()

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
        if text.lower() in {"/settings", "/config", "/set"}:
            self.action_toggle_settings()
            return

        elif text.lower() == "print messages":
            from core.messageHandler import messages
            self._add_system_message(str(messages))
            return
        elif text.lower() == "print memories":
            from knowledge.memory import retrieve_memories
            self._add_system_message(str(retrieve_memories()))
            return

        if not self.conversation_active:
            start_new_conversation()
            reset_messages()
            self.conversation_active = True

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
        self.conversation_active = True          # ← mark active so next message works
        self.query_one("#user-input", Input).focus()

    def _end_conversation(self) -> None:
        if not self.conversation_active:         # ← guard against double-call crash
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
        if not val:
            self._update_status(f"ready  ·  {MODE_LABELS.get(self.current_mode, self.current_mode).lower()} mode")

    # ── Voice modes ───────────────────────────────────────────────────────────

    def _voice_continuous(self) -> None:
        from utils import stt_hybrid as stt, tts_piper
        tts_piper.init_tts()
        self.call_from_thread(self._add_system_message, "continuous listening active")

        recorder = stt.VoiceRecorder()
        while self.current_mode == "continuous":
            success, filename = recorder.record()
            if success:
                prompt = recorder.get_whisper_result(timeout=15)
                transcription = prompt if prompt is not None else ""

                if any(word in transcription.lower() for word in ["ava", "assistant", "eva", "ayva", "evaa"]):
                    self.call_from_thread(self._add_system_message, "wake word detected")
                    start_new_conversation()
                    reset_messages()
                    self.call_from_thread(self._set_conversation_active, True)

                    while self.current_mode == "continuous":
                        while main_runner.is_set() or main_running.is_set():
                            time.sleep(0.01)

                        self.call_from_thread(self._add_user_message, transcription)

                        if any(cmd in transcription.lower() for cmd in SHUTUP_COMMANDS):
                            resp = random.choice(EXIT_RESPONSES)
                            self.call_from_thread(self._add_assistant_message, resp)
                            break

                        user_msg = f"{USER_NAME}: {transcription}"
                        add_message(role="user", content=user_msg, tool_id="")

                        main_running.wait(timeout=5.0)
                        while main_running.is_set():
                            time.sleep(0.01)

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

        recorder = stt.VoiceRecorder()
        self.call_from_thread(self._add_system_message, f"wake word mode active  ·  say '{ASSISTANT_NAME}' to activate")

        while self.current_mode == "wakeword":
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

            while self.current_mode == "wakeword":
                if not main_runner.is_set():
                    self.call_from_thread(self._add_user_message, transcription)

                    if any(cmd in transcription.lower() for cmd in SHUTUP_COMMANDS):
                        resp = random.choice(EXIT_RESPONSES)
                        self.call_from_thread(self._speak_to_ui, resp)
                        break

                    full = f"{USER_NAME}: {transcription}"
                    add_message(role="user", content=full, tool_id="")

                    main_running.wait(timeout=5.0)
                    while main_running.is_set():
                        time.sleep(0.01)

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
                f"[bold #4f9cf9]{USER_NAME}[/]  [#456186]{ts}[/]\n[#e2e8f0]{text}[/]",
                classes="user-msg",
            )
        )
        self._scroll_to_bottom()

    def _add_assistant_message(self, text: str) -> None:
        ts = datetime.now().strftime("%H:%M")
        log = self.query_one("#chat-log", Vertical)
        log.mount(
            ChatMessage(
                f"[bold #a78bfa]{ASSISTANT_NAME}[/]  [#456186]{ts}[/]\n[#e2e8f0]{text}[/]",
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
            last_panel.mount(Static(f"[#38bdf8]↳ {display_result}[/]", classes="tool-result"))
            self._scroll_to_bottom()

    def _add_system_message(self, text: str) -> None:
        log = self.query_one("#chat-log", Vertical)
        log.mount(
            ChatMessage(f"[#2d3748]─[/] [#475569 italic]{text}[/]", classes="system-msg")
        )
        self._scroll_to_bottom()

    def _add_separator(self) -> None:
        log = self.query_one("#chat-log", Vertical)
        log.mount(Rule())
        self._scroll_to_bottom()

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

def run_tui(start_mode: str = "text") -> None:
    """Launch the AVA TUI."""
    app = AVAApp(start_mode=start_mode)
    app.run()