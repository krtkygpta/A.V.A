"""
Model Context Protocol (MCP) Client Manager for A.V.A

Allows A.V.A to connect to any standard MCP server (Claude Code / Desktop compatible)
via stdio or HTTP/SSE, discover tools automatically, and register them into ToolRegistry.
"""

from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    import requests
except ImportError:
    requests = None


class MCPStdioClient:
    """Manages a standard stdio-based MCP server process."""

    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config
        self.command = config.get("command")
        self.args = config.get("args", [])
        self.env = config.get("env", {})
        self.disabled = config.get("disabled", False)

        self.process: Optional[subprocess.Popen] = None
        self._req_id = 0
        self._pending_requests: Dict[int, dict] = {}
        self._pending_events: Dict[int, threading.Event] = {}
        self._lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._is_running = False
        self.tools: List[dict] = []
        self._stderr_lines: List[str] = []

    def _next_id(self) -> int:
        with self._lock:
            self._req_id += 1
            return self._req_id

    def start(self) -> bool:
        """Start the MCP server subprocess and perform standard JSON-RPC handshake."""
        if self.disabled:
            return False

        if not self.command:
            print(f"[MCP:{self.name}] Error: Missing 'command' in server configuration.")
            return False

        import shutil

        expanded_command = os.path.expandvars(str(self.command))
        resolved_bin = shutil.which(expanded_command) or expanded_command
        cmd = [resolved_bin] + [os.path.expandvars(str(a)) for a in self.args]
        env = os.environ.copy()
        for k, v in self.env.items():
            env[str(k)] = os.path.expandvars(str(v))

        try:
            is_win = os.name == "nt"
            is_script = is_win and (str(cmd[0]).lower().endswith((".cmd", ".bat")) or expanded_command in ("npx", "npm", "uvx"))

            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                env=env,
                shell=is_script,
            )
            self._is_running = True

            # Reader loop for stdout
            self._reader_thread = threading.Thread(
                target=self._read_loop,
                name=f"MCPReader-{self.name}",
                daemon=True,
            )
            self._reader_thread.start()

            # Stderr drain thread to prevent pipe blocking
            threading.Thread(
                target=self._drain_stderr,
                name=f"MCPErr-{self.name}",
                daemon=True,
            ).start()

            # 1. Initialize Handshake (30s timeout for npx download on first run)
            init_res = self.send_request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "AVA", "version": "1.0.0"},
                },
                timeout=30.0,
            )

            if not init_res:
                print(f"[MCP:{self.name}] Initialize failed or timed out.")
                if self._stderr_lines:
                    err_snippet = "\n".join(self._stderr_lines[-5:]).strip()
                    print(f"[MCP:{self.name}] Stderr output:\n{err_snippet}")
                self.stop()
                return False

            # 2. Initialized Notification
            self.send_notification("notifications/initialized", {})

            # 3. Dynamic Tool Discovery
            tools_res = self.send_request("tools/list", {}, timeout=12.0)
            if tools_res and "tools" in tools_res:
                self.tools = tools_res["tools"]
                print(f"[MCP:{self.name}] Connected. Discovered {len(self.tools)} tool(s).")
            else:
                self.tools = []
                print(f"[MCP:{self.name}] Connected (0 tools reported).")

            return True

        except Exception as e:
            print(f"[MCP:{self.name}] Failed to spawn process: {e}")
            self.stop()
            return False

    def _read_loop(self):
        """Read JSON-RPC lines from server stdout."""
        while self._is_running and self.process and self.process.stdout:
            try:
                line = self.process.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                except Exception:
                    continue

                msg_id = data.get("id")
                if msg_id is not None:
                    with self._lock:
                        if msg_id in self._pending_events:
                            self._pending_requests[msg_id] = data
                            self._pending_events[msg_id].set()

            except Exception:
                break
        self._is_running = False

    def _drain_stderr(self):
        """Drain stderr stream to prevent process stalls and capture error messages."""
        while self._is_running and self.process and self.process.stderr:
            try:
                line = self.process.stderr.readline()
                if not line:
                    break
                s = line.strip()
                if s:
                    self._stderr_lines.append(s)
                    if len(self._stderr_lines) > 50:
                        self._stderr_lines.pop(0)
            except Exception:
                break

    def send_notification(self, method: str, params: dict | None = None):
        """Send a JSON-RPC notification."""
        if not self._is_running or not self.process or not self.process.stdin:
            return

        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        try:
            line = json.dumps(msg) + "\n"
            with self._lock:
                self.process.stdin.write(line)
                self.process.stdin.flush()
        except Exception:
            pass

    def send_request(self, method: str, params: dict | None = None, timeout: float = 30.0) -> Optional[dict]:
        """Send a JSON-RPC request and await response."""
        if not self._is_running or not self.process or not self.process.stdin:
            return None

        req_id = self._next_id()
        event = threading.Event()

        with self._lock:
            self._pending_events[req_id] = event

        msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }

        try:
            line = json.dumps(msg) + "\n"
            with self._lock:
                self.process.stdin.write(line)
                self.process.stdin.flush()

            signaled = event.wait(timeout=timeout)
            if not signaled:
                return None

            with self._lock:
                response = self._pending_requests.pop(req_id, {})
                self._pending_events.pop(req_id, None)

            if "error" in response:
                return None

            return response.get("result")

        except Exception:
            with self._lock:
                self._pending_events.pop(req_id, None)
                self._pending_requests.pop(req_id, None)
            return None

    def call_tool(self, tool_name: str, arguments: dict, timeout: float = 60.0) -> str:
        """Call an MCP tool dynamically and return string output."""
        res = self.send_request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            timeout=timeout,
        )
        if not res:
            return f"Error: No response from MCP server '{self.name}' for tool '{tool_name}'."

        if res.get("isError"):
            content_list = res.get("content", [])
            err_texts = [c.get("text", "") for c in content_list if isinstance(c, dict)]
            return f"MCP Tool Error: {' '.join(err_texts) or 'Unknown error'}"

        content_list = res.get("content", [])
        text_outputs = []
        for item in content_list:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    text_outputs.append(item.get("text", ""))
                elif item.get("type") == "resource":
                    text_outputs.append(str(item.get("resource", "")))
                else:
                    text_outputs.append(json.dumps(item))
            else:
                text_outputs.append(str(item))

        return "\n".join(text_outputs) if text_outputs else "Success"

    def stop(self):
        """Terminate server process and close streams."""
        self._is_running = False
        if self.process:
            try:
                if self.process.stdin:
                    self.process.stdin.close()
            except Exception:
                pass
            try:
                self.process.terminate()
                self.process.wait(timeout=2.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            try:
                if self.process.stdout:
                    self.process.stdout.close()
                if self.process.stderr:
                    self.process.stderr.close()
            except Exception:
                pass
            self.process = None


class MCPHttpClient:
    """Manages an HTTP/SSE remote MCP server."""

    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config
        self.url = config.get("url") or config.get("serverUrl")
        self.headers = config.get("headers", {}).copy() if isinstance(config.get("headers"), dict) else {}
        self.disabled = config.get("disabled", False)

        if self.url:
            self.url = os.path.expandvars(str(self.url))
        for k, v in list(self.headers.items()):
            self.headers[k] = os.path.expandvars(str(v))

        self.tools: List[dict] = []
        self._req_id = 0
        self._lock = threading.Lock()

    def _next_id(self) -> int:
        with self._lock:
            self._req_id += 1
            return self._req_id

    def start(self) -> bool:
        if self.disabled or not self.url or not requests:
            return False

        try:
            init_res = self.send_request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "AVA", "version": "1.0.0"},
                },
                timeout=10.0,
            )

            tools_res = self.send_request("tools/list", {}, timeout=10.0)
            if tools_res and "tools" in tools_res:
                self.tools = tools_res["tools"]
                print(f"[MCP:{self.name}] Connected. Discovered {len(self.tools)} tool(s).")
            else:
                self.tools = []

            return True
        except Exception as e:
            print(f"[MCP:{self.name}] Failed to connect to {self.url}: {e}")
            return False

    def send_request(self, method: str, params: dict | None = None, timeout: float = 30.0) -> Optional[dict]:
        if not requests or not self.url:
            return None

        req_id = self._next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        headers = {"Content-Type": "application/json"}
        headers.update(self.headers)

        try:
            resp = requests.post(self.url, json=payload, headers=headers, timeout=timeout)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if "error" in data:
                return None
            return data.get("result")
        except Exception:
            return None

    def call_tool(self, tool_name: str, arguments: dict, timeout: float = 60.0) -> str:
        res = self.send_request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            timeout=timeout,
        )
        if not res:
            return f"Error: No response from MCP server '{self.name}' for tool '{tool_name}'."

        if res.get("isError"):
            content_list = res.get("content", [])
            err_texts = [c.get("text", "") for c in content_list if isinstance(c, dict)]
            return f"MCP Tool Error: {' '.join(err_texts) or 'Unknown error'}"

        content_list = res.get("content", [])
        text_outputs = []
        for item in content_list:
            if isinstance(item, dict) and item.get("type") == "text":
                text_outputs.append(item.get("text", ""))
            else:
                text_outputs.append(str(item))

        return "\n".join(text_outputs) if text_outputs else "Success"

    def stop(self):
        pass


