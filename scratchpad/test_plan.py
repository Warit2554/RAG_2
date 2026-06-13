import asyncio
import sys
import json
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from rag_local.config import SETTINGS
from rag_local.embed import OllamaClient, build_messages
from rag_local.types import RagState
from rag_local.orchestrator import build_plan, ORCHESTRATOR_SYSTEM, NEXUS_MCP_AUTHORITY_PROMPT
from rag_local.mcp_client import mcp_manager

async def test_raw():
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
        
    client = OllamaClient()
    system_prompt = ORCHESTRATOR_SYSTEM + "\n\n" + NEXUS_MCP_AUTHORITY_PROMPT + tools_prompt
    messages = build_messages(system_prompt, "read setup_nexus.sh and explaing to me what it is")
    
    print("\n--- Sending request to Ollama ---")
    try:
        raw = await client.chat(
            SETTINGS.ollama_orchestrator_model,
            messages,
            temperature=0.2,
            keep_alive=SETTINGS.rag_keep_alive,
            format="json",
        )
        print("Raw response from Ollama:")
        print(raw)
        
        parsed = json.loads(raw)
        print("\nParsed JSON successfully:")
        print(json.dumps(parsed, indent=2))
    except Exception as e:
        print(f"Error during raw request/parse: {e}")
        
    await mcp_manager.stop_all()

if __name__ == "__main__":
    asyncio.run(test_raw())
