from mcp.server.fastmcp import FastMCP
import os
import sys
import subprocess
import platform
import re
import shutil
import ast
import json
from pathlib import Path
from typing import Any, Optional

mcp = FastMCP("Operations")

@mcp.tool()
async def get_system_status() -> str:
    """Check the local host system status including CPU load, RAM usage, Disk space, and Docker health."""
    status_report = []
    
    # 1. OS Info
    os_name = platform.system()
    os_release = platform.release()
    os_arch = platform.machine()
    hostname = platform.node()
    status_report.append(f"=== System Info ===")
    status_report.append(f"OS: {os_name} {os_release} ({os_arch})")
    status_report.append(f"Hostname: {hostname}")
    status_report.append("")
    
    # 2. CPU Load / Load Avg
    status_report.append(f"=== CPU Load ===")
    if hasattr(os, "getloadavg"):
        try:
            load_1, load_5, load_15 = os.getloadavg()
            status_report.append(f"Load Average: 1m: {load_1:.2f}, 5m: {load_5:.2f}, 15m: {load_15:.2f}")
        except Exception as e:
            status_report.append(f"Could not read load average: {e}")
    else:
        status_report.append("Load average not supported on this platform.")
    status_report.append("")
    
    # 3. Memory usage (cross-platform fallback using subprocess)
    status_report.append(f"=== Memory Usage ===")
    if os_name == "Darwin":
        try:
            # Run vm_stat to get memory pages info
            vm = subprocess.run(["vm_stat"], capture_output=True, text=True, check=True)
            page_size = 4096  # default page size on macOS is 4KB
            pages_free = 0
            pages_active = 0
            pages_inactive = 0
            pages_speculative = 0
            pages_wired = 0
            
            for line in vm.stdout.splitlines():
                if "Pages free:" in line:
                    pages_free = int(line.split()[-1].replace(".", ""))
                elif "Pages active:" in line:
                    pages_active = int(line.split()[-1].replace(".", ""))
                elif "Pages inactive:" in line:
                    pages_inactive = int(line.split()[-1].replace(".", ""))
                elif "Pages speculative:" in line:
                    pages_speculative = int(line.split()[-1].replace(".", ""))
                elif "Pages wired down:" in line:
                    pages_wired = int(line.split()[-1].replace(".", ""))
            
            total_pages = pages_free + pages_active + pages_inactive + pages_speculative + pages_wired
            total_mem_gb = (total_pages * page_size) / (1024 ** 3)
            free_mem_gb = ((pages_free + pages_speculative) * page_size) / (1024 ** 3)
            used_mem_gb = total_mem_gb - free_mem_gb
            percent_used = (used_mem_gb / total_mem_gb) * 100
            
            status_report.append(f"Total: {total_mem_gb:.2f} GB")
            status_report.append(f"Used: {used_mem_gb:.2f} GB ({percent_used:.1f}%)")
            status_report.append(f"Free/Speculative: {free_mem_gb:.2f} GB")
        except Exception as e:
            status_report.append(f"Error querying memory on macOS: {e}")
    elif os_name == "Linux":
        try:
            with open("/proc/meminfo", "r") as f:
                meminfo = {}
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        meminfo[parts[0].rstrip(":")] = int(parts[1])
            total_kb = meminfo.get("MemTotal", 0)
            free_kb = meminfo.get("MemFree", 0)
            available_kb = meminfo.get("MemAvailable", total_kb - free_kb)
            used_kb = total_kb - available_kb
            
            total_gb = total_kb / (1024 ** 2)
            used_gb = used_kb / (1024 ** 2)
            free_gb = available_kb / (1024 ** 2)
            percent_used = (used_kb / total_kb) * 100 if total_kb > 0 else 0
            
            status_report.append(f"Total: {total_gb:.2f} GB")
            status_report.append(f"Used: {used_gb:.2f} GB ({percent_used:.1f}%)")
            status_report.append(f"Free: {free_gb:.2f} GB")
        except Exception as e:
            status_report.append(f"Error querying memory on Linux: {e}")
    else:
        status_report.append("Unsupported platform for detailed memory stats.")
    status_report.append("")
    
    # 4. Disk Space (Uses shutil.disk_usage for cross-platform robustness)
    status_report.append(f"=== Disk Space ===")
    try:
        usage = shutil.disk_usage(".")
        total_gb = usage.total / (1024 ** 3)
        used_gb = usage.used / (1024 ** 3)
        free_gb = usage.free / (1024 ** 3)
        percent_used = (usage.used / usage.total) * 100
        status_report.append(f"Total Disk Size: {total_gb:.2f} GB")
        status_report.append(f"Used Disk Space: {used_gb:.2f} GB ({percent_used:.1f}%)")
        status_report.append(f"Free Disk Space: {free_gb:.2f} GB")
    except Exception as e:
        status_report.append(f"Error reading disk usage: {e}")
    status_report.append("")
    
    # 5. Docker health check
    status_report.append(f"=== Docker Containers ===")
    docker_bin = shutil.which("docker")
    if not docker_bin:
        status_report.append("Docker client is not installed or not in PATH.")
    else:
        try:
            # Check if docker daemon is reachable
            daemon_check = subprocess.run([docker_bin, "info"], capture_output=True, text=True, timeout=5)
            if daemon_check.returncode != 0:
                status_report.append("Docker client is installed, but the Docker daemon is not running.")
            else:
                # Get running containers list
                containers = subprocess.run(
                    [docker_bin, "ps", "--format", "table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=True
                )
                lines = containers.stdout.strip().splitlines()
                if len(lines) <= 1:
                    status_report.append("No active running containers found.")
                else:
                    status_report.extend(lines)
        except subprocess.TimeoutExpired:
            status_report.append("Docker status check timed out.")
        except Exception as e:
            status_report.append(f"Error checking Docker: {e}")
            
    return "\n".join(status_report)


@mcp.tool()
async def run_security_audit(directory_path: Optional[str] = None) -> str:
    """Scan the workspace directory for potential security issues, hardcoded secrets, and unsafe file permissions."""
    audit_report = []
    
    workspace_dir = Path(directory_path or ".").resolve()
    audit_report.append(f"=== Running Security Audit ===")
    audit_report.append(f"Target Directory: {workspace_dir}")
    audit_report.append("")
    
    # Secret Scanning regex patterns
    secret_patterns = {
        "AWS Access Key ID": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "AWS Secret Access Key": re.compile(r"\b[a-zA-Z0-9+/]{40}\b"),
        "Generic Password/Secret assignment": re.compile(
            r"\b(password|secret|passwd|token|api_key|access_token|private_key|auth_token)\s*=\s*['\"][A-Za-z0-9\-_\.=]{16,}\b['\"]",
            re.IGNORECASE
        ),
        "GitHub Personal Access Token": re.compile(r"\b(ghp_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]{82})\b"),
        "Slack Webhook URL": re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]{8}/B[A-Z0-9]{8}/[A-Za-z0-9]{24}"),
        "Private Key file content": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    }
    
    ignored_dirs = {".git", ".venv", "__pycache__", "node_modules", "qdrant_local", "indexes", "dist", "build"}
    ignored_extensions = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".tar", ".gz", ".db", ".sqlite", ".pyc", ".class"}
    
    found_secrets = []
    unsafe_permissions = []
    
    # 1. Secret scanning + Permission auditing
    for root, dirs, files in os.walk(workspace_dir):
        # Skip ignored directories
        dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]
        
        for file in files:
            file_path = Path(root) / file
            
            # Skip hidden files except sensitive config files
            if file.startswith(".") and file not in {".env", ".env.example", ".env.local"}:
                continue
            if file_path.suffix.lower() in ignored_extensions:
                continue
                
            # Audit file permissions on Unix-like OS
            if platform.system() != "Windows":
                try:
                    mode = file_path.stat().st_mode
                    # Check if group/other has write or read permissions on sensitive config files
                    if file in {".env", ".env.local", ".env.example", "setup_nexus.sh"}:
                        # Check group read (0o040), group write (0o020), group execute (0o010),
                        # other read (0o004), other write (0o002), other execute (0o001)
                        if mode & 0o077:
                            octal_perm = oct(mode & 0o777)
                            unsafe_permissions.append(
                                f"- {file_path.relative_to(workspace_dir)} has permissions {octal_perm}. "
                                f"It should be hardened to 600 or 700 (e.g. `chmod 600 {file}`)."
                            )
                except Exception:
                    pass
            
            # File size check to prevent loading massive files
            try:
                if file_path.stat().st_size > 1024 * 1024:
                    continue
            except Exception:
                continue
                
            # Read and scan file contents
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line_num, line in enumerate(f, start=1):
                        for label, pattern in secret_patterns.items():
                            match = pattern.search(line)
                            if match:
                                # Redact the secret in the output log
                                matched_str = match.group(0)
                                if len(matched_str) > 8:
                                    redacted = matched_str[:4] + "..." + matched_str[-4:]
                                else:
                                    redacted = "..."
                                # Avoid marking matches in .env.example files or comments explaining secrets
                                if "example" in file.lower() and "value" in line.lower():
                                    continue
                                found_secrets.append(
                                    f"- {file_path.relative_to(workspace_dir)}:L{line_num} "
                                    f"[{label}] Found: {redacted}"
                                )
            except Exception:
                pass
                
    # Format Report
    status_report = []
    
    status_report.append("--- Secret Detections ---")
    if found_secrets:
        status_report.extend(found_secrets)
    else:
        status_report.append("No hardcoded secrets found.")
    status_report.append("")
    
    status_report.append("--- Permission Warnings ---")
    if unsafe_permissions:
        status_report.extend(unsafe_permissions)
    else:
        status_report.append("All checked configuration files have safe permissions.")
        
    return "\n".join(status_report)


