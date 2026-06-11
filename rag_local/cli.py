from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .config import SETTINGS
from .graph import ask
from .ingest import ingest_directory
from .ui_textual import main as dashboard_main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rag-local")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="Ingest a directory into Qdrant")
    ingest.add_argument("path", nargs="?", default=".", help="Directory to ingest")

    query = sub.add_parser("query", help="Ask a question from the terminal")
    query.add_argument("question", help="Question to ask")

    sub.add_parser("dashboard", help="Open the Textual dashboard")

    return parser


async def _run_ingest(path: str) -> None:
    root = Path(path).expanduser().resolve()
    result = await ingest_directory(root)
    print(
        f"files_seen={result.files_seen} chunks_created={result.chunks_created} embedded={result.embedded} collection={SETTINGS.qdrant_collection}"
    )


async def _run_query(question: str) -> None:
    result = await ask(question)
    print(result.get("final_answer", ""))


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "ingest":
        asyncio.run(_run_ingest(args.path))
    elif args.command == "query":
        asyncio.run(_run_query(args.question))
    elif args.command == "dashboard":
        dashboard_main()


if __name__ == "__main__":
    main()
