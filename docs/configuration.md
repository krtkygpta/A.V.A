# Configuration Guide

This guide covers all configuration options for A.V.A, including settings files, environment variables, and runtime configuration.

## Configuration Files

A.V.A uses two main configuration files:

| File | Location | Purpose |
|------|----------|---------|
| `settings.json` | Project root | Client-side settings |
| `settings.json` | `Server/` directory | Server-side settings |

## Client Settings (settings.json)

Located in the project root directory.

```json
{
  "GROQ_API_KEY": "GSK_xxxxxxxxxxxx",
  "USER_NAME": "YourName",
  "ASSISTANT_NAME": "AVA",
  "AVA_SERVER_URL": "http://127.0.0.1:8765"
}
```

### Options

| Key | Required | Description |
|-----|----------|-------------|
| `GROQ_API_KEY` | Yes | API key for Groq services (speech recognition) |
| `USER_NAME` | No | Your display name (used in memory) |
| `ASSISTANT_NAME` | No | Name for the assistant |
| `AVA_SERVER_URL` | No | Server endpoint (default: `http://127.0.0.1:8765`) |

## Server Settings (Server/settings.json)

Located in the `Server/` directory.

```json
{
  "openai_api_key": "sk-xxxxxxxxxxxx",
  "openai_base_url": "https://api.groq.com/openai/v1",
  "model_name": "kimi-k2-instruct",
  "tts_voice": "en_US-lessac-medium",
  "tts_host": "localhost",
  "tts_port": 10200,
  "tts_startup_timeout": 30,
  "host": "0.0.0.0",
  "port": 8765,
  "conversation_dir": "data/conversations",
  "groq_api_key": "GSK_xxxxxxxxxxxx",
  "google_ai_api_key": "your_google_ai_key",
  "tavily_api_key": "your_tavily_key"
}
```

### LLM Options

| Key | Required | Description |
|-----|----------|-------------|
| `openai_api_key` | Yes | API key for LLM provider |
| `openai_base_url` | No | Base URL for API (default: Groq) |
| `model_name` | No | Model identifier (default: `kimi-k2-instruct`) |

### TTS Options

| Key | Default | Description |
|-----|---------|-------------|
| `tts_voice` | `en_US-lessac-medium` | Piper voice ID |
| `tts_host` | `localhost` | TTS server host |
| `tts_port` | `10200` | TTS server port |
| `tts_startup_timeout` | `30` | Startup timeout (seconds) |

### Server Options

| Key | Default | Description |
|-----|---------|-------------|
| `host` | `0.0.0.0` | Server bind address |
| `port` | `8765` | Server HTTP port |
| `conversation_dir` | `data/conversations` | Directory for conversation storage |

### Tool API Keys

| Key | Required | Description |
|-----|----------|-------------|
| `groq_api_key` | Yes | For Whisper transcription and image analysis |
| `google_ai_api_key` | No | For Google AI search |
| `tavily_api_key` | No | For deep research agent |

## Environment Variables

Override settings using environment variables:

```bash
AVA_SERVER_URL=http://127.0.0.1:8765
AVA_SERVER_TIMEOUT=10
GROQ_API_KEY=GSK_xxxxxxxxxxxx
```

| Variable | Description |
|----------|-------------|
| `AVA_SERVER_URL` | Server endpoint URL |
| `AVA_SERVER_TIMEOUT` | Request timeout in seconds |
| `GROQ_API_KEY` | Groq API key |
| `AVA_SERVER_MODE` | Set to `1` when running server |

## Piper TTS Setup

### Installation

```bash
git clone https://github.com/rhasspy/piper.git
cd piper/src/python && pip install -e .
mkdir -p voices
wget -O voices/en_US-lessac-medium.onnx https://github.com/rhasspy/piper/raw/main/voices/en_US-lessac-medium.onnx
```

### Available Voices

Popular English voices:

- `en_US-lessac-medium` — Male, medium quality (recommended)
- `en_US-kathleen-medium` — Female, medium quality
- `en_US-lessac-high` — Male, high quality
- `en_US-libritts-high` — Female, high quality

## Vosk Wake Word Setup

Download a Vosk model for wake-word detection:

```bash
mkdir -p models
wget -O models/vosk-model-small-en-us-0.15.zip https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip
```

Place the unzipped folder in `App/models/` or set the path in code.

## Smart Home Configuration

WiZ lights require no additional configuration—just ensure they're on the same network. The `GROQ_API_KEY` must be set for natural language control.

### Supported Devices

- WiZ smart bulbs
- WiZ smart plugs
- WiZ smart switches

## Runtime Configuration

### TUI Settings (In-App)

Press `Ctrl+I` to open settings while running:

- Theme selection
- Voice preferences
- API key management
- Mode switching

### Theme Customization

Themes are defined in `App/ui/tui.py` under `_register_themes()`:

```python
self.themes["cyber"] = {
    "primary": "#00FF85",
    "secondary": "#7000FF",
    "accent": "#FF0055",
    "background": "#0D0D0D",
    "surface": "#1A1A1A",
    "text": "#FFFFFF",
}
```

## Troubleshooting

### Settings Not Loading

1. Check file paths are correct
2. Verify JSON syntax is valid
3. Ensure file has read permissions

### API Key Errors

- Verify keys are active and have sufficient quota
- Check API key format matches expected type
- For Groq, ensure key starts with `GSK_`

### Port Conflicts

If port 8765 is in use, change the port in `Server/settings.json`:

```json
{"port": 8766}
```

Then update the client URL:

```json
{"AVA_SERVER_URL": "http://127.0.0.1:8766"}
```

## See Also

- `README.md` — Project overview
- `docs/getting-started.md` — Quick start guide
- `docs/architecture.md` — System architecture