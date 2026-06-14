import asyncio
import json
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

from rag_local.mcp_client import mcp_manager

async def main():
    print("Starting MCP manager...")
    # Let's override the connect timeout to be shorter (3 seconds) for this test
    import rag_local.mcp_client
    rag_local.mcp_client.MCP_CONNECT_TIMEOUT = 3
    
    await mcp_manager.start_all()
    print("Listing tools...")
    tools = await mcp_manager.get_all_tools()
    print(f"Found {len(tools)} tools.")
    
    output_path = Path(__file__).parent / "mcp_tools.json"
    with open(output_path, "w") as f:
        json.dump(tools, f, indent=2)
    print(f"Saved tools to {output_path}")
    
    await mcp_manager.stop_all()

if __name__ == "__main__":
    asyncio.run(main())
