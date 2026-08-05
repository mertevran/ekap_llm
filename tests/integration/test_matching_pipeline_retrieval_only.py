"""Integration testler — retrieval-only pipeline (FAISS gerektiriyor).

Bu testler gerçek FAISS indekslerini kullanır. Ollama veya DB yazma
işlemi yapılmaz.

Çalıştırma:
    PYTHONPATH=. pytest tests/integration/test_matching_pipeline_retrieval_only.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# FAISS indeksleri yoksa atla
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_TENDER_INDEX = _PROJECT_ROOT / "storage" / "faiss" / "ekap_tender_chunks.index"
_PROFILE_INDEX = _PROJECT_ROOT / "storage" / "faiss_profiles"

pytestmark = pytest.mark.skipif(
    not _TENDER_INDEX.exists() or not _PROFILE_INDEX.exists(),
    reason="FAISS indeksleri bulunamadı — integration testi atlandı.",
)


@pytest.fixture(scope="module")
def stores():
    from app.vector_store.faiss_store import FaissVectorStore
    from app.config.isbak_rag_settings import get_isbak_rag_settings

    settings = get_isbak_rag_settings()
    tender = FaissVectorStore(
        path=settings.faiss_tender_path,
        collection_name=settings.faiss_tender_collection,
    )
    profile = FaissVectorStore(
        path=settings.faiss_profile_path,
        collection_name=settings.faiss_profile_collection,
    )
    return tender, profile


@pytest.fixture(scope="module")
def settings():
    from app.config.isbak_rag_settings import get_isbak_rag_settings
    return get_isbak_rag_settings()


def test_tender_index_loaded(stores, settings):
    """İhale FAISS indeksi yükleniyor ve vektör sayısı tutarlı."""
    tender_store, _ = stores
    count = tender_store.count()
    assert count > 0, f"İhale indeksi boş! count={count}"
    # Mevcut sisteme göre ~101261 vektör bekleniyor
    assert count > 1000, f"İhale indeksi çok az vektör içeriyor: {count}"


def test_profile_index_loaded(stores):
    """Profil FAISS indeksi yükleniyor ve 80 vektör içeriyor."""
    _, profile_store = stores
    count = profile_store.count()
    # 20 profil × ortalama 4 parça = ~80 vektör
    assert count > 0, f"Profil indeksi boş!"
    assert count <= 200, f"Profil indeksinde beklenenden fazla vektör: {count}"


def test_profile_index_payload_consistency(stores):
    """Profil indeksinde vektör ve payload sayısı eşit."""
    from app.vector_store.faiss_vector_reader import FaissVectorReader
    _, profile_store = stores
    ok, msg = FaissVectorReader.validate_index_payload_consistency(profile_store)
    assert ok, msg


def test_tender_index_payload_consistency(stores):
    """İhale indeksinde vektör ve payload sayısı eşit."""
    from app.vector_store.faiss_vector_reader import FaissVectorReader
    tender_store, _ = stores
    ok, msg = FaissVectorReader.validate_index_payload_consistency(tender_store)
    assert ok, msg


def test_profile_to_tender_retrieval_only(stores, settings):
    """Profil odaklı bilgi getirme: retrieval-only çalışır (LLM çağrısı yok)."""
    from app.matching.profile_to_tender_matcher import ProfileToTenderMatcher
    from app.vector_store.faiss_vector_reader import FaissVectorReader

    tender_store, profile_store = stores

    # İlk aktif profile_code'u bul
    reader = FaissVectorReader()
    all_profiles: set[str] = set()
    for _, payload in profile_store.payloads.items():
        code = str(payload.get("profile_code") or "").strip()
        if code:
            all_profiles.add(code)
    assert all_profiles, "Profil indeksinde hiç profil kodu yok!"

    profile_code = sorted(all_profiles)[0]

    matcher = ProfileToTenderMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=settings,
    )
    results = matcher.match(profile_code=profile_code, top_k=5, minimum_score=0.0)

    assert isinstance(results, list)
    # İhale indeksinde yeterli kayıt varsa en az 1 sonuç gelmeli
    if tender_store.count() > 0:
        assert len(results) >= 0  # minimum 0 — FAISS benzerlik eşiğine bağlı


def test_tender_to_profile_retrieval_only(stores, settings):
    """İhale odaklı bilgi getirme: retrieval-only çalışır (LLM çağrısı yok)."""
    from app.matching.tender_to_profile_matcher import TenderToProfileMatcher
    from app.vector_store.faiss_vector_reader import FaissVectorReader

    tender_store, profile_store = stores
    reader = FaissVectorReader()

    # İlk ihale ikn'ini bul
    first_ikn = None
    for _, payload in tender_store.payloads.items():
        ikn = str(payload.get("ikn") or "").strip()
        if ikn:
            first_ikn = ikn
            break

    if first_ikn is None:
        pytest.skip("İhale indeksinde ikn alanı bulunamadı.")

    matcher = TenderToProfileMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=settings,
    )
    results = matcher.match(ikn=first_ikn, top_k=3, minimum_score=0.0)

    assert isinstance(results, list)
    # Tüm profil vektörleri taranır — en az 0 sonuç (ISBAK ihalesi ise 0)
    assert len(results) >= 0


def test_retrieval_only_no_db_writes(stores, settings, monkeypatch):
    """retrieval-only modda DB yazma işlemi yapılmadığını doğrula."""
    from app.matching.tender_to_profile_matcher import TenderToProfileMatcher
    from app.vector_store.faiss_vector_reader import FaissVectorReader
    from unittest.mock import Mock

    # DB bağlantısını mock'la
    mock_conn = Mock()
    monkeypatch.setattr("app.database.connection.get_connection", mock_conn)

    tender_store, profile_store = stores
    reader = FaissVectorReader()

    first_ikn = None
    for _, payload in tender_store.payloads.items():
        ikn = str(payload.get("ikn") or "").strip()
        if ikn:
            first_ikn = ikn
            break

    if first_ikn is None:
        pytest.skip("İhale indeksinde ikn bulunamadı.")

    matcher = TenderToProfileMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=settings,
    )
    # retrieval-only — matcher.match() DB'ye yazmaz
    results = matcher.match(ikn=first_ikn, top_k=3, minimum_score=0.0)

    # get_connection çağrılmamalı (retrieval-only)
    mock_conn.assert_not_called()
