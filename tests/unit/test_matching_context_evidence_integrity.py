"""Unit testler — MatchContextBuilder kanıt bütünlüğü (İhale odaklı mod).

Test kapsamı:
H. Tender context gerçek ihale metnini içermeli.
I. Tender context profil metnini içermemeli.
J. Company context profil kanıtını içerebilmeli.
K. valid_chunk_ids yalnızca tender_evidence_chunk_ids içermeli.
L. Paralel kanıt listelerinin uzunlukları farklıysa ValueError oluşmalı.
M. İhale kaynak satırında chunk_id ve section_type bulunmalı.

Ek testler:
N. Profil kaynak etiketi [PROFİL KANITI] formatında olmalı.
O. Tender context [KAYNAK] etiketi ihale chunk_id içermeli.
P. build_for_profile_match geriye uyumlu çalışmalı.
"""

from __future__ import annotations

import pytest

from app.matching.context_builder import MatchContextBuilder
from app.matching.models import (
    ProfileTenderMatch,
    TenderProfileMatch,
)

# ---------------------------------------------------------------------------
# Test yardımcıları
# ---------------------------------------------------------------------------


def _make_profile_data() -> dict:
    return {
        "profil_kodu": "AUS-01",
        "profil_adi": "Akıllı Ulaşım Sistemleri",
        "tamamlanan_projeler": ["İstanbul Trafik Yönetim Projesi"],
        "personel_kapasitesi": ["50+ mühendis"],
        "belgeler": ["ISO 27001"],
        "ihale_kategori_sinyalleri": {
            "guclu_terimler": ["trafik", "yönetim"],
            "negatif_terimler": ["beton"],
            "okas_kodlari": ["72411000"],
        },
    }


def _make_tender_match(
    *,
    tender_evidence_texts: list[str] | None = None,
    tender_evidence_chunk_ids: list[str] | None = None,
    tender_evidence_sections: list[str] | None = None,
    profile_evidence_texts: list[str] | None = None,
    profile_evidence_chunk_ids: list[str] | None = None,
    profile_evidence_sections: list[str] | None = None,
) -> TenderProfileMatch:
    return TenderProfileMatch(
        tender_id="T1",
        ikn="2026/1234",
        tender_name="Trafik Yönetim Sistemi",
        authority_name="Başka Belediye",
        profile_code="AUS-01",
        profile_name="Akıllı Ulaşım Sistemleri",
        retrieval_rank=1,
        retrieval_score=0.75,
        tender_evidence_chunk_ids=tender_evidence_chunk_ids or ["tc-1", "tc-2"],
        tender_evidence_sections=tender_evidence_sections or [
            "technical_requirements", "qualification"
        ],
        tender_evidence_texts=tender_evidence_texts or [
            "İhale teknik şartname metni", "İhale yeterlilik kriteri"
        ],
        profile_evidence_chunk_ids=profile_evidence_chunk_ids or ["pc-1"],
        profile_evidence_sections=profile_evidence_sections or ["technical"],
        profile_evidence_texts=profile_evidence_texts or [
            "İSBAK akıllı ulaşım yetkinliği"
        ],
    )


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
        evidence_texts=["Yeterlilik şartları metni", "Teknik gereksinimler"],
    )


# ---------------------------------------------------------------------------
# H. Tender context gerçek ihale metnini içermeli
# ---------------------------------------------------------------------------


def test_h_tender_context_contains_tender_text():
    """tender_context içinde ihale kanıt metinleri görünmeli."""
    builder = MatchContextBuilder()
    match = _make_tender_match(
        tender_evidence_texts=["İhale teknik şartname metni"],
        tender_evidence_chunk_ids=["tc-1"],
        tender_evidence_sections=["technical_requirements"],
        profile_evidence_texts=["İSBAK profil metni"],
        profile_evidence_chunk_ids=["pc-1"],
        profile_evidence_sections=["technical"],
    )
    ctx = builder.build_for_tender_match(
        match=match, profile_data=_make_profile_data()
    )
    assert "İhale teknik şartname metni" in ctx["tender_context"], (
        "tender_context ihale kanıt metnini içermeli"
    )


# ---------------------------------------------------------------------------
# I. Tender context profil metnini içermemeli
# ---------------------------------------------------------------------------


def test_i_tender_context_does_not_contain_profile_text():
    """tender_context içinde profil metni bulunmamalı."""
    PROFILE_TEXT = "İSBAK akıllı ulaşım yetkinliği"
    builder = MatchContextBuilder()
    match = _make_tender_match(
        tender_evidence_texts=["İhale teknik şartname metni"],
        tender_evidence_chunk_ids=["tc-1"],
        tender_evidence_sections=["technical_requirements"],
        profile_evidence_texts=[PROFILE_TEXT],
        profile_evidence_chunk_ids=["pc-1"],
        profile_evidence_sections=["technical"],
    )
    ctx = builder.build_for_tender_match(
        match=match, profile_data=_make_profile_data()
    )
    assert PROFILE_TEXT not in ctx["tender_context"], (
        "Profil metni tender_context'e girmemeli"
    )


