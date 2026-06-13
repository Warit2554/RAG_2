import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from rag_local.config import SETTINGS
from rag_local.graph import APP
from rag_local.mcp_client import mcp_manager
from rag_local.types import RagState

async def main():
    print("Starting MCP manager...")
    await mcp_manager.start_all()
    print(f"Connected to MCP servers: {mcp_manager.server_names}")
    
    query = "read setup_nexus.sh and explaing to me what it is"
    
    # ── First pass: Router determines clarification is needed ──
    print("\n=== RUNNING GRAPH PASS 1 ===")
    state1 = {
        "user_input": query,
        "chat_history": []
    }
    
    res1 = await APP.ainvoke(state1)
    prompt = res1.get("clarification_prompt")
    print(f"Pass 1 Clarification Prompt: {prompt}")
    
    if prompt:
        # Simulate auto-selection:
        default_path = prompt["paths"][prompt["default_index"]]
        print(f"Simulating auto-selection of default path: {default_path}")
        
        # ── Second pass: Run graph with clarification_response ──
        print("\n=== RUNNING GRAPH PASS 2 ===")
        state2 = {
            "user_input": query,
            "chat_history": [],
            "clarification_response": default_path
        }
        
        async for update in APP.astream(state2, stream_mode="updates"):
            print("\n--- Node update ---")
            for node, val in update.items():
                print(f"Node: {node}")
                for k, v in val.items():
                    if k == "retrieved_chunks":
                        print(f"  {k}: {len(v)} chunks")
                        for idx, chunk in enumerate(v):
                            print(f"    Chunk {idx}: source={chunk.source_path}, title={chunk.title}")
                    elif k == "plan":
                        print(f"  plan.objective: {v.objective}")
                        print(f"  plan.tasks: {[t.model_dump() for t in v.tasks]}")
                    elif k == "final_answer":
                        print(f"  final_answer: '{v}'")
                    else:
                        print(f"  {k}: {v}")
    
    await mcp_manager.stop_all()

if __name__ == "__main__":
    asyncio.run(main())
