"""
ConversationManager.py - Manages conversation threads for AVA

Features:
- Each wakeword activation creates a new conversation thread
- Conversations are named based on their content (auto-generated)
- All conversations are saved to disk for future reference
- Model can reference old conversations when needed
- Prevents context overload by keeping current conversation focused
"""

import json
import os
import re
from datetime import datetime
from typing import Optional, List, Dict, Any
from groq import Groq

# Directory to store conversations
CONVERSATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "conversations")

# Groq client for naming conversations
client = Groq(api_key=os.getenv("GROQ_API_KEY"))


def _ensure_dir():
    """Create conversations directory if it doesn't exist."""
    if not os.path.exists(CONVERSATIONS_DIR):
        os.makedirs(CONVERSATIONS_DIR)


class Conversation:
    """Represents a single conversation thread."""
    
    def __init__(self, conversation_id: str = None):
        self.id = conversation_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.name = "New Conversation"
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()
        self.messages: List[Dict[str, Any]] = []
        self.summary = ""
        self.topics: List[str] = []
    
    def add_message(self, role: str, content: str, tool_call_id: str = None):
        """Add a message to the conversation."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        if tool_call_id:
            msg["tool_call_id"] = tool_call_id
        self.messages.append(msg)
        self.updated_at = datetime.now().isoformat()
    
    def get_messages_for_api(self) -> List[Dict[str, str]]:
        """Get messages in format suitable for LLM API (without timestamps)."""
        api_messages = []
        for msg in self.messages:
            api_msg = {"role": msg["role"], "content": msg["content"]}
            if "tool_call_id" in msg:
                api_msg["tool_call_id"] = msg["tool_call_id"]
            api_messages.append(api_msg)
        return api_messages
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert conversation to dictionary for saving."""
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": self.messages,
            "summary": self.summary,
            "topics": self.topics
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Conversation':
        """Create conversation from dictionary."""
        conv = cls(data.get("id"))
        conv.name = data.get("name", "Unnamed")
        conv.created_at = data.get("created_at", conv.created_at)
        conv.updated_at = data.get("updated_at", conv.updated_at)
        conv.messages = data.get("messages", [])
        conv.summary = data.get("summary", "")
        conv.topics = data.get("topics", [])
        return conv
    
    def get_user_messages_text(self) -> str:
        """Get all user messages concatenated for naming."""
        user_msgs = [m["content"] for m in self.messages if m["role"] == "user"]
        return " ".join(user_msgs[:5])  # First 5 user messages


