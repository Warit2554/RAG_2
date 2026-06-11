# Local RAG System

This repo implements a fully local router-orchestrator RAG system with:

- LangGraph for orchestration
- Ollama for chat, routing, orchestration, and embeddings
- Qdrant for vector storage
- Tree-sitter-style AST-aware chunking with a Python AST fast path and text fallback
- Chainlit for chat UI
- Textual for a terminal dashboard
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

## Usage

Ingest a directory:

```bash
rag-local ingest .
```

Ask from the terminal:

```bash
rag-local query "Summarize the main architecture in this repository"
```

Run the Chainlit UI:

```bash
chainlit run rag_local/ui_chainlit.py
```

Run the Textual dashboard:

```bash
rag-local dashboard
```

## Notes

- The code execution tool is intentionally isolated behind Docker.
- If Docker is unavailable, the sandbox tool returns a clear error instead of falling back to host execution.
- The repository currently contains only the implementation plan documents, so the first ingestion pass will index those files unless you point it at a different directory.
- The graph is built with LangGraph and the local assistant defaults to safe, broadly available Ollama model names. You can point the environment variables at the exact router/orchestrator/embedding models you prefer.
