# Original User Request

## 2026-06-13T23:27:03+07:00

You are the E2E Testing Orchestrator for Nexus V2.
Your working directory is /root/RAG_2/.agents/teamwork_preview_orchestrator_e2e_track/.
Your task is to design and implement a comprehensive, opaque-box, requirement-driven E2E test suite for the Nexus V2 features (R1 to R5), publish TEST_INFRA.md, write the testing code, verify it, and publish TEST_READY.md.

Specifically, implement the E2E Testing Track:
1. Create a detailed /root/RAG_2/TEST_INFRA.md following the template:
   - Feature Inventory covering R1 to R5.
   - Test Architecture (test runner command, expected output, layout).
   - Test Case Design: Tier 1 (Feature Coverage >= 5 per feature), Tier 2 (Boundary & Corner Cases >= 5 per feature), Tier 3 (Cross-feature interactions), Tier 4 (Real-world scenarios).
   - Minimum total tests should cover all criteria in ORIGINAL_REQUEST.md.
2. Implement the test harness and mock files.
   - You can create mocks for the 100+ tools.
   - You can create mocks for the simulated server/VM failure (for self-healing ops).
   - You can create mock tree/log outputs to verify the 90% context compression (under 4000 characters).
3. The test suite runner should execute via a simple script/command, e.g., pytest or python scratchpad/run_tests.py.
4. Verify that the test cases are in place, build and run them (they should fail initially or run successfully on mock targets if the mock logic is in place, but since the implementation is not done yet, happy path verification tests can run against mocks or be ready to run).
5. Once the test suite is designed, implemented, and verify-ready, write /root/RAG_2/TEST_READY.md detailing how implementation agents can run it.
6. When TEST_READY.md is published, notify parent.

Remember: DO NOT write any core implementation code. Focus exclusively on test infrastructure, test cases, and mocks. Use worker agents if you need to write files.
