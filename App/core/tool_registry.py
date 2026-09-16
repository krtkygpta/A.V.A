"""
Plugin-based tool registry for A.V.A

To add a new tool, create a file in functions/plugins/ and use the @tool decorator:

    from core.tool_registry import tool

    @tool(
        name="my_tool",
        description="Does something awesome",
        params={"message": {"type": "string", "description": "What to say"}},
        required=["message"]
    )
    def my_tool(message: str) -> str:
        return f"You said: {message}"

The tool will auto-register for both execution AND schema generation.
"""

import importlib
import pkgutil
import sys
from pathlib import Path
from typing import Any, Callable


class ToolRegistry:
    """Singleton registry for all tools"""

    def __init__(self):
        self._tools: dict[str, Any] = {}
        self._initialized = False

    def register(
        self,
        name: str,
        func: Callable,
        description: str,
        params: dict,
        required: list = None,
        target: str = "all",
    ):
        """Register a tool"""
        self._tools[name] = {
            "func": func,
            "description": description,
            "params": params,
            "required": required or [],
            "target": (target or "all").lower().strip(),
        }

    def _matches_target(self, tool_target: str, target: str | None) -> bool:
        if not target or target == "all":
            return True
        target = target.lower().strip()
        tool_target = (tool_target or "all").lower().strip()
        if tool_target == "all":
            return True
        if target in ("tui", "client") and tool_target in ("tui", "client"):
            return True
        if target == "server" and tool_target == "server":
            return True
        return False

    def get(self, name: str, target: str = None) -> Callable | None:
        """Get tool function by name, optionally filtered by target runtime"""
        tool_data = self._tools.get(name)
        if not tool_data:
            return None
        if target and not self._matches_target(tool_data.get("target", "all"), target):
            return None
        return tool_data["func"]

    def all_tools(self, target: str = None) -> dict[str, dict]:
        """Get all registered tools, optionally filtered by target runtime"""
        if not target or target == "all":
            return self._tools.copy()
        return {
            k: v for k, v in self._tools.items()
            if self._matches_target(v.get("target", "all"), target)
        }

    def get_schema(self, target: str = None) -> list:
        """Generate OpenAI tool schema for tools, optionally filtered by target runtime"""
        schema = []
        for name, tool_data in self._tools.items():
            if target and not self._matches_target(tool_data.get("target", "all"), target):
                continue
            schema.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool_data["description"],
                        "parameters": {
                            "type": "object",
                            "properties": tool_data["params"],
                            "required": tool_data["required"],
                        },
                    },
                }
            )
        return schema

    def execute(self, name: str, **kwargs) -> Any:
        """Execute a tool by name with given arguments"""
        func = self.get(name)
        if func is None:
            return f"Unknown tool: {name}"
        try:
            return func(**kwargs)
        except Exception as e:
            return f"Error: {e}"


# Global registry instance
registry = ToolRegistry()


def tool(
    name: str = None,
    description: str = "",
    params: dict = None,
    required: list = None,
    target: str = "all",
) -> Callable:
    """
    Decorator to register a function as a tool.

    Usage:
        @tool(name="my_tool", description="Does X",
              params={"arg1": {"type": "string"}}, required=["arg1"], target="all")
        def my_tool(arg1: str) -> str:
            return f"Did: {arg1}"
    """

    def decorator(func: Callable) -> Callable:
        tool_name = name or func.__name__
        registry.register(
            name=tool_name,
            func=func,
            description=description or (func.__doc__ or "").strip().split("\n")[0],
            params=params or {},
            required=required or [],
            target=target,
        )
        return func

    return decorator


def get_registry() -> ToolRegistry:
    """Get the global tool registry"""
    return registry


def init_plugins():
    """Initialize plugins by discovering and importing all tool-decorated functions"""
    if registry._initialized:
        return

    # Only scan functions/plugins/ directory
    plugins_dir = Path(__file__).parent.parent / "functions" / "plugins"

    if not plugins_dir.exists():
        registry._initialized = True
        print("[ToolRegistry] No plugins directory found")
        return

    # Import all modules from plugins directory using isolated loading
    # to avoid triggering functions/__init__.py
    for importer, modname, ispkg in pkgutil.iter_modules([str(plugins_dir)]):
        if modname.startswith("_"):
            continue
        try:
            # Load module directly from file to bypass package __init__.py
            module_name = f"functions.plugins.{modname}"
            if module_name in sys.modules:
                continue

            mod_file = plugins_dir / f"{modname}.py"
            if not mod_file.exists():
                continue

            spec = importlib.util.spec_from_file_location(module_name, mod_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
        except Exception:
            # Silently ignore import errors
            pass

    # Claude Code SKILL.md folders dropped into plugins/ or skills/
    try:
        from core.cc_plugins.skills_host import get_host

        get_host().load()
    except Exception as e:
        print(f"[ToolRegistry] Claude Code skills failed to load: {e}")

    # MCP servers configured in mcps.json (target: tui / client)
    try:
        from core.mcp_manager import init_mcp_servers

        init_mcp_servers(tool_registry=registry, target="tui")
    except Exception as e:
        print(f"[ToolRegistry] MCP servers failed to load: {e}")

    registry._initialized = True
    print(f"[ToolRegistry] Loaded {len(registry._tools)} tools")
