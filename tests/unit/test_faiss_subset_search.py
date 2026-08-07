"""SQL Encoder seçiminin FAISS aday uzayını gerçekten sınırladığını doğrular."""

from __future__ import annotations

from types import SimpleNamespace

from app.retrieval.profile_vector_tender_matcher import ProfileVectorTenderMatcher
from app.vector_store.faiss_store import FaissVectorStore, VectorRecord


def test_search_subset_never_returns_an_unselected_tender(tmp_path) -> None:
    store = FaissVectorStore(path=tmp_path, collection_name="subset")
    store.ensure_collection(vector_size=3, recreate=True)
    store.upsert_records(
        [
            VectorRecord(
                point_id="selected",
                vector=[0.0, 1.0, 0.0],
                payload={"ikn": "2026/SELECTED"},
            ),
            VectorRecord(
                point_id="forbidden-best-score",
                vector=[1.0, 0.0, 0.0],
                payload={"ikn": "2026/FORBIDDEN"},
            ),
        ]
    )
    selected_id = store.uuid_to_id["selected"]

    results = store.search_subset(
        query_vector=[1.0, 0.0, 0.0],
        allowed_ids=[selected_id],
        limit=10,
    )

    assert [item["payload"]["ikn"] for item in results] == ["2026/SELECTED"]
    assert results[0]["score"] == 0.0


def test_search_subset_validates_vector_dimension(tmp_path) -> None:
    store = FaissVectorStore(path=tmp_path, collection_name="subset-dimension")
    store.ensure_collection(vector_size=3, recreate=True)
    store.upsert_records(
        [VectorRecord(point_id="one", vector=[1.0, 0.0, 0.0], payload={})]
    )

    try:
        store.search_subset(
            query_vector=[1.0, 0.0],
            allowed_ids=[store.uuid_to_id["one"]],
            limit=1,
        )
    except ValueError as exc:
        assert "boyutu" in str(exc)
    else:
        raise AssertionError("Vektör boyutu uyuşmazlığı reddedilmeliydi.")


def test_profile_matcher_keeps_sql_selection_even_below_global_threshold(
    tmp_path,
) -> None:
    tender_store = FaissVectorStore(path=tmp_path / "tenders", collection_name="t")
    profile_store = FaissVectorStore(path=tmp_path / "profiles", collection_name="p")
    tender_store.ensure_collection(vector_size=3, recreate=True)
    profile_store.ensure_collection(vector_size=3, recreate=True)
    tender_store.upsert_records(
        [
            VectorRecord(
                point_id="selected",
                vector=[0.0, 1.0, 0.0],
                payload={
                    "tender_id": "selected",
                    "ikn": "2026/SELECTED",
                    "chunk_id": "selected:main",
                    "section_id": "main",
                    "section_type": "main",
                    "title": "Temizlik malzemesi",
                    "text": "Genel malzeme alımı",
                },
            ),
            VectorRecord(
                point_id="not-selected",
                vector=[1.0, 0.0, 0.0],
                payload={
                    "tender_id": "not-selected",
                    "ikn": "2026/NOT-SELECTED",
                    "chunk_id": "not-selected:main",
                    "section_id": "main",
                    "section_type": "main",
                    "title": "Yazılım sistemi",
                    "text": "Akıllı ulaşım yazılımı",
                },
            ),
        ]
    )
    profile_store.upsert_records(
        [
            VectorRecord(
                point_id="profile-yaz",
                vector=[1.0, 0.0, 0.0],
                payload={
                    "profile_code": "YAZ-01",
                    "text": "Akıllı ulaşım yazılımı",
                    "section_order": 1,
                },
            )
        ]
    )
    settings = SimpleNamespace(
        faiss_search_top_k=10,
        faiss_max_chunks_per_tender=3,
        faiss_max_tenders_per_profile=5,
        minimum_final_score=0.95,
        weight_max_chunk=0.55,
        weight_top_chunks=0.20,
        weight_section_diversity=0.10,
        weight_okas=0.10,
        weight_title=0.05,
    )
    matcher = ProfileVectorTenderMatcher(
        tender_vector_store=tender_store,
        profile_vector_store=profile_store,
        settings=settings,
    )

    results = matcher.retrieve_profile(
        profile_code="YAZ-01",
        allowed_ikns={"2026/SELECTED"},
        limit=5,
    )

    assert [result.ikn for result in results] == ["2026/SELECTED"]
    assert results[0].scores.final < settings.minimum_final_score