class ConversationManager:
    """
    Singleton manager for all conversations.
    Handles creating, saving, loading, and switching between conversations.
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        _ensure_dir()
        self.current_conversation: Optional[Conversation] = None
        self.conversations_index: Dict[str, Dict[str, str]] = {}  # id -> {name, created_at, summary}
        self._load_index()
        self._initialized = True
    
    def _load_index(self):
        """Load the conversations index."""
        index_path = os.path.join(CONVERSATIONS_DIR, "index.json")
        if os.path.exists(index_path):
            try:
                with open(index_path, 'r', encoding='utf-8') as f:
                    self.conversations_index = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.conversations_index = {}
    
    def _save_index(self):
        """Save the conversations index."""
        index_path = os.path.join(CONVERSATIONS_DIR, "index.json")
        with open(index_path, 'w', encoding='utf-8') as f:
            json.dump(self.conversations_index, f, indent=2, ensure_ascii=False)
    
    def start_new_conversation(self) -> Conversation:
        """Start a new conversation thread. Called on each wakeword."""
        # Save current conversation if exists
        if self.current_conversation and len(self.current_conversation.messages) > 0:
            self.save_conversation()
        
        # Create new conversation
        self.current_conversation = Conversation()
        print(f"[ConversationManager] Started new conversation: {self.current_conversation.id}")
        return self.current_conversation
    
    def save_conversation(self):
        """Save current conversation to disk."""
        if not self.current_conversation:
            return
        
        conv = self.current_conversation
        
        # Generate name if still default and has messages
        if conv.name == "New Conversation" and len(conv.messages) > 1:
            conv.name = self._generate_name(conv)
            conv.summary = self._generate_summary(conv)
        
        # Save conversation file
        filepath = os.path.join(CONVERSATIONS_DIR, f"{conv.id}.json")
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(conv.to_dict(), f, indent=2, ensure_ascii=False)
        
        # Update index
        self.conversations_index[conv.id] = {
            "name": conv.name,
            "created_at": conv.created_at,
            "updated_at": conv.updated_at,
            "summary": conv.summary,
            "message_count": len(conv.messages)
        }
        self._save_index()
        
        print(f"[ConversationManager] Saved conversation: {conv.name}")
    
    def _generate_name(self, conv: Conversation) -> str:
        """Generate a short, descriptive name for the conversation using LLM."""
        try:
            user_text = conv.get_user_messages_text()
            if not user_text.strip():
                return f"Conversation {conv.id}"
            
            response = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{
                    "role": "user",
                    "content": f"Generate a very short title (3-5 words max) for this conversation. Just the title, nothing else:\n\n{user_text[:500]}"
                }],
                max_tokens=20,
                temperature=0.3
            )
            
            name = response.choices[0].message.content.strip()
            # Clean up the name (remove quotes, punctuation)
            name = re.sub(r'^["\']+|["\']+$', '', name)
            name = name[:50]  # Limit length
            return name if name else f"Conversation {conv.id}"
            
        except Exception as e:
            print(f"[ConversationManager] Error generating name: {e}")
            return f"Conversation {conv.id}"
    
    def _generate_summary(self, conv: Conversation) -> str:
        """Generate a brief summary of the conversation."""
        try:
            # Get user and assistant messages
            dialogue = []
            for msg in conv.messages[:10]:  # First 10 messages
                role = "User" if msg["role"] == "user" else "AVA"
                if msg["role"] in ["user", "assistant"]:
                    dialogue.append(f"{role}: {msg['content'][:200]}")
            
            if not dialogue:
                return ""
            
            dialogue_text = "\n".join(dialogue)
            
            response = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{
                    "role": "user",
                    "content": f"Summarize this conversation in 1-2 sentences:\n\n{dialogue_text}"
                }],
                max_tokens=100,
                temperature=0.3
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            print(f"[ConversationManager] Error generating summary: {e}")
            return ""
    
    def load_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """Load a specific conversation from disk."""
        filepath = os.path.join(CONVERSATIONS_DIR, f"{conversation_id}.json")
        if not os.path.exists(filepath):
            return None
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return Conversation.from_dict(data)
        except (json.JSONDecodeError, IOError) as e:
            print(f"[ConversationManager] Error loading conversation: {e}")
            return None
    
    def get_recent_conversations(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get list of recent conversations (for LLM context)."""
        # Sort by updated_at descending
        sorted_convos = sorted(
            self.conversations_index.items(),
            key=lambda x: x[1].get("updated_at", ""),
            reverse=True
        )
        
        result = []
        for conv_id, info in sorted_convos[:limit]:
            result.append({
                "id": conv_id,
                "name": info.get("name", "Unnamed"),
                "summary": info.get("summary", ""),
                "date": info.get("created_at", "")[:10]  # Just the date
            })
        return result
    
    def get_conversation_context_for_llm(self) -> str:
        """Get formatted context about past conversations for the LLM."""
        recent = self.get_recent_conversations(5)
        if not recent:
            return ""
        
        lines = ["[PREVIOUS CONVERSATIONS - You can reference these if relevant]"]
        for conv in recent:
            lines.append(f"- \"{conv['name']}\" ({conv['date']}): {conv['summary']}")
        
        return "\n".join(lines)
    
    def search_conversations(self, query: str) -> List[Dict[str, Any]]:
        """Search through past conversations."""
        query_lower = query.lower()
        results = []
        
        for conv_id, info in self.conversations_index.items():
            name = info.get("name", "").lower()
            summary = info.get("summary", "").lower()
            
            if query_lower in name or query_lower in summary:
                results.append({
                    "id": conv_id,
                    "name": info.get("name"),
                    "summary": info.get("summary"),
                    "match": "name" if query_lower in name else "summary"
                })
        
        return results
    
    def get_conversation_details(self, conversation_id: str) -> Optional[str]:
        """Get full details of a past conversation for the LLM to reference."""
        conv = self.load_conversation(conversation_id)
        if not conv:
            return None
        
        lines = [f"[CONVERSATION: {conv.name}]", f"Date: {conv.created_at[:10]}", ""]
        
        for msg in conv.messages:
            if msg["role"] == "user":
                lines.append(f"User: {msg['content']}")
            elif msg["role"] == "assistant":
                lines.append(f"FRIDAY: {msg['content']}")
        
        return "\n".join(lines)


# Global instance
_manager = None


def get_manager() -> ConversationManager:
    """Get the singleton ConversationManager instance."""
    global _manager
    if _manager is None:
        _manager = ConversationManager()
    return _manager


def start_new_conversation() -> Conversation:
    """Convenience function to start a new conversation."""
    return get_manager().start_new_conversation()


def save_current_conversation():
    """Convenience function to save current conversation."""
    get_manager().save_conversation()


def get_current_conversation() -> Optional[Conversation]:
    """Get the current active conversation."""
    return get_manager().current_conversation


def get_past_conversations_context() -> str:
    """Get context about past conversations for LLM."""
    return get_manager().get_conversation_context_for_llm()


