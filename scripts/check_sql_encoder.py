#!/usr/bin/env python
"""SQL Encoder şema → SQL → doğrulama → salt okunur çalıştırma duman testi."""

from __future__ import annotations

import argparse
import json

from app.sql_encoder.selection import select_tenders_via_sql_encoder


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--request",
        default="Aktif durumdaki 5 rastgele ihaleyi getir.",
        help="Doğal dilde ihale seçme isteği.",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=20260806,
        help="Rastgele seçimi yeniden üretilebilir yapan tohum.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selection = select_tenders_via_sql_encoder(
        natural_language_request=args.request,
        random_seed=args.random_seed,
    )
    print(json.dumps(selection.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
