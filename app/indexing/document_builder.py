from __future__ import annotations

from typing import Any

from app.indexing.text_utils import display_value, iso_value, normalize_text


class TenderDocumentBuilder:
    """Bir ihale kaydını kaynak izlenebilirliği olan bölümlere dönüştürür."""

    def build(self, tender: Any) -> dict[str, Any]:
        tender_id = str(getattr(tender, "id"))
        ikn = str(getattr(tender, "ikn"))
        title = normalize_text(getattr(tender, "adi", "")) or ikn
        authority_name = normalize_text(getattr(tender, "idare_adi", ""))

        sections: list[dict[str, Any]] = []

        # 1. Tender Summary
        sections.append(
            self._build_tender_summary(
                tender=tender,
                tender_id=tender_id,
                ikn=ikn,
                title=title,
                authority_name=authority_name,
            )
        )

        # 2. Scope and Location
        scope_section = self._build_scope_and_location(
            tender=tender,
            tender_id=tender_id,
            ikn=ikn,
            title=title,
            authority_name=authority_name,
        )
        if scope_section:
            sections.append(scope_section)

        # 3. Announcements
        for announcement in getattr(tender, "announcements", []) or []:
            section = self._build_announcement_section(
                announcement=announcement,
                tender_id=tender_id,
                ikn=ikn,
                title=title,
                authority_name=authority_name,
            )
            if section is not None:
                sections.append(section)

        # 4. Characteristics
        characteristics = getattr(tender, "characteristics", []) or []
        if characteristics:
            sections.append(
                self._build_characteristics_section(
                    characteristics=characteristics,
                    tender_id=tender_id,
                    ikn=ikn,
                    title=title,
                    authority_name=authority_name,
                )
            )

        # 5. OKAS
        okas_codes = getattr(tender, "okas_codes", []) or []
        if okas_codes:
            sections.append(
                self._build_okas_section(
                    okas_codes=okas_codes,
                    tender_id=tender_id,
                    ikn=ikn,
                    title=title,
                    authority_name=authority_name,
                )
            )

        return {
            "tender_id": tender_id,
            "ikn": ikn,
            "title": title,
            "sections": sections,
            "metadata": {
                "ihale_tarihi": display_value(getattr(tender, "ihale_tarihi", None)),
                "ihale_turu": display_value(getattr(tender, "ihale_turu", None)),
                "ihale_usulu": display_value(getattr(tender, "ihale_usulu", None)),
                "ihale_durumu": display_value(getattr(tender, "ihale_durumu", None)),
                "idare_adi": authority_name,
                "il": display_value(getattr(tender, "il", None)),
                "updated_at": iso_value(getattr(tender, "updated_at", None)),
            },
        }

    @staticmethod
    def _build_tender_summary(
        *,
        tender: Any,
        tender_id: str,
        ikn: str,
        title: str,
        authority_name: str,
    ) -> dict[str, Any]:
        fields = [
            ("İKN", ikn),
            ("İhale adı", title),
            ("İdare adı", authority_name),
            ("İl", getattr(tender, "il", None)),
            ("İhale tarihi", getattr(tender, "ihale_tarihi", None)),
            ("İhale türü", getattr(tender, "ihale_turu", None)),
            ("İhale usulü", getattr(tender, "ihale_usulu", None)),
            ("İhale durumu", getattr(tender, "ihale_durumu", None)),
            ("Elektronik ihale", getattr(tender, "e_ihale", None)),
            ("Kısmi teklif", getattr(tender, "kismi_teklif", None)),
            ("Doküman sayısı", getattr(tender, "dokuman_sayisi", None)),
        ]

        text = "\n".join(
            f"{label}: {display_value(value)}"
            for label, value in fields
            if value is not None and value != ""
        )

        return {
            "section_id": tender_id,
            "tender_id": tender_id,
            "ikn": ikn,
            "title": title,
            "authority_name": authority_name,
            "source_table": "public.tenders",
            "source_record_ids": [tender_id],
            "text": normalize_text(text),
            "metadata": {
                "section_type": "tender_summary",
            },
        }

    @staticmethod
    def _build_scope_and_location(
        *,
        tender: Any,
        tender_id: str,
        ikn: str,
        title: str,
        authority_name: str,
    ) -> dict[str, Any] | None:

        kapsam = normalize_text(getattr(tender, "kapsam", ""))
        ihale_yeri = normalize_text(getattr(tender, "ihale_yeri", ""))
        isin_yeri = normalize_text(getattr(tender, "isin_yeri", ""))

        if not kapsam and not ihale_yeri and not isin_yeri:
            return None

        text_parts = []
        if ihale_yeri:
            text_parts.append(f"İhale Yeri: {ihale_yeri}")
        if isin_yeri:
            text_parts.append(f"İşin Yeri: {isin_yeri}")
        if kapsam:
            if text_parts:
                text_parts.append("")
            text_parts.append(f"Kapsam:\n{kapsam}")

        return {
            "section_id": f"scope:{tender_id}",
            "tender_id": tender_id,
            "ikn": ikn,
            "title": title,
            "authority_name": authority_name,
            "source_table": "public.tenders",
            "source_record_ids": [tender_id],
            "text": "\n".join(text_parts).strip(),
            "metadata": {
                "section_type": "scope_and_location",
            },
        }

    @staticmethod
    def _build_announcement_section(
        *,
        announcement: Any,
        tender_id: str,
        ikn: str,
        title: str,
        authority_name: str,
    ) -> dict[str, Any] | None:
        announcement_id = str(getattr(announcement, "id"))
        heading = normalize_text(getattr(announcement, "baslik", "")) or "İhale İlanı"
        content = normalize_text(getattr(announcement, "icerik", ""))
        announcement_type = display_value(getattr(announcement, "ilan_tipi", None))
        announcement_date = iso_value(getattr(announcement, "ilan_tarihi", None))

        if not content and heading == "İhale İlanı":
            return None

        return {
            "section_id": announcement_id,
            "tender_id": tender_id,
            "ikn": ikn,
            "title": title,
            "authority_name": authority_name,
            "source_table": "public.tender_announcements",
            "source_record_ids": [announcement_id],
            "text": content,
            "metadata": {
                "section_type": "announcement",
                "announcement_type": announcement_type,
                "announcement_date": announcement_date,
                "heading": heading,
            },
        }

    @staticmethod
    def _build_characteristics_section(
        *,
        characteristics: list[Any],
        tender_id: str,
        ikn: str,
        title: str,
        authority_name: str,
    ) -> dict[str, Any]:
        # Characteristics are stored as list of dicts. We keep the objects so chunker can separate them.
        items = []
        source_record_ids = []
        for item in characteristics:
            rid = str(getattr(item, "id"))
            source_record_ids.append(rid)
            value = normalize_text(getattr(item, "ozellik", ""))
            if value:
                items.append({"id": rid, "text": value})

        return {
            "section_id": tender_id,
            "tender_id": tender_id,
            "ikn": ikn,
            "title": title,
            "authority_name": authority_name,
            "source_table": "public.tender_characteristics",
            "source_record_ids": source_record_ids,
            "text": "",  # Handled by chunker item by item
            "metadata": {
                "section_type": "characteristics",
                "items": items,
            },
        }

    @staticmethod
    def _build_okas_section(
        *,
        okas_codes: list[Any],
        tender_id: str,
        ikn: str,
        title: str,
        authority_name: str,
    ) -> dict[str, Any]:
        record_ids: list[str] = []
        lines: list[str] = []
        codes: list[str] = []

        for item in okas_codes:
            record_ids.append(str(getattr(item, "id")))
            code = normalize_text(getattr(item, "kod", ""))
            name = normalize_text(getattr(item, "ad", ""))
            if code:
                codes.append(code)
            if code and name:
                lines.append(f"{code} — {name}")
            elif code or name:
                lines.append(f"{code or name}")

        return {
            "section_id": tender_id,
            "tender_id": tender_id,
            "ikn": ikn,
            "title": title,
            "authority_name": authority_name,
            "source_table": "public.tender_okas_codes",
            "source_record_ids": record_ids,
            "text": "\n".join(lines),
            "metadata": {
                "section_type": "okas",
                "okas_codes": codes,
            },
        }
