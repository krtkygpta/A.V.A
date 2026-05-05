# A.V.A Architecture Documentation

This document provides an in-depth look at the A.V.A system's architecture, component interactions, and design decisions.

## Design Philosophy

A.V.A follows a client-server architecture that separates concerns between user interaction (Client) and heavy computation (Server). This design:

1. **Offloads cognitive load** to a backend for low-latency local UI
2. **Enables modularity** — tools, TTS, and LLM can be upgraded independently
3. **Supports multi-modal interaction** — voice, text, and background processing
4. **Maintains persistent state** — memory and conversations survive client restarts

## System Overview

```
CLIENT (App)                          SERVER (Backend)
┌──────────────────────┐             ┌──────────────────────┐
│  TUI (Textual)        │             │  LLM Service          │
│  Voice Modes          │◄──HTTP────►│  TTS Engine           │
│  Memory Manager       │             │  Tool Service         │
│  Function Handler     │             │  Conversation Store   │
│   └─ Local Tools      │             │   └─ Server Tools     │
└──────────────────────┘             └──────────────────────┘
```

## Component Details

### Client Components

#### App/__main__.py — Entry Point

The client application supports multiple operational modes:

| Mode | Behavior |
|------|----------|
| **TUI** (default) | Interactive terminal interface |
| **Continuous** | Background voice listening with auto transcription |
| **Wake Word** | Wait for "Hey AVA" activation |
| **Text** | Keyboard-only input (no audio) |

**Key Responsibilities:**
- Initialize configuration
- Set up audio hardware (volume ducking for wake word)
- Launch the appropriate interaction mode
- Handle graceful shutdown

#### App/core/AppStates.py — State Management

Manages application state including:
- Current conversation ID
- Processing status
- Voice mode state
- Configuration state

#### App/core/generate.py — Response Generation

Thin wrapper that delegates generation to the server:

```python
def generate_response():
    server_message = generate_remote_response(list(messages), list(tools))
    add_assistant_message(content=content, tool_calls=tool_calls)
    return message
```

#### App/core/messageHandler.py — Message Management

Maintains the local message history and tool definitions. Acts as the central message bus for the client.

**Key Functions:**
- `add_message()` — Add user or assistant messages
- `add_assistant_message()` — Handle combined content + tool calls
- Manages the tools registry (passed to LLM for function calling)

#### App/core/FuncHandler.py — Tool Execution

Routes tool calls from the LLM to appropriate handlers:

```python
TOOL_CONFIGS = {
    "music_agent": functions.run_music_controller,
    "get_time_date": functions.timedate,
    "web": web,
    "code_executor": functions.run_code_in_sandbox,
    "memory_manager": handle_memory_manager,
}
```

**Tool Categories:**

| Category | Tools |
|----------|-------|
| **Media** | Music controller, image description |
| **System** | Time/date, shutdown, notifications, smart home |
| **Web** | Web search, research |
| **File** | Create, open, delete files; create PDF |
| **Code** | Python sandbox execution |
| **Productivity** | Calendar management |
| **Memory** | Semantic memory CRUD |

#### App/core/server_api.py — Server Communication

HTTP client that communicates with the backend server. Handles:
- JSON serialization/deserialization
- Timeout management
- Error handling for network issues

#### App/core/TaskManager.py — Background Tasks

Manages concurrent background operations:
- Task queuing and dispatch
- Status tracking
- Result retrieval

### Server Components

#### Server/app.py — HTTP API Server

Threading HTTPServer that handles all backend requests.

**Route Table:**

| Method | Path | Handler |
|--------|------|---------|
| GET/POST | `/health` | Health check |
| POST | `/generate` | LLM response generation |
| POST | `/tts` | Text-to-speech synthesis |
| POST | `/conversation/start` | Start new conversation |
| POST | `/conversation/message` | Add message to conversation |
| POST | `/conversation/save` | Persist conversation |
| POST | `/conversation/list` | List recent conversations |
| POST | `/tools/google_ai` | Google AI search |
| POST | `/tools/image_analysis` | Vision analysis |
| POST | `/tools/code_execute` | Code execution |
| POST | `/tools/research` | Deep research |

#### Server/services/llm_service.py — LLM Integration

Wrapper around OpenAI-compatible API:

```python
class LLMService:
    def generate(self, messages, tools, context_message=""):
        # Inject context from conversation history
        # Call chat completion API
        # Return parsed response with tool_calls
```

**Features:**
- Temperature/presence penalty tuned to prevent repetition
- Automatic tool validation
- Context injection from recent conversation history

#### Server/services/tts_service.py — Text-to-Speech

Manages local Piper TTS engine:
- Startup/shutdown of Piper process
- WAV synthesis
- Voice selection

#### Server/services/tool_service.py — Tool Orchestration

Routes tool execution requests to appropriate handlers:

```python
class ToolService:
    def google_ai_search(self, query):
        return self.google_ai.execute(query=query)
    
    def execute_code(self, code, timeout):
        return self.code_sandbox.execute(code=code, timeout=timeout)
```

#### Server/services/conversation_store.py — Conversation Persistence

Manages persistent conversation storage:
- Hour-based context retrieval (for RAG-style context injection)
- Conversation list and retrieval
- Automatic and manual save

### Server-Side Tools

#### Server/tools/google_ai.py — Google AI Search

Grounded search using Google Gemini with grounding.

#### Server/tools/image_analysis.py — Vision Analysis

Uses Groq Vision to analyze images described by the client.

#### Server/tools/code_sandbox.py — Safe Code Execution

Isolated Python execution environment:
- Timeout enforcement (default 5s)
- Memory limits
- No filesystem or network access

#### Server/tools/research.py — Research Agent

