import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class FaissVectorCache:
    """Manages local caching of generated chunks and embeddings per tender."""

    def __init__(self, cache_dir: str = "storage/faiss/cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_tender_dir(self, tender_id: str) -> Path:
        safe_id = "".join(c for c in tender_id if c.isalnum() or c in ("-", "_"))
        return self.cache_dir / safe_id

    def save(
        self,
        tender_id: str,
        ikn: str,
        source_hash: str,
        chunking_version: str,
        embedding_model: str,
        embedding_version: str,
        vector_dimension: int,
        chunks: list[dict[str, Any]],
        vectors: list[list[float]] | np.ndarray,
    ) -> None:
        tender_dir = self._get_tender_dir(tender_id)
        tender_dir.mkdir(parents=True, exist_ok=True)

        chunks_path = tender_dir / "chunks.jsonl"
        with open(chunks_path, "w", encoding="utf-8") as f:
            for chunk in chunks:
                f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

        vectors_path = tender_dir / "vectors.npy"
        if isinstance(vectors, list):
            np_vectors = np.array(vectors, dtype=np.float32)
        else:
            np_vectors = np.asarray(vectors, dtype=np.float32)

        np.save(vectors_path, np_vectors)

        manifest_path = tender_dir / "manifest.json"
        manifest = {
            "tender_id": tender_id,
            "ikn": ikn,
            "source_hash": source_hash,
            "chunking_version": chunking_version,
            "embedding_model": embedding_model,
            "embedding_version": embedding_version,
            "vector_dimension": vector_dimension,
            "chunk_count": len(chunks),
            "created_at": datetime.now(UTC).isoformat(),
        }
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

    def load(
        self, tender_id: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]], np.ndarray] | None:
        tender_dir = self._get_tender_dir(tender_id)
        manifest_path = tender_dir / "manifest.json"
        chunks_path = tender_dir / "chunks.jsonl"
        vectors_path = tender_dir / "vectors.npy"

        if not (manifest_path.exists() and chunks_path.exists() and vectors_path.exists()):
            return None

        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)

            chunks = []
            with open(chunks_path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        chunks.append(json.loads(line))

            vectors = np.load(vectors_path)

            if len(chunks) != vectors.shape[0] or len(chunks) != manifest.get("chunk_count", 0):
                logger.warning(f"Cache mismatch for {tender_id}")
                return None

            return manifest, chunks, vectors

        except Exception as e:
            logger.error(f"Failed to load cache for {tender_id}: {e}")
            return None
