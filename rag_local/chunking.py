from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from .types import ChunkRecord, ChunkingOptions
from .utils import approximate_tokens, compact_whitespace, detect_language, read_text, stable_hash


@dataclass(slots=True)
class ParsedUnit:
    chunk_type: str
    title: str
    start_line: int | None
    end_line: int | None
    content: str
    scope: list[str]


def _python_units(source: str) -> list[ParsedUnit]:
    units: list[ParsedUnit] = []
    tree = ast.parse(source)
    lines = source.splitlines()

    class Visitor(ast.NodeVisitor):
        def __init__(self, parents: list[str]) -> None:
            self.parents = parents

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            content = "\n".join(lines[node.lineno - 1 : node.end_lineno or node.lineno])
            units.append(
                ParsedUnit(
                    chunk_type="class",
                    title=node.name,
                    start_line=node.lineno,
                    end_line=node.end_lineno,
                    content=content,
                    scope=self.parents[:],
                )
            )
            child = Visitor(self.parents + [node.name])
            for stmt in node.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    child.visit(stmt)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            content = "\n".join(lines[node.lineno - 1 : node.end_lineno or node.lineno])
            units.append(
                ParsedUnit(
                    chunk_type="function",
                    title=node.name,
                    start_line=node.lineno,
                    end_line=node.end_lineno,
                    content=content,
                    scope=self.parents[:],
                )
            )

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.visit_FunctionDef(node)

    Visitor([]).visit(tree)
    if not units and source.strip():
        units.append(
            ParsedUnit(
                chunk_type="file",
                title="module",
                start_line=1,
                end_line=len(lines),
                content=source,
                scope=[],
            )
        )
    return units


def _fallback_units(source: str) -> list[ParsedUnit]:
    lines = source.splitlines()
    if not lines:
        return []
    units: list[ParsedUnit] = []
    buffer: list[str] = []
    start = 1
    for idx, line in enumerate(lines, start=1):
        if line.startswith(("def ", "class ", "export ", "function ")):
            if buffer:
                units.append(
                    ParsedUnit(
                        chunk_type="text",
                        title=f"block_{start}",
                        start_line=start,
                        end_line=idx - 1,
                        content="\n".join(buffer),
                        scope=[],
                    )
                )
                buffer = []
            start = idx
        buffer.append(line)
    if buffer:
        units.append(
            ParsedUnit(
                chunk_type="text",
                title=f"block_{start}",
                start_line=start,
                end_line=len(lines),
                content="\n".join(buffer),
                scope=[],
            )
        )
    return units


def split_source(path: Path, options: ChunkingOptions) -> list[ChunkRecord]:
    language = detect_language(path)
    source = read_text(path)
    if not source.strip():
        return []

    units = _tree_sitter_units(source, language) or (_python_units(source) if language == "python" else _fallback_units(source))
    if not units:
        units = [ParsedUnit("file", path.name, 1, len(source.splitlines()), source, [])]

    records: list[ChunkRecord] = []
    for unit in units:
        content = compact_whitespace(unit.content)
        if approximate_tokens(content) > options.max_tokens:
            parts = _split_large_text(content, options.max_chars_fallback)
        else:
            parts = [content]
        for index, part in enumerate(parts, start=1):
            chunk_id = stable_hash(f"{path}:{unit.title}:{unit.start_line}:{unit.end_line}:{index}")
            records.append(
                ChunkRecord(
                    chunk_id=chunk_id,
                    source_path=str(path),
                    language=language,
                    chunk_type=unit.chunk_type,
                    title=f"{unit.title}" if len(parts) == 1 else f"{unit.title}:{index}",
                    content=part,
                    summary=_make_summary(path, unit.title, part),
                    scope=unit.scope,
                    start_line=unit.start_line,
                    end_line=unit.end_line,
                    metadata={
                        "path": str(path),
                        "unit_title": unit.title,
                        "chunk_index": index,
                        "content_hash": stable_hash(part)[:16],
                    },
                )
            )
    return records


def _split_large_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    lines = text.splitlines()
    chunks: list[str] = []
    current: list[str] = []
    total = 0
    for line in lines:
        line_len = len(line) + 1
        if current and total + line_len > max_chars:
            chunks.append("\n".join(current))
            current = []
            total = 0
        current.append(line)
        total += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


def _make_summary(path: Path, title: str, content: str) -> str:
    first = next((line.strip() for line in content.splitlines() if line.strip()), "")
    prefix = f"{path.name}:{title}"
    return compact_whitespace(f"{prefix} - {first[:220]}")


def _tree_sitter_units(source: str, language: str) -> list[ParsedUnit]:
    try:
        from tree_sitter_languages import get_parser
    except Exception:
        return []

    parser = None
    try:
        parser = get_parser(language)
    except Exception:
        return []
    if parser is None:
        return []

    try:
        tree = parser.parse(source.encode("utf-8", errors="ignore"))
    except Exception:
        return []

    root = tree.root_node
    units: list[ParsedUnit] = []

    def walk(node, parents: list[str]) -> None:
        node_type = getattr(node, "type", "")
        if node_type in {"function_definition", "method_definition", "class_definition", "function_declaration", "method_declaration", "class_declaration", "export_statement"}:
            start = node.start_point[0] + 1
            end = node.end_point[0] + 1
            title = _extract_tree_sitter_title(source, node, language) or node_type
            snippet = _snippet_lines(source, start, end)
            units.append(
                ParsedUnit(
                    chunk_type=node_type,
                    title=title,
                    start_line=start,
                    end_line=end,
                    content=snippet,
                    scope=parents[:],
                )
            )
            parents = parents + [title]
        for child in getattr(node, "children", []) or []:
            walk(child, parents)

    walk(root, [])
    return units


def _extract_tree_sitter_title(source: str, node, language: str) -> str:
    try:
        text = source[node.start_byte : node.end_byte]
    except Exception:
        return ""
    for prefix in ("def ", "class ", "function ", "export ", "fn "):
        if prefix in text:
            after = text.split(prefix, 1)[1]
            token = after.split("(", 1)[0].split(":", 1)[0].split("{", 1)[0].strip().split()[0:1]
            if token:
                return token[0]
    return ""


def _snippet_lines(source: str, start: int, end: int) -> str:
    lines = source.splitlines()
    return "\n".join(lines[start - 1 : end])
