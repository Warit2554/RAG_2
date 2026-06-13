# Original User Request

## Initial Request — 2026-06-13T16:21:02Z

Build "Nexus V2", a fully local, self-healing, multi-agent workspace builder and Linux operations engineer with unlimited MCP tool scalability and repository-level project memory.

Working directory: /root/RAG_2
Integrity mode: development

## Requirements

### R1. Unlimited MCP Scalability & Semantic Tool Router
- A capability registry (`mcp_capabilities.json`) must store metadata, tags, and arguments schemas of all discovered MCP tools.
- A dedicated **Tool Router Agent** must dynamically discover MCP tools, match user queries semantically using embeddings/cosine-similarity, and expose only a highly relevant, scoped subset of tool definitions to the planner (instead of listing all raw tool definitions).

### R2. Multi-Level Planning Graph & Specialized Coding Team
- Integrate a LangGraph state machine workflow: User → Intent Router → Planner → Parallel Worker Agents → Synthesizer.
- Parallel worker agents must consist of specialized roles: Architect, Backend, Frontend, DevOps, QA, and Documentation.

### R3. Repository Memory & Agent Learning Loop
- The system must automatically generate and update repository memories (`Knowledge.md`, `Architecture.md`, `Components.md`, `Decisions.md`, `Tasks.md`) as the project evolves.
- After every task execution, the system must generate a `Lessons.md` file capturing failures, fixes, and architectural choices to inform future plans.

### R4. Context Compression Engine
- Custom log/JSON/tree parsers and LLM summarization rules must reduce raw tool outputs by 90% before inserting them into synthesis prompts, preventing context window overflow.

### R5. Autonomous Builder, Test Loop, and Self-Healing Ops
- Implement a **Continuous Test Loop**: Generate code → Run tests inside isolated Docker containers → Analyze errors → Fix errors → Repeat.
- Implement an **Operations & Self-Healing Loop**: Monitor system state (simulating remote systems like Proxmox and VM clusters using local unit tests or mock APIs, plus Docker containers, disk space, services) and automatically execute diagnostic, cleanup, or recovery actions when anomalies are detected.

## Acceptance Criteria

### Tool Router & Scalability
- [ ] The semantic search router must successfully filter a mock test suite of 100+ tools down to a target subset (<= 10 tools) containing the relevant tool.
- [ ] The orchestrator planner must succeed in outputting plans using registry metadata when all tool schemas are hidden.

### Project Memory & Learning
- [ ] Running a repository generation task must auto-produce valid markdown files for `Knowledge.md` and `Architecture.md` in the target directory.
- [ ] A simulated task failure and recovery must trigger the writing of `Lessons.md` with correct diagnostics.

### Context Compression
- [ ] Directory tree and test logs must be successfully compressed to under 4000 characters without losing core error messages or top-level file markers.

### Build and Test Loops
- [ ] A generated project must automatically configure a test suite, run it inside isolated Docker, and output logs.
- [ ] The self-healing agent must detect a simulated server failure (e.g., service in down state, mock VM failure) and validate recovery.
