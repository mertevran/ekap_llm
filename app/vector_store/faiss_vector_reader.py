"""Ortak FAISS vektör okuyucu yardımcısı.

IndexIDMap yapısında saklanan vektörleri dış FAISS kimliğiyle okur.
Hem profil→ihale hem ihale→profil aramalarında kullanılır.

Mevcut FaissVectorStore yapısı:
    - IndexFlatIP (iç / temel indeks)
    - IndexIDMap (dış kimlik eşlemeli sarmalayıcı)
    - payloads: dict[int, dict] — anahtar = dış FAISS int id
"""

from __future__ import annotations

import logging
from typing import Any

import faiss
import numpy as np

logger = logging.getLogger(__name__)

# Bilinen ihale bölüm türleri — section_diversity için referans
KNOWN_SECTION_TYPES: frozenset[str] = frozenset(
    {
        "tender_summary",
        "scope_and_location",
        "characteristics",
        "announcement",
        "okas",
        "qualification",
        "technical_requirements",
        "dates_and_deadlines",
        "main_information",
    }
)


class FaissVectorReader:
    """Tek bir FaissVectorStore üzerinde düşük seviyeli okuma işlemleri.

    İndeksi yeniden yüklemez; çağrılan store'un ``index`` ve ``payloads``
    alanlarını doğrudan kullanır.
    """

    # ------------------------------------------------------------------
    # Tutarlılık doğrulama
    # ------------------------------------------------------------------

    @staticmethod
    def validate_index_payload_consistency(store: Any) -> tuple[bool, str]:
        """İndeks ve payload sayılarının eşit olduğunu doğrular.

        Returns:
            (ok, message) — ok=True ise tutarlı.
        """
        if store.index is None:
            return False, "İndeks belleğe yüklenmemiş."

        ntotal = int(store.index.ntotal)
        npayload = len(store.payloads)

        if ntotal != npayload:
            msg = (
                f"Tutarsızlık: FAISS ntotal={ntotal}, "
                f"payload sayısı={npayload}."
            )
            return False, msg

        return True, f"Tutarlı: {ntotal} vektör, {npayload} payload."

    # ------------------------------------------------------------------
    # Kimlik yardımcıları
    # ------------------------------------------------------------------

    @staticmethod
    def external_ids(store: Any) -> np.ndarray:
        """IndexIDMap'teki tüm dış kimlikleri döndürür (numpy int64)."""
        idx = store.index
        if idx is None:
            return np.array([], dtype=np.int64)

        if hasattr(idx, "id_map"):
            return faiss.vector_to_array(idx.id_map).copy()

        # Düz indeks — kimlikler 0..ntotal-1
        return np.arange(idx.ntotal, dtype=np.int64)

    @staticmethod
    def internal_position_for_external_id(store: Any, external_id: int) -> int:
        """Dış FAISS kimliğini temel indeks içindeki sıra numarasına çevirir.

        Raises:
            KeyError: Kimlik id_map içinde bulunamadı.
        """
        idx = store.index
        if idx is None:
            raise RuntimeError("İndeks belleğe yüklenmemiş.")

        if not hasattr(idx, "id_map"):
            # Düz indeks — dış kimlik = iç sıra
            if external_id < 0 or external_id >= idx.ntotal:
                raise KeyError(
                    f"Dış kimlik sınır dışı: {external_id} (ntotal={idx.ntotal})"
                )
            return int(external_id)

        ext_ids = faiss.vector_to_array(idx.id_map)
        positions = np.flatnonzero(ext_ids == external_id)
        if positions.size == 0:
            raise KeyError(
                f"Dış kimlik id_map içinde bulunamadı: {external_id}"
            )
        return int(positions[0])

    # ------------------------------------------------------------------
    # Vektör okuma
    # ------------------------------------------------------------------

    @staticmethod
    def reconstruct_vector_by_external_id(
        store: Any, external_id: int
    ) -> list[float]:
        """Dış FAISS kimliğiyle vektörü okur.

        IndexIDMap → id_map üzerinden iç sıra numarasına çevirir,
        ardından temel indeksten reconstruct eder.

        Raises:
            KeyError: Kimlik bulunamadı.
            RuntimeError: Reconstruct başarısız.
        """
        idx = store.index
        if idx is None:
            raise RuntimeError("İndeks belleğe yüklenmemiş.")

        if hasattr(idx, "id_map"):
            ext_ids = faiss.vector_to_array(idx.id_map)
            positions = np.flatnonzero(ext_ids == external_id)
            if positions.size == 0:
                raise KeyError(
                    f"Profil vektör kimliği FAISS id_map içinde bulunamadı: "
                    f"id={external_id}"
                )
            internal_pos = int(positions[0])
            base_index = idx.index
        else:
            # Düz indeks
            internal_pos = int(external_id)
            base_index = idx

        try:
            vector = base_index.reconstruct(internal_pos)
        except RuntimeError as exc:
            raise RuntimeError(
                f"Vektör temel indeksten okunamadı: "
                f"dış_id={external_id}, iç_sıra={internal_pos}"
            ) from exc

        return np.asarray(vector, dtype=np.float32).tolist()

    # ------------------------------------------------------------------
    # Payload arama yardımcıları
    # ------------------------------------------------------------------

    @staticmethod
    def find_entries_by_field(
        store: Any,
        field: str,
        value: str,
        *,
        normalize: bool = True,
    ) -> list[tuple[int, dict[str, Any]]]:
        """Payload içindeki ``field`` alanı ``value`` ile eşleşen kayıtları döndürür.

        Returns:
            [(faiss_id, payload), ...] sıralı liste.
        """
        target = value.strip().upper() if normalize else value
        entries: list[tuple[int, dict[str, Any]]] = []

        for faiss_id, payload in store.payloads.items():
            raw = str(payload.get(field) or "").strip()
            candidate = raw.upper() if normalize else raw
            if candidate == target:
                entries.append((int(faiss_id), payload))

        return entries

    @staticmethod
    def find_profile_entries(
        store: Any,
        profile_code: str,
    ) -> list[tuple[int, dict[str, Any]]]:
        """Profil FAISS indeksinden belirli profile_code'a ait parçaları döndürür.

        Sonuçlar section_order'a göre sıralanır.
        """
        normalized_code = str(profile_code).strip().upper()
        entries: list[tuple[int, dict[str, Any]]] = []

        for faiss_id, payload in store.payloads.items():
            payload_code = str(payload.get("profile_code") or "").strip().upper()
            if payload_code == normalized_code:
                entries.append((int(faiss_id), payload))

        # section_order → section adına göre sırala
        entries.sort(
            key=lambda item: (
                int(item[1].get("section_order") or 0),
                str(item[1].get("section") or ""),
            )
        )
        return entries

    @staticmethod
    def find_tender_entries(
        store: Any,
        *,
        tender_id: str | None = None,
        ikn: str | None = None,
    ) -> list[tuple[int, dict[str, Any]]]:
        """İhale FAISS indeksinden tender_id veya İKN ile parçaları döndürür.

        En az biri verilmeli. İkisi birden verilirse her ikisiyle de eşleşir.
        """
        if not tender_id and not ikn:
            raise ValueError("tender_id veya ikn'den en az biri gereklidir.")

        norm_tid = str(tender_id).strip() if tender_id else None
        norm_ikn = str(ikn).strip() if ikn else None

        entries: list[tuple[int, dict[str, Any]]] = []

        for faiss_id, payload in store.payloads.items():
            p_tid = str(payload.get("tender_id") or "").strip()
            p_ikn = str(payload.get("ikn") or "").strip()

            tid_match = (norm_tid is not None) and (p_tid == norm_tid)
            ikn_match = (norm_ikn is not None) and (p_ikn == norm_ikn)

            if tid_match or ikn_match:
                entries.append((int(faiss_id), payload))

        return entries

    # ------------------------------------------------------------------
    # İdare adı ve OKAS normalizasyonu
    # ------------------------------------------------------------------

    @staticmethod
    def resolve_authority_name(payload: dict[str, Any]) -> str:
        """İhale payload'ından idare adını öncelikli sırayla okur."""
        candidates = [
            payload.get("authority_name"),
            payload.get("idare_adi"),
        ]
        # metadata altında
        meta = payload.get("metadata")
        if isinstance(meta, dict):
            candidates.extend(
                [
                    meta.get("idare_adi"),
                    meta.get("authority_name"),
                    meta.get("administration_name"),
                    meta.get("contracting_authority"),
                ]
            )
        candidates.extend(
            [
                payload.get("administration_name"),
                payload.get("contracting_authority"),
            ]
        )

        for c in candidates:
            if c and str(c).strip():
                return str(c).strip()
        return ""

    @staticmethod
    def resolve_okas_codes(payload: dict[str, Any]) -> list[str]:
        """İhale payload'ından OKAS / CPV kodlarını çeker ve normalize eder."""
        raw_codes: list[str] = []

        # Doğrudan alanlar
        for field_name in ("okas_codes", "okas_code", "cpv_codes", "cpv_code"):
            val = payload.get(field_name)
            if isinstance(val, list):
                raw_codes.extend(str(x) for x in val if x)
            elif val and str(val).strip():
                raw_codes.append(str(val).strip())

        # metadata altında
        meta = payload.get("metadata")
        if isinstance(meta, dict):
            for field_name in ("okas_codes", "okas_code", "cpv_codes", "cpv_code"):
                val = meta.get(field_name)
                if isinstance(val, list):
                    raw_codes.extend(str(x) for x in val if x)
                elif val and str(val).strip():
                    raw_codes.append(str(val).strip())

        # Normalize: boşluk, nokta, tire kaldır
        normalized: list[str] = []
        seen: set[str] = set()
        for code in raw_codes:
            norm = code.strip().replace(" ", "").replace(".", "").replace("-", "")
            if norm and norm not in seen:
                seen.add(norm)
                normalized.append(norm)

        return normalized

    @staticmethod
    def resolve_section_type(payload: dict[str, Any]) -> str:
        """Chunk payload'ından gerçek section_type değerini döndürür."""
        st = str(payload.get("section_type") or "").strip()
        if st in KNOWN_SECTION_TYPES:
            return st
        # Geriye uyumluluk: eski alan adları
        for alt in ("section_id", "section"):
            alt_val = str(payload.get(alt) or "").strip()
            if alt_val in KNOWN_SECTION_TYPES:
                return alt_val
        return st  # bilinmeyen olsa bile koruyoruz


__all__ = ["FaissVectorReader", "KNOWN_SECTION_TYPES"]