class MCPManager:
    """Singleton manager that discovers and mounts all configured MCP servers."""

    def __init__(self):
        self.clients: Dict[str, Any] = {}
        self._initialized = False
        self._lock = threading.Lock()

    def find_config_files(self) -> List[Path]:
        """Find standard MCP configuration files (Claude Code, root mcps.json, mcp.json)."""
        root_dir = Path(__file__).resolve().parent.parent.parent
        candidates = [
            root_dir / "mcps.json",
            root_dir / "mcp.json",
            root_dir / ".claude" / "mcp.json",
            root_dir / "App" / "mcps.json",
            Path.home() / ".claude" / "mcp.json",
            Path.home() / ".gemini" / "config" / "mcp_config.json",
        ]
        found = []
        for p in candidates:
            if p.exists() and p.is_file() and p not in found:
                found.append(p)
        return found

    def load_configs(self) -> dict:
        """Merge server configurations from all found config files."""
        all_servers = {}
        for config_path in self.find_config_files():
            try:
                if config_path.stat().st_size == 0:
                    continue
                with open(config_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if not content:
                        continue
                    data = json.loads(content)

                servers = data.get("mcpServers") if isinstance(data, dict) and "mcpServers" in data else data
                if isinstance(servers, dict):
                    for name, cfg in servers.items():
                        if isinstance(cfg, dict) and name not in all_servers:
                            all_servers[name] = cfg
            except Exception as e:
                print(f"[MCPManager] Warning: could not parse {config_path}: {e}")
        return all_servers

    def init_servers(self, tool_registry=None):
        """Start all configured MCP servers and register their tools dynamically."""
        with self._lock:
            if self._initialized:
                return

            server_configs = self.load_configs()
            if not server_configs:
                self._initialized = True
                return

            for name, cfg in server_configs.items():
                if not isinstance(cfg, dict) or cfg.get("disabled", False):
                    continue

                if "url" in cfg or "serverUrl" in cfg:
                    client = MCPHttpClient(name, cfg)
                else:
                    client = MCPStdioClient(name, cfg)

                if client.start():
                    self.clients[name] = client
                    if tool_registry:
                        self._register_client_tools(client, tool_registry)

            self._initialized = True

    def _register_client_tools(self, client: Any, tool_registry):
        """Register discovered MCP tools dynamically into A.V.A's tool registry."""
        for tool_meta in client.tools:
            raw_name = tool_meta.get("name", "")
            if not raw_name:
                continue

            description = tool_meta.get("description", f"Tool from MCP server '{client.name}'")
            schema = tool_meta.get("inputSchema", {}) or {}
            params = schema.get("properties", {})
            required = schema.get("required", [])

            # Check if name collides with existing tools
            tool_name = raw_name
            if tool_registry.get(tool_name) is not None:
                tool_name = f"{client.name}__{raw_name}"

            def make_executor(cl: Any, tname: str, desc: str):
                def execute_mcp_tool(**kwargs):
                    return cl.call_tool(tname, kwargs)

                execute_mcp_tool.__doc__ = desc
                return execute_mcp_tool

            executor_func = make_executor(client, raw_name, description)

            tool_registry.register(
                name=tool_name,
                func=executor_func,
                description=description,
                params=params,
                required=required,
            )

    def stop_all(self):
        """Stop all running MCP servers."""
        with self._lock:
            for client in self.clients.values():
                try:
                    client.stop()
                except Exception:
                    pass
            self.clients.clear()
            self._initialized = False


# Global singleton
mcp_manager = MCPManager()
atexit.register(mcp_manager.stop_all)


def init_mcp_servers(tool_registry=None):
    """Entry point to initialize MCP servers."""
    mcp_manager.init_servers(tool_registry=tool_registry)


def get_mcp_manager() -> MCPManager:
    """Get the singleton MCPManager instance."""
    return mcp_manager
