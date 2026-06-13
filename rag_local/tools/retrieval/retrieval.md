# Retrieval Tool Specification

The Retrieval Tool provides search functionality across local indexed files in the workspace.

## Components
- **QdrantStore**: Connects to the local Qdrant database (or spins up an in-memory client if Qdrant is unavailable) and handles vector indexing and dense semantic queries.
- **PersistedIndex**: Maintains a BM25 sparse index sidecar file in the project folder to handle exact keyword matching (especially variable names, function declarations, and API routes).
- **hybrid_retrieve**: The main retrieval function. It retrieves dense search rankings and sparse keyword rankings, fuses them together using a custom scoring algorithm, and overrides specific exact-file match queries.

## Methods
- `ensure_collection(vector_size)`: Validates that the target Qdrant collection exists.
- `upsert_chunks(chunks, embeddings)`: Vectorizes chunks and registers them to Qdrant and the BM25 sidecar index.
- `hybrid_search(query, query_embedding, top_k)`: Fuses dense search (0.7 weight) with BM25 sparse search (0.3 weight) and prioritizes exact filename matching.
- `search_by_paths(query, paths, top_k)`: Performs path-scoped scroll retrieval over targeted paths.
