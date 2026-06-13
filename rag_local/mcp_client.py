from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


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


class MCPClientManager:
    def __init__(self, config_path: str | Path = "mcp_config.json") -> None:
        self.config_path = Path(config_path)
        self.exit_stack = AsyncExitStack()
        self.sessions: dict[str, ClientSession] = {}
        self.server_names: list[str] = []

    async def start_all(self) -> None:
        """Read config, resolve template variables, and connect to all MCP servers."""
        if not self.config_path.exists():
            print("MCP is not connected (config missing)")
            return

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            print("MCP connection failed")
            return

        servers = config.get("mcpServers", {})
        if not servers:
            print("MCP is not connected (no servers configured)")
            return

        workspace_dir = str(Path(".").resolve())
        python_executable = sys.executable
        home_dir = str(Path.home())

        # Load secrets from .env file (workspace root)
        dot_env = _load_env_file(Path(workspace_dir) / ".env")
        github_token = dot_env.get("GITHUB_PERSONAL_ACCESS_TOKEN", "") or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
        firecrawl_key = dot_env.get("FIRECRAWL_API_KEY", "") or os.environ.get("FIRECRAWL_API_KEY", "")
        postgres_conn = dot_env.get("POSTGRES_CONNECTION_STRING", "") or os.environ.get("POSTGRES_CONNECTION_STRING", "postgresql://localhost:5432/postgres")

        print("Connecting to MCP...")

        import shutil
        for name, srv_config in servers.items():
            command = srv_config.get("command", "")
            args = srv_config.get("args", [])

            # Resolve template parameters
            if command == "{{PYTHON}}":
                command = python_executable
            elif command == "{{WORKSPACE}}":
                command = workspace_dir
            elif command == "docker":
                # Find docker executable dynamically
                docker_path = shutil.which("docker")
                if not docker_path:
                    for path in [
                        "/usr/local/bin/docker",
                        "/opt/homebrew/bin/docker",
                        "/Applications/Docker.app/Contents/Resources/bin/docker",
                    ]:
                        if Path(path).exists():
                            docker_path = path
                            break
                if docker_path:
                    command = docker_path

            # Resolve env block template values
            env_block = srv_config.get("env", {})
            resolved_env_block: dict[str, str] = {}
            for k, v in env_block.items():
                v = str(v)
                v = v.replace("{{GITHUB_PERSONAL_ACCESS_TOKEN}}", github_token)
                v = v.replace("{{FIRECRAWL_API_KEY}}", firecrawl_key)
                v = v.replace("{{POSTGRES_CONNECTION_STRING}}", postgres_conn)
                v = v.replace("{{WORKSPACE}}", workspace_dir)
                v = v.replace("{{HOME}}", home_dir)
                v = v.replace("{{PYTHON}}", python_executable)
                resolved_env_block[k] = v

            resolved_args = []
            for arg in args:
                arg_str = str(arg)
                arg_str = arg_str.replace("{{WORKSPACE}}", workspace_dir)
                arg_str = arg_str.replace("{{HOME}}", home_dir)
                arg_str = arg_str.replace("{{PYTHON}}", python_executable)
                arg_str = arg_str.replace("{{GITHUB_PERSONAL_ACCESS_TOKEN}}", github_token)
                arg_str = arg_str.replace("{{FIRECRAWL_API_KEY}}", firecrawl_key)
                arg_str = arg_str.replace("{{POSTGRES_CONNECTION_STRING}}", postgres_conn)
                resolved_args.append(arg_str)

            try:
                # Build merged env: system env + .env secrets + server-specific overrides
                merged_env = os.environ.copy()
                merged_env.update(dot_env)
                merged_env.update(resolved_env_block)
                server_params = StdioServerParameters(
                    command=command,
                    args=resolved_args,
                    env=merged_env
                )
                # Enter stdio_client context
                import subprocess
                read_stream, write_stream = await self.exit_stack.enter_async_context(
                    stdio_client(server_params, errlog=subprocess.DEVNULL)
                )
                # Enter ClientSession context
                session = await self.exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                # Initialize session
                await session.initialize()
                self.sessions[name] = session
                self.server_names.append(name)
            except Exception as e:
                pass

        if self.sessions:
            print(f"MCP is connected ({len(self.sessions)}/{len(servers)} servers active)")
        else:
            print("MCP connection failed")

    async def stop_all(self) -> None:
        """Close all connections and terminate all subprocesses."""
        await self.exit_stack.aclose()
        self.sessions.clear()
        self.server_names.clear()

    async def get_all_tools(self) -> list[dict[str, Any]]:
        """Query all active sessions and return unified list of tools."""
        all_tools = []
        for name, session in self.sessions.items():
            try:
                response = await session.list_tools()
                # response typically has response.tools
                tools_list = getattr(response, "tools", [])
                for t in tools_list:
                    # Convert tool schema to standard format
                    tool_dict = {
                        "name": t.name,
                        "description": t.description,
                        "input_schema": t.inputSchema if hasattr(t, "inputSchema") else getattr(t, "input_schema", {}),
                        "server_name": name
                    }
                    all_tools.append(tool_dict)
            except Exception as e:
                pass
        return all_tools

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a specific tool on a server and format the return content as string."""
        session = self.sessions.get(server_name)
        if not session:
            return f"Error: MCP Server '{server_name}' is not running or active."

        try:
            result = await session.call_tool(tool_name, arguments)
            content_list = getattr(result, "content", [])
            output_parts = []
            for item in content_list:
                if hasattr(item, "text"):
                    output_parts.append(item.text)
                elif isinstance(item, dict) and "text" in item:
                    output_parts.append(item["text"])
                elif hasattr(item, "image"):
                    output_parts.append(f"[Image Data received]")
            return "\n".join(output_parts) if output_parts else "Success: Tool executed with no text output."
        except Exception as e:
            return f"Error executing tool '{tool_name}' on '{server_name}': {e}"


# Singleton instance manager for runtime use
mcp_manager = MCPClientManager()
