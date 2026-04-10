<div align="center">
  <h1>🎙️ A.V.A</h1>
  <p><b>Always Voiced Ally</b></p>
  <p><i>An intelligent, high-performance voice assistant system bridging local hardware with state-of-the-art LLMs.</i></p>
</div>

---

## 🌟 Overview

Built with a modular Client-Server architecture, **A.V.A** provides low-latency interactions, persistent semantic memory, and a suite of powerful local and remote tools. 

A.V.A is split into two primary components:
- 💻 **The Client (`/App`)**: Handles audio capturing (STT), speech synthesis (TTS playback), the Textual User Interface (TUI), local tool execution, and the main interaction loop.
- ⚙️ **The Server (`/Server`)**: A dedicated backend server that offloads heavy processing like LLM API requests, text-to-speech generation, context/conversation state storage, and complex data retrievals.

---

## ✨ Features

### 🎧 Advanced Audio Pipeline
* **Hybrid Speech-to-Text**: Combines local **Vosk** models for live, low-latency wake-word display and **Groq Whisper-v3** for high-precision final transcription.
* **Piper TTS Backend**: High-speed text-to-speech engine running via the `piper-tts` Python library.
* **VAD & Queue Logic**: Advanced Voice Activity Detection equipped with a 2.5s pre-buffer to ensure no words are cut off at the start or end of sentences.

### 🧠 Knowledge & Memory
* **Semantic Memories**: Automatically extracts and stores key details from discussions to build long-term context seamlessly.
* **Thread Management**: Each wake-word activation spins up a distinct, named conversation thread to prevent context bloat.

### 🎨 TUI (Textual User Interface)
* A beautifully designed Terminal UI resembling Claude Code.
* Live markdown rendering, tool execution logs, and mode-switching from directly within the terminal shell!

### 🛠️ Built-in Combined Tools
* **Smart Home**: Native control of your **Philips WiZ** smart lights over your local network.
* **Media & Music Tools**: Window-native control for Spotify/Apple Music, background music servers, and queues.
* **Web & API Tools**: Web scraping, local sandbox code execution, weather aggregation, and Google Gemini powered search fallbacks.
* **Document Tools**: Dynamically render markdown to PDFs, handle text creations, and manage workspaces locally.

---

## 🚀 Setup & Installation

### 1. Unified Configuration (`settings.json`)
A.V.A uses JSON files instead of `.env` for cross-platform robustness. 

**Client Configuration**: Create a `settings.json` file in the **project root** directory:
```json
{
  "GROQ_API_KEY": "your_groq_key",
  "OPENAI_API_KEY": "your_openai_key",
  "GOOGLE_AI_API_KEY": "your_google_ai_key",
  "WEATHER_API_KEY": "your_weather_api_key",
  "USER_NAME": "Your_Name",
  "ASSISTANT_NAME": "AVA",
  "AVA_SERVER_URL": "http://127.0.0.1:8765"
}
```

**Server Configuration**: Create a second `settings.json` natively inside the **`Server/`** directory to manage backend model operations:
```json
{
  "GROQ_API_KEY": "your_groq_key",
  "OPENAI_API_KEY": "your_openai_key",
  "SERVER_HOST": "127.0.0.1",
  "SERVER_PORT": 8765
}
```

### 2. Dependency Installation
Install the necessary requirements for both sides. We have pruned the `requirements.txt` to only include the packages actively used in the codebase:

```bash
pip install -r requirements.txt
```

> [!IMPORTANT] 
> **Playwright Setup (Required for Web Scraping & Sandbox Data)**  
> After installing the requirements, you must initialize Playwright to ensure the necessary browser binaries are downloaded:
> ```bash
> playwright install
> ```

---

## 💻 Execution Guide

Since A.V.A has a client-server relationship, you must launch the backend first before testing the client loop.

### 1. Start the Server
Navigate to the root and spin up the server module. This handles memory, LLMs, and TTS:

```bash
python -m Server
```

### 2. Start the Client
Open a secondary terminal split and launch the A.V.A interaction client:

```bash
python App/__main__.py
```

### ⚙️ Operating Modes
Set `START_MODE` within `App/__main__.py` to toggle interaction states:
* `START_MODE = "tui"`: (Default) Launches the interactive Textual terminal UI dashboard.
* `START_MODE = "continuous"`: Background mode that maps and transcribes all ambient audio indefinitely.
* `START_MODE = "vosk"`: Silently spins a low-cpu local Vosk model to watch for your wake word before turning on Groq transcription.
* `START_MODE = "text"`: Headless terminal chat execution mapped for debugging bypassing the microphone.

---

## 💡 Smart Home: WiZ Light Integration

A.V.A inherently ships with baked-in support targeting **Philips WiZ** bulbs running on your local IP.

**To add your own devices:**
1. Open `App/functions/light_control.py`.
2. Update the `main_dict` at the top of the file mapping friendly names to device MACs:
   ```python
   main_dict = {
       'Lights': 'YOUR_MAC_ADDRESS_1', # No colons
       'Lamp': 'YOUR_MAC_ADDRESS_2'
   }
   ```
3. A.V.A will dynamically broadcast and discover the IP addresses of these MAC entries on your home network during startup!

---

## 📚 Developer Notes & Shortcuts
- 🛑 Press **`Ctrl+C`** or forcefully say **"Shut Up"** during playback to immediately kill the assistant's voice streams.
- All code runs inside isolated Python sandbox endpoints (`sandbox.py`) when you ask it to generate code tools.
- All endpoints map through the robust `document_tools`, `web_tools`, `music_tools`, and `time_tools` combinations handled automatically by A.V.A via JSON payloads.
