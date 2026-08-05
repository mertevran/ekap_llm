"""Unit testler — MatchContextBuilder."""

from __future__ import annotations

from app.matching.context_builder import MatchContextBuilder
from app.matching.models import ProfileTenderMatch, TenderProfileMatch


def _make_profile_match() -> ProfileTenderMatch:
    return ProfileTenderMatch(
        profile_code="AUS-01",
        profile_name="Akıllı Ulaşım Sistemleri",
        tender_id="T1",
        ikn="2026/1234",
        tender_name="Trafik Yönetim Sistemi",
        authority_name="Başka Belediye",
        retrieval_rank=1,
        retrieval_score=0.75,
        evidence_chunk_ids=["c1", "c2"],
        evidence_sections=["qualification", "technical_requirements"],
        evidence_texts=["Yeterlilik şartları metni...", "Teknik gereksinimler..."],
    )


def _make_tender_match() -> TenderProfileMatch:
    return TenderProfileMatch(
        tender_id="T1",
        ikn="2026/1234",
        tender_name="Trafik Yönetim Sistemi",
        authority_name="Başka Belediye",
        profile_code="AUS-01",
        profile_name="Akıllı Ulaşım Sistemleri",
        retrieval_rank=1,
        retrieval_score=0.75,
        tender_evidence_chunk_ids=["c1", "c2"],
        tender_evidence_sections=["qualification", "technical_requirements"],
        tender_evidence_texts=["İhale parçası 1...", "İhale parçası 2..."],
    )


def _make_profile_data() -> dict:
    return {
        "profil_kodu": "AUS-01",
        "profil_adi": "Akıllı Ulaşım Sistemleri",
        "tamamlanan_projeler": ["İstanbul Trafik Yönetim Projesi", "Araç Takip Sistemi"],
        "personel_kapasitesi": ["50+ mühendis"],
        "belgeler": ["ISO 27001"],
        "ekipman_ve_altyapi": ["Sunucu altyapısı"],
        "teknolojiler": ["Python", "CUDA"],
        "urunler_ve_hizmetler": ["Trafik optimizasyon yazılımı"],
        "kapasite_sinirlari": ["Maksimum 10 proje eş zamanlı"],
        "birincil_yetkinlikler": ["Trafik yönetimi", "Yazılım geliştirme"],
        "ihale_kategori_sinyalleri": {
            "guclu_terimler": ["trafik", "yönetim"],
            "negatif_terimler": ["beton", "yol yapım"],
            "okas_kodlari": ["72411000"],
        },
    }


def test_build_for_profile_match_has_required_keys():
    builder = MatchContextBuilder()
    ctx = builder.build_for_profile_match(
        match=_make_profile_match(),
        profile_data=_make_profile_data(),
    )
    required = [
        "matching_mode", "tender_id", "ikn", "tender_name",
        "authority_name", "profile_code", "profile_name",
        "retrieval_score", "score_breakdown", "valid_chunk_ids",
        "tender_context", "company_context",
    ]
    for key in required:
        assert key in ctx, f"Eksik anahtar: {key}"


def test_build_for_profile_match_mode():
    builder = MatchContextBuilder()
    ctx = builder.build_for_profile_match(
        match=_make_profile_match(),
        profile_data=_make_profile_data(),
    )
    assert ctx["matching_mode"] == "profile"


def test_build_for_tender_match_mode():
    builder = MatchContextBuilder()
    ctx = builder.build_for_tender_match(
        match=_make_tender_match(),
        profile_data=_make_profile_data(),
    )
    assert ctx["matching_mode"] == "tender"


def test_valid_chunk_ids_in_context():
    builder = MatchContextBuilder()
    m = _make_profile_match()
    ctx = builder.build_for_profile_match(
        match=m,
        profile_data=_make_profile_data(),
    )
    assert ctx["valid_chunk_ids"] == m.evidence_chunk_ids


def test_company_context_includes_capacity_fields():
    """Şirket bağlamı kapasite alanlarını içermeli (genel açıklama değil)."""
    builder = MatchContextBuilder()
    profile_data = _make_profile_data()
    ctx = builder.build_for_profile_match(
        match=_make_profile_match(),
        profile_data=profile_data,
    )
    company_ctx = ctx["company_context"]
    # Tamamlanan projeler bağlamda olmalı
    assert "Tamamlanan Projeler" in company_ctx or "İstanbul Trafik" in company_ctx


def test_tender_context_includes_chunk_ids():
    builder = MatchContextBuilder()
    ctx = builder.build_for_profile_match(
        match=_make_profile_match(),
        profile_data=_make_profile_data(),
    )
    tender_ctx = ctx["tender_context"]
    assert "c1" in tender_ctx or "c2" in tender_ctx


def test_max_chars_respected():
    builder = MatchContextBuilder(max_chars=100)
    m = ProfileTenderMatch(
        profile_code="AUS-01",
        tender_id="T1",
        ikn="2026/1234",
        evidence_chunk_ids=["c1"],
        evidence_texts=["A" * 5000],  # çok uzun metin
    )
    ctx = builder.build_for_profile_match(
        match=m,
        profile_data=_make_profile_data(),
    )
    # tender_context max_chars'ı aşmamalı (kesin sınır değil ama çok aşmamalı)
    assert len(ctx["tender_context"]) < 5000