def search_conversations_by_date(date_str: str = None, time_of_day: str = None) -> List[Dict[str, Any]]:
    """
    Search conversations by date and/or time of day.
    
    Args:
        date_str: Date in various formats (e.g., "2026-01-26", "January 26", "26th", "yesterday")
        time_of_day: "morning", "afternoon", "evening", "night"
    
    Returns:
        List of matching conversations
    """
    from datetime import datetime, timedelta
    
    mgr = get_manager()
    results = []
    
    # Parse date
    target_date = None
    if date_str:
        date_lower = date_str.lower()
        today = datetime.now()
        
        if "today" in date_lower:
            target_date = today.date()
        elif "yesterday" in date_lower:
            target_date = (today - timedelta(days=1)).date()
        else:
            # Try to parse various date formats
            for fmt in ["%Y-%m-%d", "%d-%m-%Y", "%B %d", "%d %B", "%d/%m/%Y", "%m/%d/%Y"]:
                try:
                    parsed = datetime.strptime(date_str, fmt)
                    # If year not specified, assume current year
                    if parsed.year == 1900:
                        parsed = parsed.replace(year=today.year)
                    target_date = parsed.date()
                    break
                except ValueError:
                    continue
    
    # Time of day ranges (in hours)
    time_ranges = {
        "morning": (5, 12),
        "afternoon": (12, 17),
        "evening": (17, 21),
        "night": (21, 24),
        "late night": (0, 5)
    }
    
    for conv_id, info in mgr.conversations_index.items():
        created_at = info.get("created_at", "")
        if not created_at:
            continue
        
        try:
            conv_datetime = datetime.fromisoformat(created_at)
            conv_date = conv_datetime.date()
            conv_hour = conv_datetime.hour
            
            # Check date match
            date_match = target_date is None or conv_date == target_date
            
            # Check time of day match
            time_match = True
            if time_of_day:
                time_range = time_ranges.get(time_of_day.lower())
                if time_range:
                    start_hour, end_hour = time_range
                    time_match = start_hour <= conv_hour < end_hour
            
            if date_match and time_match:
                results.append({
                    "id": conv_id,
                    "name": info.get("name"),
                    "summary": info.get("summary"),
                    "date": created_at[:10],
                    "time": created_at[11:16]
                })
        except (ValueError, IndexError):
            continue
    
    return results


def handle_conversation_history(action: str, query: str = None, date: str = None, 
                                time_of_day: str = None, conversation_id: str = None) -> str:
    """
    Handler function for the LLM to search and retrieve past conversations.
    
    Args:
        action: "search" (find conversations), "get" (retrieve full conversation), "list" (show recent)
        query: Topic or keyword to search for
        date: Date to filter by (e.g., "2026-01-26", "yesterday", "January 13")
        time_of_day: "morning", "afternoon", "evening", "night"
        conversation_id: Specific conversation ID to retrieve
    
    Returns:
        Formatted string with results
    """
    mgr = get_manager()
    action = (action or "list").lower()
    
    if action == "get" and conversation_id:
        # Get full conversation details
        details = mgr.get_conversation_details(conversation_id)
        if details:
            return details
        return f"Conversation '{conversation_id}' not found."
    
    elif action == "search":
        results = []
        
        # Search by date/time if provided
        if date or time_of_day:
            results = search_conversations_by_date(date, time_of_day)
        
        # Also search by query if provided
        if query:
            query_results = mgr.search_conversations(query)
            if results:
                # Merge results (date + query)
                result_ids = {r["id"] for r in results}
                for qr in query_results:
                    if qr["id"] not in result_ids:
                        results.append(qr)
            else:
                results = query_results
        
        if not results:
            msg = "No conversations found"
            if date:
                msg += f" on {date}"
            if time_of_day:
                msg += f" in the {time_of_day}"
            if query:
                msg += f" about '{query}'"
            return msg
        
        lines = [f"Found {len(results)} conversation(s):"]
        for r in results:
            date_str = r.get("date", "")
            time_str = r.get("time", "")
            lines.append(f"- [{r['id']}] {r['name']} ({date_str} {time_str})")
            if r.get("summary"):
                lines.append(f"  Summary: {r['summary'][:100]}...")
        
        lines.append("\nUse action='get' with conversation_id to see full details.")
        return "\n".join(lines)
    
    elif action == "list":
        recent = mgr.get_recent_conversations(10)
        if not recent:
            return "No past conversations found."
        
        lines = ["Recent conversations:"]
        for conv in recent:
            lines.append(f"- [{conv['id']}] {conv['name']} ({conv['date']})")
            if conv.get("summary"):
                lines.append(f"  {conv['summary'][:80]}...")
        return "\n".join(lines)
    
    else:
        return f"Unknown action: {action}. Use 'search', 'get', or 'list'."
