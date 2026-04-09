"""
memory.py - Semantic Memory System for AVA

Stores memories as natural language sentences instead of key-value pairs.
Uses LLM to find relevant memories when retrieving.

Example memories:
- "Kartikey's favorite song is Believer by Imagine Dragons"
- "Kartikey usually wakes up at 7 AM"
- "Kartikey's best friend is named Rohan"
"""

import json
import os
from config import USER_NAME
from datetime import datetime
from typing import Optional, List, Dict, Any
from groq import Groq

# Path to memories
MEMORIES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "memories")
MEMORY_FILE = os.path.join(MEMORIES_DIR, "semantic_memories.json")

# Groq client for semantic operations
_client = None

def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    return _client


def _ensure_dir():
    """Create memories directory if it doesn't exist."""
    if not os.path.exists(MEMORIES_DIR):
        os.makedirs(MEMORIES_DIR)


def _load_memories() -> Dict[str, Any]:
    """Load all memories from file."""
    _ensure_dir()
    
    if not os.path.exists(MEMORY_FILE):
        initial = {"memories": [], "last_updated": None}
        _save_memories(initial)
        return initial
    
    try:
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"memories": [], "last_updated": None}


def _save_memories(data: Dict[str, Any]):
    """Save memories to file."""
    _ensure_dir()
    data["last_updated"] = datetime.now().isoformat()
    
    with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _format_and_check_duplicate(key: str, value: str, memories: List[Dict]) -> tuple:
    """
    Single LLM call to both format a memory as natural language AND check for duplicates.
    Returns (formatted_text: str, duplicate_index: int) where duplicate_index is -1 if new.
    
    Previously this was two separate LLM calls (_format_memory_text + _find_duplicate),
    doubling the API latency for every memory save.
    """
    existing = ""
    if memories:
        existing = "\n".join([f"{i}. {m['text']}" for i, m in enumerate(memories[-20:])])
    
    try:
        client = _get_client()
        
        prompt = f"""Do two things:
1. Convert this key-value pair into a simple, natural sentence about {USER_NAME}.
   Key: {key}
   Value: {value}
   Example: Key: favorite_song, Value: Believer → "{USER_NAME}'s favorite song is Believer."

2. Check if the sentence updates or replaces any EXISTING memory below. If yes, respond with the number. If not, respond with NEW.
"""
        if existing:
            prompt += f"\nEXISTING MEMORIES:\n{existing}\n"
        else:
            prompt += "\nNo existing memories yet.\n"
        
        prompt += "\nRespond in EXACTLY this format (two lines):\nSENTENCE: <the natural language sentence>\nDUPLICATE: <number or NEW>"
        
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0.3
        )
        
        result_text = response.choices[0].message.content.strip()
        
        # Parse the response
        sentence = ""
        duplicate_idx = -1
        
        for line in result_text.split("\n"):
            line = line.strip()
            if line.upper().startswith("SENTENCE:"):
                sentence = line[len("SENTENCE:"):].strip().strip('"\'')
            elif line.upper().startswith("DUPLICATE:"):
                dup_val = line[len("DUPLICATE:"):].strip()
                if dup_val.upper() != "NEW":
                    try:
                        idx = int(dup_val)
                        if 0 <= idx < len(memories):
                            duplicate_idx = idx
                    except ValueError:
                        pass
        
        # Ensure sentence mentions the user
        if sentence and USER_NAME.lower() not in sentence.lower():
            sentence = f"{USER_NAME}: {sentence}"
        
        # Fallback if parsing failed
        if not sentence:
            sentence = f"{USER_NAME}'s {key.replace('_', ' ')} is {value}"
        
        return sentence, duplicate_idx
        
    except Exception as e:
        print(f"[Memory] Format/duplicate check error: {e}")
        return f"{USER_NAME}'s {key.replace('_', ' ')} is {value}", -1


def save_memory(category: str, key: str, value: str) -> str:
    """
    Save a memory as a natural language sentence.
    
    Args:
        category: Category hint (favorites, facts, etc.) - used for organization
        key: What this is about
        value: The actual information
    
    Returns:
        Confirmation message
    """
    if not key or not value:
        return "Error: Both key and value are required"
    
    data = _load_memories()
    memories = data.get("memories", [])
    
    # Single LLM call: format the memory AND check for duplicates
    memory_text, duplicate_idx = _format_and_check_duplicate(key, value, memories)
    
    memory_entry = {
        "text": memory_text,
        "category": category or "general",
        "timestamp": datetime.now().isoformat(),
        "original_key": key,
        "original_value": value
    }
    
    if duplicate_idx >= 0:
        # Update existing memory
        memories[duplicate_idx] = memory_entry
        action = "Updated"
    else:
        # Add new memory
        memories.append(memory_entry)
        action = "Saved"
    
    data["memories"] = memories
    _save_memories(data)
    
    return f"{action}! I'll remember that."


