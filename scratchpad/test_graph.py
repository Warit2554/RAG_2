import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from rag_local.config import SETTINGS
from rag_local.graph import APP
from rag_local.mcp_client import mcp_manager

async def main():
    print("Starting MCP manager...")
    await mcp_manager.start_all()
    print(f"Connected to MCP servers: {mcp_manager.server_names}")
    
    query = "read setup_nexus.sh and explaing to me what it is"
    state = {
        "user_input": query,
        "chat_history": []
    }
    
    print(f"\n--- Invoking LangGraph for query: '{query}' ---")
    try:
        # We use astream to see each node's output as it finishes
        async for update in APP.astream(state, stream_mode="updates"):
            print("\n--- Node update received ---")
            for node, val in update.items():
                print(f"Node: {node}")
                for k, v in val.items():
                    if k in {"retrieved_chunks"}:
                        print(f"  {k}: {len(v)} chunks retrieved")
                    elif k in {"plan"}:
                        print(f"  plan.objective: {v.objective}")
                        print(f"  plan.tasks: {[t.model_dump() for t in v.tasks]}")
                    elif k in {"final_answer"}:
                        print(f"  final_answer: {v[:200]}...")
                    else:
                        print(f"  {k}: {v}")
    except Exception as e:
        import traceback
        print(f"\n[Error] Graph invocation failed: {e}")
        traceback.print_exc()
        
    print("\nStopping MCP manager...")
    await mcp_manager.stop_all()

if __name__ == "__main__":
    asyncio.run(main())
