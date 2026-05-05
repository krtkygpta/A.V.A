# A.V.A Tools Reference

This document provides detailed documentation for all available tools in A.V.A, organized by category.

## System Tools

### get_time_date

Get the current date and time. No parameters required.

**Example:**
```json
{
  "type": "function",
  "function": {
    "name": "get_time_date",
    "parameters": {"type": "object", "properties": {}}
  }
}
```

**Returns:** Current date and time as a formatted string.

---

### shutdown_pc

Shut down or restart the computer.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| action | string | Yes | One of: shutdown, restart, sleep, hibernate |
| delay_seconds | integer | No | Delay before executing |

**Example:**
```json
{"action": "shutdown", "delay_seconds": 60}
```

---

### ping

Set a timer or reminder.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| task | string | Yes | What to be reminded about |
| seconds | integer | Yes | Delay in seconds |

**Example:**
```json
{"task": "Take out trash", "seconds": 1800}
```

---

### send_notification

Send a system notification.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| title | string | Yes | Notification title |
| message | string | Yes | Notification body |

**Example:**
```json
{"title": "Meeting Reminder", "message": "Team sync in 5 minutes"}
```

---

### bash

Execute bash/shell commands.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| command | string | Yes | The shell command to execute |
| timeout | integer | No | Timeout in seconds (default: 30) |

**Example:**
```json
{"command": "ls -la ~/Documents", "timeout": 10}
```

**Returns:** Command output (stdout/stderr).

---

### smart_home

Control WiZ smart lights.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| action | string | Yes | One of: on, off, brightness, color |
| device | string | Yes | Device name or "all" |
| value | integer | No | Brightness (0-100) or color hex |

**Examples:**
```json
{"action": "on", "device": "Living Room"}
{"action": "brightness", "device": "Bedroom", "value": 50}
{"action": "color", "device": "all", "value": "#FF5500"}
```

---

## Web Tools

### web

Search the web and fetch content.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| query | string | Yes | Search query |
| num_results | integer | No | Number of results (default: 5) |

**Example:**
```json
{"query": "weather in London", "num_results": 5}
```

**Returns:** List of search results with titles, URLs, and snippets.

---

### research

Perform deep research on complex topics using Tavily and Groq.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| question | string | Yes | Research question |

**Example:**
```json
{"question": "What are the environmental impacts of electric vehicles?"}
```

**Returns:**
```json
{
  "answer": "Detailed research findings...",
  "sources": [{"title": "Source Title", "url": "https://..."}],
  "confidence": 0.85
}
```

---

## Productivity Tools

### calendar

Manage calendar events.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| action | string | Yes | One of: add, list, delete, today |
| title | string | No | Event title (for add) |
| date | string | No | Date in YYYY-MM-DD format |
| time | string | No | Time in HH:MM format |
| description | string | No | Event description |
| event_id | string | No | Event ID (for delete) |

**Examples:**
```json
{"action": "add", "title": "Team Meeting", "date": "2024-01-15", "time": "14:00"}
{"action": "list", "date": "2024-01-15"}
{"action": "delete", "event_id": "abc123"}
{"action": "today"}
```

---

### create_file

Create a new text file.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| path | string | Yes | File path |
| content | string | Yes | File content |

**Example:**
```json
{"path": "~/Documents/notes.txt", "content": "Meeting notes..."}
```

---

### open_file

Open and read a file.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| path | string | Yes | File path |

**Example:**
```json
{"path": "~/Documents/report.txt"}
```

**Returns:** File contents as a string.

---

### delete_file

Delete a file.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| path | string | Yes | File path to delete |

**Example:**
```json
{"path": "~/Documents/old.txt"}
```

---

### save_text

Save text to a file.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| text | string | Yes | Text to save |
| filename | string | Yes | Output filename |
| directory | string | No | Target directory |

**Example:**
```json
{"text": "Some content", "filename": "output.txt", "directory": "~/Documents"}
```

---

### create_pdf

Generate a PDF document.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| content | string | Yes | Document content (markdown supported) |
| output_path | string | Yes | Output file path |
| title | string | No | Document title |

**Example:**
```json
{"content": "# My Document\n\nContent here...", "output_path": "~/Documents/report.pdf", "title": "My Report"}
```

