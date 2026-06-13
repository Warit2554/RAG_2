from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from rag_local.config import SETTINGS


@dataclass(slots=True)
class SandboxResult:
    success: bool
    stdout: str
    stderr: str
    exit_code: int


def run_python_in_docker(code: str, *, timeout_seconds: int = 20) -> SandboxResult:
    docker = shutil.which("docker")
    if not docker:
        return SandboxResult(
            success=False,
            stdout="",
            stderr="docker is not available on this machine",
            exit_code=127,
        )

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        script = workdir / "main.py"
        script.write_text(code, encoding="utf-8")
        
        repo_dir = SETTINGS.rag_data_dir.resolve()
        cmd = [
            docker,
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--pids-limit",
            "64",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,exec,nosuid,size=64m",
            "--volume",
            f"{tmp}:/workspace:rw",
            "--volume",
            f"{repo_dir}:/repo:ro",
            "-w",
            "/workspace",
            "python:3.11-slim",
            "python",
            "/workspace/main.py",
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds)
        return SandboxResult(
            success=completed.returncode == 0,
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
        )