@mcp.tool()
async def generate_project_boilerplate(
    project_name: str,
    project_type: str,
    target_dir: Optional[str] = None
) -> str:
    """Create a structured folder template with files, Dockerfile, tests, and standard config for FastAPI, CLI, Node, or Next.js."""
    base_path = Path(target_dir or ".").resolve() / project_name
    
    if base_path.exists():
        return f"Error: Directory already exists at {base_path}"
        
    project_type = project_type.lower().strip()
    valid_types = {"fastapi", "cli", "nextjs", "nodejs"}
    if project_type not in valid_types:
        return f"Error: Invalid project type '{project_type}'. Must be one of: {', '.join(valid_types)}"
        
    try:
        os.makedirs(base_path, exist_ok=True)
        
        # Helper to create folders and write files
        def create_dir(rel_path: str):
            os.makedirs(base_path / rel_path, exist_ok=True)
            
        def write_file(rel_path: str, content: str):
            filepath = base_path / rel_path
            os.makedirs(filepath.parent, exist_ok=True)
            filepath.write_text(content, encoding="utf-8")

        # Create basic files common to all
        write_file(".gitignore", "\n".join([
            "__pycache__/",
            "*.pyc",
            ".venv/",
            ".env",
            "node_modules/",
            ".next/",
            "out/",
            "*.log",
            ".DS_Store",
            "dist/",
            "build/"
        ]))
        
        write_file("README.md", f"""# {project_name}

This project was auto-generated by the Nexus Operations engine.
Template type: `{project_type}`

## Getting Started

1. Set up configurations in `.env`.
2. Follow instructions in the specific project folder layout.
""")

        write_file(".env", "PORT=8000\nENV=development\n")

        if project_type == "fastapi":
            create_dir("app")
            create_dir("app/routers")
            create_dir("tests")
            
            write_file("app/__init__.py", "")
            write_file("app/main.py", """from fastapi import FastAPI
from app.routers import health

app = FastAPI(
    title="Auto-Generated FastAPI Service",
    description="Created via Nexus Agentic RAG Platform Operations tool",
    version="0.1.0"
)

app.include_router(health.router)

@app.get("/")
def read_root():
    return {"message": "Hello from auto-generated FastAPI server!"}
""")
            write_file("app/routers/health.py", """from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["health"])

@router.get("")
def check_health():
    return {"status": "ok", "healthy": True}
""")
            write_file("tests/__init__.py", "")
            write_file("tests/test_main.py", """from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_read_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Hello from auto-generated FastAPI server!"}

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
""")
            write_file("Dockerfile", """FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
""")
            write_file("requirements.txt", "fastapi>=0.110.0\nuvicorn>=0.28.0\npytest>=8.0.0\nhttpx>=0.27.0\n")
            write_file(".github/workflows/python-ci.yml", """name: Python CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
      - name: Run tests
        run: |
          pytest
""")

        elif project_type == "cli":
            create_dir("tests")
            write_file("main.py", """#!/usr/bin/env python3
import sys
import argparse

def process_args():
    parser = argparse.ArgumentParser(description="Auto-generated Python CLI Tool")
    parser.add_argument("--name", type=str, default="User", help="Name to greet")
    return parser.parse_args()

def main():
    args = process_args()
    print(f"Hello, {args.name}! Welcome to your CLI application.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
""")
            write_file("tests/test_cli.py", """import subprocess
import sys

def test_cli_help():
    result = subprocess.run([sys.executable, "main.py", "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "show this help message and exit" in result.stdout

def test_cli_greeting():
    result = subprocess.run([sys.executable, "main.py", "--name", "Nexus"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "Hello, Nexus!" in result.stdout
""")
            write_file("Dockerfile", """FROM python:3.11-slim
WORKDIR /app
COPY . .
ENTRYPOINT ["python", "main.py"]
""")

        elif project_type == "nodejs":
            write_file("package.json", json.dumps({
                "name": project_name,
                "version": "1.0.0",
                "description": "Auto-generated Node.js Express server",
                "main": "index.js",
                "scripts": {
                    "start": "node index.js",
                    "test": "node --test"
                },
                "dependencies": {
                    "express": "^4.19.0",
                    "dotenv": "^16.4.5"
                }
            }, indent=2))
            
            write_file("index.js", """require('dotenv').config();
const express = require('express');
const app = express();
const port = process.env.PORT || 8000;

app.get('/', (req, res) => {
  res.json({ message: 'Hello from Node.js Express server!' });
});

app.get('/health', (req, res) => {
  res.json({ status: 'ok', healthy: true });
});

app.listen(port, () => {
  console.log(`Server running at http://localhost:${port}`);
});
""")
            write_file("index.test.js", """const test = require('node:test');
const assert = require('node:assert');

test('basic placeholder math test', (t) => {
  assert.strictEqual(1 + 1, 2);
});
""")
            write_file("Dockerfile", """FROM node:20-slim
WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production
COPY . .
EXPOSE 8000
CMD ["npm", "start"]
""")

        elif project_type == "nextjs":
            write_file("package.json", json.dumps({
                "name": project_name,
                "version": "0.1.0",
                "private": True,
                "scripts": {
                    "dev": "next dev",
                    "build": "next build",
                    "start": "next start",
                    "lint": "next lint"
                },
                "dependencies": {
                    "next": "14.1.4",
                    "react": "^18.2.0",
                    "react-dom": "^18.2.0"
                }
            }, indent=2))
            
            create_dir("app")
            write_file("app/layout.jsx", """export const metadata = {
  title: 'Next.js App',
  description: 'Generated by Nexus',
}

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
""")
            write_file("app/page.jsx", """export default function Home() {
  return (
    <main style={{ padding: '2rem', fontFamily: 'sans-serif' }}>
      <h1>Welcome to Next.js Boilerplate</h1>
      <p>Created by the local RAG Operations client.</p>
    </main>
  )
}
""")
            write_file("Dockerfile", """FROM node:20-slim AS base
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build
EXPOSE 3000
CMD ["npm", "start"]
""")

        return f"Success: Boilerplate repository '{project_name}' (type: {project_type}) successfully created at: {base_path.relative_to(base_path.parent.parent)}"
        
    except Exception as e:
        return f"Error creating boilerplate repository: {e}"


