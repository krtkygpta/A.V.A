
# A.V.A API Reference

This document describes the HTTP API endpoints exposed by the A.V.A Server.

## Base URL

```
http://localhost:8765
```

---

## Health Check

### GET /health

Check if the server is running.

**Response:**
```json
{
  "status": "ok"
}
```

### POST /health

Same as GET — returns server health status.

---

## Generation

### POST /generate

Generate a response from the LLM with optional tools.

**Request Body:**
```json
{
  "messages": [
    {"role": "system", "content": "You are AVA."},
    {"role": "user", "content": "Hello, how are you?"}
  ],
  "tools": [
    {"type": "function", "function": {"name": "get_time_date", "parameters": {}}}
  ]
}
```

**Response:**
```json
{
  "role": "assistant",
  "content": "Hello! I'm doing great, how can I help you today?",
  "tool_calls": [
    {
      "id": "call_abc123",
      "type": "function",
      "function": {
        "name": "get_time_date",
        "arguments": "{}"
      }
    }
  ]
}
```

---

## Text-to-Speech

### POST /tts

Synthesize text to speech audio.

**Request Body:**
```json
{
  "text": "Hello, how are you today?"
}
```

**Response:**

- **Content-Type:** `audio/wav`
- **Body:** WAV audio bytes

**Error Response (400):**
```json
{
  "error": "Missing 'text'."
}
```

**Error Response (500):**
```json
{
  "error": "Failed to synthesize audio."
}
```

---

## Conversation Management

### POST /conversation/start

Start a new conversation session.

**Request Body:** None required

**Response:**
```json
{
  "ok": true,
  "conversation_id": "conv_20240101_120000"
}
```

---

### POST /conversation/message

Add a message to the current conversation.

**Request Body:**
```json
{
  "role": "user",
  "content": "Hello, can you help me with my schedule?",
  "tool_call_id": "call_abc123"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `role` | string | Yes | Message role: "user", "assistant", or "system" |
| `content` | string | Yes | Message content |
| `tool_call_id` | string | No | Associated tool call ID |

**Response:**
```json
{
  "ok": true,
  "conversation_id": "conv_20240101_120000"
}
```

---

### POST /conversation/save

Persist the current conversation to disk.

**Request Body:** None required

**Response:**
```json
{
  "ok": true
}
```

---

### POST /conversation/list

List recent conversations.

**Request Body:**
```json
{
  "limit": 10
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | integer | 10 | Maximum number of conversations to return |

**Response:**
```json
{
  "conversations": [
    {
      "id": "conv_20240101_120000",
      "created_at": "2024-01-01T12:00:00",
      "message_count": 15,
      "preview": "Hello, can you help me with my schedule?"
    }
  ]
}
```

---

## Tools

### POST /tools/google_ai

Search the web using Google AI with grounding.

**Request Body:**
```json
{
  "query": "What are the best practices for Python programming?"
}
```

**Response:**
```json
{
  "answer": "Here are the best practices for Python...",
  "sources": [
    {"title": "Python.org", "url": "https://python.org"},
    {"title": "Real Python", "url": "https://realpython.com"}
  ],
  "grounded": true
}
```

---

### POST /tools/image_analysis

Analyze an image using Groq Vision.

**Request Body:**
```json
{
  "image_base64": "iVBORw0KGgoAAAANSUhEUgAAAAE...",
  "query": "Describe this image in detail"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `image_base64` | string | Yes | Base64-encoded image data |
| `query` | string | No | Analysis query (default: "Describe this image") |

**Response:**
```json
{
  "description": "The image shows a sunset over the ocean..."
}
```

---

### POST /tools/code_execute

Execute Python code in a sandboxed environment.

**Request Body:**
```json
{
  "code": "import math\nprint(math.sqrt(16))",
  "timeout": 5
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `code` | string | Yes | Python code to execute |
| `timeout` | integer | 5 | Timeout in seconds |

**Response (success):**
```json
{
  "stdout": "4.0\n",
  "stderr": "",
  "return_code": 0,
  "execution_time_ms": 45
}
```

**Response (error):**
```json
{
  "error": "Execution timed out after 5 seconds"
}
```

---

### POST /tools/research

Deep research on a complex topic using Tavily and Groq.

**Request Body:**
```json
{
  "question": "What are the environmental impacts of electric vehicles?"
}
```

**Response:**
```json
{
  "answer": "Electric vehicles have several environmental impacts...",
  "sources": [
    {"title": "EPA", "url": "https://epa.gov/ev"},
    {"title": "Nature Energy", "url": "https://nature.com"}
  ],
  "confidence": 0.85
}
```

---

## Error Handling

All endpoints return errors in this format:
```json
{
  "error": "Error message description"
}
```

**HTTP Status Codes:**

| Code | Meaning |
|------|---------|
| 200 | Success |
| 400 | Bad Request — invalid or missing parameters |
| 404 | Not Found — endpoint doesn't exist |
| 500 | Internal Server Error |

---

## Client-Side Usage

The App module provides helper functions in `core/server_api.py`:

```python
from core.server_api import (
    generate_remote_response,
    synthesize_remote_tts,
    start_remote_conversation,
    add_remote_message,
    save_remote_conversation,
    list_remote_conversations,
)
```

---

## See Also

- `README.md` — Project overview
- `docs/architecture.md` — System architecture
- `docs/tools-reference.md` — Tool documentation
