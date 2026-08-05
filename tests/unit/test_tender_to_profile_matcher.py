"""Unit testler — TenderToProfileMatcher (mock store)."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest


def _make_tender_store(ikn: str = "2026/1234", n_chunks: int = 3) -> MagicMock:
    """Mock ihale store: ikn ile birden fazla parça."""
    import faiss

    dim = 4
    base = faiss.IndexFlatIP(dim)
    idx = faiss.IndexIDMap(base)

    payloads = {}
    for i in range(1, n_chunks + 1):
        vec = np.random.rand(dim).astype(np.float32)
        faiss.normalize_L2(vec.reshape(1, -1))
        ids_arr = np.array([i], dtype=np.int64)
        idx.add_with_ids(vec.reshape(1, -1), ids_arr)
        payloads[i] = {
            "tender_id": "T_TEST",
            "ikn": ikn,
            "chunk_id": f"tc{i}",
            "title": f"İhale Parçası {i}",
            "text": f"İhale metni {i}",
            "section_type": "qualification",
            "authority_name": "Dış Belediye",
        }

    store = MagicMock()
    store.index = idx
    store.payloads = payloads
    store.collection_exists.return_value = True
    store.count.return_value = n_chunks
    return store


def _make_profile_store(profile_codes: list[str] = None) -> MagicMock:
    """Mock profil store."""
    if profile_codes is None:
        profile_codes = ["AUS-01", "AUS-02"]

    store = MagicMock()
    store.collection_exists.return_value = True
    store.count.return_value = len(profile_codes) * 4

    def _search(*, query_vector, limit, score_threshold=None):
        results = []
        for i, code in enumerate(profile_codes):
            # Her profil için birkaç parça
            for j in range(1, 3):
                results.append({
                    "id": str(i * 10 + j),
                    "score": 0.85 - i * 0.05 - j * 0.01,
                    "payload": {
                        "profile_code": code,
                        "chunk_id": f"pc{i}{j}",
                        "section": f"section_{j}",
                        "text": f"Profil metni {code} {j}",
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
            "faiss_search_top_k": 100,
            "faiss_max_profiles_per_tender": 20,
            "faiss_max_chunks_per_tender": 4,
        }
    )


def test_match_requires_ikn_or_tender_id():
    from app.matching.tender_to_profile_matcher import TenderToProfileMatcher

    tender_store = _make_tender_store()
    profile_store = _make_profile_store()
    matcher = TenderToProfileMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=_make_settings(),
    )
    with pytest.raises(ValueError):
        matcher.match()  # ikn ve tender_id yok


def test_match_tender_not_found_raises():
    from app.matching.tender_to_profile_matcher import TenderToProfileMatcher

    tender_store = _make_tender_store("2026/1234")
    profile_store = _make_profile_store()
    matcher = TenderToProfileMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=_make_settings(),
    )
    with pytest.raises(KeyError):
        matcher.match(ikn="2026/NONEXISTENT")


def test_match_returns_list_of_profiles():
    from app.matching.tender_to_profile_matcher import TenderToProfileMatcher

    tender_store = _make_tender_store("2026/1234")
    profile_store = _make_profile_store(["AUS-01", "AUS-02", "AUS-03"])
    matcher = TenderToProfileMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=_make_settings(0.0),
    )
    results = matcher.match(ikn="2026/1234", top_k=5)
    assert isinstance(results, list)


def test_match_top_k_limit():
    from app.matching.tender_to_profile_matcher import TenderToProfileMatcher

    tender_store = _make_tender_store("2026/1234")
    profile_store = _make_profile_store(["AUS-01", "AUS-02", "AUS-03", "AUS-04"])
    matcher = TenderToProfileMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=_make_settings(0.0),
    )
    results = matcher.match(ikn="2026/1234", top_k=2)
    assert len(results) <= 2


def test_match_no_duplicate_profiles():
    """Aynı profil kodu birden fazla kez dönemez."""
    from app.matching.tender_to_profile_matcher import TenderToProfileMatcher

    tender_store = _make_tender_store("2026/1234", n_chunks=3)
    profile_store = _make_profile_store(["AUS-01", "AUS-02"])
    matcher = TenderToProfileMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=_make_settings(0.0),
    )
    results = matcher.match(ikn="2026/1234", top_k=10)
    codes = [r.profile_code for r in results]
    assert len(codes) == len(set(codes)), "Aynı profil kodu tekrar geldi!"


def test_match_sorted_desc():
    from app.matching.tender_to_profile_matcher import TenderToProfileMatcher

    tender_store = _make_tender_store("2026/1234")
    profile_store = _make_profile_store(["AUS-01", "AUS-02", "AUS-03"])
    matcher = TenderToProfileMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=_make_settings(0.0),
    )
    results = matcher.match(ikn="2026/1234", top_k=5)
    scores = [r.retrieval_score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_minimum_score_filter():
    from app.matching.tender_to_profile_matcher import TenderToProfileMatcher

    tender_store = _make_tender_store("2026/1234")
    profile_store = _make_profile_store(["AUS-01"])
    matcher = TenderToProfileMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=_make_settings(0.0),
    )
    results = matcher.match(ikn="2026/1234", top_k=5, minimum_score=0.999)
    assert len(results) == 0


def test_multiple_profiles_can_match():
    """Bir ihale birden fazla profile uyabilir — tek profil döndürme."""
    from app.matching.tender_to_profile_matcher import TenderToProfileMatcher

    tender_store = _make_tender_store("2026/1234")
    profile_store = _make_profile_store(["AUS-01", "AUS-02", "AUS-03"])
    matcher = TenderToProfileMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=_make_settings(0.0),
    )
    results = matcher.match(ikn="2026/1234", top_k=5)
    # Birden fazla profil dönebilir
    assert len(results) >= 1


def test_rank_assigned_correctly():
    from app.matching.tender_to_profile_matcher import TenderToProfileMatcher

    tender_store = _make_tender_store("2026/1234")
    profile_store = _make_profile_store(["AUS-01", "AUS-02"])
    matcher = TenderToProfileMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=_make_settings(0.0),
    )
    results = matcher.match(ikn="2026/1234", top_k=5)
    for i, r in enumerate(results, 1):
        assert r.retrieval_rank == i
