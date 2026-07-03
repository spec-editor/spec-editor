"""Embedding-based semantic code search.

Uses Ollama's embedding API (all-MiniLM-L6-v2: 384-dim, ~80 MB, free).
Storage: JSON chunks + NumPy .npy embeddings in $PROJECT/.spec-editor/

No graph DB or external service required — just NumPy + Ollama.
"""

from __future__ import annotations

from dataclasses import asdict
import json
import logging
import time
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np

from src.code_index.chunker import CodeChunk, chunk_project

logger = logging.getLogger(__name__)

# Ollama embedding model — small, fast, English-optimised
_EMBED_MODEL = "all-minilm:l6-v2"
_EMBED_DIM = 384
_EMBED_BATCH = 50  # chunks per Ollama call


class EmbeddingIndex:
    """Build and search a semantic code index.

    Usage:
        index = EmbeddingIndex(project_path)
        index.build()                         # chunks code → embeddings → save
        results = index.search("payment processing", top_k=10)
        for r in results:
            print(f"{r['rel_path']}:{r['line']} {r['symbol']} — score {r['score']:.2f}")
    """

    def __init__(self, project_path: Path):
        self._root = project_path
        self._dir = project_path / ".spec-editor"
        self._chunks_path = self._dir / "chunks.json"
        self._embeddings_path = self._dir / "embeddings.npy"
        self._chunks: list[dict[str, Any]] = []
        self._embeddings: np.ndarray | None = None

    # ── Build ────────────────────────────────────────────────────

    def build(self, force: bool = False) -> int:
        """Build or rebuild the semantic index. Returns chunk count."""
        self._dir.mkdir(parents=True, exist_ok=True)

        if not force and self._chunks_path.exists() and self._embeddings_path.exists():
            self._load()
            return len(self._chunks)

        start = time.monotonic()
        logger.info("semantic_index_building_start")

        raw_chunks = chunk_project(self._root)
        self._chunks = [asdict(c) for c in raw_chunks]
        if not self._chunks:
            logger.warning("semantic_index_empty")
            return 0

        # Build texts for embedding: docstring + first 500 chars of code
        texts = [_embed_text(c) for c in self._chunks]

        # Save chunks (always)
        self._chunks_path.write_text(
            json.dumps(self._chunks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Batch embed via Ollama (may fail if Ollama not running)
        try:
            embeddings = self._embed_batch(texts)
            np.save(str(self._embeddings_path), embeddings)
            self._embeddings = embeddings
        except Exception:
            # Embedding may fail if Ollama is not running or model not pulled.
            # Chunks are saved — search will work once Ollama is available.
            pass
            # Chunks saved, embeddings unavailable — run `ollama pull all-minilm:l6-v2` and rebuild

        elapsed = time.monotonic() - start
        logger.info(
            "semantic_index_built",
            chunks=len(self._chunks),
            elapsed_s=round(elapsed, 1),
        )
        return len(self._chunks)

    # ── Search ───────────────────────────────────────────────────

    def search(
        self, query: str, top_k: int = 10, min_score: float = 0.0
    ) -> list[dict[str, Any]]:
        """Search codebase semantically. Returns top-k results with scores."""
        if not self._chunks_path.exists():
            self.build()
        if self._embeddings is None:
            self._load()
        if self._embeddings is None or len(self._chunks) == 0:
            return []

        # Embed query
        q_vec = self._embed_single(query)

        # Cosine similarity (normalised dot product)
        scores = np.dot(self._embeddings, q_vec) / (
            np.linalg.norm(self._embeddings, axis=1) * np.linalg.norm(q_vec) + 1e-10
        )

        # Top-K indices
        if top_k >= len(scores):
            top_indices = np.argsort(scores)[::-1]
        else:
            top_indices = np.argpartition(scores, -top_k)[-top_k:]
            top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]

        results: list[dict[str, Any]] = []
        for idx in top_indices:
            score = float(scores[idx])
            if score < min_score:
                continue
            chunk = dict(self._chunks[idx])
            chunk["score"] = round(score, 4)
            results.append(chunk)

        return results[:top_k]

    # ── Internal ─────────────────────────────────────────────────

    def _load(self) -> None:
        if self._chunks_path.exists():
            self._chunks = json.loads(
                self._chunks_path.read_text(encoding="utf-8")
            )
        if self._embeddings_path.exists():
            self._embeddings = np.load(str(self._embeddings_path))

    def _embed_single(self, text: str) -> np.ndarray:
        return self._embed_batch([text])[0]

    def _embed_batch(self, texts: list[str]) -> np.ndarray:
        """Call Ollama embedding API, batch by _EMBED_BATCH."""
        all_vectors: list[np.ndarray] = []
        for i in range(0, len(texts), _EMBED_BATCH):
            batch = texts[i : i + _EMBED_BATCH]
            vectors = self._ollama_embed(batch)
            all_vectors.extend(vectors)
        return np.array(all_vectors, dtype=np.float32)

    @staticmethod
    def _ollama_embed(texts: list[str]) -> list[np.ndarray]:
        """Call Ollama /api/embed endpoint."""
        url = "http://127.0.0.1:11434/api/embed"
        data = json.dumps({"model": _EMBED_MODEL, "input": texts}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            raise RuntimeError(
                f"Ollama embedding failed. Is '{_EMBED_MODEL}' pulled? "
                f"Run: ollama pull {_EMBED_MODEL}\nError: {e}"
            ) from e

        embeddings = result.get("embeddings", [])
        return [np.array(emb, dtype=np.float32) for emb in embeddings]


def _embed_text(chunk: dict[str, Any]) -> str:
    """Build embedding text: docstring + code snippet."""
    parts = []
    if chunk.get("docstring"):
        # Docstring gets 3x weight by repeating it
        ds = chunk["docstring"]
        parts.append(ds)
        parts.append(ds)
        parts.append(ds)
    parts.append(chunk.get("text", "")[:500])
    return "\n".join(parts)
