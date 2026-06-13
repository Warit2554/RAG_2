import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from rag_local.mcp_client import mcp_manager

async def main():
    await mcp_manager.start_all()
    all_tools = await mcp_manager.get_all_tools()
    
    tools_prompt = ""
    if all_tools:
        tools_prompt = "\nAvailable Dynamic MCP Tools (Server -> Tool):\n"
        for t in all_tools:
            props = t.get('input_schema', {}).get('properties', {})
            req = t.get('input_schema', {}).get('required', [])
            args_hint = ", ".join(f"{k} (required)" if k in req else k for k in props.keys())
            desc = (t.get('description') or '').replace('\n', ' ')[:60]
            tools_prompt += f"- {t['server_name']} -> {t['name']}: {desc}... Args: {{{args_hint}}}\n"
        tools_prompt += (
            "\nTo use any of these dynamic MCP tools, you MUST set kind to 'mcp', and format 'query' as a JSON object: \n"
            "  \"query\": {\"server_name\": \"<server_name>\", \"tool_name\": \"<tool_name>\", \"arguments\": {<args>}}\n"
        )
    
    print(f"Total active servers: {len(mcp_manager.server_names)}")
    print(f"Active servers: {mcp_manager.server_names}")
    print(f"Total active tools: {len(all_tools)}")
    print(f"Tools Prompt length: {len(tools_prompt)} characters / ~{len(tools_prompt)//4} tokens")
    print("\nTools Prompt:")
    print(tools_prompt)
    
    await mcp_manager.stop_all()

if __name__ == "__main__":
    asyncio.run(main())
