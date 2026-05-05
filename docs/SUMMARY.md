# A.V.A Documentation Summary

Welcome to the A.V.A documentation! Use this index to find the information you need.

## Getting Started

| Document | Description |
|----------|-------------|
| `README.md` | Quick start, overview, and key features |
| `docs/getting-started.md` | Step-by-step installation and setup |

## Core Documentation

| Document | Description |
|----------|-------------|
| `docs/architecture.md` | System architecture, components, and data flow |
| `docs/configuration.md` | All configuration options and settings |
| `docs/api-reference.md` | HTTP API endpoints for the server backend |

## Features & Tools

| Document | Description |
|----------|-------------|
| `docs/memory-system.md` | Semantic memory for persistent user context |
| `docs/tools-reference.md` | Complete reference for all available tools |

## Quick Links

**Installation:**
1. Install dependencies: `pip install -r requirements.txt`
2. Install Playwright: `playwright install`
3. Configure `settings.json` with API keys
4. Start server: `python -m Server`
5. Start client: `python App/__main__.py`

**Architecture Overview:**
- Client (`App/`) — TUI, voice modes, local tools, memory
- Server (`Server/`) — LLM, TTS, remote tools, conversation storage

**Available Tools:**
- System — Time, shutdown, notifications, smart home
- Web — Search, deep research
- Productivity — Calendar, files, PDF generation
- AI — Code sandbox, image analysis
- Memory — Semantic memory management

## Need Help?

| Issue | Resource |
|-------|----------|
| Setup problems | `docs/getting-started.md` |
| Configuration questions | `docs/configuration.md` |
| Tool usage | `docs/tools-reference.md` |
| Architecture details | `docs/architecture.md` |

## Documentation Map

```
A.V.A/
├── README.md                    ← Start here for overview
└── docs/
    ├── SUMMARY.md              ← You are here
    ├── getting-started.md      ← Installation & setup
    ├── architecture.md         ← System design
    ├── configuration.md        ← Settings reference
    ├── api-reference.md        ← HTTP API docs
    ├── memory-system.md        ← Memory system
    └── tools-reference.md      ← Tool documentation
```

## Keyboard Shortcuts (TUI)

| Shortcut | Action |
|----------|--------|
| `Ctrl+C` | Emergency shutdown |
| `Ctrl+I` | Open settings |
| `Ctrl+Q` | Quit application |
| `Ctrl+F` | Search conversations |

## Operational Modes

| Mode | Command | Description |
|------|---------|-------------|
| TUI | Default | Full terminal interface |
| Continuous | `--mode continuous` | Background voice listening |
| Wake Word | `--mode wakeword` | "Hey AVA" activation |
| Text | `--mode text` | Keyboard only |