"""
tool_definitions.py - OpenAI-compatible tool schemas for AVA.

Extracted from messageHandler.py to keep tool definitions separate
from message management logic.
"""

from config import USER_NAME

tools = [
    {
        "type": "function",
        "function": {
            "name": "send_notification",
            "description": "Send a desktop notification after a task completes or when attention is not immediately required",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Title of the notification",
                    },
                    "message": {
                        "type": "string",
                        "description": "Body message of the notification",
                    },
                },
                "required": ["title", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ping",
            "description": "Ping the user",
            "parameters": {
                "type": "object",
                "properties": {
                    "duration_seconds": {
                        "type": "integer",
                        "description": "How long to ring (in seconds)",
                    },
                },
                "required": ["duration_seconds"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inform_user_between_tool_calls",
            "description": "Inform the user about something, this allows you to keep the tool_use loop working, if you want to end the tool-loop just say it in the message. ONLY USE THIS IF YOU WANNA CONTINUE THE TOOL CALL",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Message to inform the user about",
                    }
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "image_description_tool",
            "description": 'Tool to analyze images from camera or screen. Use the "query" parameter to ask specific questions. Use "camera_index" to select a specific camera if multiple are available (defaults to 1).',
            "parameters": {
                "type": "object",
                "properties": {
                    "tool": {
                        "type": "string",
                        "description": "Source of the image (camera or screen)",
                        "enum": ["camera", "screen"],
                    },
                    "query": {
                        "type": "string",
                        "description": "Specific question or instruction for the vision model regarding the image.",
                    },
                    "camera_index": {
                        "type": "integer",
                        "description": "Index of the camera to use (0 for primary, 1 for secondary, etc.)",
                    },
                },
                "required": ["tool", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_time_date",
            "description": "Retrieve the current time and date",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "Type of information to retrieve (time, date, or both)",
                        "enum": ["time", "date", "both"],
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shutdown_pc",
            "description": "Shutdown or restart the computer",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action to perform (shutdown or restart)",
                        "enum": ["shutdown", "restart"],
                    }
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_pdf",
            "description": "Convert Markdown content to PDF with support for tables, code blocks, headings, lists, and embedded charts/images",
            "parameters": {
                "type": "object",
                "properties": {
                    "markdownData": {
                        "type": "string",
                        "description": "Markdown content as a string. Supports headings (# ##), tables (| col |), code blocks (```), lists (- or 1.), bold (**text**), italic (*text*), and embedded images/charts via URLs",
                    },
                    "outputLocation": {
                        "type": "string",
                        "description": "Full file path where the PDF should be saved (e.g., '/path/to/report.pdf' or 'reports/output.pdf')",
                    },
                },
                "required": ["outputLocation", "markdownData"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_text",
            "description": "Save text content to a file with a custom name",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The text content to save",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Name of the file to save the text to (will append .txt if not included)",
                    },
                    "location": {
                        "type": "string",
                        "description": "Directory path where the file should be saved",
                    },
                },
                "required": ["text", "filename", "location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Create a file with a custom name and extension",
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "string",
                        "description": "The text content to save",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Name of the file to save the text to (will append .txt if not included)",
                    },
                    "location": {
                        "type": "string",
                        "description": "Directory path where the file should be saved",
                    },
                    "extension": {
                        "type": "string",
                        "description": "Extension of the file to save the text to (will append .txt if not included)",
                    },
                },
                "required": ["data", "filename", "location", "extension"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_file",
            "description": "Open a file using the default system application",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Name of the file to open",
                    },
                    "location": {
                        "type": "string",
                        "description": "Directory path where the file is located",
                    },
                },
                "required": ["filename", "location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file from the specified location",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Name of the file to delete",
                    },
                    "location": {
                        "type": "string",
                        "description": "Directory path where the file is located",
                    },
                },
                "required": ["filename", "location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List all files in a directory",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "Directory path to list files from",
                    }
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code_executor",
            "description": (
                "Execute Python code in a temporary isolated environment. "
                "Use this for any calculations or tasks that can be done by running code. "
                "Installed modules available: webbrowser, keyboard, plus Python standard library."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The Python code to execute.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Maximum execution time in seconds. Default is 5.",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_manager",
            "description": (
                "Manage persistent memories about the user. Use this to remember and recall information about "
                f"{USER_NAME} - his personality, preferences, favorite things, habits, relationships, and important facts. "
                "ALWAYS use this when the user shares personal info, preferences, or when you need to recall something about them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": 'Action to perform: "save" to store, "retrieve" to recall, "delete" to remove, "categories" to list all categories',
                        "enum": ["save", "retrieve", "delete", "categories"],
                    },
                    "category": {
                        "type": "string",
                        "description": "Memory category",
                        "enum": [
                            "personality",
                            "preferences",
                            "favorites",
                            "habits",
                            "relationships",
                            "facts",
                            "work",
                            "health",
                        ],
                    },
                    "key": {
                        "type": "string",
                        "description": 'What this memory is about (e.g., "favorite_song", "morning_routine", "best_friend")',
                    },
                    "value": {
                        "type": "string",
                        "description": "The information to remember",
                    },
                    "search": {
                        "type": "string",
                        "description": "Search term when retrieving memories",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "background_task",
            "description": (
                "Dispatch a long-running task to run in the background. "
                "Use this for tasks that take time like research, web scraping, or timers. "
                "The task will run while you continue chatting with the user. "
                "You will be notified when the task completes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_type": {
                        "type": "string",
                        "description": "Type of background task to run.",
                        "enum": ["research", "scrape", "timer", "smarthome"],
                    },
                    "topic": {
                        "type": "string",
                        "description": "For research tasks: the topic to research.",
                    },
                    "url": {
                        "type": "string",
                        "description": "For scrape tasks: the URL to fetch data from.",
                    },
                    "duration": {
                        "type": "integer",
                        "description": "For timer tasks: duration in seconds.",
                    },
                    "message": {
                        "type": "string",
                        "description": "For timer tasks: message to show when timer completes.",
                    },
                    "command": {
                        "type": "string",
                        "description": "For smarthome tasks: the natural language instruction to execute.",
                    },
                },
                "required": ["task_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_background_tasks_status",
            "description": "Get the status of all currently running background tasks.",
        },
    },
    {
        "type": "function",
        "function": {
            "name": "conversation_history",
            "description": (
                "Search and retrieve past conversations with the user. "
                "Use this when the user asks about previous discussions, or wants to recall what was said before. "
                'Can search by date (e.g., "yesterday", "January 13"), time of day (morning, afternoon, evening), or topic.'
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": 'What to do: "search" to find conversations, "get" to retrieve full conversation, "list" to show recent',
                        "enum": ["search", "get", "list"],
                    },
                    "query": {
                        "type": "string",
                        "description": 'Topic or keyword to search for (e.g., "weather", "music", "work")',
                    },
                    "date": {
                        "type": "string",
                        "description": 'Date to filter by (e.g., "2026-01-26", "yesterday", "January 13", "today")',
                    },
                    "time_of_day": {
                        "type": "string",
                        "description": "Time of day filter",
                        "enum": ["morning", "afternoon", "evening", "night"],
                    },
                    "conversation_id": {
                        "type": "string",
                        "description": 'Specific conversation ID to retrieve (use with action="get")',
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar",
            "description": "Google Calendar: list upcoming events, create new events, or delete events. Use when user wants to check, add, or remove calendar events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "description": 'What to do: "list", "create", or "delete"',
                        "enum": ["list", "create", "delete"],
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max events to return (for list)",
                    },
                    "days_ahead": {
                        "type": "integer",
                        "description": "Days ahead to look (for list)",
                    },
                    "title": {
                        "type": "string",
                        "description": "Event title (for create)",
                    },
                    "start_datetime": {
                        "type": "string",
                        "description": 'Start time ISO format (for create, e.g., "2024-04-25T14:00:00")',
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": "Duration in minutes (for create)",
                    },
                    "description": {
                        "type": "string",
                        "description": "Event description (for create)",
                    },
                    "location": {
                        "type": "string",
                        "description": "Event location (for create)",
                    },
                    "event_id": {
                        "type": "string",
                        "description": "Event ID to delete (for delete)",
                    },
                },
                "required": ["operation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute Windows bash command. First time prompts user for permission (Always/Session/Deny).",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Command to execute"},
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 30)",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web",
            "description": "Unified web: search, fetch URLs, AI search, weather, GPS. Use for any internet task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "description": 'Operation: "search", "fetch", "ai_search", "weather", or "gps"',
                        "enum": ["search", "fetch", "ai_search", "weather", "gps"],
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query (for search/ai_search)",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of results (for search)",
                    },
                    "url": {
                        "type": "string",
                        "description": "URL to fetch (for fetch)",
                    },
                    "location": {
                        "type": "string",
                        "description": 'City for weather, or "current" for GPS location',
                    },
                },
                "required": ["operation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "music_agent",
            "description": "Control YouTube Music: play, pause, next, previous, search songs, add/remove from queue, view queue, and adjust volume using natural language input.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language command for music control. Example: 'play kesariya next', 'pause music', 'add blinding lights to queue', 'what is playing', 'set volume to 50'",
                    }
                },
                "required": ["query"],
            },
        },
    },
]
