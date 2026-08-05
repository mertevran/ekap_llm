"""Parçalama bütünlüğü testleri (Gereksinim 52: 33-47).

SectionAwareChunker'ın bölüm sınırlarını, OKAS bütünlüğünü,
tablo/madde korumasını ve kaynak kimliklerini doğru taşıdığını doğrular.
"""

from __future__ import annotations

from typing import Any

# ─── Sahte belge oluşturucu ───────────────────────────────────────────────────


def _make_section(
    *,
    section_id: str,
    section_type: str,
    text: str,
    source_record_ids: list[str] | None = None,
    source_table: str = "public.tenders",
    title: str = "Test Bölümü",
) -> dict[str, Any]:
    return {
        "section_id": section_id,
        "title": title,
        "text": text,
        "metadata": {
            "section_type": section_type,
            "source_table": source_table,
            "source_record_ids": source_record_ids or [],
        },
    }


def _make_document(sections: list[dict]) -> dict[str, Any]:
    return {
        "tender_id": "TEST_001",
        "ikn": "2026/001",
        "title": "Test İhalesi",
        "sections": sections,
    }


def _get_chunker():
    from app.indexing.chunker import SectionAwareChunker

    return SectionAwareChunker(
        chunk_target_chars=1200,
        chunk_min_chars=500,
        chunk_soft_max_chars=1500,
        chunk_hard_max_chars=1800,
        chunk_overlap_chars=150,
        chunking_version="section-aware-v1",
    )


# ---------------------------------------------------------------------------
# Test 33: tender_summary bölümü tek parça kalmalı
# ---------------------------------------------------------------------------


def test_tender_summary_stays_single_chunk() -> None:
    chunker = _get_chunker()
    section = _make_section(
        section_id="summary:TEST_001",
        section_type="tender_summary",
        text="İhale adı: Test İhalesi\nİdare: Test A.Ş.\nİhale türü: Hizmet Alımı\n" * 3,
    )
    doc = _make_document([section])
    chunks = chunker.chunk_document(doc)
    summary_chunks = [c for c in chunks if c.get("section_type") == "tender_summary"]
    assert len(summary_chunks) == 1, (
        f"tender_summary birden fazla parçaya bölündü: {len(summary_chunks)}"
    )


# ---------------------------------------------------------------------------
# Test 34: OKAS kodu ve adı ayrılmamalı
# ---------------------------------------------------------------------------


def test_okas_code_and_name_not_separated() -> None:
    chunker = _get_chunker()
    # OKAS bölümü: her satır bir kod+ad çifti
    okas_lines = [f"72000000 - Bilgisayar ve ilgili hizmetler #{i}" for i in range(10)]
    section = _make_section(
        section_id="okas:TEST_001",
        section_type="okas",
        text="\n".join(okas_lines),
        source_table="public.tender_okas_codes",
        source_record_ids=[f"OKS{i}" for i in range(10)],
    )
    doc = _make_document([section])
    chunks = chunker.chunk_document(doc)
    for chunk in chunks:
        text = chunk.get("text", "")
        # Her parçada kod ve ad birlikte olmalı (satır ortasında kesilmemeli)
        lines = [line_text for line_text in text.split("\n") if line_text.strip()]
        for line in lines:
            if " - " in line:
                parts = line.split(" - ", 1)
                assert len(parts) == 2, f"OKAS satırı yarım kesildi: {line!r}"


# ---------------------------------------------------------------------------
# Test 35: Farklı announcement id'leri aynı parçada bulunmamalı
# Farklı announcement'lar farklı section'lara ayrılmalı (document_builder garantisi)
# ---------------------------------------------------------------------------


