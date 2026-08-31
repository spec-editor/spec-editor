"""Embedding-based semantic code search.

Uses fastembed (ONNX Runtime) for embeddings — a pure pip dependency,
no external service required. The model (all-MiniLM-L6-v2, 384-dim)
is downloaded on first use and cached locally.

Storage: JSON chunks + NumPy .npy embeddings in $PROJECT/.spec-editor/
"""

from __future__ import annotations

from dataclasses import asdict
import json
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

from src.code_index.chunker import CodeChunk, chunk_project

logger = logging.getLogger(__name__)

# Embedding model — small, fast, English-optimised (384-dim)
_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_EMBED_DIM = 384

# Source extensions and dirs used for staleness detection (mirrors chunker).
_SOURCE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".rs"}
_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", "out", "target", ".spec-editor",
    "test-results", "dry_run_output", ".vscode-test",
}


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
        self._model = None

    # ── Embedding model (lazy, fastembed) ─────────────────────────

    def _get_model(self):
        """Lazy-load the fastembed embedding model (downloads on first use)."""
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=_EMBED_MODEL)
        return self._model

    def is_ready(self) -> bool:
        """True if both chunks and embeddings exist on disk (index searchable)."""
        return self._chunks_path.exists() and self._embeddings_path.exists()

    def is_stale(self) -> bool:
        """True if any source file changed since the index was built.

        Compares the index build time (chunks.json mtime) against the
        newest source file mtime. Uses os.walk with directory pruning so
        .venv/node_modules/.git are skipped entirely (huge speedup vs rglob).
        """
        if not self.is_ready():
            return False
        try:
            index_mtime = self._chunks_path.stat().st_mtime
        except OSError:
            return False

        import os

        exts = tuple(_SOURCE_EXTS)
        for dirpath, dirnames, filenames in os.walk(self._root):
            # Prune non-source directories entirely — don't walk them.
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for name in filenames:
                if not name.endswith(exts):
                    continue
                try:
                    if os.path.getmtime(os.path.join(dirpath, name)) > index_mtime:
                        return True
                except OSError:
                    continue
        return False

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

        # Embed via fastembed (raises on failure — surface the error)
        embeddings = self._embed_batch(texts)
        np.save(str(self._embeddings_path), embeddings)
        self._embeddings = embeddings

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
        if self._embeddings is None and self._chunks:
            # Chunks exist but embeddings are missing (partial/inconsistent
            # index) — rebuild to restore embeddings.
            self.build(force=True)
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
        """Embed a batch of texts via fastembed."""
        try:
            model = self._get_model()
            vectors = list(model.embed(texts))
        except ImportError as exc:
            raise RuntimeError(
                "fastembed is not installed. Run: pip install fastembed"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"Embedding failed (model '{_EMBED_MODEL}'): {exc}. "
                f"First use downloads the model — check network access."
            ) from exc
        return np.array(vectors, dtype=np.float32)


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