Multi-step research pipeline using Tavily and Groq LLM.

### Client-Side Tools

#### App/functions/system/ — System Control

| File | Function |
|------|----------|
| `bash_executor.py` | Execute bash/shell commands |
| `smart_home.py` | WiZ light control |
| `smart_home_agent.py` | AI-guided smart home control |
| `system_control.py` | Shutdown, restart, lockscreen |
| `time_tools.py` | Timers, reminders |
| `notifiers.py` | System notifications |
| `sandbox.py` | Local code sandbox |

#### App/functions/web/ — Web Tools

| File | Function |
|------|----------|
| `internet.py` | Web search and scraping |
| `deep_research.py` | Client-side deep research |

#### App/functions/media/ — Media Control

| File | Function |
|------|----------|
| `vision.py` | Screenshot and image capture |
| `music_agent_pear_desktop.py` | Desktop music control |

#### App/functions/productivity/ — Productivity

| File | Function |
|------|----------|
| `calendar.py` | Calendar event management |
| `document_creator.py` | Document/PDF creation |

### Knowledge & Memory

#### App/knowledge/memory.py — Semantic Memory System

Stores memories as natural language sentences rather than key-value pairs:

```
Example storage:
- "YourName's favorite song is Believer by Imagine Dragons"
- "YourName usually wakes up at 7 AM"
```

**Key Features:**
- Single LLM call to format AND check duplicates
- Semantic retrieval via LLM search
- Category-based organization
- Context injection for personalization

#### App/knowledge/ConversationManager.py — History Management

Manages conversation history for recall and search.

### User Interface

#### App/ui/tui.py — Terminal User Interface

Built with Textual framework, provides:

| Component | Purpose |
|-----------|---------|
| `ChatMessage` | Display user/assistant/tool messages |
| `ToolCallPanel` | Show tool execution UI |
| `ModeSwitcher` | Switch between operational modes |
| `StatusBar` | Show connection status |
| `TypingIndicator` | Show when AVA is "thinking" |
| `SettingsScreen` | Configuration editor |
| `HelpOverlay` | Keyboard shortcuts reference |
| `SearchOverlay` | Search conversation history |

**Key Features:**
- Live markdown rendering
- Real-time tool log streaming
- Theme support (light/dark)
- Keyboard shortcuts
- Live transcription display

## Data Flow

### Typical Conversation Flow

```
User Input ─► TUI ─► Message Handler ─► Server API
                                              │
                                              ▼
                                        LLM Service
                                              │
                               ┌───────────────┴───────────────┐
                               ▼                               ▼
                        TTS Svc                        Tool Call
                               │                               │
                               ▼                               ▼
                        Playback                           FuncHandler
                               │                               │
                               ▼                               ▼
                        Audio Output              Tool Result to LLM
```

### Voice Mode Flow (Wake Word)

```
Microphone ─► Vosk Model ─► Wake Detection ─► Whisper STT ─► Process
 (ambient)    (local)       (wake word)       (Groq)
```

## Configuration

### Client Configuration (App/settings.json)

```json
{
  "groq_api_key": "...",
  "user_name": "User",
  "assistant_name": "AVA",
  "ava_server_url": "http://127.0.0.1:8765"
}
```

### Server Configuration (Server/settings.json)

```json
{
  "openai_api_key": "...",
  "openai_base_url": "...",
  "model_name": "kimi-k2-instruct",
  "tts_voice": "en_US-lessac-medium",
  "groq_api_key": "...",
  "google_ai_api_key": "...",
  "tavily_api_key": "...",
  "host": "0.0.0.0",
  "port": 8765
}
```

## Extension Points

### Adding a New Tool (Client-side)

1. Create a new file in `App/functions/<category>/`
2. Implement the function with appropriate parameters
3. Add to `TOOL_CONFIGS` in `App/core/FuncHandler.py`
4. Add tool definition to the tools registry in `messageHandler.py`

**Example:**

```python
# App/functions/productivity/weather.py
def get_weather(location: str) -> str:
    """Get weather for a location."""
    return f"The weather in {location} is sunny."
```

Then add to `FuncHandler.py`:

```python
TOOL_CONFIGS = {
    "weather": get_weather,
}
```

### Adding a New Server Endpoint

1. Implement the handler method in `AvaServer` class
2. Add the route to `self.routes` dictionary

```python
("POST", "/tools/my_tool"): self.my_tool_handler,
```

## Security Considerations

### Code Sandbox

The code sandbox provides isolated execution:
- Restricted imports (only `math`, `random`, `json`, `re`)
- Execution timeout prevents infinite loops
- No filesystem or network access

### API Key Storage

- API keys stored in JSON configuration files
- Consider using environment variables for production
- Keys should never be committed to version control

## Performance Characteristics

| Operation | Expected Latency |
|-----------|-----------------|
| Server health check | < 10ms |
| LLM response (cached) | 100-500ms |
| LLM response (uncached) | 1-3s |
| TTS synthesis (short) | 200-500ms |
| TTS synthesis (long) | 500ms-2s |
| Tool execution (local) | 50-500ms |
| Tool execution (remote) | 500ms-10s |
| Wake word detection | < 100ms |
| Whisper transcription | 500ms-2s |

## Error Handling

The system handles errors at multiple levels:

1. **Server API** — Returns JSON error payloads with appropriate HTTP status codes
2. **Client server_api.py** — Raises exceptions for network failures
3. **FuncHandler.py** — Catches tool execution errors and returns error messages
4. **TUI** — Displays errors in the conversation stream without crashing

## See Also

- `README.md` — Quick start and overview
- `docs/tools-reference.md` — Detailed tool documentation
- `docs/configuration.md` — Configuration reference