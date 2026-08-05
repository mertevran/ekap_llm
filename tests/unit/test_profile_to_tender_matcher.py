"""Unit testler — ProfileToTenderMatcher (mock store)."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest


def _make_profile_store(profile_code: str = "AUS-01", n_chunks: int = 2) -> MagicMock:
    """Mock profil store: profile_code ile birden fazla parça."""
    import faiss

    dim = 4
    base = faiss.IndexFlatIP(dim)
    idx = faiss.IndexIDMap(base)

    payloads = {}
    for i in range(1, n_chunks + 1):
        vec = np.random.rand(dim).astype(np.float32)
        faiss.normalize_L2(vec.reshape(1, -1))
        ids = np.array([i], dtype=np.int64)
        idx.add_with_ids(vec.reshape(1, -1), ids)
        payloads[i] = {
            "profile_code": profile_code,
            "section": f"section_{i}",
            "section_order": i,
            "text": f"Profil metni {i}",
        }

    store = MagicMock()
    store.index = idx
    store.payloads = payloads
    store.collection_exists.return_value = True
    store.count.return_value = n_chunks
    store.search.return_value = []
    return store


def _make_tender_store(n_tenders: int = 3) -> MagicMock:
    """Mock ihale store."""
    store = MagicMock()
    store.collection_exists.return_value = True
    store.count.return_value = n_tenders * 2

    # Her aramada birkaç farklı ihale döndür
    def _search(*, query_vector, limit, score_threshold=None):
        results = []
        for i in range(1, n_tenders + 1):
            results.append({
                "id": str(i * 10),
                "score": 0.9 - i * 0.05,
                "payload": {
                    "tender_id": f"T{i}",
                    "ikn": f"2026/{1000 + i}",
                    "chunk_id": f"tc{i}",
                    "title": f"İhale {i}",
                    "text": f"İhale metni {i}",
                    "section_type": "announcement",
                    "authority_name": "Başka Belediye",
                    "idare_adi": "Başka Belediye",
                },
            })
        return results

    store.search.side_effect = _search
    return store


def _make_settings(min_score: float = 0.0):
    from app.config.isbak_rag_settings import IsbakRagSettings
    return IsbakRagSettings(
        **{
            "minimum_final_score": min_score,
            "faiss_search_top_k": 10,
            "faiss_max_tenders_per_profile": 30,
            "faiss_max_chunks_per_tender": 4,
        }
    )


def test_match_returns_list():
    from app.matching.profile_to_tender_matcher import ProfileToTenderMatcher

    profile_store = _make_profile_store("AUS-01", 2)
    tender_store = _make_tender_store(3)
    settings = _make_settings(0.0)

    matcher = ProfileToTenderMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=settings,
    )
    results = matcher.match(profile_code="AUS-01", top_k=5)
    assert isinstance(results, list)


def test_match_profile_not_found_raises():
    from app.matching.profile_to_tender_matcher import ProfileToTenderMatcher

    profile_store = _make_profile_store("AUS-01", 2)
    tender_store = _make_tender_store(1)
    settings = _make_settings(0.0)

    matcher = ProfileToTenderMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=settings,
    )
    with pytest.raises(KeyError):
        matcher.match(profile_code="NONEXISTENT", top_k=5)


def test_match_top_k_limit():
    from app.matching.profile_to_tender_matcher import ProfileToTenderMatcher

    profile_store = _make_profile_store("AUS-01", 2)
    tender_store = _make_tender_store(5)
    settings = _make_settings(0.0)

    matcher = ProfileToTenderMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=settings,
    )
    results = matcher.match(profile_code="AUS-01", top_k=2)
    assert len(results) <= 2


def test_match_sorted_by_score_desc():
    from app.matching.profile_to_tender_matcher import ProfileToTenderMatcher

    profile_store = _make_profile_store("AUS-01", 1)
    tender_store = _make_tender_store(3)
    settings = _make_settings(0.0)

    matcher = ProfileToTenderMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=settings,
    )
    results = matcher.match(profile_code="AUS-01", top_k=5)
    scores = [r.retrieval_score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_match_deduplicates_tenders():
    """Aynı ihale iki kez gelmemeli."""
    from app.matching.profile_to_tender_matcher import ProfileToTenderMatcher

    profile_store = _make_profile_store("AUS-01", 2)
    tender_store = _make_tender_store(2)
    settings = _make_settings(0.0)

    matcher = ProfileToTenderMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=settings,
    )
    results = matcher.match(profile_code="AUS-01", top_k=10)
    ikns = [r.ikn for r in results]
    assert len(ikns) == len(set(ikns)), "Aynı ihale tekrar geldi!"


def test_match_minimum_score_filter():
    from app.matching.profile_to_tender_matcher import ProfileToTenderMatcher

    profile_store = _make_profile_store("AUS-01", 1)
    tender_store = _make_tender_store(3)
    settings = _make_settings(0.0)

    matcher = ProfileToTenderMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=settings,
    )
    # Çok yüksek eşik — hiç sonuç dönmemeli
    results = matcher.match(profile_code="AUS-01", top_k=10, minimum_score=0.999)
    assert len(results) == 0


def test_isbak_own_tender_filtered():
    """İSBAK kendi ihaleleri filtrelenmeli."""
    from app.matching.profile_to_tender_matcher import ProfileToTenderMatcher

    profile_store = _make_profile_store("AUS-01", 1)
    tender_store = MagicMock()
    tender_store.collection_exists.return_value = True
    tender_store.count.return_value = 1
    tender_store.search.return_value = [
        {
            "score": 0.95,
            "payload": {
                "tender_id": "T_ISBAK",
                "ikn": "2026/ISBAK",
                "chunk_id": "tc_isbak",
                "title": "İSBAK İhalesi",
                "text": "isbak metni",
                "section_type": "announcement",
                "authority_name": "İSBAK A.Ş.",
                "idare_adi": "ISBAK",
            },
        }
    ]

    settings = _make_settings(0.0)
    matcher = ProfileToTenderMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=settings,
    )
    results = matcher.match(profile_code="AUS-01", top_k=10)
    assert all(r.ikn != "2026/ISBAK" for r in results)


def test_rank_assigned():
    from app.matching.profile_to_tender_matcher import ProfileToTenderMatcher

    profile_store = _make_profile_store("AUS-01", 1)
    tender_store = _make_tender_store(3)
    settings = _make_settings(0.0)

    matcher = ProfileToTenderMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=settings,
    )
    results = matcher.match(profile_code="AUS-01", top_k=3)
    ranks = [r.retrieval_rank for r in results]
    assert ranks == list(range(1, len(ranks) + 1))
