# Workspace Exploration Analysis Report — RAG_2

This report details the architectural structure, current repository state, environment configurations, and recommended development steps for the **Local Advanced RAG System (Nexus)**.

---

## 1. Repository State Overview
The workspace contains a fully functional, locally run advanced RAG system that coordinates a stateless router, a stateful planner/orchestrator, and parallel worker agents utilizing the **Model Context Protocol (MCP)**.

### Directory Structure & Key Files
- `rag_local/`: Core source package containing:
  - `cli.py`: The terminal-based REPL shell (`nexus` command) with interactive menus, theme selection, slash commands, and history management.
  - `config.py`: Configuration loader mapping env variables from `.env` to a `Settings` dataclass.
  - `embed.py`: Ollama Client wrapper managing embeddings and chat completion endpoints (`/api/chat` and `/api/embeddings`).
  - `graph.py`: LangGraph state machine compiling the router, planning, retrieval, execution, and synthesis pipeline.
  - `ingest.py`: Code directory discovery and batch ingestion engine.
  - `chunking.py`: AST-aware tree-sitter chunker with fallback Python AST (`ast.NodeVisitor`) and textual fallback chunkers.
  - `router.py`: LLM-as-a-router implementation with a local keyword/regex fallback router.
  - `orchestrator.py`: Multi-step JSON execution planner that coordinates task execution.
  - `types.py` & `utils.py`: Dataclasses, TypeDicts, and common utility functions.
  - `tools/`: Specialized workspace worker submodules and custom MCP servers:
    - `retrieval/tool.py`: Dense Qdrant vector retrieval combined with a sparse lexical `rank-bm25` sidecar database.
    - `code_execution/tool.py`: Sandboxed Python execution engine inside a secure, unprivileged Docker container.
    - `git_inspector/tool.py`: Safe, read-only Git commands checker.
    - `image_downloader/tool.py`: Web scraper + API collector for image search and download.
    - `web_scraper/tool.py`: HTMLParser-based text scraping engine.
    - `web_search/tool.py`: Hybrid search using a local SearXNG instance or HTML DuckDuckGo scraper.
    - `database_mcp.py`: FastMCP server exposing database query/modification functions.
    - `operations_mcp.py`: FastMCP server exposing host status, security checks, and code syntax validation tools.
    - `search_fetch_mcp.py` & `time_mcp.py`: FastMCP servers exposing search/scrape and time tools.
- `docker_scripts/`: Docker orchestration assets including:
  - `Dockerfile.mcp`: Environment image description for Python MCP execution.
  - `extract_image.py` / `extract_image.md`: Media extraction scripts inside the sandbox.
- `scratchpad/`: Scripted test suites for system validation (`test_ollama.py`, `test_mcp_client.py`, `test_full_flow.py`, `test_graph.py`).
- `setup_nexus.sh`: Headless initialization script that sets up `.venv`, copies `.env`, verifies Docker, starts Qdrant, builds custom images, and pre-pulls MCP tools.
- `mcp_config.json`: Host definition config map detailing how the MCP Client connects to each server (e.g. via stdio parameters running inside Docker).
- `docker-compose.yml`: Local Qdrant container orchestrator exposed on port `6333`.

---

## 2. Architectural Layout
The system follows a strict hierarchical Router-Orchestrator architecture integrated via **LangGraph**:

```
                              [ User Input (CLI REPL) ]
                                         |
                                         v
                            +--------------------------+
                            |     Router Node          |  <-- Checks for Clarification (Ollama)
                            +--------------------------+
                                  /       |       \
                         (general)  (clarify)  (rag/code/web)
                            /             |             \
                           v              |              v
                  [ General Node ]        |       +-------------------------+
                           \              |       |     Planning Node       |  <-- Plan Generation (Ollama)
                            \             |       +-------------------------+
                             \            |                  |
                              \           |                  v
                               \          |       +-------------------------+
                                \         |       |     Retrieval Node      |  <-- Hybrid Search (Qdrant + BM25)
                                 \        |       +-------------------------+
                                  \       |                  |
                                   \      |                  v
                                    \     |       +-------------------------+
                                     \    |       |      Workers Node       |  <-- Parallel Execution (asyncio)
                                      \   |       +-------------------------+
                                       \  |                  |
                                        v v                  v
                                  +-----------------------------------------+
                                  |            Synthesizer Node             |  <-- Synthesis & Streaming Answer
                                  +-----------------------------------------+
```