---

### list_files

List files in a directory.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| path | string | Yes | Directory path |
| pattern | string | No | Filter pattern (e.g., "*.txt") |

**Example:**
```json
{"path": "~/Documents", "pattern": "*.txt"}
```

---

## Media Tools

### image_description_tool

Analyze or describe an image.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| image_path | string | Yes | Path to image file |
| query | string | No | What to look for in the image |

**Example:**
```json
{"image_path": "~/Photos/screenshot.png", "query": "Describe this image"}
```

---

### music_agent

Control desktop music playback.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| action | string | Yes | One of: play, pause, next, previous, volume |
| value | integer | No | Volume level 0-100 (for volume action) |

**Examples:**
```json
{"action": "play"}
{"action": "volume", "value": 50}
```

---

## AI Tools

### code_executor

Execute Python code in a sandboxed environment.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| code | string | Yes | Python code to execute |
| timeout | integer | No | Timeout in seconds (default: 5) |

**Example:**
```json
{"code": "import math\nprint(math.sqrt(16))", "timeout": 5}
```

**Response (success):**
```json
{
  "stdout": "4.0\n",
  "stderr": "",
  "return_code": 0,
  "execution_time_ms": 45
}
```

**Allowed Imports:** math, random, json, re, datetime, time, numpy, scipy

**Security:** No filesystem or network access permitted.

---

### google_ai

Grounded search using Google AI (server-side).

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| query | string | Yes | Search query |

**Example:**
```json
{"query": "latest Python best practices"}
```

---

### analyze_image

Analyze images using Groq Vision (server-side).

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| image_base64 | string | Yes | Base64-encoded image |
| query | string | No | Analysis query |

**Example:**
```json
{"image_base64": "iVBORw0KGgoAAAANSUhEUgAAAAE...", "query": "Describe this image"}
```

---

## Memory Tools

### memory_manager

Manage the semantic memory system.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| action | string | Yes | One of: save, retrieve, delete, categories |
| category | string | No | Memory category |
| key | string | No | Memory identifier |
| value | string | No | Information to remember |
| search | string | No | Search query |

**Actions:**

**Save a Memory:**
```json
{"action": "save", "category": "favorites", "key": "favorite_color", "value": "blue"}
```

**Retrieve Memories:**
```json
{"action": "retrieve", "search": "What are my preferences?"}
```

**Delete a Memory:**
```json
{"action": "delete", "key": "favorite_color"}
```

**List Categories:**
```json
{"action": "categories"}
```

---

### conversation_history

Search past conversations.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| query | string | Yes | Search query |
| limit | integer | No | Maximum results (default: 10) |

**Example:**
```json
{"query": "previous discussion about Python", "limit": 5}
```

---

## Background Tasks

### background_task

Start a long-running task in the background.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| task_type | string | Yes | Type of task to run |
| params | object | No | Task-specific parameters |

**Example:**
```json
{"task_type": "web_search", "params": {"query": "latest AI news"}}
```

---

### get_background_tasks_status

Check status of all running background tasks. No parameters required.

**Returns:** Summary of all background tasks with their status.

---

## Tool Development Guide

### Creating a New Tool

1. Implement the function in the appropriate category:
```python
# App/functions/productivity/weather.py
def get_weather(location: str) -> str:
    """Get weather information for a location."""
    return f"The weather in {location} is sunny, 72F."
```

2. Register in `App/core/FuncHandler.py`:
```python
TOOL_CONFIGS = {
    "weather": get_weather,
}
```

3. Add tool definition to the tools registry in `messageHandler.py`.

### Tool Return Format

Tools should return strings. For structured data, serialize to JSON:
```python
import json

def my_tool(param: str) -> str:
    result = {"status": "success", "data": param}
    return json.dumps(result)
```

### Error Handling

Return error messages as strings:
```python
def my_tool(param: str) -> str:
    try:
        return "Success message"
    except Exception as e:
        return f"Error: {str(e)}"
```

---

## See Also

- `README.md` — Project overview
- `docs/architecture.md` — System architecture
- `docs/getting-started.md` — Quick start guide
- `docs/api-reference.md` — HTTP API documentation