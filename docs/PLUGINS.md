# Plugin System

A.V.A supports a plugin-based tool system that lets you add new capabilities without modifying core code.

## Quick Start

### 1. Create a Plugin File

Create a new `.py` file in `App/functions/plugins/`:

```C:\Data\Codes\A.V.A\App\functions\plugins\my_tools.py#L1-25
from core.tool_registry import tool


@tool(
    name="my_tool",
    description="Does something useful",
    params={
        "input": {"type": "string", "description": "What to process"}
    },
    required=["input"]
)
def my_tool(input: str) -> str:
    """Optional docstring for the function"""
    return f"Processed: {input}"
```

### 2. Done!

Tools auto-load on startup. No registration or configuration needed.

---

## The `@tool` Decorator

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | `str` | No | Tool name (defaults to function name) |
| `description` | `str` | Yes | What the tool does (used by LLM) |
| `params` | `dict` | Yes | Parameter schema for OpenAI |
| `required` | `list` | No | List of required parameter names |

### `params` Structure

The `params` dict follows OpenAI's function calling schema:

```C:\Data\Codes\A.V.A\App\functions\plugins\example_schema.py#L1-30
params={
    "param_name": {
        "type": "string",           # string, integer, number, boolean, array, object
        "description": "What this parameter does",
        "enum": ["a", "b", "c"]     # optional:限定可选值
    }
}
```

### Examples

**Single parameter:**
```C:\Data\Codes\A.V.A\App\functions\plugins\examples\quick.py#L1-15
@tool(
    name="get_weather",
    description="Get current weather for a city",
    params={
        "city": {
            "type": "string",
            "description": "City name (e.g., 'Tokyo', 'London')"
        }
    },
    required=["city"]
)
def get_weather(city: str) -> str:
    # Your implementation
    return f"Weather in {city}: Sunny, 72°F"
```

**Multiple parameters:**
```C:\Data\Codes\A.V.A\App\functions\plugins\examples\advanced.py#L1-25
@tool(
    name="send_email",
    description="Send an email to a recipient",
    params={
        "to": {"type": "string", "description": "Recipient email address"},
        "subject": {"type": "string", "description": "Email subject"},
        "body": {"type": "string", "description": "Email body content"},
        "cc": {"type": "string", "description": "CC recipient (optional)"}
    },
    required=["to", "subject", "body"]
)
def send_email(to: str, subject: str, body: str, cc: str = "") -> str:
    # Your implementation
    return f"Email sent to {to}"
```

**Enum (constrained values):**
```C:\Data\Codes\A.V.A\App\functions\plugins\examples\enum_example.py#L1-15
@tool(
    name="set_volume",
    description="Set system volume level",
    params={
        "level": {
            "type": "string",
            "description": "Volume level",
            "enum": ["mute", "low", "medium", "high", "max"]
        }
    },
    required=["level"]
)
def set_volume(level: str) -> str:
    return f"Volume set to {level}"
```

---

## Best Practices

### 1. Return Strings
Always return a `str`. The system coerces other types, but explicit strings work best:

```python
# Good
def my_tool() -> str:
    return "Success: task completed"

# Avoid
def my_tool() -> dict:
    return {"status": "ok"}  # Will be JSON-serialized
```

### 2. Handle Errors Gracefully
Catch exceptions and return meaningful error messages:

```python
@tool(name="fetch_data", description="Fetch data from API", params={...})
def fetch_data(url: str) -> str:
    try:
        import urllib.request
        response = urllib.request.urlopen(url, timeout=5)
        return response.read().decode()
    except Exception as e:
        return f"Error: {e}"
```

### 3. Keep Descriptions Clear
The LLM uses descriptions to decide when to call your tool:

```python
# Good - specific and actionable
description="Search the web for current information on any topic"

# Bad - vague
description="Search function"
```

### 4. Group Related Tools
Put related tools in the same file:

```C:\Data\Codes\A.V.A\App\functions\plugins\weather_tools.py#L1-30
# weather_tools.py
from core.tool_registry import tool

@tool(name="get_weather", ...)
def get_weather(city: str) -> str: ...

@tool(name="get_forecast", ...)
def get_forecast(city: str, days: int) -> str: ...

@tool(name="get_conditions", ...)
def get_conditions(location: str) -> str: ...
```

---

## Tool Execution Flow

```
User Input
    ↓
LLM decides to call tool
    ↓
handle_tool_call() in FuncHandler.py
    ↓
Checks registry.get(name) first
    ↓ Falls back to
TOOL_CONFIGS (legacy tools)
    ↓
Function executes, returns string
    ↓
Result fed back to LLM for response
```

---

## Troubleshooting

### Tool not loading?
- Check for import errors in your plugin file
- Ensure `@tool` decorator is used
- Look for errors in console output during startup

### Tool not being called?
- Verify `description` is clear and descriptive
- Ensure `params` schema matches expected input
- Check that `required` list includes all mandatory params

### Import errors?
Plugins are loaded in isolation. You can safely import packages that aren't in `functions/__init__.py`:

```python
from core.tool_registry import tool
import my_custom_package  # This works!
```

---

## Example: Complete Plugin

```C:\Data\Codes\A.V.A\App\functions\plugins\todo_plugin.py#L1-50
"""
Todo list plugin for A.V.A
"""
from core.tool_registry import tool


@tool(
    name="add_todo",
    description="Add a task to the todo list",
    params={
        "task": {"type": "string", "description": "The task to add"},
        "priority": {
            "type": "string",
            "description": "Priority level",
            "enum": ["low", "medium", "high"]
        }
    },
    required=["task", "priority"]
)
def add_todo(task: str, priority: str) -> str:
    """Add a todo item"""
    # Implementation here
    return f"Added: {task} (priority: {priority})"


@tool(
    name="list_todos",
    description="Show all todo items",
    params={},
    required=[]
)
def list_todos() -> str:
    """List all todo items"""
    # Implementation here
    return "Your todos:\n1. Buy groceries (high)\n2. Call mom (medium)"
```
