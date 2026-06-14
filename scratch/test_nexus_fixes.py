import asyncio
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

from rag_local.config import SETTINGS
from rag_local.types import RagState
from rag_local.router import route_query
from rag_local.orchestrator import build_plan
from rag_local.mcp_client import mcp_manager

# Mock MCP Manager to avoid starting real servers in tests
async def mock_start_all(*args, **kwargs):
    pass

async def mock_stop_all(*args, **kwargs):
    pass

async def mock_get_all_tools(*args, **kwargs):
    tools_file = Path(__file__).parent / "mcp_tools.json"
    if tools_file.exists():
        with open(tools_file, "r") as f:
            return json.load(f)
    return []

mcp_manager.start_all = mock_start_all
mcp_manager.stop_all = mock_stop_all
mcp_manager.get_all_tools = mock_get_all_tools

async def main():
    output = []
    def log(msg):
        print(msg)
        output.append(str(msg))
        
    log("=== Testing Generic Download Strategy (Mocked MCP) ===")
    
    # Test Query 1: Debian ISO Download
    test_query_iso = "Download the Debian stable netinst ISO image"
    log(f"\n1. Testing routing for ISO download: '{test_query_iso}'")
    decision_iso = await route_query(test_query_iso)
    log(f"Routed to: '{decision_iso.decision.route}' (Reason: {decision_iso.decision.reason})")
    assert decision_iso.decision.route != "general", "FAILED: ISO download should route to planning"
    
    log("\n2. Testing planner for ISO download...")
    state_iso = RagState(user_input=test_query_iso, route=decision_iso.decision.route)
    plan_iso = await build_plan(state_iso)
    log(f"Plan Objective: {plan_iso.objective}")
    log("Plan Success Criteria:")
    for sc in plan_iso.success_criteria:
        log(f"  - {sc}")
    log("Plan Tasks:")
    for task in plan_iso.tasks:
        log(f"  - [{task.kind}] {task.name}: {task.query[:150]}")
        
    assert len(plan_iso.success_criteria) > 0, "FAILED: success_criteria list should not be empty"
    # Ensure the first task is a search/browse/web search task to find the URL
    first_task = plan_iso.tasks[0]
    log(f"First task query: {first_task.query}")
    assert "search" in first_task.name.lower() or "search" in first_task.query.lower() or "duckduckgo" in first_task.query.lower() or "playwright" in first_task.query.lower(), \
        f"FAILED: The first task should be a search/browse task to find the URL, got {first_task.name}"
        
    log("✓ SUCCESS: Planner correctly plans a search/browse step for generic download without hardcoding.")
    log("\n=== All Tests Passed! ===")
    
    # Write output to file
    output_path = Path(__file__).parent / "test_output.txt"
    with open(output_path, "w") as f:
        f.write("\n".join(output))
        
    sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
