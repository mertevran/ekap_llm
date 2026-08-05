from __future__ import annotations

import pytest

pytest.importorskip("qdrant_client")

from app.vector_store.qdrant_store import (
    QdrantVectorStore,
    VectorRecord,
    deterministic_point_id,
)


def test_qdrant_local_upsert_and_search(tmp_path) -> None:
    store = QdrantVectorStore(
        path=tmp_path / "qdrant",
        collection_name="test_collection",
    )

    try:
        store.ensure_collection(
            vector_size=4,
            recreate=True,
        )

        records = [
            VectorRecord(
                point_id=deterministic_point_id("2026/1", "chunk-1"),
                vector=[1.0, 0.0, 0.0, 0.0],
                payload={
                    "ikn": "2026/1",
                    "text": "Yazılım geliştirme hizmeti",
                },
            ),
            VectorRecord(
                point_id=deterministic_point_id("2026/2", "chunk-1"),
                vector=[0.0, 1.0, 0.0, 0.0],
                payload={
                    "ikn": "2026/2",
                    "text": "İnşaat yapım işi",
                },
            ),
        ]

        inserted = store.upsert_records(records, batch_size=1)

        assert inserted == 2
        assert store.count() == 2

        results = store.search(
            query_vector=[1.0, 0.0, 0.0, 0.0],
            limit=1,
        )

        assert len(results) == 1
        assert results[0]["payload"]["ikn"] == "2026/1"
    finally:
        store.close()
