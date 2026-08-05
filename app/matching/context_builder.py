"""LLM bağlam üreticisi.

Her iki çalışma modu (profil odaklı + ihale odaklı) aynı bağlam üreticisini kullanır.

Kurallar:
- Şirket profilinin genel açıklaması yeterliliğin kanıtı olarak kullanılmaz.
- Profil içindeki kapasiteye ait somut veriler (projeler, belgeler, ekipman vb.)
  kanıt olarak kabul edilir.
- Eksik veri varmış gibi kabul edilmez.

Kanıt bütünlüğü (İhale odaklı mod):
- tender_context içine yalnızca tender_evidence_* alanları yazılır.
- profile_evidence_* alanları company_context içinde ayrı bölüm olarak gösterilir.
- İhale kaynak formatı: [KAYNAK N | chunk_id: ... | bölüm: ...]
- Profil kaynak formatı: [PROFİL KANITI N | profile_chunk_id: ...]
- Paralel kanıt listeleri (chunk_ids, sections, texts) eşit uzunluktaysa
  kullanılır; aksi hâlde ValueError fırlatılır.
"""

from __future__ import annotations

from typing import Any

from app.matching.models import MatchScoreBreakdown, ProfileTenderMatch, TenderProfileMatch

# Profil kapasitesi için kullanılacak alanlar (genel açıklama hariç)
_PROFILE_CAPACITY_FIELDS: list[tuple[str, str]] = [
    ("tamamlanan_projeler", "Tamamlanan Projeler"),
    ("is_deneyim_belgeleri", "İş Deneyim Belgeleri"),
    ("personel_kapasitesi", "Personel Kapasitesi"),
    ("belgeler", "Belgeler ve Sertifikalar"),
    ("ekipman_ve_altyapi", "Ekipman ve Altyapı"),
    ("teknolojiler", "Teknolojiler"),
    ("urunler_ve_hizmetler", "Ürünler ve Hizmetler"),
    ("kapasite_sinirlari", "Kapasite Sınırları"),
    ("birincil_yetkinlikler", "Birincil Yetkinlikler"),
]


