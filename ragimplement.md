Local Advanced RAG Development Plan with Ollama

(Local Router-Orchestrator & Parallel Agents Architecture)

Building an advanced RAG (Retrieval-Augmented Generation) system locally using a Router-Orchestrator architecture with parallel worker agents requires highly efficient management of hardware resources (VRAM/RAM). This document outlines the optimal tech stack and a step-by-step implementation guide for a 100% local setup.

1. Tech Stack Selection

1.1 Tech Stack (Optimal for Local Execution)

LLM Engine: Ollama (Excellent for model management and rapid VRAM loading/unloading).

Orchestration Framework: LangGraph (Currently the best framework for building state machines, controlling parallel agents, and handling complex routing).

Vector Database: Qdrant (Running via Docker) - Supports extremely fast Hybrid Search (Dense Vectors + Sparse BM25) with a low resource footprint.

Recommended Models (via Ollama):

Embedding Model: qwen3-embedding:0.6b.

Router Model:lfm2.5:8b (Requires high speed and low latency for quick decision-making).

Orchestrator / Coder Model: qwen3.6:27b or lfm2.5:8b. (Requires advanced reasoning and planning capabilities).

UI Frameworks:

GUI: Chainlit (Designed specifically to visualize Agent thought processes and steps; better suited for this than Streamlit).

CLI: Textual (For displaying terminal dashboards and system logs).

2. Local Architecture Diagram

                                [ User (Chainlit GUI / Textual CLI) ]
                                                |
                                                v
+-----------------------------------------------------------------------------------------------+
|                                      ROUTER NODE (LangGraph)                                  |
| Model: lfm2.5:8b (Fast/Light) | Role: Intent classification (General, RAG, Code-Analysis).    |
+-----------------------------------------------------------------------------------------------+
          | (General Query)                                    | (Needs Search or Code Analysis)
          v                                                    v
 [ Respond instantly ]                 +---------------------------------------------------+
 [ using 8B model    ]                 |              ORCHESTRATOR NODE (LangGraph)        |
                                       | Model: qwen3.6:27b or lfm2.5:8b.                  |
                                       | Role: Generate Execution Plan & Share State       |
                                       +---------------------------------------------------+
                                                               |
                                   [ Execute via `builder.add_edge` (Parallel in LangGraph) ]
                                                               |
                +----------------------------------------------+----------------------------------------------+
                |                                              |                                              |
                v                                              v                                              v
+--------------------------------+             +--------------------------------+             +--------------------------------+
|  Worker 1: Document RAG Agent  |             | Worker 2: Code Analysis Agent  |             | Worker 3: Local Search Agent   |
|  Tool: Qdrant Hybrid Search    |             | Tool: AST Tree-sitter Parser   |             | Tool: SearxNG (Local Web)      |
+--------------------------------+             +--------------------------------+             +--------------------------------+
                |                                              |                                              |
                +----------------------------------------------+----------------------------------------------+
                                                               |
                                                               v
                                       +---------------------------------------------------+
                                       |               SYNTHESIZER NODE                    |
                                       | Model: qwen3.6:27b or lfm2.5:8b (Compile data & generate answer) |
                                       +---------------------------------------------------+
                                                               |
                                                               v
                                                    [ Return response to User ]


3. Step-by-Step Implementation Guide

Phase 1: Local Setup & Data Ingestion

Initialize Core Services: Run Ollama and pull the required models (ollama run nomic-embed-text, ollama run qwen3.6:27b, ollama run lfm2.5:8b). Spin up a Qdrant instance using Docker.

Code Ingestion Pipeline:

Utilize tree-sitter to parse source code directories and extract logical blocks (functions/classes) rather than arbitrary text chunks.

Use OllamaEmbeddings(model="qwen3-embedding:0.6b
") to vectorize the code and documentation.

Store the embeddings in Qdrant with Hybrid Indexing enabled (BM25 + Vector).

Phase 2: Build Local Tools (Python)

Qdrant Retriever Tool: Create a retrieval function to query Qdrant and wrap it with LangChain's @tool decorator.

Code Execution Tool: Develop a secure function to execute Python code inside an isolated Docker Container (acting as a local sandbox, replacing Bubblewrap).

Local Web Search: Deploy SearxNG via Docker to allow the agent to perform web searches without relying on external, paid API keys.

Phase 3: Build the LangGraph Workflow (The Core)

Define the State: Create a TypedDict class to hold the global state (e.g., messages, plan, documents_found, code_analysis_result).

Create Nodes (Functions):

router_node: Uses the small model to analyze intent and return the next routing edge.

orchestrator_node: Uses the large model to read the state and generate a list of tasks.

worker_nodes: Specialized agents equipped with specific tools.

synthesizer_node: Aggregates all data from the state to formulate the final response.

Connect Graph for Parallel Processing:

In LangGraph, parallel execution is achieved by directing conditional edges from the orchestrator_node to multiple worker_nodes simultaneously.

Implement a Fan-out/Fan-in pattern: ensure all workers complete their tasks before mapping their outputs back to the synthesizer_node.

Compile Graph: Execute app = graph.compile().

Phase 4: GUI and CLI Development

GUI with Chainlit:

Develop an app.py script using Chainlit, integrating the compiled LangGraph application.

Chainlit will provide a ChatGPT-like interface and display real-time execution steps (e.g., showing when the system is routing, orchestrating, or running 3 parallel agents).

CLI with Textual:

Create an admin dashboard to monitor system resources (like Ollama VRAM usage in real-time) and stream LangGraph execution logs directly in the terminal.

4. Pro Tips for Local Execution

Ollama Keep-Alive: By default, Ollama unloads models from VRAM after a short period of inactivity. Switching rapidly between the Router (3B) and Orchestrator (32B) can cause massive latency due to reloading. If your VRAM permits, set keep_alive=-1 (or a high value like keep_alive="1h") in your API requests to keep both models resident in memory.

End-to-End Streaming: To prevent the UI from feeling unresponsive, enable streaming in LangGraph. This allows tokens to be sent to Chainlit the moment the Synthesizer node begins generating the final answer.

Context Window Management: Local models can crash or hallucinate if the context gets too large. Implement a mechanism where the Router node summarizes the chat history before passing it to the Orchestrator, ensuring the context remains concise and relevant.