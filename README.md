# A.V.A — Always Voiced Ally

Personal AI assistant that runs on your machine: chat in a terminal UI, speak with voice, run tools (web, research, files, smart home, coding jobs), and remember things across sessions.

```
You (TUI / mic)  ──HTTP──►  Server (LLM + TTS + research)
       │                         │
   local tools              API keys / models
```

---

## Quick start (minimum path)

You need **two terminals**. One for the server, one for the client.

```bash
# 1) Setup (once)
cd A.V.A
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
# source .venv/bin/activate

pip install -r requirements.txt

# 2) Create settings (see "Configuration" below), then:

# Terminal A — backend
python -m Server

# Terminal B — client (default: TUI text mode)
python -m App
# or:
python App/__main__.py
```

Type something like `What time is it?` in the TUI. If the server is up and API keys are set, AVA should answer.

---

## What you need

### Software

| Requirement | Notes |
|-------------|--------|
| **Python 3.10+** | Check with `python --version` |
| **pip** + **venv** | Recommended so deps stay isolated |
| **Microphone + speakers** | Only if you want voice; **text mode works without a mic** |
| **Git** (optional) | To clone the repo |

### API keys (accounts)

| Key | Where | Used for |
|-----|--------|----------|
| **OPENAI_API_KEY** (or pool) | `Server/settings.json` | Main chat LLM (any OpenAI-compatible API) |
| **GROQ_API_KEY** | Client + Server settings | Fast STT (Whisper) and some client features |
| **TAVILY_API_KEY** | Client + Server (optional) | Deep research / web research tools |
| **GOOGLE_AI_API_KEY** | Server (optional) | Google AI grounded search |
| **WEATHER_API_KEY** | Client (optional) | Weather tool |

