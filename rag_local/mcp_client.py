from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Per-server connection timeout (seconds). 8s is enough for pre-pulled Docker images.
MCP_CONNECT_TIMEOUT = 8


def _load_env_file(env_path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file and return as dict."""
    env_vars: dict[str, str] = {}
    if not env_path.exists():
        return env_vars
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env_vars[key.strip()] = value.strip()
    return env_vars


def _resolve_command(command: str) -> str:
    """Resolve binary path for docker, npx, python, etc."""
    import shutil
    if command == "docker":
        path = shutil.which("docker")
        if not path:
            for candidate in [
                "/usr/local/bin/docker",
                "/opt/homebrew/bin/docker",
                "/Applications/Docker.app/Contents/Resources/bin/docker",
            ]:
                if Path(candidate).exists():
                    return candidate
        return path or "docker"
    resolved = shutil.which(command)
    return resolved or command


async def _try_connect_server(
    name: str,
    command: str,
    resolved_args: list[str],
    merged_env: dict[str, str],
    timeout: float,
) -> tuple[str, AsyncExitStack | None, ClientSession | None]:
    """
    Try to connect a single MCP server with a deadline.

    Each connection runs in its own asyncio.Task so anyio cancel scopes
    stay task-bound. On timeout we cancel the Task directly (not anyio
    scopes), which avoids the 'cancel scope in different task' RuntimeError.
    """
    result: list[tuple[AsyncExitStack, ClientSession]] = []

    async def _connect() -> None:
        stack = AsyncExitStack()
        try:
            params = StdioServerParameters(
                command=command, args=resolved_args, env=merged_env
            )
            read_stream, write_stream = await stack.enter_async_context(
                stdio_client(params, errlog=subprocess.DEVNULL)
            )
            session = await stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
            result.append((stack, session))
        except Exception:
            try:
                await stack.aclose()
            except Exception:
                pass

    task = asyncio.create_task(_connect())
    # Silence 'Task exception was never retrieved' warnings from asyncio GC
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() and t.done() and t.exception() else None)

    try:
        # shield prevents wait_for from cancelling the inner task;
        # we cancel it ourselves below so anyio scopes stay in their task.
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except (asyncio.TimeoutError, Exception):
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        return name, None, None

    if result:
        stack, session = result[0]
        return name, stack, session
    return name, None, None


class MCPClientManager:
    def __init__(self, config_path: str | Path = "mcp_config.json") -> None:
        path = Path(config_path)
        if not path.is_absolute():
            from .config import WORKSPACE_DIR, PACKAGE_ROOT
            workspace_config = WORKSPACE_DIR / path
            if workspace_config.exists():
                path = workspace_config
            else:
                path = PACKAGE_ROOT / path
        self.config_path = path
        self._exit_stacks: dict[str, AsyncExitStack] = {}
        self.sessions: dict[str, ClientSession] = {}
        self.server_names: list[str] = []

    async def start_all(self, timeout: float = MCP_CONNECT_TIMEOUT) -> None:
        """Connect to ALL servers in parallel, each with its own timeout."""
        if not self.config_path.exists():
            print("MCP is not connected (config missing)")
            return

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception:
            print("MCP connection failed (bad config)")
            return

        servers = config.get("mcpServers", {})
        if not servers:
            print("MCP is not connected (no servers configured)")
            return

        # ── Resolve template variables ──────────────────────────────
        from .config import WORKSPACE_DIR, PACKAGE_ROOT
        workspace_dir     = str(WORKSPACE_DIR)
        python_executable = sys.executable
        home_dir          = str(Path.home())
        package_root      = str(PACKAGE_ROOT)

        env_path = Path(workspace_dir) / ".env"
        if not env_path.exists():
            env_path = PACKAGE_ROOT / ".env"
        dot_env       = _load_env_file(env_path)
        github_token  = dot_env.get("GITHUB_PERSONAL_ACCESS_TOKEN", "") or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
        firecrawl_key = dot_env.get("FIRECRAWL_API_KEY", "")           or os.environ.get("FIRECRAWL_API_KEY", "")
        postgres_conn = dot_env.get("POSTGRES_CONNECTION_STRING", "")  or os.environ.get("POSTGRES_CONNECTION_STRING", "postgresql://localhost:5432/postgres")

        def _t(s: str) -> str:
            return (
                s.replace("{{WORKSPACE}}", workspace_dir)
                 .replace("{{HOME}}", home_dir)
                 .replace("{{PYTHON}}", python_executable)
                 .replace("{{PACKAGE_ROOT}}", package_root)
                 .replace("{{GITHUB_PERSONAL_ACCESS_TOKEN}}", github_token)
                 .replace("{{FIRECRAWL_API_KEY}}", firecrawl_key)
                 .replace("{{POSTGRES_CONNECTION_STRING}}", postgres_conn)
            )

        base_env = {**os.environ, **dot_env}

        print(f"Connecting to MCP... (parallel, {timeout:.0f}s timeout per server)")

        # ── Build one coroutine per server ──────────────────────────
        coros = []
        for name, srv_config in servers.items():
            command       = _resolve_command(_t(srv_config.get("command", "")))
            resolved_args = [_t(str(a)) for a in srv_config.get("args", [])]
            server_env    = {k: _t(str(v)) for k, v in srv_config.get("env", {}).items()}
            merged_env    = {**base_env, **server_env}
            coros.append(
                _try_connect_server(name, command, resolved_args, merged_env, timeout)
            )

        # ── Run all in parallel ─────────────────────────────────────
        outcomes = await asyncio.gather(*coros, return_exceptions=True)

        for outcome in outcomes:
            if isinstance(outcome, Exception):
                continue
            name, stack, session = outcome
            if session is not None and stack is not None:
                self._exit_stacks[name] = stack
                self.sessions[name] = session
                self.server_names.append(name)

        total  = len(servers)
        active = len(self.sessions)
        if active:
            print(f"MCP is connected ({active}/{total} servers active)")
        else:
            print("MCP connection failed (0 servers responded)")

    async def stop_all(self) -> None:
        """Close all connections and terminate all subprocesses."""
        close_tasks = [
            asyncio.ensure_future(stack.aclose())
            for stack in self._exit_stacks.values()
        ]
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
        self._exit_stacks.clear()
        self.sessions.clear()
        self.server_names.clear()

    async def get_all_tools(self) -> list[dict[str, Any]]:
        """Query all active sessions in parallel and return a unified tool list."""
        async def _list(name: str, session: ClientSession) -> list[dict[str, Any]]:
            try:
                response   = await asyncio.wait_for(session.list_tools(), timeout=5.0)
                tools_list = getattr(response, "tools", [])
                return [
                    {
                        "name":         t.name,
                        "description":  t.description,
                        "input_schema": (
                            t.inputSchema if hasattr(t, "inputSchema")
                            else getattr(t, "input_schema", {})
                        ),
                        "server_name": name,
                    }
                    for t in tools_list
                ]
            except Exception:
                return []

        results = await asyncio.gather(
            *[_list(name, session) for name, session in self.sessions.items()],
            return_exceptions=False,
        )
        return [tool for sublist in results for tool in sublist]

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float | None = None,
    ) -> str:
        """Call a specific tool on a server and return the result as a string.

        ``timeout`` overrides the per-call deadline. When omitted, the default
        is ``SETTINGS.mcp_ops_timeout`` for long-running operation commands and
        60 seconds for all other tools.
        """
        session = self.sessions.get(server_name)
        if not session:
            return f"Error: MCP Server '{server_name}' is not running or active."

        # ── Allowlist enforcement ────────────────────────────────────────────
        from .orchestrator import ALLOWED_MCP_TOOLS  # local import to avoid circular
        if server_name in ALLOWED_MCP_TOOLS:
            allowed_tools = ALLOWED_MCP_TOOLS[server_name]
            if allowed_tools is not None and tool_name not in allowed_tools:
                return (
                    f"Error: Tool '{tool_name}' on server '{server_name}' is not in the"
                    f" allowed tool list {sorted(allowed_tools)}."
                )

        # ── Resolve relative paths for filesystem server ─────────────────────
        if server_name == "filesystem" and arguments:
            from .config import WORKSPACE_DIR
            workspace_dir = str(WORKSPACE_DIR)
            for key in ["path", "source", "destination"]:
                if key in arguments and isinstance(arguments[key], str):
                    val = arguments[key]
                    if val and not os.path.isabs(val):
                        arguments[key] = os.path.abspath(os.path.join(workspace_dir, val))

        # ── Determine call timeout ───────────────────────────────────────────
        if timeout is None:
            from .config import SETTINGS
            if server_name == "operations" and tool_name == "execute_operational_command":
                timeout = SETTINGS.mcp_ops_timeout
            else:
                timeout = 60.0

        try:
            result       = await asyncio.wait_for(
                session.call_tool(tool_name, arguments), timeout=timeout
            )
            content_list = getattr(result, "content", [])
            parts: list[str] = []
            for item in content_list:
                if hasattr(item, "text"):
                    parts.append(item.text)
                elif isinstance(item, dict) and "text" in item:
                    parts.append(item["text"])
                elif hasattr(item, "image"):
                    parts.append("[Image Data received]")
            return "\n".join(parts) if parts else "Success: Tool executed with no text output."
        except asyncio.TimeoutError:
            return f"Error: Tool '{tool_name}' on '{server_name}' timed out after {timeout:.0f}s."
        except Exception as e:
            return f"Error executing tool '{tool_name}' on '{server_name}': {e}"


# Singleton instance manager for runtime use
mcp_manager = MCPClientManager()
