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
            "description": (
                "Show a desktop toast + short chime. Use when a background task "
                "finishes (research, delegate, mission, demo) or for non-urgent "
                "updates. On [BACKGROUND TASK DONE], call this with a clear title "
                "and one-line summary BEFORE speaking the update. Does not block."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Title of the notification (e.g. 'Task done' or task name)",
                    },
                    "message": {
                        "type": "string",
                        "description": "Short body: what finished and key outcome (one line)",
                    },
                },
                "required": ["title", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "make_phone_call",
            "description": "Place an outbound phone call on the user's behalf and speak a short message. Requires configured Twilio-compatible credentials.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone_number": {
                        "type": "string",
                        "description": "Destination phone number in E.164 format, for example +15551234567.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Short message to speak on the call.",
                    },
                    "voice": {
                        "type": "string",
                        "description": "Provider voice name. Defaults to alice.",
                    },
                },
                "required": ["phone_number", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ping",
            "description": (
                "Audible beep to grab attention. Non-blocking. "
                "continuous=true: keep ringing until the user responds (real alarms). "
                "continuous=false: ring only for duration_seconds (short cue). "
                "For casual reminders prefer send_notification only — do not continuous-ring. "
                "User silences by speaking or any key."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "duration_seconds": {
                        "type": "integer",
                        "description": (
                            "One-shot ring length in seconds (1–120) when continuous is false. "
                            "2–4 = short; 8–15 = stronger burst. Ignored if continuous=true."
                        ),
                    },
                    "continuous": {
                        "type": "boolean",
                        "description": (
                            "If true, keep ringing until the user responds (or max ~3 min). "
                            "Use only for serious alarms (school, wake-up, critical). "
                            "Default false."
                        ),
                    },
                    "max_seconds": {
                        "type": "integer",
                        "description": "Cap for continuous mode (default 180).",
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
    # {
    #     "type": "function",
    #     "function": {
    #         "name": "image_description_tool",
    #         "description": 'Tool to analyze images from camera or screen. Use the "query" parameter to ask specific questions. Use "camera_index" to select a specific camera if multiple are available (defaults to 1).',
    #         "parameters": {
    #             "type": "object",
    #             "properties": {
    #                 "tool": {
    #                     "type": "string",
    #                     "description": "Source of the image (camera or screen)",
    #                     "enum": ["camera", "screen"],
    #                 },
    #                 "query": {
    #                     "type": "string",
    #                     "description": "Specific question or instruction for the vision model regarding the image.",
    #                 },
    #                 "camera_index": {
    #                     "type": "integer",
    #                     "description": "Index of the camera to use (0 for primary, 1 for secondary, etc.)",
    #                 },
    #             },
    #             "required": ["tool", "query"],
    #         },
    #     },
    # },
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": (
                "Capture the current screen on the client, upload it to the AVA server "
                "(stored under Server/data/screenshots), and return the server absolute "
                "path plus the image itself so you can see what is on screen. "
                "Use when the user asks what is on their screen, about a screenshot, "
                "or you need to look at the desktop yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": (
                            "Optional file name only (e.g. 'desktop.png'). "
                            "Saved under data/screenshots; path traversal is ignored. "
                            "Default: screenshot_YYYYMMDD_HHMMSS.png"
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_image",
            "description": (
                "Load and visually inspect an image from the client machine (via local file path "
                "such as 'C:/path/to/image.png' or '~/Downloads/photo.jpg') or a web URL. "
                "The tool reads the image directly on the client and injects it into your visual context "
                "so your vision capabilities can inspect and answer questions about it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {
                        "type": "string",
                        "description": (
                            "The local file path (e.g. 'C:/Users/name/Pictures/img.png', '~/diagram.jpg', "
                            "'./sample.webp') or web/file URL of the image to inspect."
                        ),
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional custom filename to save as on the server (e.g. 'chart.png').",
                    },
                },
                "required": ["image_path"],
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
                        "description": (
                            "Directory to save into. Use friendly names that resolve to the "
                            "real user folders: 'desktop', 'documents', 'downloads', 'home', "
                            "or a full path / %USERPROFILE%\\Desktop. "
                            "Do NOT use a relative word like desktop if you mean the Windows Desktop — "
                            "the alias 'desktop' maps to the real Desktop."
                        ),
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
                        "description": (
                            "Directory to save into. Prefer 'desktop' or 'documents' "
                            "(resolved to the real user Desktop/Documents on Windows), "
                            "or an absolute path. Bare 'desktop' is NOT a folder in the app cwd."
                        ),
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
            "name": "read_file",
            "description": "Read file contents with 1-indexed line numbers, line slicing (start_line, end_line), and safe length limits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to read (absolute or relative to project).",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Optional 1-indexed starting line number.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Optional 1-indexed ending line number.",
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": "Maximum number of lines to return (default 400).",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Surgically search and replace a unique block of text within a file. Exact match required.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to edit.",
                    },
                    "target_content": {
                        "type": "string",
                        "description": "The exact existing text chunk to replace.",
                    },
                    "replacement_content": {
                        "type": "string",
                        "description": "The new replacement text.",
                    },
                    "allow_multiple": {
                        "type": "boolean",
                        "description": "If true, replace all occurrences. Default false (errors if not unique).",
                    },
                },
                "required": ["path", "target_content", "replacement_content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write or create a complete file with the specified content. Creates parent directories automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Destination file path.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write into the file.",
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "Whether to overwrite if file exists. Default true.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search for a keyword or regex pattern across files in a directory or file with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "String or regex pattern to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory or file to search within. Defaults to current workspace.",
                    },
                    "is_regex": {
                        "type": "boolean",
                        "description": "Whether query is a regular expression. Default false.",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "Whether search is case-sensitive. Default false.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max matching lines to return. Default 50.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "Explore directory structure with file sizes and depth limit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path to list. Defaults to current directory.",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Directory recursion depth (default 2).",
                    },
                },
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
                "Use this for tasks that take time like research, web scraping, timers, "
                # "smart home work, open-ended delegated AI work, or a timed demo job for testing. "
                "The task will run while you continue chatting with the user. "
                "You will be notified when the task completes. "
                "For consecutive work (e.g. timer THEN research), set follow_up to the next "
                "task JSON — it auto-starts when this job completes, even after a new conversation. "
                "For coding / app building / self-editing use task_type coding (native Pi-inspired delegate coding agent). "
                "ALWAYS pass objective (the coding instruction). "
                "To continue a prior run also pass session_id — title alone is NOT enough."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_type": {
                        "type": "string",
                        "description": (
                            "Type of background task. "
                            "'coding' = Native Pi-inspired delegate coding agent with precision tools (read/edit/write/bash/search/list). "
                            "Can build apps, write scripts, or edit AVA itself safely. "
                            "'delegate' = Specialized AI worker across various domains (coder, researcher, etc.). "
                            "'research' = Multi-source web investigation. "
                            "'opencode' = legacy OpenCode CLI. "
                            "'demo' is a deterministic timed job (no LLM) for testing."
                        ),
                        "enum": [
                            "coding",
                            "delegate",
                            "research",
                            "scrape",
                            "timer",
                            "mission",
                            "demo",
                            "opencode",
                        ],
                    },
                    "model_tier": {
                        "type": "string",
                        "description": (
                            "Model tier to use for this background job. "
                            "'low' = fast/budget; 'medium_low' = light worker; "
                            "'medium' = balanced everyday; 'high' = advanced reasoning & heavy coding; "
                            "'very_high' = world-class flagship for critical architecture, tough debugging, and self-editing."
                        ),
                        "enum": ["low", "medium_low", "medium", "high", "very_high"],
                    },
                    "topic": {
                        "type": "string",
                        "description": "For research or demo: topic / label of the work.",
                    },
                    "level": {
                        "type": "integer",
                        "description": (
                            "For research only: depth 1–5. "
                            "1 = quick fact check (fast, few sources); "
                            "2 = light multi-source; "
                            "3 = standard (default) with page deep-dives; "
                            "4 = deep multi-angle + PDFs + Instagram for people; "
                            "5 = exhaustive under the 100K token budget + Instagram for people. "
                            "Pick from user intent: casual 'look up X' → 1–2; "
                            "'research X' → 3; 'deep dive' / full life profile / person OSINT → 4–5."
                        ),
                        "minimum": 1,
                        "maximum": 5,
                    },
                    "url": {
                        "type": "string",
                        "description": "For scrape tasks: the URL to fetch data from.",
                    },
                    "duration": {
                        "type": "integer",
                        "description": (
                            "For timer or demo: duration in seconds. "
                            "Demo default 20 (min 2, max 600). Timer is the countdown."
                        ),
                    },
                    "message": {
                        "type": "string",
                        "description": (
                            "For timer: short spoken/toast text when it fires "
                            "(NOT a substitute for starting the next job). "
                            "For demo: optional label."
                        ),
                    },
                    "follow_up": {
                        "type": "string",
                        "description": (
                            "Optional JSON for what happens when THIS job completes. "
                            "Works across /new. Two styles:\n"
                            "1) SMALL next step (WhatsApp, notify, one tool) — use MAIN agent, no worker:\n"
                            '   {"task_type":"main_action","objective":"WhatsApp Mein Hi Hoon the current time"}\n'
                            "2) HEAVY next job (research, another timer, long work) — background:\n"
                            '   {"task_type":"research","topic":"keyboards","level":3}\n'
                            "Do NOT use delegate for a single WhatsApp/notify. "
                            "Do NOT put the next step only in the timer message string."
                        ),
                    },
                    "alert_mode": {
                        "type": "string",
                        "description": (
                            "Auto-alert when THIS job finishes. Default silent = no system beep/toast; "
                            "on DONE you decide whether to call ping or send_notification. "
                            "'silent' = nothing automatic (preferred for timer→WhatsApp/tools). "
                            "'notify' = desktop toast only (no beep). "
                            "'short' = brief auto-ping. "
                            "'continuous' = keep ringing until response — real alarms only."
                        ),
                        "enum": ["silent", "notify", "short", "continuous"],
                    },
                    # "command": {
                    #     "type": "string",
                    #     "description": "For smarthome tasks: the natural language instruction to execute.",
                    # },
                    "objective": {
                        "type": "string",
                        "description": (
                            "REQUIRED for opencode/coding (and for delegate/mission): the actual work. "
                            "For delegate, this is the task the specialist worker executes. "
                            "OpenCode examples: 'Rewrite test.py to print Hello World only' or "
                            "'Add a countdown timer to Desktop/test.py'. "
                            "When continuing a session you MUST still pass objective with the new change — "
                            "session_id alone or title alone will not run."
                        ),
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Alias for objective when task_type is opencode.",
                    },
                    "directory": {
                        "type": "string",
                        "description": (
                            "For opencode: working directory. Prefer 'desktop/project-name', "
                            "'documents/...', or an absolute path. Bare names become "
                            "Desktop/AVA_OpenCode/<name>. Empty = new folder under "
                            "Desktop/AVA_OpenCode. Do NOT use the AVA repo path unless asked."
                        ),
                    },
                    "session_id": {
                        "type": "string",
                        "description": (
                            "For opencode or delegate: existing session token (del_... or ses_...) to continue. "
                            "Always pair with a non-empty objective. "
                            "From HELD DOSSIERS / manage_background_tasks delegate_list/opencode_list."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": (
                            "For opencode: short label only (UI/session list). "
                            "Not a substitute for objective."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": (
                            "For opencode: optional longer label. Prefer objective for the real instruction."
                        ),
                    },
                    "permissions": {
                        "type": "string",
                        "description": (
                            "For opencode: tool permissions. "
                            "'auto' (default) = approve tools (--auto); "
                            "'deny' = safe mode, reject risky tools; "
                            "'ask' = main agent decides yes/no before granting auto."
                        ),
                        "enum": ["auto", "deny", "ask"],
                    },
                    "continue_session": {
                        "type": "boolean",
                        "description": "For opencode or delegate: continue the last session if session_id omitted.",
                    },
                    "mission": {
                        "type": "string",
                        "description": "For mission tasks: a broad goal that should be split across multiple background workers.",
                    },
                    "context": {
                        "type": "string",
                        "description": "For delegate, mission, or opencode: useful context, constraints, or source material.",
                    },
                    "expected_output": {
                        "type": "string",
                        "description": "For delegate tasks: what the worker should return when done.",
                    },
                    "specialist": {
                        "type": "string",
                        "description": (
                            "For delegate: name of the specialized delegate agent with custom system prompt. "
                            "Choices: 'researcher', 'coder', 'writer', 'data_analyst', 'file_manager', "
                            "'automation_ops', 'troubleshooter', 'planner', 'smart_home_iot', 'communicator', "
                            "or 'generic' (default). Delegate workers have access to all tools and custom plugins, "
                            "excluding background task tools."
                        ),
                    },
                    "worker_tools": {
                        "type": "string",
                        "description": (
                            "For delegate: optional comma-separated extra/override tool names "
                            "the worker may use (e.g. 'web,create_file,whatsapp_plugin'). "
                            "Merged with the specialist's default kit. "
                            "Cannot grant blocked tools (bash, delete_file, background_task, "
                            "make_phone_call, shutdown_pc)."
                        ),
                    },
                    "max_workers": {
                        "type": "integer",
                        "description": "For mission tasks: maximum parallel worker agents to launch, from 1 to 5.",
                    },
                    "ask": {
                        "type": "boolean",
                        "description": (
                            "For demo: if true, pause mid-run and ask main/user a clarification "
                            "question (tests the needs_input bridge)."
                        ),
                    },
                    "question": {
                        "type": "string",
                        "description": "For demo with ask=true: custom clarification question.",
                    },
                    "require_user": {
                        "type": "boolean",
                        "description": (
                            "For demo with ask=true: if true (default), main must ask the user "
                            "rather than self-answering."
                        ),
                    },
                    "kind": {
                        "type": "string",
                        "description": (
                            "Optional override for completion behavior only (jobs always outlive chats). "
                            "'long' = sticky unread inbox until result/dismiss. "
                            "'short' = one-shot notify, never re-nag on new conversations. "
                            "Defaults: research/mission/delegate/demo=long; timer/scrape=short. "
                            "Timers are always treated as short for inbox (they still survive conversations while counting)."
                        ),
                        "enum": ["short", "long"],
                    },
                    "difficulty": {
                        "type": "string",
                        "description": (
                            "Optional work difficulty. 'hard' may bump the model tier "
                            "(e.g. small→main). Defaults to normal."
                        ),
                        "enum": ["easy", "normal", "hard"],
                    },
                    "model_tier": {
                        "type": "string",
                        "description": (
                            "Optional model tier for LLM workers: none|nano|small|main|heavy. "
                            "Timers/demo use none. Delegate defaults small; research heavy."
                        ),
                        "enum": ["none", "nano", "small", "main", "heavy"],
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
            "name": "manage_background_tasks",
            "description": (
                "List, inspect, retrieve results from, cancel, dismiss, or answer "
                "background tasks. List separates CURRENT vs OLD/HANDLED. "
                "'result' returns the job output plus held dossier (paths/continue hints) "
                "and marks a long job's inbox item acknowledged; "
                "'context' / 'dossier' returns full origin conversation pack + artifacts + "
                "how to continue (use when user says run the program / open that / continue) — "
                "omit task_id to list recent held dossiers; "
                "'dismiss' fully clears a finished job (timers AND inbox) so you never "
                "re-mention it — pass the task id, or 'short'/'all' to clear finished noise; "
                "'answer' resumes a needs_input job (prefer silent to user if you self-answer)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Task management action.",
                        "enum": [
                            "list",
                            "status",
                            "result",
                            "tool_calls",
                            "steps",
                            "context",
                            "dossier",
                            "cancel",
                            "dismiss",
                            "answer",
                            "coding_sessions",
                            "delegate_list",
                            "opencode_list",
                            "opencode_sessions",
                        ],
                    },
                    "task_id": {
                        "type": "string",
                        "description": (
                            "Task ID for status/result/context/cancel/dismiss/answer. "
                            "For context without id: lists recent held dossiers. "
                            "For opencode_list: optional search text to match session descriptions. "
                            "For dismiss only: also 'short', 'all', or 'handled' to bulk-clear finished noise."
                        ),
                    },
                    "request_id": {
                        "type": "string",
                        "description": "Clarification request id (preferred for action answer). From [TASK NEEDS INPUT].",
                    },
                    "answer": {
                        "type": "string",
                        "description": "For action answer: the short reply text to deliver to the waiting sub-agent.",
                    },
                    "source": {
                        "type": "string",
                        "description": "For action answer: 'main' if you answered from memory/tools, 'user' if the user provided it.",
                        "enum": ["main", "user"],
                    },
                },
                "required": ["action"],
            },
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
            "description": "Google Calendar: list/search upcoming events, create events, update/reschedule events, or delete events. Use when user wants to check, add, reschedule, or remove calendar events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "description": 'What to do: "list" (or search), "create", "update", "delete", or "get"',
                        "enum": ["list", "create", "update", "delete", "get"],
                    },
                    "query": {
                        "type": "string",
                        "description": "Keyword or title search term (for list/search, e.g., 'birthday', 'dentist', 'meeting')",
                    },
                    "date": {
                        "type": "string",
                        "description": "Target date filter (for list, e.g., '2026-09-07' or for creating all-day events)",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Start date for range filter (for list, e.g., '2026-09-01')",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End date for range filter (for list, e.g., '2026-09-30')",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max events to return (for list, default 10)",
                    },
                    "days_ahead": {
                        "type": "integer",
                        "description": "Days ahead to look (for list, default 7)",
                    },
                    "title": {
                        "type": "string",
                        "description": "Event title/summary (for create/update)",
                    },
                    "start_datetime": {
                        "type": "string",
                        "description": 'Start time in ISO format or date (for create/update, e.g., "2026-09-01T14:00:00" or "2026-09-01")',
                    },
                    "end_datetime": {
                        "type": "string",
                        "description": 'End time in ISO format (for create/update, optional, defaults to start + duration)',
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": "Duration in minutes (for create/update, default 60)",
                    },
                    "description": {
                        "type": "string",
                        "description": "Event description (for create/update)",
                    },
                    "location": {
                        "type": "string",
                        "description": "Event location (for create/update)",
                    },
                    "event_id": {
                        "type": "string",
                        "description": "Event ID (for delete, update, or get)",
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
            "description": (
                "Unified web: search, fetch URLs, AI search, weather, GPS. "
                "operation='weather' returns rich conditions, multi-day forecasts (days=1..14), "
                "specific date queries (date='tomorrow' or 'YYYY-MM-DD'), hourly snapshots (hour=0..23), "
                "and hourly ranges (start_hour=14, end_hour=18 or time_range='2pm-6pm'). "
                "operation='search' returns title/url/snippet results (Tavily primary). "
                "Use for any internet task."
            ),
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
                        "description": "Number of results for search (1–10, default 5)",
                    },
                    "url": {
                        "type": "string",
                        "description": "URL to fetch (for fetch)",
                    },
                    "location": {
                        "type": "string",
                        "description": 'City for weather, or "current" for GPS location',
                    },
                    "date": {
                        "type": "string",
                        "description": "Target date for weather forecast or history (e.g. 'today', 'tomorrow', '2026-08-30', 'in 3 days')",
                    },
                    "days": {
                        "type": "integer",
                        "description": "Number of forecast days (1–14, default 1). Use for multi-day forecasts (e.g. 3 or 7 days)",
                    },
                    "hour": {
                        "type": "integer",
                        "description": "Specific hour of day (0–23 or '2pm') for single-hour forecast",
                    },
                    "start_hour": {
                        "type": "integer",
                        "description": "Starting hour (0–23 or '9am') for hourly range forecast",
                    },
                    "end_hour": {
                        "type": "integer",
                        "description": "Ending hour (0–23 or '5pm') for hourly range forecast",
                    },
                    "time_range": {
                        "type": "string",
                        "description": "Time range string for hourly forecast (e.g. '2pm-8pm', '14:00-20:00')",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Start date for date range forecast (e.g. 'today', '2026-08-29')",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End date for date range forecast (up to 14 days from today)",
                    },
                    "include_hourly": {
                        "type": "boolean",
                        "description": "If true, includes complete hour-by-hour forecast breakdown",
                    },
                },
                "required": ["operation"],
            },
        },
    },

]
