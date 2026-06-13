# Local RAG System

This repo implements a fully local router-orchestrator RAG system with:

- LangGraph for orchestration
- Ollama for chat, routing, orchestration, and embeddings
- Qdrant for vector storage
- Tree-sitter-style AST-aware chunking with a Python AST fast path and text fallback
- Chainlit for chat UI
- Optional local web search through SearxNG
- Optional Python code execution in an isolated Docker container

## What is implemented

- Router node that classifies the query into `general`, `rag`, `code_analysis`, or `web_search`
- Orchestrator node that builds a task plan
- Parallel worker execution for retrieval, code, and web tasks
- Synthesizer node that produces the final answer
- Ingestion pipeline that walks a local directory, chunks content, embeds it through Ollama, and stores it in Qdrant
- Hybrid retrieval that fuses Qdrant dense search with a local BM25 sidecar

## Requirements

- Python 3.11+
- Ollama running locally
- Qdrant running locally
- Optional: Docker for sandboxed code execution
- Optional: SearxNG for local web search

## Setup

1. Copy `.env.example` to `.env` and adjust values if needed.
2. Install dependencies:

```bash
pip install -e .
```

3. Pull models in Ollama:

```bash
ollama pull llama3.1:8b
ollama pull nomic-embed-text
```

4. Start Qdrant:

```bash
docker compose up -d
```

5. Start optional SearxNG if you want web search.

## Installation & Setup

Set up the environment, install dependencies, and build the command-line tool on Linux/macOS:

```bash
./setup_nexus.sh
```

Follow the output instructions of the script to export the virtual environment's bin folder to your shell's `PATH`.

## Usage

Run the interactive CLI REPL chat loop by typing:

```bash
nexus
```
*(Or run `.venv/bin/nexus` directly)*

Inside the REPL, you can type your questions to chat with your files. It supports the following slash commands:
- `/ingest` - Re-indexes the current directory into Qdrant
- `/clear` - Clears the current conversation history
- `/exit` or `/quit` - Exits the interactive chat session

Run the Chainlit UI:

```bash
chainlit run rag_local/ui/chainlit.py
```

## Notes

- The code execution tool is intentionally isolated behind Docker.
- If Docker is unavailable, the sandbox tool returns a clear error instead of falling back to host execution.
- The repository currently contains only the implementation plan documents, so the first ingestion pass will index those files unless you point it at a different directory.
- The graph is built with LangGraph and the local assistant defaults to safe, broadly available Ollama model names. You can point the environment variables at the exact router/orchestrator/embedding models you prefer.

RAG_2/
├── generate_script/        # Contains scripts created by or run via Docker
│   ├── extract_image.py    # Python script executing in Docker to download images
│   └── extract_image.md    # Auto-generated documentation for the docker script
│
├── indexes/                # Stores local indexing metadata (e.g. local BM25 sidecar database)
│
├── qdrant_local/           # Persistent volume storage for the local Qdrant Vector database
│
├── scratch/                # Experimental scripts and temporary scratchpad workspace files
│
└── rag_local/              # Core Application Source Directory
    ├── cli.py              # The Nexus interactive REPL terminal loop (started by the 'nexus' command)
    ├── config.py           # Config manager (Ollama hosts, models, database directories, etc.)
    ├── embed.py            # Interfaces with local Ollama service for chat generation and embeddings
    ├── graph.py            # LangGraph state machine orchestrating the router, planning, execution, and synthesis steps
    ├── ingest.py           # Ingestion pipeline parsing local documents with AST chunkers and loading them to Qdrant
    ├── memory.py           # Summarizes/compresses chat history to fit inside the local model's context window
    ├── router.py           # Routes incoming queries to 'general', 'rag', 'code_analysis', or 'web_search'
    ├── orchestrator.py     # LLM planner that creates tasks and coordinates execution
    ├── types.py            # Type declarations and dataclasses for state variables
    │
    └── tools/              # Specialized tools executed by the RAG orchestrator tasks:
        ├── code_execution/ # Executes python scripts safely inside an isolated Docker sandbox container
        ├── git_inspector/  # Runs read-only Git commands (diff, log, status) on the local repo
        ├── image_downloader/# Downloads images from URL sources using search-and-fallback logic
        ├── retrieval/      # Queries Qdrant dense vector search combined with a BM25 sparse index
        ├── web_scraper/    # Scrapes and parses webpage content from specific target URLs
        └── web_search/     # Searches the internet using SearXNG or DuckDuckGo fallback
