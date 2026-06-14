from __future__ import annotations

import asyncio
import os
import platform
import shutil
import subprocess
from typing import Any

async def collect_world_state(mcp_manager: Any = None) -> dict[str, Any]:
    """Collects operating system status, workspace files, Docker status, and running services."""
    os_info = f"{platform.system()} {platform.release()} ({platform.machine()})"
    
    # 1. Available MCP Tools
    mcp_tools = []
    if mcp_manager:
        try:
            all_tools = await mcp_manager.get_all_tools()
            mcp_tools = [
                {
                    "server": t["server_name"],
                    "name": t["name"],
                    "description": t.get("description", "")
                }
                for t in all_tools
            ]
        except Exception:
            pass
            
    # 2. Key installed software
    software_keys = ["git", "docker", "curl", "wget", "python", "npm", "npx", "pytest", "cron"]
    installed_software = [sw for sw in software_keys if shutil.which(sw) is not None]

    # 3. Workspace Files (limit to depth 2, exclude noise, max 35 items)
    workspace_files = []
    try:
        count = 0
        for root, dirs, files in os.walk(".", topdown=True):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"node_modules", "__pycache__", "venv", ".venv", ".git"}]
            rel_root = os.path.relpath(root, ".")
            if rel_root == ".":
                rel_root = ""
            for f in files:
                if not f.startswith("."):
                    path = os.path.join(rel_root, f) if rel_root else f
                    workspace_files.append(path)
                    count += 1
                    if count >= 35:
                        break
            if count >= 35 or (rel_root.count(os.sep) >= 1):
                del dirs[:]
                if count >= 35:
                    break
    except Exception:
        pass

    # 4. Active Docker Containers
    docker_containers = []
    if "docker" in installed_software:
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "ps", "--format", "{{.Names}} ({{.Image}})",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=1.5)
            if proc.returncode == 0:
                docker_containers = [line.decode().strip() for line in stdout.splitlines() if line.strip()]
        except Exception:
            pass

    # 5. Running Services (launchd on macOS, systemd on Linux)
    running_services = []
    try:
        if platform.system() == "Darwin" and shutil.which("launchctl"):
            proc = await asyncio.create_subprocess_exec(
                "launchctl", "list",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=1.5)
            if proc.returncode == 0:
                for line in stdout.splitlines()[:50]: # Scan first 50 lines for brevity
                    parts = line.decode().split()
                    if len(parts) >= 3 and parts[0].isdigit():
                        running_services.append(parts[2])
        elif platform.system() == "Linux" and shutil.which("systemctl"):
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "list-units", "--type=service", "--state=running", "--no-legend",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=1.5)
            if proc.returncode == 0:
                for line in stdout.splitlines():
                    parts = line.decode().split()
                    if len(parts) >= 1:
                        running_services.append(parts[0])
    except Exception:
        pass

    return {
        "os": os_info,
        "mcp_tools": mcp_tools[:20],
        "installed_software": installed_software,
        "workspace_files": workspace_files[:35],
        "docker_containers": docker_containers[:10],
        "running_services": running_services[:15],
    }
