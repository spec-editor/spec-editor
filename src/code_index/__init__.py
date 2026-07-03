"""Semantic code search — tree-sitter chunking + embedding + search.

Layers:
  chunker  — tree-sitter extraction: functions, classes, methods from code
  index    — NumPy cosine similarity over Ollama embeddings (no graph DB needed)
  tool     — MCP tool: search_semantic(query, top_k=10)

Storage: $PROJECT/.spec-editor/chunks.json + embeddings.npy (~1-10 MB)
"""

from src.code_index.chunker import CodeChunk, chunk_project
from src.code_index.index import EmbeddingIndex

__all__ = ["CodeChunk", "chunk_project", "EmbeddingIndex"]
