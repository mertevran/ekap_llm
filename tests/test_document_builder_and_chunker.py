from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.indexing.chunker import SectionAwareChunker
from app.indexing.document_builder import TenderDocumentBuilder


@dataclass
class Announcement:
    id: str
    tender_id: str
    ilan_tipi: str
    ilan_tarihi: datetime
    baslik: str
    icerik: str
    created_at: datetime | None = None


@dataclass
class Characteristic:
    id: str
    tender_id: str
    ozellik: str


@dataclass
class Okas:
    id: str
    tender_id: str
    kod: str
    ad: str


@dataclass
class Tender:
    id: str = "tender-1"
    ikn: str = "2026/1"
    adi: str = "Yazılım geliştirme hizmeti"
    idare_adi: str = "Örnek İdare"
    il: str = "İstanbul"
    ihale_tarihi: str = "30.07.2026 10:00"
    ihale_turu: str = "Hizmet"
    ihale_usulu: str = "Açık"
    ihale_durumu: str = "İhale İlanı Yayımlanmış, Katılıma Açık"
    kapsam: str = "4734"
    e_ihale: bool = True
    kismi_teklif: bool = False
    ihale_yeri: str = "İstanbul"
    isin_yeri: str = "İstanbul"
    dokuman_sayisi: int = 3
    updated_at: datetime = datetime(2026, 7, 20)
    announcements: list[Announcement] = field(default_factory=list)
    characteristics: list[Characteristic] = field(default_factory=list)
    okas_codes: list[Okas] = field(default_factory=list)


def test_document_builder_preserves_sources() -> None:
    tender = Tender(
        announcements=[
            Announcement(
                id="a1",
                tender_id="tender-1",
                ilan_tipi="İhale İlanı",
                ilan_tarihi=datetime(2026, 7, 20),
                baslik="Yazılım hizmeti alınacaktır",
                icerik="Python ve veri tabanı geliştirme hizmeti.",
            )
        ],
        characteristics=[
            Characteristic(
                id="c1",
                tender_id="tender-1",
                ozellik="E İhale",
            )
        ],
        okas_codes=[
            Okas(
                id="o1",
                tender_id="tender-1",
                kod="72200000",
                ad="Yazılım programlama ve danışmanlık hizmetleri",
            )
        ],
    )

    document = TenderDocumentBuilder().build(tender)

    assert document["ikn"] == "2026/1"
    assert len(document["sections"]) == 5
    assert {section["source_table"] for section in document["sections"]} == {
        "public.tenders",
        "public.tender_announcements",
        "public.tender_characteristics",
        "public.tender_okas_codes",
    }


def test_chunker_creates_deterministic_chunks() -> None:
    tender = Tender(
        announcements=[
            Announcement(
                id="a1",
                tender_id="tender-1",
                ilan_tipi="İhale İlanı",
                ilan_tarihi=datetime(2026, 7, 20),
                baslik="Uzun ilan",
                icerik=("Yazılım geliştirme ve bakım hizmeti. " * 100),
            )
        ]
    )

    document = TenderDocumentBuilder().build(tender)
    chunker = SectionAwareChunker(
        chunk_target_chars=300,
        chunk_overlap_chars=50,
        chunk_hard_max_chars=400,
    )

    first = chunker.chunk_document(document)
    second = chunker.chunk_document(document)

    assert len(first) > 2
    assert [item["chunk_id"] for item in first] == [item["chunk_id"] for item in second]
    assert all(item["ikn"] == "2026/1" for item in first)
    assert all(item["text"].strip() for item in first)