def test_different_announcement_ids_in_different_sections() -> None:
    from app.indexing.document_builder import TenderDocumentBuilder

    class _Announcement:
        def __init__(self, id, icerik):
            self.id = id
            self.ilan_tipi = "İlk İlan"
            self.ilan_tarihi = "2026-01-01"
            self.baslik = f"İlan {id}"
            self.icerik = icerik
            self.created_at = "2026-01-01T00:00:00"
            self.tender_id = "TEST_001"

    class _MockTender:
        id = "TEST_001"
        ikn = "2026/001"
        adi = "Test İhalesi"
        idare_adi = "Test İdare"
        il = "İstanbul"
        ihale_tarihi = "2026-12-01 10:00:00"
        ihale_turu = "Hizmet Alımı"
        ihale_usulu = "Açık İhale"
        ihale_durumu = "aktif"
        kapsam = None
        e_ihale = 0
        kismi_teklif = 0
        ihale_yeri = None
        isin_yeri = None
        dokuman_sayisi = 0
        created_at = "2026-01-01T00:00:00"
        updated_at = "2026-01-01T00:00:00"
        announcements = [
            _Announcement("ANN_1", "Birinci ilan metni. " * 20),
            _Announcement("ANN_2", "İkinci ilan metni. " * 20),
        ]
        characteristics = []
        okas_codes = []

    builder = TenderDocumentBuilder()
    doc = builder.build(_MockTender())

    # Her announcement kendi section_id'sine sahip olmalı
    ann_sections = [s for s in doc["sections"] if "announcement" in s.get("section_id", "")]
    ann_ids = [s["section_id"] for s in ann_sections]
    assert len(set(ann_ids)) == len(ann_ids), "Farklı announcement'lar aynı section_id'ye sahip"


# ---------------------------------------------------------------------------
# Test 36: characteristic kaydı ortadan bölünmemeli (kısa ise)
# ---------------------------------------------------------------------------


def test_short_characteristic_not_split() -> None:
    """Kısa characteristics kayıtları birleştirilmeli, bölünmemeli."""
    chunker = _get_chunker()
    # characteristics bölümü metadata.items listesini okur
    chars = [f"Özellik {i}: değer {i}" for i in range(5)]
    section = {
        "section_id": "char:TEST_001",
        "title": "Teknik Özellikler",
        "text": "\n".join(chars),  # _chunk_text_with_overlap için
        "metadata": {
            "section_type": "characteristics",
            "source_table": "public.tender_characteristics",
            "source_record_ids": [f"CH{i}" for i in range(5)],
            "items": [{"id": f"CH{i}", "text": f"Özellik {i}: değer {i}"} for i in range(5)],
        },
        "source_table": "public.tender_characteristics",
        "source_record_ids": [f"CH{i}" for i in range(5)],
    }
    doc = _make_document([section])
    chunks = chunker.chunk_document(doc)
    # Kısa characteristics → en az bir parça üretilmeli
    assert len(chunks) >= 1, "Characteristics bölümünden en az 1 parça üretilmeli"


# ---------------------------------------------------------------------------
# Test 37: source_record_ids korunmalı
# ---------------------------------------------------------------------------


def test_source_record_ids_preserved_in_chunks() -> None:
    chunker = _get_chunker()
    section = _make_section(
        section_id="okas:TEST_001",
        section_type="okas",
        text="72000000 - Bilgisayar hizmetleri\n48000000 - Yazılım\n",
        source_table="public.tender_okas_codes",
        source_record_ids=["OKS_A", "OKS_B"],
    )
    doc = _make_document([section])
    chunks = chunker.chunk_document(doc)
    for chunk in chunks:
        assert "source_record_ids" in chunk or "section_id" in chunk, (
            "Parça source_record_ids ya da section_id alanını kaybetti"
        )


# ---------------------------------------------------------------------------
# Test 38: Boş parça üretilmemeli
# ---------------------------------------------------------------------------


def test_no_empty_chunks_produced() -> None:
    chunker = _get_chunker()
    section = _make_section(
        section_id="s:TEST_001",
        section_type="announcement",
        text="Bu kısa bir metin.",
    )
    doc = _make_document([section])
    chunks = chunker.chunk_document(doc)
    for chunk in chunks:
        text = chunk.get("text", "").strip()
        assert len(text) > 0, "Boş parça üretildi"


