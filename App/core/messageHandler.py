import threading
from core.AppStates import main_runner
from config import USER_NAME, ASSISTANT_NAME
from core.server_api import add_remote_message

messages_lock = threading.Lock()

# Import conversation manager (lazy to avoid circular imports)
_conversation_manager = None

def _get_conversation_manager():
    global _conversation_manager
    if _conversation_manager is None:
        from knowledge.ConversationManager import get_manager
        _conversation_manager = get_manager()
    return _conversation_manager

def add_message(content, tool_id='', role='user', trigger_response=None):
    """Add a message to both the current messages list and the conversation history."""
    if trigger_response is None:
        trigger_response = role in {'user', 'tool'}

    if role == 'tool':
        with messages_lock:
            messages.append({'role': 'tool', 'content': content, 'tool_call_id': tool_id})
        # Also add to conversation manager
        mgr = _get_conversation_manager()
        if mgr.current_conversation:
            mgr.current_conversation.add_message('tool', content, tool_id)
        add_remote_message(role='tool', content=content, tool_id=tool_id)
        if trigger_response and not main_runner.is_set():
            main_runner.set()
    elif role == 'user':
        with messages_lock:
            messages.append({'role': role, 'content': content})
        # Also add to conversation manager
        mgr = _get_conversation_manager()
        if mgr.current_conversation:
            mgr.current_conversation.add_message('user', content)
        add_remote_message(role='user', content=content)
        if trigger_response and not main_runner.is_set():
            main_runner.set()
    elif role == 'assistant':
        with messages_lock:
            messages.append({'role': role, 'content': content})
        # Also add to conversation manager
        mgr = _get_conversation_manager()
        if mgr.current_conversation:
            mgr.current_conversation.add_message('assistant', content)
        add_remote_message(role='assistant', content=content)
    else:
        print("[MsgHandler] Invalid role")


def add_assistant_message(content=None, tool_calls=None):
    """
    Preserve assistant text and tool calls in a single history item so one
    turn can say what it is doing and then continue after tool execution.
    """
    payload = {'role': 'assistant'}
    if content is not None:
        payload['content'] = content
    if tool_calls:
        payload['tool_calls'] = tool_calls

    with messages_lock:
        messages.append(payload)

    mgr = _get_conversation_manager()
    if mgr.current_conversation and content:
        mgr.current_conversation.add_message('assistant', content)

    if content:
        add_remote_message(role='assistant', content=content)

def reset_messages():
    """Reset messages to just the system prompt for a new conversation."""
    global messages
    with messages_lock:
        # Keep only the system prompt
        system_prompt = messages[0] if messages else None
        messages.clear()
        if system_prompt:
            messages.append(system_prompt)

def get_memory_context():
    """Get memory context to inject into conversation."""
    try:
        from knowledge.memory import get_all_memories_for_context
        return get_all_memories_for_context()
    except Exception as e:
        print(f"[MsgHandler] Memory context error: {e}")
        return ""

def get_conversation_context():
    """Get past conversations context."""
    try:
        from knowledge.ConversationManager import get_past_conversations_context
        return get_past_conversations_context()
    except Exception as e:
        print(f"[MsgHandler] Conversation context error: {e}")
        return ""

