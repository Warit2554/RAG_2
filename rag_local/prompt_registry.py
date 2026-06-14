"""Nexus Prompt Registry.

Loads, versions, and serves prompts from the ``prompts/`` directory.
Prompts are stored as plain text files with optional YAML front-matter for
metadata.

File format
-----------
Each prompt is a ``.txt`` or ``.md`` file.  Optional YAML front-matter
(between ``---`` delimiters) can specify:

  ---
  name: My Prompt
  version: 2
  description: Used for XYZ
  tags: [planner, system]
  ---

  Prompt body goes here…

Usage
-----
  from rag_local.prompt_registry import registry

  text = registry.get("orchestrator_system")        # latest version
  text = registry.get("orchestrator_system", ver=1)  # specific version
  registry.list_all()                                # list metadata

CLI command: /prompts
---------------------
  /prompts               — list all prompts
  /prompts <name>        — show prompt text
  /prompts <name> edit   — open in $EDITOR
"""
from __future__ import annotations

import os
import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class PromptMeta:
    name: str
    version: int = 1
    description: str = ""
    tags: list[str] = field(default_factory=list)
    path: Path = field(default_factory=Path)
    body: str = ""


class PromptRegistry:
    """Loads prompts from the filesystem prompt directory."""

    def __init__(self, prompts_dir: Path | None = None) -> None:
        if prompts_dir is None:
            from .config import SETTINGS
            prompts_dir = SETTINGS.prompts_dir
            if not prompts_dir.is_absolute():
                from .config import WORKSPACE_DIR
                prompts_dir = WORKSPACE_DIR / prompts_dir
        self._dir = prompts_dir
        self._cache: dict[str, list[PromptMeta]] = {}  # name → versions (sorted)

    # ── Loading ───────────────────────────────────────────────────────────────

    def _load_file(self, path: Path) -> PromptMeta:
        raw = path.read_text(encoding="utf-8")
        meta = PromptMeta(name=path.stem, path=path)
        m = _FRONT_MATTER_RE.match(raw)
        if m:
            try:
                import yaml  # type: ignore[import-untyped]
                fm = yaml.safe_load(m.group(1)) or {}
            except Exception:
                fm = {}
            meta.name = str(fm.get("name", path.stem))
            meta.version = int(fm.get("version", 1))
            meta.description = str(fm.get("description", ""))
            meta.tags = list(fm.get("tags") or [])
            meta.body = raw[m.end():].strip()
        else:
            meta.body = raw.strip()
        return meta

    def load(self) -> None:
        """(Re)load all prompts from disk."""
        self._cache.clear()
        if not self._dir.is_dir():
            logger.debug("[PromptRegistry] Directory '%s' not found.", self._dir)
            return
        for path in sorted(self._dir.rglob("*.txt")) + sorted(self._dir.rglob("*.md")):
            try:
                prompt = self._load_file(path)
                self._cache.setdefault(prompt.name, []).append(prompt)
            except Exception as exc:
                logger.warning("[PromptRegistry] Could not load '%s': %s", path, exc)

        # Sort versions descending within each name
        for name in self._cache:
            self._cache[name].sort(key=lambda p: p.version, reverse=True)

        logger.info("[PromptRegistry] Loaded %d prompts from '%s'.", len(self._cache), self._dir)

    def _ensure_loaded(self) -> None:
        if not self._cache:
            self.load()

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, name: str, ver: int | None = None) -> str:
        """Return the body of a prompt by name (and optionally version).

        Falls back to the built-in default if not found on disk.
        """
        self._ensure_loaded()
        versions = self._cache.get(name, [])
        if not versions:
            return _BUILTIN_PROMPTS.get(name, "")
        if ver is None:
            return versions[0].body  # latest
        for v in versions:
            if v.version == ver:
                return v.body
        return versions[0].body  # fallback to latest

    def list_all(self) -> list[PromptMeta]:
        """Return the latest version of every known prompt."""
        self._ensure_loaded()
        result = []
        for versions in self._cache.values():
            if versions:
                result.append(versions[0])
        # Also include built-ins not on disk
        for name, body in _BUILTIN_PROMPTS.items():
            if name not in self._cache:
                result.append(PromptMeta(name=name, description="(built-in)", body=body))
        return sorted(result, key=lambda p: p.name)

    def get_meta(self, name: str) -> PromptMeta | None:
        self._ensure_loaded()
        versions = self._cache.get(name, [])
        return versions[0] if versions else None

    def reload(self) -> None:
        self._cache.clear()
        self.load()

    # ── CLI helpers ───────────────────────────────────────────────────────────

    def format_listing(self) -> str:
        """Human-readable prompt list for the CLI."""
        prompts = self.list_all()
        if not prompts:
            return "  No prompts found."
        lines = []
        for p in prompts:
            source = "disk" if p.path and p.path.name else "built-in"
            tags = f" [{', '.join(p.tags)}]" if p.tags else ""
            lines.append(f"  {p.name:<30} v{p.version}  ({source}){tags}")
            if p.description:
                lines.append(f"    {p.description}")
        return "\n".join(lines)

    def open_in_editor(self, name: str) -> None:
        """Open a prompt in $EDITOR.  Creates it in prompts_dir if new."""
        self._ensure_loaded()
        versions = self._cache.get(name, [])
        if versions and versions[0].path.exists():
            path = versions[0].path
        else:
            self._dir.mkdir(parents=True, exist_ok=True)
            path = self._dir / f"{name}.txt"
            if not path.exists():
                body = _BUILTIN_PROMPTS.get(name, f"# {name} prompt\n")
                path.write_text(body, encoding="utf-8")
        editor = os.environ.get("EDITOR", "nano")
        os.system(f'{editor} "{path}"')
        self.reload()


# ---------------------------------------------------------------------------
# Built-in prompt defaults (shipped with the code)
# These are used when no file exists in the prompts/ directory.
# ---------------------------------------------------------------------------

_BUILTIN_PROMPTS: dict[str, str] = {
    "orchestrator_system": "",   # filled from orchestrator.py at runtime
    "synthesizer_system": "",    # filled from orchestrator.py at runtime
    "router_system": (
        "You are a query router for a local RAG/agent system. "
        "Classify the user's query into one of: general, rag, code_analysis, web_search. "
        "Return JSON: {\"route\": \"...\", \"confidence\": 0.9, \"reason\": \"...\"}"
    ),
    "verification_system": (
        "You are a verification agent. Given a task result, determine if it is correct and complete. "
        "Return JSON: {\"passed\": true, \"reason\": \"...\", \"confidence\": 0.9}"
    ),
    "compression_system": (
        "Summarise the older part of a conversation into a compact paragraph preserving "
        "key facts, task outcomes, and unresolved follow-ups. "
        "Output a single paragraph of at most 300 words."
    ),
}


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

registry = PromptRegistry()