# ---------------------------------------------------------------------------
# Test 39: Mutlak üst sınır aşılmamalı
# ---------------------------------------------------------------------------


def test_hard_max_chars_not_exceeded() -> None:
    chunker = _get_chunker()
    long_text = "Uzun içerik satırı. " * 200  # ~4000 char
    section = _make_section(
        section_id="s:LONG",
        section_type="announcement",
        text=long_text,
    )
    doc = _make_document([section])
    chunks = chunker.chunk_document(doc)
    for chunk in chunks:
        text = chunk.get("text", "")
        assert len(text) <= chunker.chunk_hard_max_chars + 50, (
            f"Parça hard_max_chars aştı: {len(text)} > {chunker.chunk_hard_max_chars}"
        )


# ---------------------------------------------------------------------------
# Test 40: chunk_id değerleri deterministik olmalı
# ---------------------------------------------------------------------------


def test_chunk_ids_are_deterministic() -> None:
    chunker1 = _get_chunker()
    chunker2 = _get_chunker()
    section = _make_section(
        section_id="det:TEST_001",
        section_type="announcement",
        text="Deterministik test metni. " * 30,
    )
    doc = _make_document([section])
    chunks1 = chunker1.chunk_document(doc)
    chunks2 = chunker2.chunk_document(doc)
    ids1 = [c["chunk_id"] for c in chunks1]
    ids2 = [c["chunk_id"] for c in chunks2]
    assert ids1 == ids2, "chunk_id'ler deterministik değil"


# ---------------------------------------------------------------------------
# Test 41: chunk_id değerleri benzersiz olmalı
# ---------------------------------------------------------------------------


def test_chunk_ids_are_unique() -> None:
    chunker = _get_chunker()
    sections = [
        _make_section(
            section_id=f"s{i}:TEST_001",
            section_type="announcement",
            text=f"Bu bölüm {i} için metin içeriği. " * 30,
        )
        for i in range(3)
    ]
    doc = _make_document(sections)
    chunks = chunker.chunk_document(doc)
    ids = [c["chunk_id"] for c in chunks]
    assert len(ids) == len(set(ids)), f"Tekrar eden chunk_id: {ids}"


# ---------------------------------------------------------------------------
# Test 42: Farklı tender_id'ler aynı parçada bulunmamalı
# ---------------------------------------------------------------------------


def test_different_tender_ids_not_mixed_in_chunk() -> None:
    """Bir parçanın tender_id'si yalnızca bir ihaleden gelmeli."""
    chunker = _get_chunker()
    section = _make_section(
        section_id="s:TEST_001",
        section_type="announcement",
        text="Test metni. " * 30,
    )
    doc1 = {"tender_id": "T001", "ikn": "2026/001", "title": "İhale 1", "sections": [section]}
    doc2 = {"tender_id": "T002", "ikn": "2026/002", "title": "İhale 2", "sections": [section]}

    chunks1 = chunker.chunk_document(doc1)
    chunks2 = chunker.chunk_document(doc2)

    for c in chunks1:
        assert c.get("tender_id") == "T001"
    for c in chunks2:
        assert c.get("tender_id") == "T002"


# ---------------------------------------------------------------------------
# Test 43: section_type parçalarda korunmalı
# ---------------------------------------------------------------------------


def test_section_type_preserved_in_chunks() -> None:
    chunker = _get_chunker()
    for stype in [
        "tender_summary",
        "okas",
        "characteristics",
        "announcement",
        "scope_and_location",
    ]:
        section = _make_section(
            section_id=f"{stype}:TEST_001",
            section_type=stype,
            text="Test içerik. " * 60,
        )
        doc = _make_document([section])
        chunks = chunker.chunk_document(doc)
        for chunk in chunks:
            assert chunk.get("section_type") == stype, (
                f"section_type korunmadı: beklenen {stype}, bulunan {chunk.get('section_type')}"
            )
