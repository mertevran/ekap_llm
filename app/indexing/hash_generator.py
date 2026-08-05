import hashlib
import json
from datetime import datetime
from typing import Any

from app.domain.tender import TenderRecord


def _serialize_datetime(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def generate_source_hash(tender: TenderRecord) -> str:
    """
    TenderRecord ve altındaki ilan, özellik ve OKAS kodlarını birleştirerek
    deterministik bir SHA-256 özeti çıkarır.
    """

    # 1. Ana İhale Bilgileri
    canonical_tender = {
        "id": tender.id,
        "ikn": tender.ikn,
        "adi": tender.adi,
        "idare_adi": tender.idare_adi,
        "il": tender.il,
        "ihale_tarihi": tender.ihale_tarihi,
        "ihale_turu": tender.ihale_turu,
        "ihale_usulu": tender.ihale_usulu,
        "ihale_durumu": tender.ihale_durumu,
        "kapsam": tender.kapsam,
        "e_ihale": tender.e_ihale,
        "kismi_teklif": tender.kismi_teklif,
        "ihale_yeri": tender.ihale_yeri,
        "isin_yeri": tender.isin_yeri,
        "dokuman_sayisi": tender.dokuman_sayisi,
        # created_at, updated_at, takip_durumu hash'e dahil edilmez
        # çünkü ihale içeriğini değiştiren şeyler değillerdir.
    }

    # 2. İlanlar
    announcements = sorted(
        [
            {
                "id": a.id,
                "ilan_tipi": a.ilan_tipi,
                "ilan_tarihi": a.ilan_tarihi,
                "baslik": a.baslik,
                "icerik": a.icerik,
            }
            for a in tender.announcements
        ],
        key=lambda x: str(x["id"]),
    )

    # 3. Özellikler
    characteristics = sorted(
        [{"id": c.id, "ozellik": c.ozellik} for c in tender.characteristics],
        key=lambda x: int(x["id"]),
    )

    # 4. OKAS Kodları
    okas_codes = sorted(
        [{"id": o.id, "kod": o.kod, "ad": o.ad} for o in tender.okas_codes],
        key=lambda x: str(x["id"]),
    )

    canonical_payload = {
        "tender": canonical_tender,
        "announcements": announcements,
        "characteristics": characteristics,
        "okas_codes": okas_codes,
    }

    json_bytes = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        default=_serialize_datetime,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(json_bytes).hexdigest()
