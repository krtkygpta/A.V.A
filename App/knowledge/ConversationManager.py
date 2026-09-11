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
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any
from groq import Groq
_settings_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'settings.json')
try:
    with open(_settings_path, 'r') as _f:
        os.environ.update({k: str(v) for k, v in json.load(_f).items()})
except Exception:
    pass

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
    
    def add_message(self, role: str, content: str, tool_call_id: str = None, attachments: Optional[List[Dict[str, Any]]] = None):
        """Add a message to the conversation."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        if tool_call_id:
            msg["tool_call_id"] = tool_call_id
        if attachments:
            msg["attachments"] = attachments
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
    
    def _backfill_from_llm_messages(self) -> int:
        """
        If the current conversation is empty, copy dialogue from the live
        LLM message list (core.messageHandler.messages).

        The first TUI session historically never called start_new_conversation,
        so messages lived only in the LLM list and were lost on /new.
        """
        if not self.current_conversation:
            return 0
        if self.current_conversation.messages:
            return 0
        try:
            from core.messageHandler import messages, messages_lock

            with messages_lock:
                snapshot = list(messages)
        except Exception:
            return 0

        added = 0
        for m in snapshot:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            if role not in ("user", "assistant", "tool"):
                continue
            content = m.get("content")
            if content is None:
                content = ""
            content_str = str(content)
            if not content_str.strip():
                # Keep a breadcrumb for tool-only assistant turns
                if role == "assistant" and m.get("tool_calls"):
                    content_str = "[used tools]"
                elif role != "tool":
                    continue
            self.current_conversation.add_message(
                role, content_str, m.get("tool_call_id")
            )
            added += 1
        if added:
            print(f"[ConvMgr] Backfilled {added} message(s) from live LLM history")
        return added

    def ensure_current_conversation(self) -> Conversation:
        """Return the active conversation, creating one if the first chat never started one."""
        if self.current_conversation is None:
            self.current_conversation = Conversation()
            print(f"[ConvMgr] Auto-started: {self.current_conversation.id}")
            self._backfill_from_llm_messages()
            # Mirror start_new_conversation bookkeeping so bg jobs attach correctly.
            try:
                from core.TaskManager import update_conversation_state

                update_conversation_state(
                    self.current_conversation.id,
                    self.current_conversation.name,
                    is_active=True,
                )
            except Exception:
                pass
            if os.getenv("AVA_SERVER_MODE") != "1":
                try:
                    from core.server_api import start_remote_conversation

                    start_remote_conversation()
                except Exception:
                    pass
        return self.current_conversation

    def start_new_conversation(self) -> Conversation:
        """Start a new conversation thread. Called on each wakeword / /new."""
        # Recover the first-session case: no Conversation object yet, but the
        # LLM already has user/assistant turns in messageHandler.messages.
        if self.current_conversation is None:
            self.current_conversation = Conversation()
            self._backfill_from_llm_messages()
        else:
            # Also recover if an empty shell exists while LLM history is populated
            self._backfill_from_llm_messages()

        # Save current conversation if it has any dialogue
        if self.current_conversation and len(self.current_conversation.messages) > 0:
            self.save_conversation()

        # Create new conversation
        self.current_conversation = Conversation()
        print(f"[ConvMgr] Started: {self.current_conversation.id}")
        return self.current_conversation

    def save_conversation(self):
        """Save current conversation to disk.

        Saves immediately with a placeholder name, then generates
        a proper name + summary in a background thread to avoid
        blocking the main thread (~1-2s of Groq LLM calls).
        """
        if self.current_conversation is None:
            self.current_conversation = Conversation()
        self._backfill_from_llm_messages()

        conv = self.current_conversation
        if not conv or len(conv.messages) == 0:
            return

        # Save conversation file immediately (with placeholder name if needed)
        filepath = os.path.join(CONVERSATIONS_DIR, f"{conv.id}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(conv.to_dict(), f, indent=2, ensure_ascii=False)

        # Update index immediately
        self.conversations_index[conv.id] = {
            "name": conv.name,
            "created_at": conv.created_at,
            "updated_at": conv.updated_at,
            "summary": conv.summary,
            "message_count": len(conv.messages),
        }
        self._save_index()
        print(f"[ConvMgr] Saved: {conv.id} ({len(conv.messages)} msgs)")

        # Generate name + summary in background, then re-save
        is_placeholder = (
            conv.name in ("New Conversation", "Unnamed")
            or bool(re.match(r"^Conversation \d{8}_\d{6}$", conv.name))
        )
        if is_placeholder and len(conv.messages) >= 1:
            def _enrich(c, fp):
                try:
                    c.name = self._generate_name(c)
                    c.summary = self._generate_summary(c)
                    with open(fp, "w", encoding="utf-8") as f:
                        json.dump(c.to_dict(), f, indent=2, ensure_ascii=False)
                    self.conversations_index[c.id]["name"] = c.name
                    self.conversations_index[c.id]["summary"] = c.summary
                    self._save_index()
                    print(f"[ConvMgr] Enriched: {c.name}")
                except Exception as e:
                    print(f"[ConvMgr] Enrich error: {e}")

            threading.Thread(target=_enrich, args=(conv, filepath), daemon=True).start()
    
    def _generate_name(self, conv: Conversation) -> str:
        """Generate a short, descriptive name for the conversation using LLM or smart heuristics."""
        try:
            user_text = conv.get_user_messages_text()
            if not user_text.strip():
                return f"Conversation {conv.id}"
            
            # Clean text: remove user name prefix like "User: "
            clean_prompt = re.sub(r"^[A-Za-z0-9_\-\s]+:\s*", "", user_text.strip())
            prompt_snippet = clean_prompt[:500]

            # 1. Try primary server LLM first (always available and fast)
            try:
                try:
                    from core.server_api import generate_remote_response
                except ImportError:
                    from App.core.server_api import generate_remote_response

                res = generate_remote_response(
                    [
                        {
                            "role": "user",
                            "content": (
                                "Generate a very short, descriptive title (3-5 words max in English) for a conversation "
                                f"starting with this user query. Always use English words so it renders cleanly in terminal sidebars. Return ONLY the title, no quotes or punctuation:\n\n{prompt_snippet}"
                            ),
                        }
                    ],
                    tools=[],
                    model_tier="small",
                )
                name = (res.get("content") or "").strip()
                name = re.sub(r'^["\']+|["\']+$', "", name).strip()
                name = re.sub(r"^Title:\s*", "", name, flags=re.IGNORECASE).strip()
                if name and len(name) <= 60 and "error" not in name.lower():
                    return name
            except Exception as e:
                print(f"[ConvMgr] Server API name generation fallback: {e}")

            # 2. Try Groq with available fallback models
            groq_key = os.getenv("GROQ_API_KEY")
            if groq_key:
                for g_model in ["groq/compound-mini", "qwen/qwen3.8-27b", "allam-2-7b"]:
                    try:
                        response = client.chat.completions.create(
                            model=g_model,
                            messages=[{
                                "role": "user",
                                "content": f"Generate a very short title (3-5 words max in English) for this conversation. Always use English words so it renders cleanly in terminal sidebars. Just the title, nothing else:\n\n{prompt_snippet}"
                            }],
                            max_tokens=20,
                            temperature=0.3
                        )
                        name = response.choices[0].message.content.strip()
                        name = re.sub(r'^["\']+|["\']+$', '', name).strip()
                        name = re.sub(r"^Title:\s*", "", name, flags=re.IGNORECASE).strip()
                        if name and len(name) <= 60:
                            return name
                    except Exception:
                        continue

            # 3. Intelligent heuristic fallback from user message
            words = clean_prompt.split()
            if words:
                if len(words) > 1 and words[0].lower() in ("can", "could", "please", "hey", "ava", "tell", "show", "what", "how"):
                    words = words[1:]
                if len(words) > 1 and words[0].lower() in ("you", "me", "is", "are", "the"):
                    words = words[1:]
                title = " ".join(words[:5]).strip(".,!?:;").title()
                if title:
                    return title[:50]

            return f"Chat {conv.created_at[:10]}"
        except Exception as e:
            print(f"[ConvMgr] Name generation error: {e}")
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
            
            # 1. Try server LLM
            try:
                try:
                    from core.server_api import generate_remote_response
                except ImportError:
                    from App.core.server_api import generate_remote_response

                res = generate_remote_response(
                    [
                        {
                            "role": "user",
                            "content": f"Summarize this conversation in 1-2 concise sentences:\n\n{dialogue_text}",
                        }
                    ],
                    tools=[],
                    model_tier="small",
                )
                summary = (res.get("content") or "").strip()
                if summary and "error" not in summary.lower():
                    return summary
            except Exception:
                pass

            # 2. Try Groq
            groq_key = os.getenv("GROQ_API_KEY")
            if groq_key:
                for g_model in ["groq/compound-mini", "qwen/qwen3.8-27b", "allam-2-7b"]:
                    try:
                        response = client.chat.completions.create(
                            model=g_model,
                            messages=[{
                                "role": "user",
                                "content": f"Summarize this conversation in 1-2 sentences:\n\n{dialogue_text}"
                            }],
                            max_tokens=100,
                            temperature=0.3
                        )
                        return response.choices[0].message.content.strip()
                    except Exception:
                        continue
            
            return ""
        except Exception as e:
            print(f"[ConvMgr] Summary generation error: {e}")
            return ""

    def backfill_unnamed_conversations(self, limit: int = 50) -> int:
        """Scan conversations index in reverse (newest first) and rename placeholder/unnamed conversations."""
        count = 0
        for conv_id, info in reversed(list(self.conversations_index.items())):
            name = info.get("name", "")
            if name in ("New Conversation", "Unnamed", "Conversation") or re.match(r"^Conversation \d{8}_\d{6}$", name):
                conv = self.load_conversation(conv_id)
                if conv and conv.messages:
                    new_name = self._generate_name(conv)
                    if new_name and not new_name.startswith("Conversation "):
                        conv.name = new_name
                        if not conv.summary:
                            conv.summary = self._generate_summary(conv)
                        filepath = os.path.join(CONVERSATIONS_DIR, f"{conv.id}.json")
                        try:
                            with open(filepath, "w", encoding="utf-8") as f:
                                json.dump(conv.to_dict(), f, indent=2, ensure_ascii=False)
                            self.conversations_index[conv_id]["name"] = conv.name
                            self.conversations_index[conv_id]["summary"] = conv.summary
                            count += 1
                        except Exception as e:
                            print(f"[ConvMgr] Failed to re-save {conv_id}: {e}")
                        if count >= limit:
                            break
        if count > 0:
            self._save_index()
            print(f"[ConvMgr] Backfilled names for {count} conversations")
        return count
    
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
            print(f"[ConvMgr] Load error: {e}")
            return None
    
    def get_recent_conversations(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get list of recent conversations (for LLM context)."""
        return self.get_all_conversations(limit=limit)

    def get_all_conversations(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get list of all saved conversations sorted by updated_at descending."""
        # Ensure files on disk are indexed
        try:
            if os.path.exists(CONVERSATIONS_DIR):
                for f in os.listdir(CONVERSATIONS_DIR):
                    if f.endswith(".json") and f != "index.json":
                        cid = f[:-5]
                        if cid not in self.conversations_index:
                            fp = os.path.join(CONVERSATIONS_DIR, f)
                            try:
                                with open(fp, "r", encoding="utf-8") as jf:
                                    cdata = json.load(jf)
                                    self.conversations_index[cid] = {
                                        "name": cdata.get("name", "Conversation"),
                                        "created_at": cdata.get("created_at", ""),
                                        "updated_at": cdata.get("updated_at", ""),
                                        "summary": cdata.get("summary", ""),
                                        "message_count": len(cdata.get("messages", [])),
                                    }
                            except Exception:
                                pass
        except Exception:
            pass

        current_id = self.current_conversation.id if self.current_conversation else ""
        
        # Sort by updated_at / created_at descending
        sorted_convos = sorted(
            self.conversations_index.items(),
            key=lambda x: str(x[1].get("updated_at", "") or x[1].get("created_at", "") or x[0]),
            reverse=True
        )
        
        result = []
        for conv_id, info in sorted_convos[:limit]:
            dt = str(info.get("created_at", "") or info.get("updated_at", ""))
            date_str = dt[:10] if len(dt) >= 10 else conv_id[:8]
            time_str = dt[11:16] if len(dt) >= 16 else ""
            result.append({
                "id": conv_id,
                "name": info.get("name", "Conversation"),
                "summary": info.get("summary", ""),
                "date": date_str,
                "time": time_str,
                "updated_at": info.get("updated_at") or info.get("created_at") or "",
                "message_count": info.get("message_count", 0),
                "is_current": (conv_id == current_id),
            })
        return result

    def switch_to_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """Switch active conversation to a specific historical conversation thread."""
        # Save current active conversation first
        if self.current_conversation and len(self.current_conversation.messages) > 0:
            self.save_conversation()

        conv = self.load_conversation(conversation_id)
        if not conv:
            return None

        self.current_conversation = conv
        print(f"[ConvMgr] Switched to conversation: {conv.id} ({len(conv.messages)} msgs)")

        # Sync into core.messageHandler live queue
        try:
            from core.messageHandler import messages, messages_lock
            with messages_lock:
                messages.clear()
                for m in conv.messages:
                    if isinstance(m, dict):
                        role = m.get("role")
                        content = m.get("content")
                        tool_id = m.get("tool_call_id", "")
                        tool_calls = m.get("tool_calls")
                        msg_dict = {"role": role, "content": content}
                        if tool_id:
                            msg_dict["tool_call_id"] = tool_id
                        if tool_calls:
                            msg_dict["tool_calls"] = tool_calls
                        messages.append(msg_dict)
        except Exception as e:
            print(f"[ConvMgr] messageHandler sync error: {e}")

        return conv

    def _parse_iso(self, s: str) -> Optional[datetime]:
        if not s:
            return None
        try:
            return datetime.fromisoformat(str(s).replace("Z", "+00:00").split("+")[0])
        except (ValueError, TypeError):
            return None

    def _clip_msg_for_context(self, content: Any, max_chars: int) -> str:
        if content is None:
            return ""
        if isinstance(content, list):
            parts = []
            for p in content:
                if isinstance(p, dict) and p.get("type") == "text":
                    parts.append(str(p.get("text") or ""))
                elif isinstance(p, str):
                    parts.append(p)
            text = " ".join(parts)
        else:
            text = str(content)
        text = text.replace("[stop_chat]", "").replace("[STOP_CHAT]", "").strip()
        # Drop huge tool/system noise from stored history
        if text.startswith("[BACKGROUND TASK") or text.startswith("[TASK "):
            return ""
        if text.startswith("[PENDING MAIN ACTION"):
            return ""
        if len(text) > max_chars:
            return text[: max(0, max_chars - 3)] + "..."
        return text

    def _format_conversation_block(
        self,
        conv: "Conversation",
        *,
        max_msg_chars: int,
        max_msgs: int,
        budget_chars: int,
    ) -> str:
        """One conversation as a transcript block (user/assistant only)."""
        name = conv.name or "Unnamed"
        when = (conv.updated_at or conv.created_at or "")[:16].replace("T", " ")
        header = f'### "{name}" ({when}) id={conv.id}'
        if conv.summary:
            header += f"\nsummary: {conv.summary[:200]}"

        body_lines: List[str] = []
        # Prefer last N messages (most relevant for continue)
        msgs = [m for m in (conv.messages or []) if m.get("role") in ("user", "assistant")]
        if max_msgs > 0 and len(msgs) > max_msgs:
            msgs = msgs[-max_msgs:]

        for msg in msgs:
            role = msg.get("role")
            text = self._clip_msg_for_context(msg.get("content"), max_msg_chars)
            if not text:
                continue
            label = "User" if role == "user" else "AVA"
            body_lines.append(f"{label}: {text}")

        if not body_lines:
            # Index-only fallback
            return header + "\n(no stored messages)"

        block = header + "\n" + "\n".join(body_lines)
        if len(block) > budget_chars:
            # Keep header + tail of transcript
            tail_budget = max(200, budget_chars - len(header) - 20)
            joined = "\n".join(body_lines)
            block = header + "\n..." + joined[-tail_budget:]
        return block

    def get_conversation_context_for_llm(self) -> str:
        """
        Context for continued conversations (injected at reset_messages).

        Policy:
          1) If any conversations were updated in the last 15 minutes → include
             their full (clipped) transcripts.
          2) Else → include the last 5 conversations the same way.

        Caps via env:
          AVA_CONV_CONTEXT_MINUTES (default 15)
          AVA_CONV_CONTEXT_FALLBACK_N (default 5)
          AVA_CONV_CONTEXT_MAX_CHARS (default 12000 ≈ a few k tokens; keep tight)
          AVA_CONV_CONTEXT_MAX_MSG_CHARS (default 400)
          AVA_CONV_CONTEXT_MAX_MSGS_PER (default 40)

        Injected as a separate system message on /generate (not the main system prompt).
        """
        try:
            window_min = int(os.getenv("AVA_CONV_CONTEXT_MINUTES", "15"))
        except ValueError:
            window_min = 15
        try:
            fallback_n = int(os.getenv("AVA_CONV_CONTEXT_FALLBACK_N", "5"))
        except ValueError:
            fallback_n = 5
        try:
            # ~12k characters keeps prior-chat context lean for the main model
            max_chars = int(os.getenv("AVA_CONV_CONTEXT_MAX_CHARS", "12000"))
        except ValueError:
            max_chars = 12000
        try:
            max_msg_chars = int(os.getenv("AVA_CONV_CONTEXT_MAX_MSG_CHARS", "400"))
        except ValueError:
            max_msg_chars = 400
        try:
            max_msgs_per = int(os.getenv("AVA_CONV_CONTEXT_MAX_MSGS_PER", "40"))
        except ValueError:
            max_msgs_per = 40

        window_min = max(1, min(window_min, 24 * 60))
        fallback_n = max(1, min(fallback_n, 20))
        max_chars = max(2000, min(max_chars, 40000))
        max_msg_chars = max(80, min(max_msg_chars, 2000))
        max_msgs_per = max(4, min(max_msgs_per, 100))

        now = datetime.now()
        current_id = ""
        try:
            if self.current_conversation:
                current_id = self.current_conversation.id or ""
        except Exception:
            current_id = ""

        # Sort all known convos by updated_at desc
        sorted_items = sorted(
            self.conversations_index.items(),
            key=lambda x: x[1].get("updated_at") or x[1].get("created_at") or "",
            reverse=True,
        )

        recent_window: List[str] = []  # ids updated within window
        for conv_id, info in sorted_items:
            if conv_id == current_id:
                continue
            ts = self._parse_iso(info.get("updated_at") or info.get("created_at") or "")
            if ts is None:
                continue
            age_sec = (now - ts).total_seconds()
            if age_sec <= window_min * 60:
                recent_window.append(conv_id)

        if recent_window:
            selected_ids = recent_window
            mode_label = f"last {window_min} minutes (full transcripts, clipped)"
        else:
            selected_ids = [
                cid for cid, _ in sorted_items if cid != current_id
            ][:fallback_n]
            mode_label = f"last {fallback_n} conversations (no activity in {window_min}m)"

        if not selected_ids:
            return "(no prior conversations)"

        # Budget per conversation roughly equal share
        per_budget = max(800, max_chars // max(1, min(len(selected_ids), 8)))

        blocks: List[str] = []
        used = 0
        for conv_id in selected_ids:
            if used >= max_chars:
                break
            conv = self.load_conversation(conv_id)
            if conv is None:
                info = self.conversations_index.get(conv_id) or {}
                blocks.append(
                    f"### \"{info.get('name', 'Unnamed')}\" id={conv_id}\n"
                    f"summary: {(info.get('summary') or '')[:200] or '(unavailable)'}"
                )
                continue
            remaining = max_chars - used
            block = self._format_conversation_block(
                conv,
                max_msg_chars=max_msg_chars,
                max_msgs=max_msgs_per,
                budget_chars=min(per_budget, remaining),
            )
            blocks.append(block)
            used += len(block) + 2

        header = (
            f"[PRIOR CONVERSATIONS — {mode_label}]\n"
            "Use for continuity (what you were building, open tasks, names). "
            "Do not monologue about these on greeting — only when relevant.\n"
        )
        body = "\n\n".join(blocks)
        out = header + body
        if len(out) > max_chars + 200:
            out = out[: max_chars + 200] + "\n...(truncated)"
        return out
    
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
    conv = get_manager().start_new_conversation()

    # Sync with state manager for background task tracking.
    # Mark active so completions that finish *during* this chat can announce,
    # but do NOT wake the LLM here — wait for the user's first command.
    try:
        from core.TaskManager import update_conversation_state
        update_conversation_state(conv.id, conv.name, is_active=True)
    except Exception:
        pass

    # Intentionally no flush_pending_announcements(trigger_response=True).
    # That auto-started the model monologuing about old research on every new
    # chat. Deferred / unannounced jobs ride along with the *next user message*
    # (response_loop + optional passive inbox in the system prompt).

    # Client mirrors conversations to server storage.
    if os.getenv("AVA_SERVER_MODE") != "1":
        try:
            from core.server_api import start_remote_conversation
            start_remote_conversation()
        except Exception:
            pass

    return conv


def save_current_conversation():
    """Convenience function to save current conversation."""
    conv = get_manager().current_conversation
    get_manager().save_conversation()

    try:
        from core.TaskManager import update_conversation_state
        update_conversation_state(
            conv.id if conv else "",
            conv.name if conv else "",
            is_active=False,
        )
    except Exception:
        pass

    # Client mirrors conversations to server storage.
    if os.getenv("AVA_SERVER_MODE") != "1":
        try:
            from core.server_api import save_remote_conversation
            save_remote_conversation()
        except Exception:
            pass


def get_current_conversation() -> Optional[Conversation]:
    """Get the current active conversation."""
    return get_manager().current_conversation


def get_past_conversations_context() -> str:
    """Get context about past conversations for LLM."""
    return get_manager().get_conversation_context_for_llm()


def get_recent_hour_conversations_context() -> str:
    """
    Server / worker path: same policy as system-prompt conversation context
    (15-minute window full transcripts, else last 5). Kept name for callers.
    """
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