class MatchContextBuilder:
    """LLM'ye gönderilecek bağlamı iki mod için ortak biçimde oluşturur."""

    def __init__(self, max_chars: int = 8000) -> None:
        self.max_chars = max_chars

    # ------------------------------------------------------------------
    # Profil odaklı bağlam
    # ------------------------------------------------------------------

    def build_for_profile_match(
        self,
        *,
        match: ProfileTenderMatch,
        profile_data: dict[str, Any],
        company_master: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """ProfileTenderMatch için LLM bağlamı oluşturur."""
        tender_ctx = self._build_tender_context(
            tender_id=match.tender_id,
            ikn=match.ikn,
            tender_name=match.tender_name,
            authority_name=match.authority_name,
            evidence_chunk_ids=match.evidence_chunk_ids,
            evidence_sections=match.evidence_sections,
            evidence_texts=match.evidence_texts,
        )
        company_ctx = self._build_company_context(
            profile_code=match.profile_code,
            profile_name=match.profile_name,
            profile_data=profile_data,
            company_master=company_master,
        )
        return {
            "matching_mode": "profile",
            "tender_id": match.tender_id,
            "ikn": match.ikn,
            "tender_name": match.tender_name,
            "authority_name": match.authority_name,
            "profile_code": match.profile_code,
            "profile_name": match.profile_name,
            "retrieval_score": match.retrieval_score,
            "score_breakdown": match.score_breakdown.model_dump(),
            "valid_chunk_ids": match.evidence_chunk_ids,
            "tender_context": tender_ctx,
            "company_context": company_ctx,
        }

    # ------------------------------------------------------------------
    # İhale odaklı bağlam
    # ------------------------------------------------------------------

    def build_for_tender_match(
        self,
        *,
        match: TenderProfileMatch,
        profile_data: dict[str, Any],
        company_master: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """TenderProfileMatch için LLM bağlamı oluşturur.

        Kanıt bütünlüğü garantisi:
            - tender_context içine yalnızca tender_evidence_* alanları yazılır.
            - Profil kanıtları company_context içinde ayrı bölüm olarak gösterilir.
            - valid_chunk_ids yalnızca tender_evidence_chunk_ids içerir.
        """
        tender_ctx = self._build_tender_context(
            tender_id=match.tender_id,
            ikn=match.ikn,
            tender_name=match.tender_name,
            authority_name=match.authority_name,
            evidence_chunk_ids=match.tender_evidence_chunk_ids,
            evidence_sections=match.tender_evidence_sections,
            evidence_texts=match.tender_evidence_texts,
        )
        company_ctx = self._build_company_context(
            profile_code=match.profile_code,
            profile_name=match.profile_name,
            profile_data=profile_data,
            company_master=company_master,
            profile_evidence_chunk_ids=match.profile_evidence_chunk_ids,
            profile_evidence_sections=match.profile_evidence_sections,
            profile_evidence_texts=match.profile_evidence_texts,
        )
        return {
            "matching_mode": "tender",
            "tender_id": match.tender_id,
            "ikn": match.ikn,
            "tender_name": match.tender_name,
            "authority_name": match.authority_name,
            "profile_code": match.profile_code,
            "profile_name": match.profile_name,
            "retrieval_score": match.retrieval_score,
            "score_breakdown": match.score_breakdown.model_dump(),
            "valid_chunk_ids": match.tender_evidence_chunk_ids,
            "tender_context": tender_ctx,
            "company_context": company_ctx,
        }

    # ------------------------------------------------------------------
    # İç yardımcılar
    # ------------------------------------------------------------------

    def _build_tender_context(
        self,
        *,
        tender_id: str,
        ikn: str,
        tender_name: str,
        authority_name: str,
        evidence_chunk_ids: list[str],
        evidence_sections: list[str],
        evidence_texts: list[str],
    ) -> str:
        """İhale bağlamını ihale kanıt listelerinden oluşturur.

        Raises:
            ValueError: Kanıt listelerinin uzunlukları eşit değilse.
        """
        # Uzunluk doğrulaması — zip kayıplarını önler
        if evidence_chunk_ids or evidence_texts:
            # sections listesi boş olabilir; chunk_ids ve texts eşit olmalı
            if len(evidence_chunk_ids) != len(evidence_texts):
                raise ValueError(
                    "İhale kanıt listelerinin uzunlukları eşit olmalıdır: "
                    f"chunk_ids={len(evidence_chunk_ids)}, "
                    f"texts={len(evidence_texts)}"
                )
            # sections listesi verilmişse üçü de eşit olmalı
            if evidence_sections and len(evidence_sections) != len(evidence_chunk_ids):
                raise ValueError(
                    "İhale kanıt listelerinin uzunlukları eşit olmalıdır: "
                    f"chunk_ids={len(evidence_chunk_ids)}, "
                    f"sections={len(evidence_sections)}, "
                    f"texts={len(evidence_texts)}"
                )

        parts: list[str] = [
            f"İhale ID: {tender_id}",
            f"İKN: {ikn}",
            f"İhale Adı: {tender_name}",
            f"İdare: {authority_name}",
        ]

        total_chars = sum(len(p) for p in parts)

        for index in range(len(evidence_chunk_ids)):
            if total_chars >= self.max_chars:
                break
            remaining = self.max_chars - total_chars

            chunk_id = evidence_chunk_ids[index]
            text = evidence_texts[index]

            chunk_text = str(text).strip()
            if not chunk_text:
                continue

            # Bölüm bilgisini kaynak etiketine ekle
            if evidence_sections and index < len(evidence_sections):
                section_label = evidence_sections[index]
            else:
                section_label = ""

            if section_label:
                header = f"[KAYNAK {index + 1} | chunk_id: {chunk_id} | bölüm: {section_label}]"
            else:
                header = f"[KAYNAK {index + 1} | chunk_id: {chunk_id}]"

            if len(chunk_text) > remaining:
                chunk_text = chunk_text[:remaining]

            chunk_entry = f"\n{header}\n{chunk_text}"
            parts.append(chunk_entry)
            total_chars += len(chunk_entry)

        return "\n".join(parts)

    def _build_company_context(
        self,
        *,
        profile_code: str,
        profile_name: str,
        profile_data: dict[str, Any],
        company_master: dict[str, Any] | None,
        profile_evidence_chunk_ids: list[str] | None = None,
        profile_evidence_sections: list[str] | None = None,
        profile_evidence_texts: list[str] | None = None,
    ) -> str:
        """Profil kapasitesini somut kanıt alanlarından oluşturur.

        İhale odaklı modda profil kanıt parçaları da ayrı bölüm olarak eklenir.
        Profil kayıtları hiçbir zaman ihale KAYNAK etiketiyle gösterilmez.

        Genel açıklama (description_expanded) tek başına kanıt sayılmaz;
        sadece bağlam için eklenir. Kapasite kanıtı somut veri alanlarından
        okunur.
        """
        parts: list[str] = [
            f"Profil Kodu: {profile_code}",
            f"Profil Adı: {profile_name}",
        ]

        if company_master:
            kurum_adi = company_master.get("kurum_adi", "")
            if kurum_adi:
                parts.append(f"Kurum: {kurum_adi}")

        # Kapasite kanıtı alanları
        for field_key, label in _PROFILE_CAPACITY_FIELDS:
            val = profile_data.get(field_key)
            if not val:
                continue
            if isinstance(val, list) and val:
                formatted = "\n".join(f"  - {item}" for item in val[:10])
                parts.append(f"\n{label}:\n{formatted}")
            elif isinstance(val, str) and val.strip():
                parts.append(f"\n{label}: {val.strip()}")

        # Sinyal terimleri (güçlü ve negatif)
        signals = profile_data.get("ihale_kategori_sinyalleri", {})
        if isinstance(signals, dict):
            strong = signals.get("guclu_terimler", [])
            if strong:
                parts.append(f"\nGüçlü Terimler: {', '.join(str(t) for t in strong[:15])}")
            negative = signals.get("negatif_terimler", [])
            if negative:
                parts.append(f"\nNegatif Terimler: {', '.join(str(t) for t in negative[:10])}")
            okas = signals.get("okas_kodlari", [])
            if okas:
                parts.append(f"\nOKAS Kodları: {', '.join(str(o) for o in okas[:10])}")

        # Vektörel eşleşmede kullanılan profil kanıtları (ihale odaklı mod)
        p_cids = profile_evidence_chunk_ids or []
        p_secs = profile_evidence_sections or []
        p_texts = profile_evidence_texts or []

        if p_cids and p_texts:
            parts.append("\nVektörel Eşleşmede Kullanılan Profil Kanıtları:")
            for idx in range(len(p_cids)):
                if idx >= len(p_texts):
                    break
                p_cid = p_cids[idx]
                p_text = str(p_texts[idx]).strip()
                if not p_cid or not p_text:
                    continue
                section_label = p_secs[idx] if idx < len(p_secs) else ""
                if section_label:
                    header = f"[PROFİL KANITI {idx + 1} | profile_chunk_id: {p_cid} | bölüm: {section_label}]"
                else:
                    header = f"[PROFİL KANITI {idx + 1} | profile_chunk_id: {p_cid}]"
                parts.append(f"\n{header}\n{p_text[:500]}")

        return "\n".join(parts)

    def format_score_breakdown(self, breakdown: MatchScoreBreakdown) -> str:
        """Skor kırılımını LLM'ye uygun formatta döndürür."""
        return (
            f"Maksimum benzerlik: {breakdown.max_similarity:.4f}\n"
            f"Ortalama benzerlik: {breakdown.top_similarity_mean:.4f}\n"
            f"Bölüm çeşitliliği: {breakdown.section_diversity:.4f}\n"
            f"OKAS desteği: {breakdown.okas_support:.4f}\n"
            f"Başlık desteği: {breakdown.title_support:.4f}\n"
            f"Negatif ceza: {breakdown.negative_term_penalty:.4f}\n"
            f"Nihai skor: {breakdown.final_score:.4f}"
        )


__all__ = ["MatchContextBuilder"]