def retrieve_memories(category: Optional[str] = None, search: Optional[str] = None) -> str:
    """
    Retrieve relevant memories using semantic search.
    
    Args:
        category: Optional category filter
        search: Search query to find relevant memories
    
    Returns:
        Formatted string of relevant memories
    """
    data = _load_memories()
    memories = data.get("memories", [])
    
    if not memories:
        return "I don't have any memories about you yet. Tell me things about yourself!"
    
    # Filter by category if specified
    if category:
        memories = [m for m in memories if m.get("category") == category]
    
    # If search query provided, use LLM to find relevant memories
    if search:
        try:
            client = _get_client()
            
            all_memories = "\n".join([f"- {m['text']}" for m in memories])
            
            response = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{
                    "role": "user",
                    "content": f"""Find memories relevant to this query.

ALL MEMORIES:
{all_memories}

QUERY: "{search}"

Return ONLY the relevant memories, one per line. If none are relevant, say "No relevant memories found."
"""
                }],
                max_tokens=300,
                temperature=0.2
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            print(f"[Memory] Search error: {e}")
            # Fallback to returning all
    
    # Return all memories
    return "Here's what I remember:\n" + "\n".join([f"- {m['text']}" for m in memories])


def get_all_memories_for_context() -> str:
    """
    Get all memories formatted for injection into the system prompt.
    """
    data = _load_memories()
    memories = data.get("memories", [])
    
    if not memories:
        return ""
    
    lines = [f"[THINGS I REMEMBER ABOUT {USER_NAME.upper()}]"]
    for m in memories[-30:]:  # Last 30 memories
        lines.append(f"- {m['text']}")
    
    return "\n".join(lines)


def delete_memory(category: str = None, key: str = None) -> str:
    """Delete a memory by searching for it."""
    if not key:
        return "Please specify what memory to delete"
    
    data = _load_memories()
    memories = data.get("memories", [])
    
    if not memories:
        return "No memories to delete"
    
    # Find matching memory
    search_term = key.lower()
    matching_indices = []
    
    for i, m in enumerate(memories):
        if search_term in m.get("text", "").lower() or search_term in m.get("original_key", "").lower():
            matching_indices.append(i)
    
    if not matching_indices:
        return f"No memories found matching '{key}'"
    
    # Delete the first match
    deleted = memories.pop(matching_indices[0])
    data["memories"] = memories
    _save_memories(data)
    
    return f"Deleted: {deleted['text']}"


def list_categories() -> str:
    """List memory statistics by category."""
    data = _load_memories()
    memories = data.get("memories", [])
    
    if not memories:
        return "No memories stored yet."
    
    # Count by category
    categories = {}
    for m in memories:
        cat = m.get("category", "general")
        categories[cat] = categories.get(cat, 0) + 1
    
    lines = [f"I have {len(memories)} memories about you:"]
    for cat, count in categories.items():
        lines.append(f"  - {cat}: {count} memories")
    
    return "\n".join(lines)


# Main handler function called by FuncHandler
def handle_memory_manager(action: str, category: str = None, key: str = None, 
                          value: str = None, search: str = None) -> str:
    """
    Main memory management function called by the LLM.
    
    Actions:
        - save: Save a new memory
        - retrieve: Get memories (semantic search)
        - delete: Remove a memory
        - categories: List memory stats
    """
    try:
        action = (action or "").lower().strip()
        
        if action == "save":
            if not key or not value:
                return "Please provide what to remember (key) and the information (value)"
            return save_memory(category or "general", key, value)
        
        elif action in ["retrieve", "get", "recall", "search"]:
            return retrieve_memories(category=category, search=search or key)
        
        elif action in ["delete", "remove", "forget"]:
            return delete_memory(category, key)
        
        elif action in ["categories", "list", "stats"]:
            return list_categories()
        
        else:
            return f"Unknown action: {action}. Use: save, retrieve, delete, or categories"
    
    except Exception as e:
        print(f"[Memory] Error: {e}")
        return f"Memory error: {str(e)}"


# Initialize on import
_ensure_dir()
