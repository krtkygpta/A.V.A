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
2. Configure `settings.json` with API keys
3. Start server: `python -m Server`
4. Start client: `python -m App`

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
| `Ctrl+I` / `/settings` | Open settings |
| `Ctrl+Q` | Quit application |
| `Ctrl+F` / `/search` | Search conversations |
| `Ctrl+M` | Cycle input mode |
| `Ctrl+H` | History sidebar |
| `Ctrl+B` | Tasks sidebar |
| `Ctrl+T` / `/theme` | Theme picker |
| `Ctrl+N` / `/new` | New conversation |

## Operational Modes

| Mode | Command | Description |
|------|---------|-------------|
| Text | Default | Keyboard input |
| Continuous | `Ctrl+M` or `/mode continuous` | Always-on mic |
| Wake Word | `Ctrl+M` or `/mode wakeword` | Voice-activated |