### Core Architecture Highlights
1. **Tiered Intent Routing**: First, the user query is parsed by the Stateless Router node. If clarification is needed (e.g., missing download path or file name), it loops to the Synthesizer with a clarification prompt. Otherwise, it routes to `general` (bypassing the plan), or to `plan` (`rag`, `code_analysis`, or `web_search`).
2. **Stateful Planning**: The Orchestrator compiles a JSON plan containing multiple structured sub-tasks.
3. **Parallel Asyncio Execution**: In the execution phase, `run_parallel_tasks` leverages `asyncio.gather()` to fan-out and execute the worker tasks in parallel.
4. **Model Context Protocol (MCP)**: The orchestrator automatically wraps and routes execution tasks to standard MCP servers (e.g. `duckduckgo`, `fetch`, `git`, `filesystem`, `operations`, `database`) running in isolated environments via stdio streams.
5. **Ephemerality & Security**: Python code generated by the agent is executed inside a dockerized `python:3.11-slim` container with disabled networking (`--network none`), memory limits (`512m`), read-only root directories, and no-new-privileges flags.

---

## 3. Environment Analysis
The local environment is configured and validated as follows:
- **Virtual Environment (`.venv`)**: Initialized with Python `3.13.5`. All dependencies (`langgraph`, `mcp`, `qdrant-client`, `rank-bm25`, `tree-sitter`, `chainlit`, `textual`, `httpx`, `pydantic`) are present and functional.
- **Configuration (`.env`)**: Properly maps host parameters:
  - `OLLAMA_HOST` connects to remote host `http://100.113.213.113:11434`
  - Chat/Router/Orchestrator models are set to `gemma4:e4b`
  - Embedding model is set to `embeddinggemma:latest`
  - Qdrant is mapped to `http://localhost:6333`
- **Docker Integration**: Docker is active and running.
  - Qdrant container (`rag_2-qdrant-1`) is up and exposed on port `6333`.
  - Multiple helper MCP service containers (`postgres-mcp`, `context7`, `mcp/docker`) are running in the background.

---

## 4. System Gaps & Recommended Development Steps
While the codebase is robust and fully configured, there are critical gaps and mismatches between the documentation/scaffolding and the actual implementation:

### ⚠️ Critical Gaps
1. **Missing GUI (Chainlit) and TUI (Textual)**:
   - The `README.md` and `Research_1.md` refer to starting Chainlit via `chainlit run rag_local/ui/chainlit.py` and Textual via `nexus`.
   - The codebase has **no** `ui_chainlit.py` or `ui_textual.py` source files.
   - Traces of these files exist only as stale cache outputs in `rag_local/__pycache__/` (`ui_chainlit.cpython-313.pyc`, `ui_textual.cpython-313.pyc`, `sandbox.cpython-313.pyc`, `store.cpython-313.pyc`, etc.), suggesting a previous refactoring deleted the source code.
2. **Directory & Parameter Mismatches**:
   - `README.md` refers to a `generate_script` directory, but it is actually named `docker_scripts` in the repository.
   - In `test_mcp_client.py` (line 25), the connection tests call `get_current_time` with no arguments, but the Time MCP server requires a `timezone` argument, resulting in validation failures.
   - DuckDuckGo tool tests (line 39) query `duckduckgo_search`, but the actual tool name is `search` in the DuckDuckGo MCP schema.

### 🛠️ Recommended Development Steps
1. **Implement the Chainlit Chat Interface**:
   - Create `rag_local/ui/chainlit.py` to expose the LangGraph `APP` streaming tokens to the UI in real-time. Use `cl.on_chat_start` and `cl.on_message` decorators.
2. **Implement the Textual UI**:
   - Re-introduce `rag_local/ui_textual.py` or `rag_local/ui/textual.py` to allow headless or terminal-based visual dashboard widgets with CSS layouts (`DEFAULT_CSS` in python).
3. **Clean Up and Align Directory/Documentation Structure**:
   - Update `README.md` to reference `docker_scripts/` instead of `generate_script/`.
   - Purge `rag_local/__pycache__` to eliminate confusing compiled residues of deleted files.
4. **Fix Test Scripts**:
   - Fix `scratchpad/test_mcp_client.py` argument calling errors for `time` and `duckduckgo` tools.