# ---------------------------------------------------------------------------
# J. Company context profil kanıtını içerebilmeli
# ---------------------------------------------------------------------------


def test_j_company_context_may_contain_profile_evidence():
    """company_context profil kanıt parçalarını [PROFİL KANITI] etiketiyle içermeli."""
    PROFILE_TEXT = "İSBAK akıllı ulaşım yetkinliği"
    builder = MatchContextBuilder()
    match = _make_tender_match(
        profile_evidence_texts=[PROFILE_TEXT],
        profile_evidence_chunk_ids=["pc-1"],
        profile_evidence_sections=["technical"],
    )
    ctx = builder.build_for_tender_match(
        match=match, profile_data=_make_profile_data()
    )
    # Profil kanıt bölümü company_context içinde olabilir
    # (profil kanıtı eklendiğinde)
    assert PROFILE_TEXT in ctx["company_context"], (
        "Profil kanıt metni company_context içinde olmalı"
    )


# ---------------------------------------------------------------------------
# K. valid_chunk_ids yalnızca tender_evidence_chunk_ids içermeli
# ---------------------------------------------------------------------------


def test_k_valid_chunk_ids_are_only_tender_chunk_ids():
    """valid_chunk_ids == tender_evidence_chunk_ids (profil chunk_id içermemeli)."""
    builder = MatchContextBuilder()
    match = _make_tender_match(
        tender_evidence_chunk_ids=["tc-1", "tc-2"],
        profile_evidence_chunk_ids=["pc-1", "pc-2"],
    )
    ctx = builder.build_for_tender_match(
        match=match, profile_data=_make_profile_data()
    )
    assert ctx["valid_chunk_ids"] == ["tc-1", "tc-2"], (
        "valid_chunk_ids yalnızca tender_evidence_chunk_ids içermeli"
    )
    # Profil chunk_id'leri valid_chunk_ids içinde olmamalı
    for pcid in ["pc-1", "pc-2"]:
        assert pcid not in ctx["valid_chunk_ids"], (
            f"Profil chunk_id {pcid!r} valid_chunk_ids içinde olmamalı"
        )


# ---------------------------------------------------------------------------
# L. Paralel kanıt listelerinin uzunlukları farklıysa ValueError oluşmalı
# ---------------------------------------------------------------------------


def test_l_mismatched_evidence_list_lengths_raise_value_error():
    """chunk_ids ve texts uzunlukları farklıysa ValueError fırlatılmalı."""
    builder = MatchContextBuilder()
    match = _make_tender_match(
        tender_evidence_chunk_ids=["tc-1", "tc-2"],  # 2 eleman
        tender_evidence_sections=["s1", "s2"],
        tender_evidence_texts=["metin-1"],  # 1 eleman — hatalı
        profile_evidence_chunk_ids=["pc-1"],
        profile_evidence_sections=["ps1"],
        profile_evidence_texts=["profil metin"],
    )
    with pytest.raises(ValueError, match="eşit olmalıdır"):
        builder.build_for_tender_match(
            match=match, profile_data=_make_profile_data()
        )


def test_l_mismatched_sections_length_raise_value_error():
    """chunk_ids ve sections uzunlukları farklıysa ValueError fırlatılmalı."""
    builder = MatchContextBuilder()
    match = _make_tender_match(
        tender_evidence_chunk_ids=["tc-1", "tc-2"],
        tender_evidence_sections=["s1"],  # eksik — 1 eleman
        tender_evidence_texts=["metin-1", "metin-2"],
    )
    with pytest.raises(ValueError, match="eşit olmalıdır"):
        builder.build_for_tender_match(
            match=match, profile_data=_make_profile_data()
        )


# ---------------------------------------------------------------------------
# M. İhale kaynak satırında chunk_id ve section_type bulunmalı
# ---------------------------------------------------------------------------


def test_m_tender_source_label_contains_chunk_id_and_section():
    """[KAYNAK N | chunk_id: ... | bölüm: ...] formatı kullanılmalı."""
    builder = MatchContextBuilder()
    match = _make_tender_match(
        tender_evidence_chunk_ids=["abc-123"],
        tender_evidence_sections=["technical_requirements"],
        tender_evidence_texts=["İhale metni"],
    )
    ctx = builder.build_for_tender_match(
        match=match, profile_data=_make_profile_data()
    )
    tender_ctx = ctx["tender_context"]
    assert "KAYNAK 1" in tender_ctx, "KAYNAK 1 etiketi olmalı"
    assert "chunk_id: abc-123" in tender_ctx, "chunk_id etikette olmalı"
    assert "bölüm: technical_requirements" in tender_ctx, "section_type etikette olmalı"


# ---------------------------------------------------------------------------
# N. Profil kaynak etiketi [PROFİL KANITI] formatında olmalı
# ---------------------------------------------------------------------------