@mcp.tool()
async def execute_operational_command(
    command: str,
    timeout_seconds: int = 30,
    directory: Optional[str] = None
) -> str:
    """Run a CLI command on the host (e.g. status, docker-compose, pytest, service status, network checks)."""
    # Force execution relative to the current workspace root or specified sub-directory
    workspace_dir = Path(".").resolve()
    cwd_path = Path(directory).resolve() if directory else workspace_dir
    
    # Restrict execution directory to remain inside allowed user workspace or home
    if not cwd_path.is_relative_to(workspace_dir) and not cwd_path.is_relative_to(Path.home()):
        cwd_path = workspace_dir
        
    try:
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=str(cwd_path)
        )
        
        output = []
        output.append(f"Exit Code: {completed.returncode}")
        
        if completed.stdout.strip():
            output.append("\n=== STDOUT ===")
            output.append(completed.stdout)
            
        if completed.stderr.strip():
            output.append("\n=== STDERR ===")
            output.append(completed.stderr)
            
        return "\n".join(output) if len(output) > 1 else f"Exit Code: {completed.returncode} (No terminal output)"
        
    except subprocess.TimeoutExpired:
        return f"Error: Command execution timed out after {timeout_seconds}s."
    except Exception as e:
        return f"Error executing command: {e}"