Get keys from the providers you use (e.g. [Groq](https://console.groq.com/), [Tavily](https://tavily.com/), Google AI Studio, or your OpenAI-compatible host such as Ollama Cloud).

### Models & local assets (voice)

| Asset | Purpose | How to get it |
|-------|---------|----------------|
| **Piper voice** (`.onnx`) | Text-to-speech | Already in `assets/en_US-hfc_female-medium.onnx` if present; server can also try to download a voice on first run |
| **Vosk model** | Live partial captions + wake word | Unpack a small English model into `App/data/vosk-model/` (see below) |
| **piper-tts** package | Runs Piper HTTP server from Python | `pip install piper-tts` |

**Text-only use:** you can skip Vosk and still chat in TUI **text** mode. TTS still needs Piper + the voice model if you want spoken replies.

### Optional extras

| Extra | Why |
|-------|-----|
| [OpenCode](https://opencode.ai) CLI | Background coding agent (`task_type=opencode`) |
| Google Calendar OAuth files | Calendar tool (`App/data/calendar_client_secrets.json`) |
| Twilio env vars | Outbound phone calls |
| Smart home credentials | LG ThinQ / WiZ (in client settings) |

---

## Installation (step by step)

### 1. Get the code

```bash
cd A.V.A
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

**Windows (PowerShell / cmd):**

```bash
.venv\Scripts\activate
```

**macOS / Linux:**

```bash
source .venv/bin/activate
```

### 3. Install Python packages

```bash
pip install -r requirements.txt
```

This installs the core stack plus **`python-dotenv`** (server config) and **`piper-tts`** (speech via `python -m piper.http_server`).

### 4. (Voice) Install a Vosk model

Only needed for **continuous** / **wakeword** modes (and live partial transcripts).

1. Download a small English model, e.g.  
   [vosk-model-small-en-us-0.15](https://alphacephei.com/vosk/models)
2. Unzip it so the model files live at:

```text
App/data/vosk-model/
  am/
  conf/
  graph/
  ...
```

(not `App/data/vosk-model/vosk-model-small-.../`)

### 5. (Voice) Confirm Piper voice

Default server setting points at:

```text
./assets/en_US-hfc_female-medium
```

That means files like:

```text
assets/en_US-hfc_female-medium.onnx
assets/en_US-hfc_female-medium.onnx.json
```

If missing, either place a Piper voice there or change `tts_voice` in `Server/settings.json`. On first start the server may attempt `python -m piper.download_voices <name>`.

### 6. Create config files

Settings files are **gitignored** (they hold secrets). Create them yourself.

#### A) Client — `App/settings.json`

Also create the same content as root **`settings.json`** if you can: some startup paths load the project root file, while most App code loads `App/settings.json`. Keeping both identical avoids “works in TUI settings but not at launch” confusion.

```json
{
  "USER_NAME": "YourName",
  "ASSISTANT_NAME": "AVA",
  "AVA_SERVER_URL": "http://127.0.0.1:8765",
  "AVA_START_MODE": "tui_text",
  "GROQ_API_KEY": "gsk_your_groq_key",
  "TAVILY_API_KEY": "tvly_your_tavily_key",
  "WEATHER_API_KEY": "",
  "THEME": "monokai"
}
```

| Key | Required? | Meaning |
|-----|-----------|---------|
| `GROQ_API_KEY` | Strongly recommended | Speech / Whisper and some tools |
| `AVA_SERVER_URL` | Yes for normal use | Must match the running server |
| `AVA_START_MODE` | No | Startup mode (see below) |
| `USER_NAME` / `ASSISTANT_NAME` | No | Names used in prompts |
| `TAVILY_API_KEY` | No | Research quality |
| `THEME` | No | TUI theme name |

#### B) Server — `Server/settings.json`

```json
{
  "OPENAI_API_KEY": "your_llm_api_key",
  "OPENAI_API_KEYS": [
    "your_llm_api_key"
  ],
  "OPENAI_BASE_URL": "https://api.groq.com/openai/v1",
  "MODEL_NAME": "llama-3.3-70b-versatile",
  "AVA_SERVER_HOST": "127.0.0.1",
  "AVA_SERVER_PORT": 8765,
  "AVA_LOCAL_PIPER_HOST": "127.0.0.1",
  "AVA_LOCAL_PIPER_PORT": 5000,
  "AVA_LOCAL_PIPER_TIMEOUT": 60,
  "GROQ_API_KEY": "gsk_your_groq_key",
  "GOOGLE_AI_API_KEY": "",
  "TAVILY_API_KEY": "tvly_your_tavily_key",
  "tts_voice": "./assets/en_US-hfc_female-medium"
}
```

| Key | Required? | Meaning |
|-----|-----------|---------|
| `OPENAI_API_KEY` or `OPENAI_API_KEYS` | **Yes** | Main model; pool supports rotation / 429 retry |
| `OPENAI_BASE_URL` | Yes | OpenAI-compatible base URL (Groq, Ollama, etc.) |
| `MODEL_NAME` | Yes | Model id your provider accepts |
| `GROQ_API_KEY` | Recommended | Server-side tools that call Groq |
| `tts_voice` | For speech | Path (no `.onnx` suffix required) under project root |
| `AVA_SERVER_PORT` | No | Default `8765` |

Use any OpenAI-compatible host. Examples:

- Groq: `OPENAI_BASE_URL` = `https://api.groq.com/openai/v1`
- Ollama cloud / local OpenAI bridge: set base URL + model accordingly

---

## Running A.V.A

Always start the **server first**.

### Terminal 1 — Server

```bash
# venv activated, project root
python -m Server
```

You want something like: listening on port **8765**, and Piper TTS starting if voice is configured.

### Terminal 2 — Client

```bash
python -m App
```

Default mode is **`tui_text`**: full terminal UI, type messages with the keyboard.

### Startup modes (`AVA_START_MODE`)

Set in `App/settings.json` / root `settings.json`:

| Value | Behavior |
|-------|----------|
| `tui_text` | TUI + keyboard (best first try) |
| `tui_continuous` | TUI + always-on mic |
| `tui_wakeword` | TUI + “Hey AVA”-style wake word (needs Vosk) |
| `text` | Classic CLI text (no TUI) |
| `continuous` | Classic continuous voice |
| `vosk` | Classic wake-word voice |

Inside the TUI you can also switch with:

```text
/mode text
/mode continuous
/mode wakeword
```

or **Ctrl+M** to cycle modes.

---

## First things to try

With server + client running (text mode is fine):

1. `Hello — who are you?`
2. `What time is it?`
3. `Remember that my favorite color is blue`
4. `What's the weather like?` (needs weather key)
5. `Search the web for recent news about Python 3.13`
6. `Research level 2: overview of solid-state batteries` (needs Tavily / research stack)

### Useful TUI keys

| Shortcut | Action |
|----------|--------|
| **?** | Help |
| **Ctrl+Q** | Quit |
| **Ctrl+N** / `/new` | New conversation |
| **Ctrl+I** / `/settings` | Settings |
| **Ctrl+T** / `/theme` | Themes |
| **Ctrl+M** | Cycle input mode |
| **Ctrl+B** | Tasks sidebar |
| **Ctrl+D** / `/tasks` | Task dashboard |
| **Ctrl+H** | History sidebar |

Slash examples: `/help`, `/clear`, `/quiet`, `/task <id>`.

---

## How the pieces fit (newbie map)

| Piece | Command / path | Role |
|-------|----------------|------|
| **Server** | `python -m Server` | LLM calls, TTS, research tools, conversation store, memory dreamer |
| **Client** | `python -m App` | TUI, mic, local tools, talks to server over HTTP |
| **Jobs** | background tasks | Timers, research, OpenCode, missions — show up in `/tasks` |
| **Data** | `App/data/`, `Server/data/` | Conversations, memories, recordings, task state (**local only**, not for git) |
| **Settings** | `*.settings.json` | Secrets + prefs (**local only**) |

---

## Features (high level)

- **Chat** in a Textual TUI with themes and tool activity
- **Voice** — continuous listen or wake word (Vosk + Groq Whisper)
- **Tools** — time, notifications, bash (with permissions), files/PDFs, web, deep research, calendar, smart home, code sandbox, memory
- **Background jobs** — long work (research, OpenCode, etc.) with inbox / dossiers / completion announces
- **Memory** — semantic memories + server “dreamer” that digests idle conversations
- **Plugins** — drop Python tools into `App/functions/plugins/` (see `docs/PLUGINS.md`)

---

## Optional: OpenCode coding agent

1. Install the OpenCode CLI (`opencode` on your PATH).
2. Ask AVA to build or edit a project; it can spawn `task_type=opencode` jobs.
3. Continue sessions with stored `ses_...` ids from the task UI / dossiers.

Without OpenCode installed, other features still work.

---

## Troubleshooting

### “Connection refused” / client can’t reach server

- Start `python -m Server` first.
- Check `AVA_SERVER_URL` is `http://127.0.0.1:8765` (or your host/port).
- Port busy?

```bash
# Windows
netstat -ano | findstr :8765

# macOS / Linux
lsof -i :8765
```

Change `AVA_SERVER_PORT` in `Server/settings.json` and match the client URL.

### LLM / “no API key” errors

- Set `OPENAI_API_KEY` or `OPENAI_API_KEYS` in **`Server/settings.json`**.
- Confirm `OPENAI_BASE_URL` and `MODEL_NAME` match your provider.
- Rate limits: put several keys in `OPENAI_API_KEYS` (server rotates and retries on 429).

### No speech output

- Install `piper-tts`.
- Confirm the `.onnx` voice exists under `assets/` (or your `tts_voice` path).
- Watch the server log for `[ServerTTS]` / Piper startup errors.
- Text mode still works without audio.

### Mic / wake word issues

- OS mic permission + default input device.
- Vosk model must be at `App/data/vosk-model/`.
- Fall back to `/mode text` or `AVA_START_MODE: "tui_text"`.

### Import errors after install

```bash
# venv active, project root
pip install -r requirements.txt
```

Some tools pull extra optional stacks (camera, automation). Install only what you use if a heavy import fails.

### Settings not applying

- Edit **`App/settings.json`** and root **`settings.json`** together for client keys.
- Server only reads **`Server/settings.json`**.
- Restart server and client after changes.

### Privacy note

Do **not** commit `settings.json`, `App/data/`, or `Server/data/`. They can contain API keys, chat logs, and recordings. `.gitignore` is set up to keep them out of git.

---

## Project layout (short)

```text
A.V.A/
├── App/                 # Client (TUI, tools, STT)
│   ├── __main__.py      # python -m App
│   ├── settings.json    # your client secrets (create this)
│   ├── core/            # agent loop, jobs, tools wiring
│   ├── functions/       # tool implementations + plugins/
│   ├── knowledge/       # conversations + memory
│   ├── ui/tui.py        # terminal UI
│   └── data/            # local runtime data (ignored by git)
├── Server/              # Backend
│   ├── __main__.py      # python -m Server
│   ├── settings.json    # your server secrets (create this)
│   ├── services/        # LLM, TTS, tools, key rotator
│   └── tools/           # research, sandbox, …
├── assets/              # chime + Piper voice models
├── docs/                # deeper documentation
└── requirements.txt
```

---

## More documentation

| Doc | Topic |
|-----|--------|
| [`docs/getting-started.md`](docs/getting-started.md) | Longer setup notes |
| [`docs/configuration.md`](docs/configuration.md) | Config reference |
| [`docs/architecture.md`](docs/architecture.md) | How components connect |
| [`docs/tools-reference.md`](docs/tools-reference.md) | Tools |
| [`docs/memory-system.md`](docs/memory-system.md) | Memory |
| [`docs/PLUGINS.md`](docs/PLUGINS.md) | Custom tools |
| [`docs/api-reference.md`](docs/api-reference.md) | HTTP API |
| [`docs/SUMMARY.md`](docs/SUMMARY.md) | Doc index |

---

## License & credits

Personal / educational use of this project. Third-party packages keep their own licenses.

- [Piper](https://github.com/rhasspy/piper) — TTS  
- [Vosk](https://alphacephei.com/vosk/) — offline STT  
- [Textual](https://textual.textualize.io/) — TUI  
- [Groq](https://groq.com/) — fast inference / Whisper  
