# Project: Nexus V2 - Local Multi-Agent Workspace Builder and Ops Engineer

## Architecture
Nexus V2 enhances the base Local RAG system to include:
1. **MCP Registry & Semantic Router**: A JSON registry (`mcp_capabilities.json`) storing all tool metadata, plus a Tool Router Agent that matches queries using embeddings/cosine similarity to expose a small subset (<=10) of relevant tools.
2. **Multi-Level Planning Graph**: A LangGraph state machine: User -> Intent Router -> Planner -> Parallel Worker Agents (specialized roles: Architect, Backend, Frontend, DevOps, QA, Documentation) -> Synthesizer.
3. **Repository Memory & Agent Learning Loop**: Automated generation of `Knowledge.md`, `Architecture.md`, `Components.md`, `Decisions.md`, `Tasks.md`, and failure-driven `Lessons.md` generation.
4. **Context Compression Engine**: Log, JSON, and tree output compression by 90% (under 4000 characters).
5. **Continuous Test Loop & Self-Healing Ops**: Sandboxed Docker code compilation and testing, system status monitoring, and auto-healing of mock servers and containers.

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|---|---|---|---|
| M1 | E2E Test Suite & Infra | Create test suite for all R1-R5 requirements; output TEST_READY.md | None | PLANNED |
| M2 | MCP Registry & Semantic Router (R1) | Build `mcp_capabilities.json` registry and Tool Router Agent | M1 | PLANNED |
| M3 | Multi-Level Planning Graph (R2) | Implement specialized agents and LangGraph planning state machine | M1, M2 | PLANNED |
| M4 | Repository Memory & Learning (R3) | Implement memory updater and `Lessons.md` failure-diagnostics loop | M1, M3 | PLANNED |
| M5 | Context Compression Engine (R4) | Implement custom log, JSON, and tree compressions | M1, M3 | PLANNED |
| M6 | Test Loop & Self-Healing Ops (R5) | Implement Docker test runner and self-healing loop | M1, M3 | PLANNED |
| M7 | E2E Integration & Hardening | Complete full flow integration, run E2E test suite, adversarial testing | M1-M6 | PLANNED |

## Code Layout
- `rag_local/`:
  - `mcp_registry.py`: Capability registry logic and tool router agent.
  - `graph.py`: LangGraph workflow orchestration.
  - `specialized_team.py`: Definition and behaviors of specialized workers.
  - `memory.py`: Repo memories updating and lessons-learned logs.
  - `compression.py`: Context compression rules.
  - `ops.py`: Docker test execution and self-healing operations logic.
- `scratchpad/`:
  - `test_nexus_v2.py`: E2E validation script.