@mcp.tool()
async def check_code_syntax(file_path: str) -> str:
    """Validate python file syntax using abstract syntax tree parsing to report line, column, and details of syntax errors."""
    path = Path(file_path).resolve()
    if not path.exists():
        return f"Error: File '{file_path}' does not exist."
        
    if not path.is_file():
        return f"Error: '{file_path}' is not a file."
        
    if path.suffix.lower() != ".py":
        return f"Error: Syntax checking is currently supported only for Python files (.py)."
        
    try:
        content = path.read_text(encoding="utf-8")
        ast.parse(content, filename=str(path))
        return f"Success: Python file '{path.name}' parsed cleanly with no syntax errors."
    except SyntaxError as e:
        err_details = {
            "file": e.filename,
            "line": e.lineno,
            "column": e.offset,
            "text": e.text.strip() if e.text else "",
            "message": e.msg
        }
        return (
            f"Syntax Error detected in '{path.name}':\n"
            f"Line: {err_details['line']}, Col: {err_details['column']}\n"
            f"Code snippet: `{err_details['text']}`\n"
            f"Details: {err_details['message']}"
        )
    except Exception as e:
        return f"Error validating python code: {e}"

@mcp.tool()
async def check_network_status() -> str:
    """Check the host network settings, active interfaces, external connectivity, and listening ports."""
    network_info = []
    os_name = platform.system()

    # 1. External connection check
    network_info.append("=== External Connectivity ===")
    try:
        ping_cmd = ["ping", "-c", "1", "-t", "2", "1.1.1.1"] if os_name != "Windows" else ["ping", "-n", "1", "-w", "2000", "1.1.1.1"]
        ping_res = subprocess.run(ping_cmd, capture_output=True, text=True)
        if ping_res.returncode == 0:
            network_info.append("Internet status: Connected (Successfully pinged 1.1.1.1)")
        else:
            network_info.append("Internet status: Disconnected (Ping to 1.1.1.1 failed)")
    except Exception as e:
        network_info.append(f"Internet status check failed: {e}")
    network_info.append("")

    # 2. Interfaces list
    network_info.append("=== Network Interfaces ===")
    try:
        if os_name == "Windows":
            res = subprocess.run(["ipconfig"], capture_output=True, text=True)
            lines = [line.strip() for line in res.stdout.splitlines() if "IPv" in line or "Subnet" in line or "Default Gateway" in line]
            network_info.extend(lines[:15])
        else:
            cmd = ["ifconfig"] if shutil.which("ifconfig") else ["ip", "addr"]
            res = subprocess.run(cmd, capture_output=True, text=True)
            lines = []
            for line in res.stdout.splitlines():
                if line and not line.startswith(" ") and not line.startswith("\t"):
                    lines.append(line.split(":")[0] if ":" in line else line.split()[0])
                if "inet " in line or "inet6 " in line:
                    lines.append(f"  {line.strip()}")
            network_info.extend(lines[:20])
    except Exception as e:
        network_info.append(f"Error querying interfaces: {e}")
    network_info.append("")

    # 3. Listening Ports
    network_info.append("=== Listening Ports (Local) ===")
    try:
        if os_name == "Darwin":
            res = subprocess.run(["lsof", "-i", "-P", "-n"], capture_output=True, text=True)
            lines = [line.strip() for line in res.stdout.splitlines() if "LISTEN" in line]
            if lines:
                network_info.extend(lines[:15])
            else:
                network_info.append("No active listening ports detected via lsof.")
        elif os_name == "Linux":
            cmd = ["ss", "-tulpn"] if shutil.which("ss") else ["netstat", "-tulpn"]
            res = subprocess.run(cmd, capture_output=True, text=True)
            lines = [line.strip() for line in res.stdout.splitlines() if "LISTEN" in line or "Local" in line]
            if lines:
                network_info.extend(lines[:15])
            else:
                network_info.append("No active listening ports detected via ss/netstat.")
        else:
            network_info.append("Listening ports enumeration not supported on this platform.")
    except Exception as e:
        network_info.append(f"Error querying listening ports: {e}")

    return "\n".join(network_info)


