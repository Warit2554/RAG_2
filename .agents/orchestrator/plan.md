# Plan - Nexus V2 Development

## Mission
Build "Nexus V2", a fully local, self-healing, multi-agent workspace builder and Linux operations engineer with unlimited MCP tool scalability and repository-level project memory.

## Development Milestones
1. **Milestone 1: E2E Test Suite & Infra**:
   - Establish the test harness and mock assets.
   - Define a test suite covering semantic tool routing, registry planning, repo memory creation, lessons generation, context compression, Docker test looping, and self-healing.
   - Verify test suite and output `TEST_READY.md`.
2. **Milestone 2: MCP Registry & Semantic Router (R1)**:
   - Implement scan of MCP servers to create `mcp_capabilities.json`.
   - Implement Tool Router Agent using embeddings/cosine-similarity to filter down tools to <= 10.
   - Test router filtering.
3. **Milestone 3: Multi-Level Planning Graph & Coding Team (R2)**:
   - Enhance the LangGraph workflow to map: User -> Intent Router -> Planner -> Parallel Worker Agents -> Synthesizer.
   - Add specialized worker roles (Architect, Backend, Frontend, DevOps, QA, Documentation).
4. **Milestone 4: Repository Memory & Learning Loop (R3)**:
   - Generate repository markdown memories (`Knowledge.md`, `Architecture.md`, `Components.md`, `Decisions.md`, `Tasks.md`).
   - Generate `Lessons.md` with correct diagnostics upon failure and recovery.
5. **Milestone 5: Context Compression Engine (R4)**:
   - Implement parsers/rules to compress tree/log output to under 4000 chars while preserving core error messages and file markers.
6. **Milestone 6: Continuous Test Loop & Self-Healing Ops (R5)**:
   - Sandbox code execution in Docker and run tests.
   - Monitor system state (simulating Proxmox/VM/Docker/services/disk space) and trigger recovery operations.
7. **Milestone 7: Integration and Adversarial Hardening**:
   - Run full E2E test suite and white-box adversarial verification.