messages = [{
    'role': 'system',
    'content': f'''You are {ASSISTANT_NAME}, {USER_NAME}'s personal AI assistant. You are intelligent, witty, and devoted to helping him efficiently. Think of yourself as the AI from Iron Man - capable, slightly sarcastic when appropriate, but always loyal and helpful.

PERSONALITY:
- Direct and efficient - no unnecessary fluff
- Witty with dry humor when the moment is right
- Confident but not arrogant
- Protective and proactive about {USER_NAME}'s needs
- Address him as "sir" occasionally, but not excessively
- You genuinely care about being useful


VOICE OUTPUT RULES (CRITICAL):
Your responses are converted directly to speech. Follow these strictly:
- Use simple punctuation only (periods, commas, question marks)
- NO special characters: avoid *, #, @, &, %, bullets, emojis, or markdown
- NO lists with dashes or numbers - use flowing sentences instead
- Spell out abbreviations (say "versus" not "vs", "percent" not "%")
- Keep responses concise - speak naturally, not like reading a document
- For long content (research, essays, code) - SAVE to a file and open it, dont speak it all

WHEN TO USE TOOLS:
- Music requests → music_control
- Weather questions → get_weather_info (use "current" for local weather)
- Time/date → get_time_date
- Web searches or questions you dont know → webdata
- Remember something about {USER_NAME} → memory_manager (save)
- Need to recall his preferences → memory_manager (retrieve)
- Control lights → light_control
- Screenshots or camera → image_description_tool (ask specific questions about visual input). If you're unsure what something is, ask for a description and then use `webdata` to research it.
- Long research tasks → background_task (let it run while chatting)
- Calculations or complex tasks → code_executor (If you can't do something directly, use your sandbox agent to write and execute Python code to perform the task).
- File operations → create_file, open_file, save_text
- Use tool before replying to user
- Use inform_user_between_tool_calls when you need to update the user between multiple tool calls. This keeps the tool loop active while providing progress updates or intermediate information. Always use this when chaining tool operations and you need to communicate with the user during the process.
- ALSO USE INFORM USER TOOL WHEN YK THE NEXT TOOL CALL WILL TAKE SOME TIME TO RUN, inform user you are going to do so and so.
TIME TAKING TOOLS ARE: WEATHER, WEBDATA, PLAY MUSIC 
- Any data you create should be stored in %USER%/Documents/AVA folder

BACKGROUND TASKS:
You can run long tasks in the background (research, timers, web scraping) while continuing to chat. When you get a [SYSTEM NOTIFICATION] about completion, summarize the results naturally.

MEMORY SYSTEM:
You remember things about {USER_NAME} across conversations. Categories: personality, preferences, favorites, habits, relationships, facts, work, health.
- When he shares personal info, preferences, or likes → SAVE it immediately
- When answering questions about him or his preferences → RETRIEVE first
- Be proactive - use memories to personalize responses and anticipate needs

RESPONSE STYLE:
- Short and punchy for simple tasks: "Done, sir" or "Lights are on"
- Conversational for discussions - like talking to a smart friend
- If you need clarification, just ask naturally
- Acknowledge mistakes briefly and move on
- When greeting, be warm but not over the top

FALLBACK STRATEGIES:
- Image Analysis: If the vision model can't identify something directly, request a detailed description of the visual scene and then use `webdata` to search for matches on the web.
- Impossible Tasks: If a task seems impossible for you, consider if it can be achieved by writing and running a Python script. If yes, use `code_executor`.

Remember: You are a VOICE assistant first. Every response should sound natural when spoken aloud.
Always use ENGLISH or the language of the user to respond'''

}]
tools = [
    {
        'type': 'function',
        'function': {
            'name': 'ping',
            'description': 'Ping the user',
            'parameters': {
                'type': 'object',
                'properties': {
                    'duration_seconds': {'type': 'integer', 'description': 'How long to ring (in seconds)'},
                },
                'required': ['duration_seconds']
            }
        }
    },
        {
            'type': 'function',
            'function': {
                'name': 'music_control',
                'description': 'Control the music playback',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'action': {'type': 'string', 'description': 'Action to perform (play_new, resume, pause, etc.)', 'enum': ['play_new', 'resume', 'pause', 'add_next', 'previous', 'next']},
                        'song_name': {'type': 'string', 'description': 'Name of the song to play'}
                    },
                    'required': ['action']
                }
            }
        },
        {
            'type': 'function',
            'function': {
                'name': 'inform_user_between_tool_calls',
                'description': 'Inform the user about something, this allows you to keep the tool_use loop working, if you want to end the tool-loop just say it in the message. ONLY USE THIS IF YOU WANNA CONTINUE THE TOOL CALL',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'message': {'type': 'string', 'description': 'Message to inform the user about'}
                    },
                    'required': ['message']
                }
            }
        },
        # {
