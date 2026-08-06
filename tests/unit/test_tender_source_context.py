from __future__ import annotations

from app.decision.tender_source_context import (
    TenderSourceContextBuilder,
    render_database_tender_context,
)
from app.domain import (
    TenderAnnouncement,
    TenderCharacteristic,
    TenderOkasCode,
    TenderRecord,
)


def _tender(*, partial: int | None = 1) -> TenderRecord:
    return TenderRecord(
        id="db-1",
        ikn="2026/DB-1",
        adi="Kamera Sistemi ve Araç Kiralama Kısımları",
        idare_adi="Test İdaresi",
        ihale_turu="Mal Alımı",
        ihale_usulu="Açık İhale",
        ihale_durumu="İhale İlanı Yayımlanmış, Katılıma Açık",
        kismi_teklif=partial,
        kapsam=(
            "1. Kısım: Akıllı kamera sistemi\n"
            "2. Kısım: Sürücülü araç kiralama"
        ),
        announcements=[
            TenderAnnouncement(
                id="ann-1",
                tender_id="db-1",
                ilan_tipi="İhale İlanı",
                baslik="Teknik şartlar",
                icerik="Kameralar merkezi yönetim yazılımına bağlanacaktır.",
            )
        ],
        characteristics=[
            TenderCharacteristic(
                id=11,
                tender_id="db-1",
                ozellik="Kamera en az 4K çözünürlük sağlayacaktır.",
            ),
            TenderCharacteristic(
                id=12,
                tender_id="db-1",
                ozellik="Kayıt sistemi ONVIF uyumlu olacaktır.",
            ),
        ],
        okas_codes=[
            TenderOkasCode(
                id=21,
                tender_id="db-1",
                kod="32323500",
                ad="Video gözetim sistemi",
            ),
            TenderOkasCode(
                id=22,
                tender_id="db-1",
                kod="60170000",
                ad="Yolcu taşıma aracı kiralama",
            ),
        ],
    )


def test_database_context_preserves_true_fields_and_source_parts() -> None:
    source = TenderSourceContextBuilder().build(
        _tender(),
        profile_signals={
            "guclu_terimler": ["kamera sistemi"],
            "negatif_terimler": ["araç kiralama"],
        },
    )
    rendered = render_database_tender_context(source, max_chars=30_000)

    assert "Gerçek ihale türü: Mal Alımı" in rendered
    assert "32323500 — Video gözetim sistemi" in rendered
    assert "60170000 — Yolcu taşıma aracı kiralama" in rendered
    assert "1: Akıllı kamera sistemi" in rendered
    assert "2: Sürücülü araç kiralama" in rendered
    assert "Kamera en az 4K çözünürlük sağlayacaktır." in rendered
    assert "Kayıt sistemi ONVIF uyumlu olacaktır." in rendered
    assert source.parts[0].source_table == "public.tenders"
    assert source.selection.case_type == "partial"
    assert source.selection.evidence_limit == 4
    assert source.missing_required_fields == []


def test_evidence_limit_is_dynamic_for_clear_ambiguous_and_mixed_cases() -> None:
    builder = TenderSourceContextBuilder()
    non_partial = _tender(partial=0).model_copy(
        update={"kapsam": "Kamera sistemi ve araç kiralama hizmetleri"}
    )
    clear = builder.build(
        non_partial,
        profile_signals={"guclu_terimler": ["kamera sistemi"]},
    )
    ambiguous = builder.build(
        non_partial,
        profile_signals={"guclu_terimler": ["meteoroloji radarı"]},
    )
    mixed = builder.build(
        non_partial,
        profile_signals={
            "guclu_terimler": ["kamera sistemi"],
            "negatif_terimler": ["araç kiralama"],
        },
    )

    assert (clear.selection.case_type, clear.selection.evidence_limit) == (
        "clear",
        1,
    )
    assert (
        ambiguous.selection.case_type,
        ambiguous.selection.evidence_limit,
    ) == ("ambiguous", 3)
    assert (mixed.selection.case_type, mixed.selection.evidence_limit) == (
        "mixed",
        4,
    )


def test_unknown_partial_flag_and_missing_parts_mark_source_incomplete() -> None:
    tender = _tender(partial=None).model_copy(update={"kapsam": "Genel alım"})
    source = TenderSourceContextBuilder().build(tender, profile_signals={})
    validation = source.validation_context(profile_signals={}, retrieval_score=0.7)

    assert validation.source_origin == "postgresql"
    assert validation.source_complete is False
    assert "kismi_teklif" in validation.source_missing_fields


def test_every_chunk_carries_canonical_tender_metadata() -> None:
    source = TenderSourceContextBuilder().build(_tender(), profile_signals={})
    document = TenderSourceContextBuilder().document_builder.build(_tender())
    chunks = TenderSourceContextBuilder().chunker.chunk_document(document)

    assert source.all_evidence_chunks
    assert chunks
    for chunk in chunks:
        assert chunk["ihale_turu"] == "Mal Alımı"
        assert chunk["kismi_teklif"] == 1
        assert len(chunk["okas_codes"]) == 2
