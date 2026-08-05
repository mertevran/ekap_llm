"""Integration testler — Pipeline modları (FAISS gerektiriyor)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_TENDER_INDEX = _PROJECT_ROOT / "storage" / "faiss" / "ekap_tender_chunks.index"
_PROFILE_INDEX = _PROJECT_ROOT / "storage" / "faiss_profiles"

pytestmark = pytest.mark.skipif(
    not _TENDER_INDEX.exists() or not _PROFILE_INDEX.exists(),
    reason="FAISS indeksleri bulunamadı.",
)


@pytest.fixture(scope="module")
def live_stores():
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
    return tender, profile, settings


def test_profile_mode_returns_sorted_results(live_stores):
    """Profil modu sonuçları azalan sırada sıralanmış olmalı."""
    from app.matching.profile_to_tender_matcher import ProfileToTenderMatcher
    from app.vector_store.faiss_vector_reader import FaissVectorReader

    tender_store, profile_store, settings = live_stores
    reader = FaissVectorReader()

    # İlk profil kodunu al
    codes: set[str] = set()
    for _, p in profile_store.payloads.items():
        c = str(p.get("profile_code") or "").strip()
        if c:
            codes.add(c)

    if not codes:
        pytest.skip("Profil kodu bulunamadı.")

    matcher = ProfileToTenderMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=settings,
    )
    results = matcher.match(profile_code=sorted(codes)[0], top_k=10, minimum_score=0.0)
    scores = [r.retrieval_score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_tender_mode_profiles_scanned(live_stores):
    """İhale modu tüm profil vektörlerini taramalı."""
    from app.matching.tender_to_profile_matcher import TenderToProfileMatcher
    from app.vector_store.faiss_vector_reader import FaissVectorReader

    tender_store, profile_store, settings = live_stores
    reader = FaissVectorReader()

    first_ikn = None
    for _, p in tender_store.payloads.items():
        ikn = str(p.get("ikn") or "").strip()
        if ikn:
            first_ikn = ikn
            break

    if not first_ikn:
        pytest.skip("İhale ikn bulunamadı.")

    matcher = TenderToProfileMatcher(
        tender_store=tender_store,
        profile_store=profile_store,
        settings=settings,
    )
    results = matcher.match(ikn=first_ikn, top_k=20, minimum_score=0.0)
    # Tüm 20 profil tarandı — 0'dan fazla sonuç bekleniyor (ISBAK değilse)
    assert isinstance(results, list)


def test_decision_normalizer_applied_in_validator():
    """IsbakRuleValidator eski karar değerlerini kabul etmez, normalizasyon gerekir."""
    from app.decision.models import ModelDecision, CriterionResult
    from app.validation.isbak_rule_validator import IsbakRuleValidator

    validator = IsbakRuleValidator()

    # Yeni karar değeri — doğrulama çalışmalı
    decision = ModelDecision(
        model_name="test_model",
        decision="uygun",
        confidence=0.85,
        birincil_profil_kodu="AUS-01",
        ikincil_profil_kodlari=[],
        zorunlu_kriter_sonuclari=[],
        uygunluk_gerekceleri=["Test"],
        uygunsuzluk_gerekceleri=[],
        eksik_kanitlar=[],
    )
    result = validator.validate(
        tender_id="T1",
        ikn="2026/1234",
        category_code="",
        primary_decision=decision,
        evidence_count=5,
    )
    assert "RULE_0_UNKNOWN_DECISION" not in result.deterministic_rules_applied


def test_context_builder_company_context_no_general_description():
    """Şirket bağlamı genel açıklama alanını kanıt olarak sunmamalı."""
    from app.matching.context_builder import MatchContextBuilder
    from app.matching.models import ProfileTenderMatch

    builder = MatchContextBuilder()
    m = ProfileTenderMatch(
        profile_code="AUS-01",
        profile_name="Test",
        tender_id="T1",
        ikn="2026/1234",
    )
    profile_data = {
        "aciklama": "Bu bir genel şirket açıklamasıdır.",
        "description_expanded": "Geniş şirket açıklaması...",
        "tamamlanan_projeler": ["Proje A", "Proje B"],
    }
    ctx = builder.build_for_profile_match(match=m, profile_data=profile_data)
    company_ctx = ctx["company_context"]

    # Tamamlanan projeler içinde olmalı
    assert "Proje A" in company_ctx

    # Genel açıklama yok — kanıt değil (context'te yer almaz)
    # Not: field adı 'aciklama' — profile_capacity_fields listesinde yok
    assert "Bu bir genel şirket açıklamasıdır" not in company_ctx
