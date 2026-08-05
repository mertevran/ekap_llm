from __future__ import annotations

import logging
from collections.abc import Sequence

import numpy as np
from sentence_transformers import SentenceTransformer

from app.config import get_settings

logger = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(
        self,
        model_name: str | None = None,
        device: str | None = None,
        batch_size: int | None = None,
        normalize_embeddings: bool | None = None,
    ) -> None:
        settings = get_settings()

        self.model_name = model_name or settings.embedding_model
        self.device = device or settings.embedding_device
        self.batch_size = batch_size or settings.embedding_batch_size

        if normalize_embeddings is None:
            self.normalize_embeddings = settings.embedding_normalize
        else:
            self.normalize_embeddings = normalize_embeddings

        self.cache_folder = str(settings.model_cache_path)
        self._model: SentenceTransformer | None = None
        self._vector_size: int | None = None

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            logger.info(
                "Gömme modeli yükleniyor: %s | cihaz=%s",
                self.model_name,
                self.device,
            )

            self._model = SentenceTransformer(
                self.model_name,
                device=self.device,
                cache_folder=self.cache_folder,
            )

            dimension = self._model.get_sentence_embedding_dimension()

            if dimension is None:
                raise RuntimeError("Gömme modelinin vektör boyutu belirlenemedi.")

            self._vector_size = int(dimension)

            logger.info(
                "Gömme modeli hazır: %s boyut=%s",
                self.model_name,
                self._vector_size,
            )

        return self._model

    @property
    def vector_size(self) -> int:
        if self._vector_size is None:
            _ = self.model

        if self._vector_size is None:
            raise RuntimeError("Vektör boyutu belirlenemedi.")

        return self._vector_size

    def encode_texts(
        self,
        texts: Sequence[str],
    ) -> list[list[float]]:
        cleaned_texts = [text.strip() for text in texts if text and text.strip()]

        if not cleaned_texts:
            return []

        vectors = self.model.encode(
            cleaned_texts,
            batch_size=self.batch_size,
            show_progress_bar=len(cleaned_texts) > self.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=self.normalize_embeddings,
        )

        array = np.asarray(vectors, dtype=np.float32)

        if array.ndim == 1:
            array = array.reshape(1, -1)

        if array.shape[1] != self.vector_size:
            raise RuntimeError(
                "Üretilen vektör boyutu model boyutuyla uyuşmuyor: "
                f"{array.shape[1]} != {self.vector_size}"
            )

        return array.tolist()

    def encode_query(self, query: str) -> list[float]:
        vectors = self.encode_texts([query])

        if not vectors:
            raise ValueError("Sorgu metni boş olamaz.")

        return vectors[0]
