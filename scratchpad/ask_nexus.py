import asyncio
import os
import sys

# Ensure active directory is in python path
sys.path.insert(0, os.path.abspath("."))

from rag_local.graph import APP

async def main():
    question = (
        "Create a detailed markdown documentation for the Shiba Inu image extractor script "
        "located at `docker_scripts/extract_image.py`. Include the docker command to run it: "
        "`docker run --rm -v $(pwd):/workspace python:3.11-slim python /workspace/docker_scripts/extract_image.py` "
        "and explain the URLs/fallback strategies it uses."
    )
    print("Asking Nexus...")
    res = await APP.ainvoke({
        "user_input": question,
        "chat_history": [],
        "clarification_response": "."
    })
    content = res.get("final_answer", "")
    if not content:
        content = res.get("general_answer", "Error: No answer synthesized.")
    
    os.makedirs("docker_scripts", exist_ok=True)
    with open("docker_scripts/extract_image.md", "w", encoding="utf-8") as f:
        f.write(content)
    print("Created docker_scripts/extract_image.md successfully!")

if __name__ == "__main__":
    asyncio.run(main())