@mcp.tool()
async def get_system_logs(log_name: str = "syslog", lines: int = 50) -> str:
    """Retrieve system log buffers for diagnostics (options: syslog, system.log, messages, journald)."""
    os_name = platform.system()
    
    if lines <= 0 or lines > 500:
        lines = 50
        
    log_name = log_name.lower().strip()
    
    if "/" in log_name or "\\" in log_name:
        return "Error: Directory traversal not allowed in log name."
        
    try:
        if log_name == "journald" and os_name == "Linux" and shutil.which("journalctl"):
            res = subprocess.run(["journalctl", "-n", str(lines)], capture_output=True, text=True)
            return res.stdout or "No journald logs found."
            
        candidates = []
        if os_name == "Darwin":
            candidates = ["/var/log/system.log"]
        else:
            candidates = [f"/var/log/{log_name}", "/var/log/syslog", "/var/log/messages"]
            
        target_file = None
        for cand in candidates:
            p = Path(cand)
            if p.exists() and p.is_file():
                target_file = p
                break
                
        if not target_file:
            custom_path = Path("/var/log") / log_name
            if custom_path.exists() and custom_path.is_file():
                target_file = custom_path
                
        if not target_file:
            return f"Error: Could not locate a matching log file for '{log_name}' under /var/log."
            
        content = target_file.read_text(encoding="utf-8", errors="ignore")
        log_lines = content.splitlines()
        last_lines = log_lines[-lines:]
        return f"=== Last {len(last_lines)} lines of {target_file} ===\n" + "\n".join(last_lines)
        
    except PermissionError:
        return f"Error: Insufficient permission to read {log_name} log files on the host."
    except Exception as e:
        return f"Error reading system logs: {e}"


