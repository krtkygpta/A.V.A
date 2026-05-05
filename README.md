# A.V.A: Always Voiced Ally

A hyper-responsive, modular neural assistant that bridges human intuition and machine precision through a sophisticated client-server architecture.

![Status](https://img.shields.io/badge/STATUS-OPERATIONAL-00FF85?style=for-the-badge)
![Version](https://img.shields.io/badge/VERSION-2.4.0-7000FF?style=for-the-badge)

---

## Overview

A.V.A is a personal AI assistant designed to run locally with intelligent offloading to backend services. It features a modern terminal user interface, voice interaction capabilities, semantic memory, and extensible tool integrations.

### Key Features

- **Multi-Mode Voice Interaction** — Continuous listening, wake-word activation, or keyboard input
- **Semantic Memory** — Learns and remembers user preferences over time
- **Neural TTS** — Fast local text-to-speech using Piper
- **Hybrid STT** — Combines local Vosk wake-word detection with Groq Whisper for accurate transcription
- **Tool Orchestration** — Executes system commands, web searches, code, and more
- **Smart Home Integration** — Controls WiZ smart lights natively
- **Document Generation** — Creates PDFs and manages workspace files

---

## Architecture

```
CLIENT (App)                          SERVER (Backend)
┌──────────────────────┐             ┌──────────────────────┐
│  TUI (Textual)        │             │  LLM Service         │
│  Voice Modes          │◄──HTTP────►│  TTS Engine          │
│  Memory Manager       │             │  Tool Service         │
│  Function Handler     │             │  Conversation Store   │
│   └─ Local Tools      │             │   └─ Server Tools     │
└──────────────────────┘             └──────────────────────┘
```

### Directory Structure

```
A.V.A/
├── App/                     # Client application
│   ├── __main__.py          # Entry point
│   ├── core/                 # Core functionality
│   │   ├── generate.py       # Response generation
│   │   ├── messageHandler.py # Message management
│   │   ├── FuncHandler.py    # Tool execution
│   │   └── server_api.py     # HTTP client for server
│   ├── functions/            # Tool implementations
│   │   ├── system/           # System control tools
│   │   ├── web/              # Web/search tools
│   │   ├── media/            # Media control tools
│   │   └── productivity/     # Productivity tools
│   ├── knowledge/            # Memory system
│   │   └── memory.py         # Semantic memory manager
│   └── ui/
│       └── tui.py            # Terminal UI (Textual)
├── Server/                   # Backend server
│   ├── __main__.py           # Server entry point
│   ├── app.py                # HTTP API server
│   ├── config.py             # Server configuration
│   └── services/             # Backend services
│       ├── llm_service.py     # LLM integration
│       ├── tts_service.py     # Text-to-speech
│       ├── tool_service.py    # Tool orchestration
│       └── conversation_store.py
├── docs/                     # Documentation
└── requirements.txt         # Python dependencies
```

---

## Getting Started

### Prerequisites

- Python 3.10+
- [Piper TTS](https://github.com/rhasspy/piper) (for text-to-speech)
- API keys (see Configuration below)

### Installation

```bash
# Clone or navigate to the project
cd A.V.A

# Install Python dependencies
pip install -r requirements.txt

# Install Playwright (for web browsing)
playwright install
```

### Configuration

Configure your settings in two locations:

**Root `settings.json`:**

```json
{
  "GROQ_API_KEY": "your_groq_api_key",
  "USER_NAME": "YourName",
  "ASSISTANT_NAME": "AVA",
  "AVA_SERVER_URL": "http://127.0.0.1:8765"
}
```

**Server `Server/settings.json`:**

```json
{
  "openai_api_key": "your_api_key",
  "openai_base_url": "https://api.groq.com/openai/v1",
  "model_name": "kimi-k2-instruct",
  "tts_voice": "en_US-lessac-medium",
  "tts_host": "localhost",
  "tts_port": 10200,
  "groq_api_key": "your_groq_key",
  "google_ai_api_key": "your_google_key",
  "tavily_api_key": "your_tavily_key"
}
```

### Running the Application

**1. Start the Server (Backend):**

```bash
python -m Server
```

You should see:

```
[AVA-SERVER] Listening on http://127.0.0.1:8765
```

**2. Start the Client (in a separate terminal):**

```bash
python App/__main__.py
```

---

## Operational Modes

| Mode | Command | Description |
|------|---------|-------------|
| **TUI** | Default | Full terminal interface with live logs |
| **Continuous** | `--mode continuous` | Background listening with auto transcription |
| **Wake Word** | `--mode wakeword` | Power-efficient monitoring for "Hey AVA" |
| **Text** | `--mode text` | Direct keyboard input (no audio) |

### System Controls (in TUI)

- `Ctrl+C` — Emergency shutdown
- `Ctrl+I` — Open settings
- `Ctrl+Q` — Quit application

---

## Available Tools

### System Tools

| Tool | Function |
|------|----------|
| `get_time_date` | Get current time and date |
| `shutdown_pc` | Shut down the computer |
| `ping` | Set a timer/reminder |
| `send_notification` | Send system notification |
| `bash` | Execute bash commands |
| `smart_home` | Control WiZ smart lights |

### Web Tools

| Tool | Function |
|------|----------|
| `web` | Search the web and fetch content |
| `research` | Deep research on complex topics |

### Productivity Tools

| Tool | Function |
|------|----------|
| `calendar` | Manage calendar events |
| `create_pdf` | Generate PDF documents |
| `create_file` | Create text files |
| `open_file` | Open and read files |
| `delete_file` | Delete files |

### AI Tools

| Tool | Function |
|------|----------|
| `code_executor` | Run Python code safely in sandbox |
| `image_description_tool` | Analyze images |
| `memory_manager` | Save/retrieve semantic memories |
| `conversation_history` | Search past conversations |

---

## API Reference

The server exposes the following endpoints:

### Generation

```
POST /generate
```

Generate a response from the LLM.

**Request Body:**

```json
{
  "messages": [{"role": "user", "content": "Hello"}],
  "tools": [{"type": "function", "function": {...}}]
}
```

**Response:**

```json
{
  "role": "assistant",
  "content": "Hello! How can I help you?",
  "tool_calls": []
}
```

### Text-to-Speech

```
POST /tts
```

Synthesize text to speech.

**Request Body:**

```json
{
  "text": "Hello, how are you today?"
}
```

**Response:** Audio file (audio/wav)

### Conversation Management

| Endpoint | Description |
|----------|-------------|
| `POST /conversation/start` | Start new conversation |
| `POST /conversation/message` | Add a message |
| `POST /conversation/save` | Save current conversation |
| `POST /conversation/list` | List recent conversations |

### Tools

| Endpoint | Description |
|----------|-------------|
| `POST /tools/google_ai` | Google AI search |
| `POST /tools/image_analysis` | Analyze images |
| `POST /tools/code_execute` | Execute Python code |
| `POST /tools/research` | Deep research |

---

## Memory System

A.V.A maintains a semantic memory that persists across sessions. Memories are stored as natural language sentences and retrieved contextually.

The LLM automatically manages memories when relevant. Examples:

- "My favorite color is blue" — stored as "Your favorite color is blue"
- "I usually wake up at 7 AM" — stored for future reference

### Manual Memory Operations

Available through the `memory_manager` tool:

- **save**: Store new information
- **retrieve**: Find relevant memories
- **delete**: Remove a memory
- **categories**: View memory statistics

---

## Customization

### Themes

The TUI supports custom themes. Theme configurations are defined in `App/ui/tui.py`.

### Voice Settings

Configure TTS voice in `Server/settings.json`:

```json
{
  "tts_voice": "en_US-lessac-medium"
}
```

Available voices depend on your Piper installation.

---

## Troubleshooting

### Server won't start

Ensure port 8765 is available:

```bash
# Windows
netstat -ano | findstr :8765

# Linux/Mac
lsof -i :8765
```

### Voice recognition issues

- Verify microphone is properly configured
- For wake-word mode, ensure Vosk model is downloaded
- Check audio device settings in system

### Memory not persisting

Ensure write permissions to `App/data/memories/` directory.

---

## Documentation

More detailed documentation is available in the `docs/` folder:

- `docs/getting-started.md` — Step-by-step setup guide
- `docs/architecture.md` — System architecture and components
- `docs/configuration.md` — All configuration options
- `docs/api-reference.md` — HTTP API documentation
- `docs/memory-system.md` — Memory system details
- `docs/tools-reference.md` — Complete tool reference
- `docs/SUMMARY.md` — Documentation index

---

## License

This project is for personal use. See individual component licenses for third-party dependencies.

---

## Acknowledgments

- [Piper TTS](https://github.com/rhasspy/piper) — Neural text-to-speech
- [Vosk](https://alphacephei.com/vosk/) — Offline speech recognition
- [Textual](https://textual.textualize.io/) — Terminal UI framework
- [Groq](https://groq.com/) — Fast LLM inference