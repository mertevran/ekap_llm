from __future__ import annotations

import argparse
from pathlib import Path

from app.config import get_settings
from app.database import TenderNotFoundError, TenderRepository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PostgreSQL veritabanından tek bir ihaleyi okur.")
    parser.add_argument(
        "ikn",
        help="Okunacak ihale kayıt numarası",
    )
    return parser.parse_args()


def build_output_path(output_dir: Path, ikn: str) -> Path:
    safe_ikn = ikn.replace("/", "_").replace("\\", "_")
    return output_dir / f"tender_{safe_ikn}.json"


def main() -> int:
    args = parse_args()
    settings = get_settings()
    repository = TenderRepository()

    print("EKAP İhale Verisi Okuma")
    print("-" * 50)
    print(f"İKN: {args.ikn}")
    print()

    try:
        tender = repository.get_by_ikn(args.ikn)
    except TenderNotFoundError as exc:
        print(f"[HATA] {exc}")
        return 1
    except Exception as exc:
        print(f"[HATA] İhale okunamadı: {exc}")
        return 1

    output_path = build_output_path(
        output_dir=settings.output_dir,
        ikn=tender.ikn,
    )

    output_path.write_text(
        tender.model_dump_json(indent=2),
        encoding="utf-8",
    )

    print(f"İhale adı       : {tender.adi or '(boş)'}")
    print(f"İdare           : {tender.idare_adi or '(boş)'}")
    print(f"İhale türü      : {tender.ihale_turu or '(boş)'}")
    print(f"İhale tarihi    : {tender.ihale_tarihi or '(boş)'}")
    print()
    print(f"İlan sayısı     : {len(tender.announcements)}")
    print(f"Özellik sayısı  : {len(tender.characteristics)}")
    print(f"OKAS kodu sayısı: {len(tender.okas_codes)}")
    print()
    print(f"JSON çıktısı    : {output_path}")
    print("İhale verisi başarıyla okundu.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