@mcp.tool()
async def get_running_services() -> str:
    """Retrieve lists of active/running background services on the host."""
    os_name = platform.system()
    services_info = []
    
    services_info.append("=== Running Services ===")
    try:
        if os_name == "Darwin":
            res = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
            lines = res.stdout.splitlines()
            running = []
            for line in lines:
                parts = line.split()
                if len(parts) >= 3 and parts[0] != "-" and parts[0].isdigit():
                    running.append(line.strip())
            if running:
                services_info.extend(running[:30])
                services_info.append(f"\n(Showing {len(running[:30])} of {len(running)} active launchd services)")
            else:
                services_info.append("No active launchd services with PIDs found.")
        elif os_name == "Linux" and shutil.which("systemctl"):
            res = subprocess.run(
                ["systemctl", "list-units", "--type=service", "--state=running", "--no-legend"],
                capture_output=True,
                text=True
            )
            lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            if lines:
                services_info.extend(lines[:30])
                services_info.append(f"\n(Showing {len(lines[:30])} of {len(lines)} active systemd services)")
            else:
                services_info.append("No active running systemd services found.")
        else:
            services_info.append("Services listing not supported or systemctl/launchctl not found on this platform.")
    except Exception as e:
        services_info.append(f"Error checking services: {e}")
        
    return "\n".join(services_info)


if __name__ == "__main__":
    mcp.run()
