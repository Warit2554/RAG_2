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
        
    log("=== Testing Nexus Fixes (Mocked MCP) ===")
    
    # 1. Test Routing Logic
    log("\n1. Testing routing for action verbs...")
    test_query = "Create a Minecraft Fabric Server 1.21.11, download fabric1.21.11.jar first, ready to join."
    decision = await route_query(test_query)
    log(f"Query: '{test_query}'")
    log(f"Routed to: '{decision.decision.route}' (Reason: {decision.decision.reason})")
    
    # Assert route is NOT general
    assert decision.decision.route != "general", f"FAILED: Action verb request should not route to general, got {decision.decision.route}"
    log("✓ SUCCESS: Routing override works correctly.")

    # 2. Test Planning & Success Criteria
    log("\n2. Testing planner for success criteria and executable tasks...")
    state = RagState(user_input=test_query, route=decision.decision.route)
    plan = await build_plan(state)
    
    log(f"Plan Objective: {plan.objective}")
    log("Plan Success Criteria:")
    for sc in plan.success_criteria:
        log(f"  - {sc}")
    log("Plan Tasks:")
    for task in plan.tasks:
        log(f"  - [{task.kind}] {task.name}: {task.query[:150]}")
        
    assert len(plan.success_criteria) > 0, "FAILED: success_criteria list should not be empty"
    assert len(plan.tasks) > 0, "FAILED: plan should have at least one task"
    
    # Check if fabric server jar download or creation tasks are present
    has_download_task = any("download" in str(task.name).lower() or "download" in str(task.query).lower() for task in plan.tasks)
    has_server_task = any("server" in str(task.name).lower() or "server" in str(task.query).lower() for task in plan.tasks)
    log(f"Has download task: {has_download_task}")
    log(f"Has server task: {has_server_task}")
    
    log("✓ SUCCESS: Planner correctly generates success criteria and tasks.")
    log("\n=== All Tests Passed! ===")
    
    # Write output to file
    output_path = Path(__file__).parent / "test_output.txt"
    with open(output_path, "w") as f:
        f.write("\n".join(output))
        
    sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