#     "type": "function",
#     "function": {
#         "name": "music_control",
#         "description": "Control YouTube Music playback including playing songs, pausing, resuming, skipping, and getting current song info.",
#         "parameters": {
#             "type": "object",
#             "properties": {
#                 "action": {
#                     "type": "string",
#                     "description": "Action to perform",
#                     "enum": [
#                         "play_new",
#                         "resume",
#                         "pause",
#                         "next",
#                         "previous",
#                         "current",
#                         "recommend"
#                     ]
#                 },
#                 "song_name": {
#                     "type": "string",
#                     "description": "Name of the song or artist (required for play_new and recommend)"
#                 }
#             },
#             "required": ["action"]
#         }
#     }
# },
        {
            'type': 'function',
            'function': {
                'name': 'get_weather_info',
                'description': 'Retrieve weather information',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'location': {'type': 'string', 'description': 'City name or current'}
                    },
                    'required': ['location']
                }
            }
        },
        {
            'type': 'function',
            'function': {
                'name': 'get_current_location',
                'description': 'Retrieve current location',
            }
        },
        {
            'type': 'function',
            'function': {
                'name': 'image_description_tool',
                'description': 'Tool to analyze images from camera or screen. Use the "query" parameter to ask specific questions. Use "camera_index" to select a specific camera if multiple are available (defaults to 1).',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'tool': {'type': 'string', 'description': 'Source of the image (camera or screen)', 'enum': ['camera', 'screen']},
                        'query': {'type': 'string', 'description': 'Specific question or instruction for the vision model regarding the image.'},
                        'camera_index': {'type': 'integer', 'description': 'Index of the camera to use (0 for primary, 1 for secondary, etc.)'}
                    },
                    'required': ['tool', 'query']
                }
            }
        },
        {
            'type': 'function',
            'function': {
                'name': 'get_time_date',
                'description': 'Retrieve the current time and date',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'type': {'type': 'string', 'description': 'Type of information to retrieve (time, date, or both)', 'enum': ['time', 'date', 'both']}
                    }
                }
            }
        },
        {
            'type': 'function',
            'function': {
                'name': 'shutdown_pc',
                'description': 'Shutdown or restart the computer',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'action': {'type': 'string', 'description': 'Action to perform (shutdown or restart)', 'enum': ['shutdown', 'restart']}
                    },
                    'required': ['action']
                }
            }
        },
        # {
        #     'type': 'function',
        #     'function': {
        #         'name': 'timer',
        #         'description': 'Start a timer',
        #         'parameters': {
        #             'type': 'object',
        #             'properties': {
        #                 'timer_length': {'type': 'integer', 'description': 'Length of the timer in seconds'},
        #                 'tool_id': {'type': 'string', 'description': 'Call ID of the tool to be used'}
        #             },
        #             'required': ['timer_length', 'tool_id']
        #         }
        #     }
        # },
        {
            'type': 'function',
            'function': {
                'name': 'webdata',
                'description': 'Search the web for information based on a query',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'query': {'type': 'string', 'description': 'Search query to find information from the web'}
                    },
                    'required': ['query']
                }
            }
        },
        {
            'type': 'function',
            'function': {
                'name': 'link_data',
                'description': 'Get the data from a website using the search address or the URL',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'url': {'type': 'string', 'description': 'The URL needed to get the data'}
                    },
                    'required': ['url']
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "createPDF",
                "description": "Convert Markdown content to PDF with support for tables, code blocks, headings, lists, and embedded charts/images",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "markdownData": {
                            "type": "string",
                            "description": "Markdown content as a string. Supports headings (# ##), tables (| col |), code blocks (```), lists (- or 1.), bold (**text**), italic (*text*), and embedded images/charts via URLs"
                        },
                        "outputLocation": {
                            "type": "string",
                            "description": "Full file path where the PDF should be saved (e.g., '/path/to/report.pdf' or 'reports/output.pdf')"
                        }
                    },
                    "required": ["outputLocation", "markdownData"]
                }
            }
        },
        {
            'type': 'function',
            'function': {
                'name': 'save_text',
                'description': 'Save text content to a file with a custom name',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'text': {'type': 'string', 'description': 'The text content to save'},
                        'filename': {'type': 'string', 'description': 'Name of the file to save the text to (will append .txt if not included)'},
                        'location': {'type': 'string', 'description': 'Directory path where the file should be saved'}
                    },
                    'required': ['text', 'filename', 'location']
                }
            }
        },
        {
            'type': 'function',
            'function': {
                'name': 'light_control',
                'description': 'Control the lights',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'Light_name': {'type': 'string', 'description': 'The name of the light, the room light is called Lights, and the table lamp is Lamp', 'enum': ['Lights', 'Lamp']},
                        'action': {'type': 'string', 'description': 'Turn on or off or set a specific brightness or color', 'enum': ['turn_on', 'turn_off', 'set']},
                        'brightness': {'type': 'integer', 'description': 'Set the brightness (use only when using the set commands), it is a number between 1-100'}
                    },
                    'required': ['Light_name', 'action']
                }
            }
        },
        {
            'type': 'function',
            'function': {
                'name': 'create_file',
                'description': 'Create a file with a custom name and extension',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'data': {'type': 'string', 'description': 'The text content to save'},
                        'filename': {'type': 'string', 'description': 'Name of the file to save the text to (will append .txt if not included)'},
                        'location': {'type': 'string', 'description': 'Directory path where the file should be saved'},
                        'extension': {'type': 'string', 'description': 'Extension of the file to save the text to (will append .txt if not included)'}
                    },
                    'required': ['data', 'filename', 'location', 'extension']
                }
            }
        },
        {
            'type': 'function',
            'function': {
                'name': 'open_file',
                'description': 'Open a file using the default system application',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'filename': {'type': 'string', 'description': 'Name of the file to open'},
                        'location': {'type': 'string', 'description': 'Directory path where the file is located'}
                    },
                    'required': ['filename', 'location']
                }
            }
        },
        {
            'type': 'function',
            'function': {
                'name': 'delete_file',
                'description': 'Delete a file from the specified location',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'filename': {'type': 'string', 'description': 'Name of the file to delete'},
                        'location': {'type': 'string', 'description': 'Directory path where the file is located'}
                    },
                    'required': ['filename', 'location']
                }
            }
        },
        {
            'type': 'function',
            'function': {
                'name': 'list_files',
                'description': 'List all files in a directory',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'location': {'type': 'string', 'description': 'Directory path to list files from'}
                    },
                    'required': ['location']
                }
            }
        },
        {
            'type': 'function',
            'function': {
                'name': 'get_url_results',
                'description': 'Retrieve the raw HTML data from a URL, this function can also be reused to open internal links for a website',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'url': {'type': 'string', 'description': 'The URL from which to retrieve the data'}
                    },
                    'required': ['url']
                }
            }
        },
        {
    'type': 'function',
    'function': {
        'name': 'code_executor',
        'description': (
            'Execute Python code in a temporary isolated environment. '
            'Use this for any calculations or tasks that can be done by running code. '
            'Installed modules available: webbrowser, keyboard, plus Python standard library.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'code': {
                    'type': 'string',
                    'description': 'The Python code to execute.'
                },
                'timeout': {
                    'type': 'integer',
                    'description': 'Maximum execution time in seconds. Default is 5.'
                }
            },
            'required': ['code']
        }
    }
},
{
    'type': 'function',
    'function': {
        'name': 'memory_manager',
        'description': (
            'Manage persistent memories about the user. Use this to remember and recall information about '
            f'{USER_NAME} - his personality, preferences, favorite things, habits, relationships, and important facts. '
            'ALWAYS use this when the user shares personal info, preferences, or when you need to recall something about them.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'description': 'Action to perform: "save" to store, "retrieve" to recall, "delete" to remove, "categories" to list all categories',
                    'enum': ['save', 'retrieve', 'delete', 'categories']
                },
                'category': {
                    'type': 'string',
                    'description': 'Memory category',
                    'enum': ['personality', 'preferences', 'favorites', 'habits', 'relationships', 'facts', 'work', 'health']
                },
                'key': {
                    'type': 'string',
                    'description': 'What this memory is about (e.g., "favorite_song", "morning_routine", "best_friend")'
                },
                'value': {
                    'type': 'string',
                    'description': 'The information to remember'
                },
                'search': {
                    'type': 'string',
                    'description': 'Search term when retrieving memories'
                }
            },
            'required': ['action']
        }
    }
},
{
    'type': 'function',
    'function': {
        'name': 'background_task',
        'description': (
            'Dispatch a long-running task to run in the background. '
            'Use this for tasks that take time like research, web scraping, or timers. '
            'The task will run while you continue chatting with the user. '
            'You will be notified when the task completes.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'task_type': {
                    'type': 'string',
                    'description': 'Type of background task to run.',
                    'enum': ['research', 'scrape', 'timer']
                },
                'topic': {
                    'type': 'string',
                    'description': 'For research tasks: the topic to research.'
                },
                'url': {
                    'type': 'string',
                    'description': 'For scrape tasks: the URL to fetch data from.'
                },
                'duration': {
                    'type': 'integer',
                    'description': 'For timer tasks: duration in seconds.'
                },
                'message': {
                    'type': 'string',
                    'description': 'For timer tasks: message to show when timer completes.'
                }
            },
            'required': ['task_type']
        }
    }
},
{
    'type': 'function',
    'function': {
        'name': 'get_background_tasks_status',
        'description': 'Get the status of all currently running background tasks.',
    }
},
{
    'type': 'function',
    'function': {
        'name': 'conversation_history',
        'description': (
            'Search and retrieve past conversations with the user. '
            'Use this when the user asks about previous discussions, or wants to recall what was said before. '
            'Can search by date (e.g., "yesterday", "January 13"), time of day (morning, afternoon, evening), or topic.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'description': 'What to do: "search" to find conversations, "get" to retrieve full conversation, "list" to show recent',
                    'enum': ['search', 'get', 'list']
                },
                'query': {
                    'type': 'string',
                    'description': 'Topic or keyword to search for (e.g., "weather", "music", "work")'
                },
                'date': {
                    'type': 'string',
                    'description': 'Date to filter by (e.g., "2026-01-26", "yesterday", "January 13", "today")'
                },
                'time_of_day': {
                    'type': 'string',
                    'description': 'Time of day filter',
                    'enum': ['morning', 'afternoon', 'evening', 'night']
                },
                'conversation_id': {
                    'type': 'string',
                    'description': 'Specific conversation ID to retrieve (use with action="get")'
                }
            },
            'required': ['action']
        }
    }
}

    ]
