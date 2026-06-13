import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from rag_local.config import SETTINGS
from rag_local.embed import OllamaClient

async def main():
    print(f"Ollama host: {SETTINGS.ollama_host}")
    print(f"Chat model configured: {SETTINGS.ollama_chat_model}")
    print(f"Embed model configured: {SETTINGS.ollama_embed_model}")
    
    client = OllamaClient()
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello, answer in 5 words."}
    ]
    
    print("\n--- Testing standard chat ---")
    try:
        ans = await client.chat(SETTINGS.ollama_chat_model, messages)
        print(f"Response: {ans}")
    except Exception as e:
        print(f"Error in chat: {e}")
        
    print("\n--- Testing streaming chat ---")
    try:
        ans_stream = ""
        async for token in client.chat_stream(SETTINGS.ollama_chat_model, messages):
            sys.stdout.write(token)
            sys.stdout.flush()
            ans_stream += token
        print(f"\nStream Response length: {len(ans_stream)}")
    except Exception as e:
        print(f"\nError in chat_stream: {e}")

if __name__ == "__main__":
    asyncio.run(main())
