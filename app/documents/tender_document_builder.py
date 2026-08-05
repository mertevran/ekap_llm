from __future__ import annotations

from app.documents.normalizer import normalize_text
from app.domain import TenderDocument, TenderRecord, TenderSection


def format_binary_value(value: int | None) -> str | None:
    if value is None:
        return None

    if value == 1:
        return "Evet"

    if value == 0:
        return "Hayır"

    return str(value)


def build_labeled_text(
    fields: list[tuple[str, str | int | None]],
) -> str:
    lines: list[str] = []

    for label, raw_value in fields:
        if raw_value is None:
            continue

        value = normalize_text(str(raw_value))

        if value:
            lines.append(f"{label}: {value}")

    return "\n".join(lines)


class TenderDocumentBuilder:
    def build(self, tender: TenderRecord) -> TenderDocument:
        sections: list[TenderSection] = []

        main_section = self._build_main_section(tender)

        if main_section.text:
            sections.append(main_section)

        sections.extend(self._build_announcement_sections(tender))

        characteristics_section = self._build_characteristics_section(tender)

        if characteristics_section is not None:
            sections.append(characteristics_section)

        okas_section = self._build_okas_section(tender)

        if okas_section is not None:
            sections.append(okas_section)

        return TenderDocument(
            tender_id=tender.id,
            ikn=tender.ikn,
            title=normalize_text(tender.adi) or tender.ikn,
            sections=sections,
        )

    @staticmethod
    def _build_main_section(tender: TenderRecord) -> TenderSection:
        text = build_labeled_text(
            [
                ("İKN", tender.ikn),
                ("İhale adı", tender.adi),
                ("İdare adı", tender.idare_adi),
                ("İl", tender.il),
                ("İhale tarihi", tender.ihale_tarihi),
                ("İhale türü", tender.ihale_turu),
                ("İhale usulü", tender.ihale_usulu),
                ("İhale durumu", tender.ihale_durumu),
                ("Kapsam", tender.kapsam),
                ("Elektronik ihale", format_binary_value(tender.e_ihale)),
                (
                    "Kısmi teklif",
                    format_binary_value(tender.kismi_teklif),
                ),
                ("İhale yeri", tender.ihale_yeri),
                ("İşin yeri", tender.isin_yeri),
                ("Doküman sayısı", tender.dokuman_sayisi),
            ]
        )

        return TenderSection(
            section_id=f"tender:{tender.id}:main",
            tender_id=tender.id,
            ikn=tender.ikn,
            source_table="tenders",
            source_record_ids=[tender.id],
            title="Ana İhale Bilgileri",
            text=text,
            metadata={
                "section_type": "tender_main",
            },
        )

    @staticmethod
    def _build_announcement_sections(
        tender: TenderRecord,
    ) -> list[TenderSection]:
        sections: list[TenderSection] = []

        for index, announcement in enumerate(
            tender.announcements,
            start=1,
        ):
            announcement_title = normalize_text(announcement.baslik) or f"İhale İlanı {index}"

            text = build_labeled_text(
                [
                    ("İlan başlığı", announcement.baslik),
                    ("İlan tipi", announcement.ilan_tipi),
                    ("İlan tarihi", announcement.ilan_tarihi),
                    ("İlan içeriği", announcement.icerik),
                ]
            )

            if not text:
                continue

            sections.append(
                TenderSection(
                    section_id=f"announcement:{announcement.id}",
                    tender_id=tender.id,
                    ikn=tender.ikn,
                    source_table="tender_announcements",
                    source_record_ids=[announcement.id],
                    title=announcement_title,
                    text=text,
                    metadata={
                        "section_type": "announcement",
                        "announcement_type": announcement.ilan_tipi,
                        "announcement_date": announcement.ilan_tarihi,
                    },
                )
            )

        return sections

    @staticmethod
    def _build_characteristics_section(
        tender: TenderRecord,
    ) -> TenderSection | None:
        lines: list[str] = []
        record_ids: list[str] = []

        for item in tender.characteristics:
            characteristic = normalize_text(item.ozellik)

            if not characteristic:
                continue

            lines.append(f"- {characteristic}")
            record_ids.append(str(item.id))

        if not lines:
            return None

        return TenderSection(
            section_id=f"tender:{tender.id}:characteristics",
            tender_id=tender.id,
            ikn=tender.ikn,
            source_table="tender_characteristics",
            source_record_ids=record_ids,
            title="İhale Özellikleri",
            text="\n".join(lines),
            metadata={
                "section_type": "characteristics",
                "record_count": len(lines),
            },
        )

    @staticmethod
    def _build_okas_section(
        tender: TenderRecord,
    ) -> TenderSection | None:
        lines: list[str] = []
        record_ids: list[str] = []

        for item in tender.okas_codes:
            code = normalize_text(item.kod)
            name = normalize_text(item.ad)

            if code and name:
                line = f"- {code}: {name}"
            elif code:
                line = f"- {code}"
            elif name:
                line = f"- {name}"
            else:
                continue

            lines.append(line)
            record_ids.append(str(item.id))

        if not lines:
            return None

        return TenderSection(
            section_id=f"tender:{tender.id}:okas",
            tender_id=tender.id,
            ikn=tender.ikn,
            source_table="tender_okas_codes",
            source_record_ids=record_ids,
            title="OKAS Kodları",
            text="\n".join(lines),
            metadata={
                "section_type": "okas_codes",
                "record_count": len(lines),
            },
        )
