from mcp.server.fastmcp import FastMCP
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
import json
import os
import sys

# Ensure active directory is in python path
sys.path.insert(0, os.path.abspath("."))

from rag_local.config import SETTINGS
from rag_local.tools.retrieval.tool import QdrantStore, hybrid_retrieve

mcp = FastMCP("Database")

@mcp.tool()
async def list_collections() -> list[str]:
    """List all available collections in the local Qdrant database."""
    store = QdrantStore()
    collections = store.client.get_collections().collections
    return [c.name for c in collections]

@mcp.tool()
async def search_database(query: str, limit: int = 5) -> str:
    """Perform a hybrid dense/sparse search on the local RAG database."""
    result = await hybrid_retrieve(query, top_k=limit)
    if not result.hits:
        return "No matching database entries found."
    lines = []
    for hit in result.hits:
        lines.append(
            f"- Chunk: {hit.chunk_id} (Score: {hit.score:.2f}) | File: {hit.source_path}\n"
            f"  Title: {hit.title}\n"
            f"  Summary: {hit.summary}\n"
            f"  Content: {hit.content[:300]}..."
        )
    return "\n".join(lines)

@mcp.tool()
async def modify_database_record(chunk_id: str, field: str, value: str) -> str:
    """Modify/update a specific payload or metadata field in a database chunk.
    
    If the field is not a standard chunk field, it will be placed inside 'metadata'.
    """
    store = QdrantStore()
    collection = store.collection
    
    # 1. Update the local BM25 persistent index memory
    found_in_bm25 = False
    for chunk in store.persisted.chunks:
        if str(chunk.get("chunk_id")) == chunk_id:
            found_in_bm25 = True
            if field in chunk:
                # Type cast if original type was int
                orig_type = type(chunk[field])
                try:
                    chunk[field] = orig_type(value)
                except ValueError:
                    chunk[field] = value
            else:
                if "metadata" not in chunk:
                    chunk["metadata"] = {}
                chunk["metadata"][field] = value
            break
            
    if found_in_bm25:
        store.persisted.save()
        
    # 2. Update the payload in the active Qdrant Vector database
    import uuid
    point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk_id))
    try:
        # Check if point exists
        points = store.client.retrieve(collection_name=collection, ids=[point_id])
        if not points:
            # Try by string UUID direct
            points = store.client.retrieve(collection_name=collection, ids=[chunk_id])
            if points:
                point_id = chunk_id
                
        if points:
            payload = points[0].payload or {}
            if field in payload:
                orig_type = type(payload[field])
                try:
                    payload[field] = orig_type(value)
                except ValueError:
                    payload[field] = value
            else:
                if "metadata" not in payload:
                    payload["metadata"] = {}
                payload["metadata"][field] = value
                
            store.client.set_payload(
                collection_name=collection,
                payload=payload,
                points=[point_id]
            )
            return f"Success: Modified database record '{chunk_id}' setting '{field}' to '{value}'."
        else:
            if found_in_bm25:
                return f"Success: Modified record in BM25 index memory, but point ID '{point_id}' was not found in active Qdrant database."
            return f"Error: Database record '{chunk_id}' not found."
    except Exception as e:
        return f"Error updating Qdrant: {e}"

if __name__ == "__main__":
    mcp.run()
