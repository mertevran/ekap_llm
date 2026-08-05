from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient
from qdrant_client.http import models

DEFAULT_QDRANT_PATH = Path("storage/qdrant")
DEFAULT_COLLECTION_NAME = "ekap_tender_chunks"


@dataclass(frozen=True)
class VectorRecord:
    point_id: str
    vector: list[float]
    payload: dict[str, Any]


class QdrantVectorStore:
    """Qdrant yerel kipinde vektör kayıtlarını yönetir."""

    def __init__(
        self,
        *,
        path: str | Path = DEFAULT_QDRANT_PATH,
        collection_name: str = DEFAULT_COLLECTION_NAME,
    ) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name
        self.client = QdrantClient(path=str(self.path))

    def close(self) -> None:
        close_method = getattr(self.client, "close", None)
        if callable(close_method):
            close_method()

    def collection_exists(self) -> bool:
        return self.client.collection_exists(self.collection_name)

    def ensure_collection(
        self,
        *,
        vector_size: int,
        recreate: bool = False,
    ) -> None:
        if vector_size <= 0:
            raise ValueError("vector_size pozitif olmalıdır.")

        exists = self.collection_exists()

        if exists and recreate:
            self.client.delete_collection(self.collection_name)
            exists = False

        if not exists:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=vector_size,
                    distance=models.Distance.COSINE,
                ),
            )
            return

        collection = self.client.get_collection(self.collection_name)
        vectors_config = collection.config.params.vectors

        current_size = getattr(vectors_config, "size", None)
        if current_size is None and isinstance(vectors_config, dict):
            first_config = next(iter(vectors_config.values()), None)
            current_size = getattr(first_config, "size", None)

        if current_size is not None and int(current_size) != vector_size:
            raise ValueError(
                "Koleksiyon vektör boyutu uyuşmuyor: "
                f"mevcut={current_size}, beklenen={vector_size}. "
                "Koleksiyonu yeniden oluşturmak için --recreate kullanın."
            )

    def upsert_records(
        self,
        records: Iterable[VectorRecord],
        *,
        batch_size: int = 64,
    ) -> int:
        if batch_size <= 0:
            raise ValueError("batch_size pozitif olmalıdır.")

        total = 0
        batch: list[models.PointStruct] = []

        for record in records:
            batch.append(
                models.PointStruct(
                    id=record.point_id,
                    vector=record.vector,
                    payload=record.payload,
                )
            )

            if len(batch) >= batch_size:
                self.client.upsert(
                    collection_name=self.collection_name,
                    points=batch,
                    wait=True,
                )
                total += len(batch)
                batch.clear()

        if batch:
            self.client.upsert(
                collection_name=self.collection_name,
                points=batch,
                wait=True,
            )
            total += len(batch)

        return total

    def delete_by_tender_id(
        self,
        tender_id: str,
    ) -> None:
        normalized_tender_id = str(tender_id).strip()

        if not normalized_tender_id:
            raise ValueError("tender_id boş olamaz.")

        if not self.collection_exists():
            return

        self.client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="tender_id",
                            match=models.MatchValue(
                                value=normalized_tender_id,
                            ),
                        )
                    ]
                )
            ),
            wait=True,
        )

    def count(self) -> int:
        result = self.client.count(
            collection_name=self.collection_name,
            exact=True,
        )
        return int(result.count)

    def scroll(
        self,
        *,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            raise ValueError("limit pozitif olmalıdır.")

        points, _ = self.client.scroll(
            collection_name=self.collection_name,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )

        return [
            {
                "id": str(point.id),
                "payload": dict(point.payload or {}),
            }
            for point in points
        ]

    def search(
        self,
        *,
        query_vector: list[float],
        limit: int = 5,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            raise ValueError("limit pozitif olmalıdır.")

        if hasattr(self.client, "query_points"):
            response = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                limit=limit,
                score_threshold=score_threshold,
                with_payload=True,
                with_vectors=False,
            )
            points = response.points
        else:
            points = self.client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                limit=limit,
                score_threshold=score_threshold,
                with_payload=True,
                with_vectors=False,
            )

        return [
            {
                "id": str(point.id),
                "score": float(point.score),
                "payload": dict(point.payload or {}),
            }
            for point in points
        ]


def deterministic_point_id(*parts: object) -> str:
    normalized = "|".join(str(part) for part in parts if part is not None)
    if not normalized:
        raise ValueError("Kimlik üretmek için en az bir değer gereklidir.")
    return str(uuid5(NAMESPACE_URL, normalized))
