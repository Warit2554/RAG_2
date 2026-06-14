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
from rag_local.embed import OllamaClient

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
    query = "Create a minecraft server local 100% and ready to join on /root/minecraft_server"
    
    SETTINGS.verbose_mode = True
    
    print(f"Routing query: '{query}'...")
    decision = await route_query(query)
    print(f"Routed to: '{decision.decision.route}'")
    
    print("\nGenerating plan...")
    state = RagState(user_input=query, route=decision.decision.route)
    plan = await build_plan(state)
    
    print("\n=== Generated Plan ===")
    print(f"Objective: {plan.objective}")
    print("Success Criteria:")
    for sc in plan.success_criteria:
        print(f"  - {sc}")
    print("Constraints:")
    for c in plan.constraints:
        print(f"  - {c}")
    print("Tasks:")
    for idx, task in enumerate(plan.tasks, start=1):
        print(f"Task #{idx}:")
        print(f"  Name: {task.name}")
        print(f"  Kind: {task.kind}")
        print(f"  Assigned Agent: {task.assigned_agent}")
        print(f"  Depends on: {task.depends_on}")
        print(f"  Query: {task.query}")
        print(f"  Artifact Targets: {task.artifact_targets}")
        print(f"  Verification Rules: {task.verification_rules}")
        print()

if __name__ == "__main__":
    asyncio.run(main())
