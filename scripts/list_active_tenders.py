from __future__ import annotations

import argparse

from app.database.tender_repository import TenderRepository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Teklif süresi devam eden aktif ihaleleri listeler."
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Gösterilecek en fazla ihale sayısı",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    repository = TenderRepository()
    tenders = repository.get_active_tenders(limit=args.limit)

    print("Aktif ve tarihi geçmemiş ihaleler")
    print("-" * 100)
    print(f"Bulunan kayıt: {len(tenders)}")
    print()

    for index, tender in enumerate(tenders, start=1):
        print(f"{index}. İKN       : {tender.ikn}")
        print(f"   İhale adı  : {tender.adi}")
        print(f"   İhale tarihi: {tender.ihale_tarihi}")
        print(f"   Durum      : {tender.ihale_durumu}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
