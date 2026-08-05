from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from sentence_transformers import SentenceTransformer

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
DEFAULT_MODEL_CACHE = Path("storage/model_cache")


class BgeM3Embedder:
    """BGE-M3 ile normalize edilmiş yoğun gömme vektörü üretir."""

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        device: str = "cpu",
        cache_folder: str | Path = DEFAULT_MODEL_CACHE,
        batch_size: int = 8,
        show_progress_bar: bool = False,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size pozitif olmalıdır.")

        self.model_name = model_name
        self.device = device
        self.cache_folder = Path(cache_folder)
        self.cache_folder.mkdir(parents=True, exist_ok=True)
        self.batch_size = batch_size
        self.show_progress_bar = show_progress_bar

        self.model = SentenceTransformer(
            model_name,
            device=device,
            cache_folder=str(self.cache_folder),
        )
        self._vector_size: int | None = None

    @property
    def vector_size(self) -> int:
        if self._vector_size is None:
            probe = self.embed(["vektör boyutu denetimi"])
            self._vector_size = len(probe[0])
        return self._vector_size

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        clean_texts = [str(text).strip() for text in texts]

        if not clean_texts:
            return []

        vectors = self.model.encode(
            clean_texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=self.show_progress_bar,
            convert_to_numpy=True,
        )

        result = vectors.tolist()

        if self._vector_size is None and result:
            self._vector_size = len(result[0])

        return result
