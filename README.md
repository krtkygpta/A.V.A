<div align="center">
  <h1>🎙️ A.V.A</h1>
  <p><b>Always Voiced Ally</b></p>
  <p><i>An intelligent, high-performance voice assistant system bridging local hardware with state-of-the-art LLMs.</i></p>
</div>

---

## 🌟 Overview

Built with a modular Client-Server architecture, **A.V.A** provides low-latency interactions, persistent semantic memory, and a suite of powerful local and remote tools. 

A.V.A is split into two primary components:
- 💻 **The Client**: Handles audio capturing (STT), speech synthesis (TTS), local tool execution, and the main interaction loop.
- ⚙️ **The Server**: An optional but powerful backend that offloads heavy processing like vision analysis, complex data retrieval, and acts as a relay for specific API integrations.

---

## ✨ Features

### 🎧 Advanced Audio Pipeline
* **Hybrid Speech-to-Text**: Combines local **Vosk** models for live, low-latency wake-word display and **Groq Whisper-v3** for high-precision final transcription.
* **Piper TTS**: Fully local, high-speed text-to-speech engine running via the `piper-tts` Python library.
* **VAD & Queue Logic**: Advanced Voice Activity Detection equipped with a 2.5s pre-buffer to ensure no words are cut off at the start or end of sentences.

### 🧠 Knowledge & Memory
* **Semantic Memories**: Automatically extracts and stores key details from discussions to build long-term context seamlessly.
* **Thread Management**: Each wake-word activation spins up a distinct, named conversation thread to prevent context bloat.

### 🛠️ Built-in Tools
* **Smart Home**: Native control of your **Philips WiZ** smart lights over your local network.
* **Media Control**: Window-native control for Spotify/Apple Music and volume logic.
* **Research & Sandbox**: Web scraping, local sandbox code execution, and vision-based image analysis requests.

---

## 🚀 Setup & Installation

### 1. Environment Configuration
Create a `.env` file in the **project root** directory. Below are the required API keys and where to obtain them:

```env
# Core API Keys
GROQ_API_KEY=your_groq_key             # 🔑 Get it here: https://console.groq.com/keys
OPENAI_API_KEY=your_openai_key         # 🔑 Get it here: https://platform.openai.com/api-keys
GOOGLE_AI_API_KEY=your_google_ai_key   # 🔑 Get it here: https://aistudio.google.com/app/apikey
WEATHER_API_KEY=your_weather_api_key   # 🔑 Get it here: https://www.weatherapi.com/

# Bot Personality & Addressing
USER_NAME="Your_Name"
ASSISTANT_NAME="FRIDAY"

# Optional overrides (e.g., for local models via LM Studio)
OPENAI_BASE_URL="https://api.groq.com/openai/v1" 
MODEL_NAME="moonshotai/kimi-k2-instruct-0905" 
```

### 2. Dependency Installation
Install the necessary requirements. We have pruned the `requirements.txt` to only include the packages actively used in the codebase to keep your environment lightweight:

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

The Client is the main interface you interact with continuously. Start the client by running:

```bash
python App/__main__.py
```

For the current architecture, start the backend server first (generation, TTS, and conversation storage now run there):

```bash
python -m Server
```

The backend code is now separated in the top-level `Server/` folder and organized for easy extension:
- `Server/app.py` contains the route table and endpoint handlers
- `Server/services/` contains `llm_service.py`, `tts_service.py`, and `conversation_store.py`
- To add future server-side tools/functions, add new route handlers in `Server/app.py` and call new service methods

Optional `.env` values for client/server connection:

```env
AVA_SERVER_HOST=127.0.0.1
AVA_SERVER_PORT=8765
AVA_SERVER_URL=http://127.0.0.1:8765
```

### ⚙️ Configuration Modes
Set the mode within `App/__main__.py` depending on your current needs:
* `WAKE_MODE = "continuous"`: (Default) Transcribes all ambient audio to watch explicitly for wake-words.
* `WAKE_MODE = "vosk"`: Uses local Vosk context to silently run wake-word detection **(Recommended footprint)**.
* `WAKE_MODE = "text"`: Terminal mode mapped for quick debugging via keyboard input.

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
- 💾 All conversation threads and episodic memories are transparently cached as JSON inside the `App/data/` directory.
