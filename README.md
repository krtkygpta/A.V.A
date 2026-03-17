# A.V.A (Always Voiced Ally)

A.V.A is an intelligent, high-performance voice assistant system designed to bridge local hardware (microphones, smart lights, media players) with state-of-the-art Large Language Models. Built with a modular Client-Server architecture, A.V.A provides low-latency interactions, persistent semantic memory, and a suite of powerful local and remote tools.

---

## [ Architecture Overview ]

A.V.A is split into two primary components:
- **The Client**: Handles audio capturing (STT), speech synthesis (TTS), local tool execution, and the main interaction loop.
- **The Server**: An optional but powerful backend that offloads heavy processing like vision analysis (Image Tool), complex data retrieval, and acts as a relay for specific API integrations.

---

## [ Features ]

### / Advanced Audio Pipeline
- **Hybrid Speech-to-Text**: Combines local **Vosk** models for live, low-latency display with **Groq Whisper-v3** for high-precision final transcription.
- **Piper TTS**: Fully local, high-speed text-to-speech engine. 
  - [Piper Documentation & Voice Models](https://github.com/rhasspy/piper)
- **VAD & Queue Logic**: Advanced Voice Activity Detection with a 2.5s pre-buffer to ensure no words are cut off at the start or end of sentences.

### / Knowledge & Memory
- **Semantic Memories**: Automatically extracts and stores key details from your conversations to build long-term context.
- **Thread Management**: Each wake-word activation creates a distinct, named conversation thread to prevent context bloat.

### / Built-in Tools
- **Smart Home**: Native support for **Philips WiZ lights**.
- **Media Control**: Windows-native control for Spotify/Apple Music.
- **Research & Vision**: Web scraping, sandbox code execution, and vision-based image analysis (requires Server).

---

## [ Setup & Installation ]

### 1. Environment Configuration
Create a `.env` file in the **project root** directory:
```env
# .env file
GROQ_API_KEY=your_groq_key
OPENAI_API_KEY=your_openai_key
USER_NAME="Your_Name"
ASSISTANT_NAME="FRIDAY"
# Optional overrides
MODEL_NAME=llama-3.3-70b-versatile
```

### 2. Dependency Installation
Install the core requirements:
```bash
pip install -r requirements.txt
```

### 3. Piper TTS Setup (Local Voice)
A.V.A uses Piper for high-speed local speech synthesis. To set it up:
1. **Download Piper**: Go to [Piper Releases](https://github.com/rhasspy/piper/releases) and download the `piper_windows_amd64.zip` (or your OS equivalent).
2. **Extract Engine**: Extract all files from the zip (especially `piper.exe` and `.dll` files) into `Client/utils/piper/`.
3. **Get Voice Models**: Visit the [Piper Voice Samples](https://rhasspy.github.io/piper-samples/) to choose a voice.
   - Download both the `.onnx` and `.onnx.json` files for your preferred voice.
4. **Place Models**: 
   - Move the `.onnx` file to `Client/data/models/friday.onnx`.
   - Move the `.onnx.json` file to `Client/data/models/friday.json`.
   - Alternatively, update `Client/utils/tts.py` to point to your specific model names.

---

## [ Execution Guide ]

### / Running the Client
The Client is the main application you interact with.
```bash
python Client/__main__.py
```
**Configuration Modes** (Set in `Client/__main__.py`):
- `WAKE_MODE = "continuous"`: (Default) Transcribes all audio to watch for wake-words.
- `WAKE_MODE = "vosk"`: Uses local Vosk for silent wake-word detection (Recommended).
- `WAKE_MODE = "text"`: Terminal mode for debugging via typing.

### / Running the Server
The Server is required for features like the **Image Tool** and certain heavy API operations.
```bash
python server/_server_.py
```
The server runs on `http://127.0.0.1:5000` by default.

---

## [ Smart Home: WiZ Light Integration ]

A.V.A currently supports **Philips WiZ** smart bulbs.

### / How to add your own devices:
1. Open `Client/functions/light_control.py`.
2. Update the `main_dict` at the top:
   ```python
   main_dict = {
       'Lights': 'YOUR_MAC_ADDRESS_1', # No colons
       'Lamp': 'YOUR_MAC_ADDRESS_2'
   }
   ```
3. A.V.A will automatically discover the IP addresses of these devices on your network during startup.

---

## [ Developer Notes ]
- Press **Ctrl+C** or say **"Shut Up"** during playback to immediately stop the assistant's voice output.
- All conversation data and memories are stored in the `Client/data/` directory for easy backup or inspection.