def test_n_profile_evidence_label_format():
    """Profil kanıtları [PROFİL KANITI N | profile_chunk_id: ...] etiketiyle gösterilmeli."""
    builder = MatchContextBuilder()
    match = _make_tender_match(
        profile_evidence_chunk_ids=["pc-xyz"],
        profile_evidence_sections=["technical"],
        profile_evidence_texts=["Profil teknik yetkinlik"],
    )
    ctx = builder.build_for_tender_match(
        match=match, profile_data=_make_profile_data()
    )
    company_ctx = ctx["company_context"]
    assert "PROFİL KANITI 1" in company_ctx, "PROFİL KANITI etiketi olmalı"
    assert "profile_chunk_id: pc-xyz" in company_ctx, "profile_chunk_id etikette olmalı"
    # [KAYNAK] etiketi company_context'te olmamalı (ihale formatı değil)
    assert "[KAYNAK" not in company_ctx, (
        "company_context'te [KAYNAK] etiketi kullanılmamalı"
    )


# ---------------------------------------------------------------------------
# O. Tender context [KAYNAK] etiketi ihale chunk_id içermeli
# ---------------------------------------------------------------------------


def test_o_tender_source_label_not_profile_format():
    """tender_context [KAYNAK] etiketi kullanmalı, [PROFİL KANITI] değil."""
    builder = MatchContextBuilder()
    match = _make_tender_match(
        tender_evidence_chunk_ids=["tc-check"],
        tender_evidence_sections=["qualification"],
        tender_evidence_texts=["Yeterlilik metni"],
    )
    ctx = builder.build_for_tender_match(
        match=match, profile_data=_make_profile_data()
    )
    tender_ctx = ctx["tender_context"]
    assert "[KAYNAK" in tender_ctx, "İhale bağlamında [KAYNAK] etiketi olmalı"
    assert "PROFİL KANITI" not in tender_ctx, (
        "İhale bağlamında PROFİL KANITI etiketi bulunmamalı"
    )


# ---------------------------------------------------------------------------
# P. build_for_profile_match geriye uyumlu çalışmalı
# ---------------------------------------------------------------------------


def test_p_build_for_profile_match_backward_compatible():
    """ProfileTenderMatch ile build_for_profile_match değişmeden çalışmalı."""
    builder = MatchContextBuilder()
    match = _make_profile_match()
    ctx = builder.build_for_profile_match(
        match=match, profile_data=_make_profile_data()
    )
    required = [
        "matching_mode", "tender_id", "ikn", "tender_name",
        "authority_name", "profile_code", "profile_name",
        "retrieval_score", "score_breakdown", "valid_chunk_ids",
        "tender_context", "company_context",
    ]
    for key in required:
        assert key in ctx, f"Eksik anahtar: {key}"

    assert ctx["matching_mode"] == "profile"
    assert ctx["valid_chunk_ids"] == match.evidence_chunk_ids


# ---------------------------------------------------------------------------
# Ek: Max chars sınırı tender_context'te korunmalı
# ---------------------------------------------------------------------------


def test_max_chars_respected_in_tender_context():
    """max_chars sınırı tender_context uzunluğunu sınırlamalı."""
    builder = MatchContextBuilder(max_chars=200)
    match = _make_tender_match(
        tender_evidence_chunk_ids=["tc-1"],
        tender_evidence_sections=["technical_requirements"],
        tender_evidence_texts=["X" * 5000],
    )
    ctx = builder.build_for_tender_match(
        match=match, profile_data=_make_profile_data()
    )
    assert len(ctx["tender_context"]) < 5000, (
        "tender_context max_chars'ı çok aşmamalı"
    )


# ---------------------------------------------------------------------------
# Ek: Eşit uzunluklarda valid çalışmalı
# ---------------------------------------------------------------------------


def test_valid_equal_length_lists_no_error():
    """Eşit uzunluklarda kanıt listeleri hata vermemeli."""
    builder = MatchContextBuilder()
    match = _make_tender_match(
        tender_evidence_chunk_ids=["tc-1", "tc-2"],
        tender_evidence_sections=["s1", "s2"],
        tender_evidence_texts=["metin-1", "metin-2"],
    )
    # ValueError fırlatılmamalı
    ctx = builder.build_for_tender_match(
        match=match, profile_data=_make_profile_data()
    )
    assert "tender_context" in ctx


# ---------------------------------------------------------------------------
# Ek: Boş kanıt listelerinde de çalışmalı
# ---------------------------------------------------------------------------


def test_empty_evidence_lists_no_error():
    """Boş kanıt listelerinde ValueError fırlatılmamalı."""
    builder = MatchContextBuilder()
    match = TenderProfileMatch(
        tender_id="T1",
        ikn="2026/0000",
        profile_code="AUS-01",
    )
    ctx = builder.build_for_tender_match(
        match=match, profile_data=_make_profile_data()
    )
    assert "tender_context" in ctx
    assert ctx["valid_chunk_ids"] == []
