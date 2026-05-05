# A.V.A Memory System

This document describes the semantic memory system that allows A.V.A to remember information about the user across sessions.

## Overview

Unlike traditional key-value storage, A.V.A stores memories as natural language sentences. This approach:

- **Feels natural** — Memories are stored exactly as a person would describe them
- **Scales gracefully** — No schema management required
- **Enables semantic search** — Find relevant memories using natural queries

## How It Works

### Storage Format

Memories are stored in `App/data/memories/semantic_memories.json`:

```json
{
  "memories": [
    {
      "text": "YourName's favorite color is blue",
      "category": "preferences",
      "timestamp": "2024-01-15T10:30:00",
      "original_key": "favorite_color",
      "original_value": "blue"
    }
  ],
  "last_updated": "2024-01-15T10:30:00"
}
```

### Memory Lifecycle

```
User says: "Remember that I love pizza"
              │
              ▼
┌─────────────────────────────────────────┐
│  LLM Formats + Checks Duplicates        │
│                                         │
│  - Converts to natural sentence         │
│  - Checks for existing similar memories │
│  - Returns: "YourName loves pizza"      │
└─────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│  Storage                                │
│                                         │
│  - If duplicate found → UPDATE entry    │
│  - If new → APPEND to memories         │
└─────────────────────────────────────────┘
```

## Memory Categories

Memories are organized into categories:

| Category | Description | Example |
|----------|-------------|---------|
| `preferences` | User preferences and favorites | favorite color, music genre |
| `habits` | Regular behaviors and schedules | wake time, exercise routine |
| `facts` | Factual information | job title, location |
| `general` | Uncategorized memories | — |

## Usage

### Automatic Memory Management

A.V.A automatically saves memories when you share information about yourself:

```
You: "My favorite movie is Inception"
AVA: "Got it! I'll remember that Inception is your favorite movie."
```

### Manual Memory Operations

You can also explicitly ask A.V.A to remember things:

```
You: "Remember my birthday is March 15th"
You: "Save that I prefer dark mode"
```

## Retrieval

### Natural Language Search

Ask A.V.A to recall information:

```
You: "What do you remember about me?"
You: "What are my preferences?"
```

### Semantic Matching

A.V.A uses an LLM to find relevant memories based on your query:

```
You: "What time do I usually wake up?"
AVA: "Based on what I remember, you usually wake up at 7 AM."
```

## Memory Manager Tool

The `memory_manager` tool provides direct access to memory operations:

### Save a Memory

```json
{
  "action": "save",
  "category": "preferences",
  "key": "favorite_sport",
  "value": "basketball"
}
```

**Result:** "Saved! I'll remember that."

### Retrieve Memories

```json
{
  "action": "retrieve",
  "search": "sports"
}
```

### Delete a Memory

```json
{
  "action": "delete",
  "key": "favorite_sport"
}
```

**Result:** "Deleted: YourName's favorite sport is basketball"

### List Categories

```json
{
  "action": "categories"
}
```

**Result:**
```
I have 5 memories about you:
  - preferences: 2 memories
  - habits: 2 memories
  - general: 1 memories
```

## Context Injection

A.V.A automatically includes relevant memories in the system prompt before each conversation.

The last 30 memories are included to provide personalized responses without overwhelming the context window.

## Duplicates and Updates

### How Duplicates Are Detected

When saving a memory, A.V.A checks if a similar memory already exists:

```
User: "I love coding in Python"
Existing: "YourName loves coding in Python"

→ UPDATE existing memory (no duplicate created)
```

```
User: "My favorite IDE is VS Code"
Existing: "YourName's favorite IDE is PyCharm"

→ CREATE new memory (different topic)
```

### Update Strategy

Rather than keeping old memories, A.V.A updates existing memories when the same fact changes:

```
Before: "YourName's favorite color is red"
After:  "YourName's favorite color is blue"
```

## Configuration

### Memory Directory

Memories are stored in `App/data/memories/`. To change this location, edit `App/knowledge/memory.py`:

```python
MEMORIES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "memories")
MEMORY_FILE = os.path.join(MEMORIES_DIR, "semantic_memories.json")
```

### LLM Model for Memory Operations

Memory formatting and search use `llama-3.1-8b-instant` for efficiency:

```python
response = client.chat.completions.create(
    model="llama-3.1-8b-instant",
    messages=[...],
    max_tokens=150,
    temperature=0.3
)
```

## Privacy Considerations

### Data Storage

- Memories are stored locally in JSON format
- No data is sent to external servers
- The `USER_NAME` from settings is used to personalize memory text

### What Gets Stored

A.V.A only stores information that you explicitly share or confirm:

- Explicit statements: "My name is John"
- Preferences you mention: "I prefer tea over coffee"
- Facts you share: "I work at Company X"

**Never stored unless you tell it to:** passwords, credentials, financial information

### Managing Privacy

You can delete specific memories:

```
You: "Forget that I told you my birthday"
You: "Delete my favorite color memory"
```

## Troubleshooting

### Memories Not Saving

1. Check that `App/data/memories/` directory exists
2. Verify write permissions
3. Check the JSON file isn't corrupted

### Duplicates Not Being Detected

The duplicate detection looks for semantically similar memories. If two memories seem similar but aren't being merged:

- The topics might be different enough
- Try using the same key when saving related information

### Memory Search Returns Nothing

The semantic search uses an LLM. If search isn't working:

- Check that the Groq API key is valid
- Try rephrasing your search query
- Ensure the LLM service is accessible

## See Also

- `README.md` — Project overview
- `docs/architecture.md` — System architecture
- `docs/configuration.md` — Settings reference