from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ALLOWED_GIT_COMMANDS = {"status", "diff", "log", "show", "branch", "describe"}


def run_git_command(args: list[str]) -> str:
    """Executes a Git inspection command safely and returns its stdout/stderr."""
    git = shutil.which("git")
    if not git:
        return "git is not installed or available on this system."

    if not args:
        return "No arguments provided to git tool. Try status or log."

    cmd_type = args[0].strip().lower()
    if cmd_type not in ALLOWED_GIT_COMMANDS:
        return f"Unauthorized command '{cmd_type}'. Only read-only commands ({', '.join(ALLOWED_GIT_COMMANDS)}) are allowed."

    # Prevent arguments that could execute external scripts or break boundaries
    # E.g. --ext-diff or configuring hooks
    for arg in args:
        if arg.startswith("--ext-diff") or "exec" in arg:
            return f"Argument '{arg}' is disallowed for security reasons."

    # Construct the full execution command
    full_cmd = [git] + args
    
    # Locate project root (assuming we run within the workspace or adjacent to RAG_2)
    # Target directory is parent of the tool directory or workspace
    cwd = Path(__file__).resolve().parents[3] # RAG_2 root
    
    try:
        completed = subprocess.run(
            full_cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=15.0
        )
        if completed.returncode != 0:
            return f"Git error:\n{completed.stderr.strip()}"
        return completed.stdout.strip() if completed.stdout.strip() else "Git command completed with no output."
    except Exception as exc:
        return f"Error executing git command: {exc}"
