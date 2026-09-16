"""
Server Tool Registry for A.V.A Central Server

Discovers, registers, and executes:
1. Python plugins from Server/plugins/ and App/functions/plugins/ (via @tool or @server_tool)
2. Claude Code skills from Server/skills/, App/functions/skills/, and App/functions/plugins/
3. MCP (Model Context Protocol) servers from Server/mcps.json, mcps.json, etc. (target: server / all)

Provides async-safe execution, dynamic OpenAI tool schemas, and process management.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("ServerToolRegistry")

# Base directory resolution
SERVER_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = SERVER_DIR.parent
APP_DIR = ROOT_DIR / "App"

# Ensure search paths are available (SERVER_DIR at highest priority)
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))
if str(APP_DIR) not in sys.path:
    sys.path.append(str(APP_DIR))

from core.tool_registry import ToolRegistry, registry, tool


class ServerToolRegistry:
    """Manages all server-executable tools, plugins, and MCP servers."""

    def __init__(self):
        self._registry: ToolRegistry = registry
        self._initialized: bool = False
        self._mcp_manager = None
        self._skills_host = None
        self._tool_metadata: Dict[str, dict] = {}

    def init(self) -> None:
        """Initialize all server tools, plugins, and MCP connections."""
        if self._initialized:
            return

        logger.info("[ServerTools] Initializing server plugins and MCP tools...")

        # 1. Load Server-specific plugins
        self._load_python_plugins(SERVER_DIR / "plugins", prefix="server.plugins")

        # 2. Load Shared App plugins
        self._load_python_plugins(APP_DIR / "functions" / "plugins", prefix="functions.plugins")

        # 3. Load Claude Code skills
        self._load_skills()

        # 4. Connect to MCP servers with target="server"
        self._load_mcps()

        self._initialized = True
        active_tools = self.get_tools()
        logger.info(f"[ServerTools] Initialized {len(active_tools)} server-executable tool(s).")

    def _load_python_plugins(self, directory: Path, prefix: str) -> None:
        """Discover and import Python plugin files in a directory."""
        if not directory.exists() or not directory.is_dir():
            return

        for py_file in directory.glob("*.py"):
            if py_file.name.startswith(("_", ".")):
                continue

            mod_stem = py_file.stem.replace("-", "_")
            module_name = f"{prefix}.{mod_stem}"

            if module_name in sys.modules:
                continue

            try:
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    spec.loader.exec_module(module)
                    logger.debug(f"[ServerTools] Loaded plugin module: {module_name}")
            except Exception as e:
                logger.warning(f"[ServerTools] Failed to load plugin {py_file.name}: {e}")

    def _load_skills(self) -> None:
        """Load Claude Code skills from server and shared directories."""
        try:
            from core.cc_plugins.skills_host import get_host

            self._skills_host = get_host()
            skill_dirs = [
                SERVER_DIR / "skills",
                SERVER_DIR / "plugins",
                APP_DIR / "functions" / "skills",
                APP_DIR / "functions" / "plugins",
            ]
            valid_dirs = [d for d in skill_dirs if d.exists() and d.is_dir()]
            if valid_dirs:
                self._skills_host.load(*valid_dirs)
                logger.debug(f"[ServerTools] Loaded skills from: {[d.name for d in valid_dirs]}")
        except Exception as e:
            logger.warning(f"[ServerTools] Claude Code skills loader failed: {e}")

    def _load_mcps(self) -> None:
        """Connect to MCP servers configured for the server."""
        try:
            from core.mcp_manager import get_mcp_manager

            self._mcp_manager = get_mcp_manager()
            self._mcp_manager.init_servers(tool_registry=self._registry, target="server")
            logger.debug("[ServerTools] MCP servers initialized with target='server'.")
        except Exception as e:
            logger.warning(f"[ServerTools] MCP manager failed to initialize: {e}")

    def has_tool(self, name: str) -> bool:
        """Check if a tool exists and is executable on the server."""
        return self._registry.get(name, target="server") is not None

    def get_tool(self, name: str) -> Optional[Callable]:
        """Get the callable function for a registered tool."""
        return self._registry.get(name, target="server")

    def get_tools(self) -> Dict[str, dict]:
        """Return all server-executable tool definitions."""
        return self._registry.all_tools(target="server")

    def get_schemas(self) -> List[dict]:
        """Return OpenAI-compatible function calling schemas for all server tools."""
        return self._registry.get_schema(target="server")

    async def execute_async(self, name: str, args: dict) -> Any:
        """
        Execute a registered tool asynchronously.
        If the tool function is a coroutine, it will be awaited.
        If it is synchronous, it will be run in a thread pool to avoid blocking the event loop.
        """
        func = self.get_tool(name)
        if func is None:
            return {"error": f"Tool '{name}' not found on server"}

        try:
            if inspect.iscoroutinefunction(func):
                result = await func(**args)
            else:
                result = await asyncio.to_thread(func, **args)

            # Ensure serializable output
            if isinstance(result, (dict, list, str, int, float, bool)) or result is None:
                return result
            return str(result)
        except TypeError as te:
            logger.warning(f"[ServerTools] Tool '{name}' argument mismatch: {te}")
            # Attempt to call with positional or raw payload if dict unpacked failed
            try:
                if inspect.iscoroutinefunction(func):
                    return await func(args)
                else:
                    return await asyncio.to_thread(func, args)
            except Exception as e:
                return {"error": f"Tool '{name}' execution failed: {e}"}
        except Exception as e:
            logger.error(f"[ServerTools] Error executing '{name}': {e}", exc_info=True)
            return {"error": f"Tool '{name}' execution error: {e}"}

    def stop_all(self) -> None:
        """Stop all running MCP servers and child processes."""
        if self._mcp_manager:
            try:
                self._mcp_manager.stop_all()
                logger.info("[ServerTools] Stopped all server MCP client processes.")
            except Exception as e:
                logger.warning(f"[ServerTools] Error stopping MCP servers: {e}")


# Global singleton
server_tool_registry = ServerToolRegistry()


def server_tool(
    name: str = None,
    description: str = "",
    params: dict = None,
    required: list = None,
) -> Callable:
    """Decorator to register a server-exclusive tool."""
    return tool(
        name=name,
        description=description,
        params=params,
        required=required,
        target="server",
    )
