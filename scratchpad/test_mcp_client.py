import asyncio
import os
import sys

# Ensure active directory is in python path
sys.path.insert(0, os.path.abspath("."))

from rag_local.mcp_client import mcp_manager

async def test():
    print("=== Starting MCP client ===")
    await mcp_manager.start_all()
    
    print("\n=== Available Servers ===")
    print(mcp_manager.server_names)
    
    print("\n=== Fetching all tools ===")
    tools = await mcp_manager.get_all_tools()
    for t in tools:
        print(f"- {t['server_name']} | Tool: {t['name']}")
        print(f"  Description: {t['description']}")
        
    print("\n=== Testing Time MCP Tool ===")
    if "time" in mcp_manager.sessions:
        time_result = await mcp_manager.call_tool("time", "get_current_time", {})
        print(f"Time response:\n{time_result}")
    else:
        print("Time server not running.")
        
    print("\n=== Testing Database MCP Tool ===")
    if "database" in mcp_manager.sessions:
        db_result = await mcp_manager.call_tool("database", "list_collections", {})
        print(f"Database collections response:\n{db_result}")
    else:
        print("Database server not running.")
        
    print("\n=== Testing DuckDuckGo Search MCP Tool ===")
    if "duckduckgo" in mcp_manager.sessions:
        search_result = await mcp_manager.call_tool("duckduckgo", "duckduckgo_search", {"query": "Shiba Inu"})
        print(f"DuckDuckGo search response:\n{search_result}")
    else:
        print("DuckDuckGo search server not running.")
        
    print("\n=== Shutting down ===")
    await mcp_manager.stop_all()

if __name__ == "__main__":
    asyncio.run(test())
