# Getting Started with A.V.A

This guide walks you through setting up and running A.V.A on your system.

## Prerequisites

Before installing A.V.A, ensure you have:

- **Python 3.10+** — Check with `python --version`
- **Piper TTS** — Required for text-to-speech functionality
- **API Keys** — You'll need keys for:
  - Groq API (for LLM inference and Whisper)
  - Google AI API (for web search with grounding)
  - Tavily API (for research agent)

## Installation

### 1. Navigate to the Project

```bash
cd A.V.A
```

### 2. Install Python Dependencies

```bash
pip install -r requirements.txt
```

### 3. Install Playwright

```bash
playwright install
```

### 4. Install Piper TTS

Download and install Piper TTS from the official releases. You'll need:

- The Piper binary for your platform
- A voice model (e.g., `en_US-lessac-medium.onnx`)

Place the voice model in an accessible directory and note the path.

## Configuration

### Root Settings

Edit `A.V.A/settings.json` in the root directory:

```json
{
  "GROQ_API_KEY": "your_groq_api_key_here",
  "USER_NAME": "YourName",
  "ASSISTANT_NAME": "AVA",
  "AVA_SERVER_URL": "http://127.0.0.1:8765"
}
```

### Server Settings

Edit `A.V.A/Server/settings.json`:

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

## Running A.V.A

### Step 1: Start the Server

In one terminal, start the backend server:

```bash
python -m Server
```

You should see:

```
[AVA-SERVER] Listening on http://127.0.0.1:8765
```

### Step 2: Start the Client

In another terminal, launch the client:

```bash
python App/__main__.py
```

The TUI interface will appear. By default, it starts in **TUI mode**.

## Operational Modes

### TUI Mode (Default)

Full terminal interface with message history, live transcription, and real-time tool logs.

```bash
python App/__main__.py
```

### Continuous Voice Mode

Background listening with automatic transcription. Press `Enter` to stop listening.

```bash
python App/__main__.py --mode continuous
```

### Wake Word Mode

Power-efficient mode that listens for "Hey AVA" activation phrase.

```bash
python App/__main__.py --mode wakeword
```

### Text Mode

Direct keyboard input without audio. Useful for quiet environments.

```bash
python App/__main__.py --mode text
```

## Quick Test

After starting both server and client, try these commands:

1. **Basic conversation:**
   ```
   Hello! How are you today?
   ```

2. **Get the time:**
   ```
   What time is it?
   ```

3. **Save a memory:**
   ```
   Remember that my favorite color is blue
   ```

4. **Search the web:**
   ```
   Search for the latest news about AI
   ```

## Troubleshooting

### Port Already in Use

If port 8765 is occupied:

**Windows:**
```bash
netstat -ano | findstr :8765
```

**Linux/Mac:**
```bash
lsof -i :8765
```

Kill the process or change the port in `Server/settings.json`.

### Audio Issues

**No audio output:**
- Verify your audio device
- Check TTS voice path in settings

**Microphone not working:**
- Ensure microphone is set as default
- Test with system recorder first

### API Errors

**Invalid API key:**
- Verify keys are correct in settings files
- Check for extra spaces or quotes

**Rate limiting:**
- Wait and retry (Groq has rate limits)
- Consider upgrading your API plan

## Next Steps

- Explore the `docs/tools-reference.md` for all available functions
- Learn about the `docs/memory-system.md`
- Customize your setup in the `docs/configuration.md`
