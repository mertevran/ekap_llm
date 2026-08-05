from app.documents import TenderDocumentBuilder
from app.domain import (
    TenderAnnouncement,
    TenderCharacteristic,
    TenderOkasCode,
    TenderRecord,
)


def build_sample_tender() -> TenderRecord:
    return TenderRecord(
        id="tender-1",
        ikn="2026/TEST",
        adi="Örnek İhale",
        idare_adi="Örnek İdare",
        il="İstanbul",
        ihale_tarihi="01.08.2026",
        ihale_turu="Mal Alımı",
        announcements=[
            TenderAnnouncement(
                id="announcement-1",
                tender_id="tender-1",
                ilan_tipi="İhale İlanı",
                ilan_tarihi="10.07.2026",
                baslik="Örnek ilan",
                icerik="<p>Teknik belge sunulmalıdır.</p>",
            )
        ],
        characteristics=[
            TenderCharacteristic(
                id=1,
                tender_id="tender-1",
                ozellik="Kısmi teklife açıktır.",
            )
        ],
        okas_codes=[
            TenderOkasCode(
                id=1,
                tender_id="tender-1",
                kod="12345678",
                ad="Örnek ürün",
            )
        ],
    )


def test_builder_creates_expected_sections() -> None:
    builder = TenderDocumentBuilder()

    document = builder.build(build_sample_tender())

    source_tables = {section.source_table for section in document.sections}

    assert document.ikn == "2026/TEST"
    assert document.title == "Örnek İhale"
    assert "tenders" in source_tables
    assert "tender_announcements" in source_tables
    assert "tender_characteristics" in source_tables
    assert "tender_okas_codes" in source_tables


def test_builder_does_not_create_empty_sections() -> None:
    tender = TenderRecord(
        id="tender-2",
        ikn="2026/EMPTY",
        adi="Boş Alt Kayıt Testi",
    )

    document = TenderDocumentBuilder().build(tender)

    assert len(document.sections) == 1
    assert document.sections[0].source_table == "tenders"


def test_builder_preserves_source_record_ids() -> None:
    document = TenderDocumentBuilder().build(build_sample_tender())

    announcement_section = next(
        section for section in document.sections if section.source_table == "tender_announcements"
    )

    assert announcement_section.source_record_ids == ["announcement-1"]
