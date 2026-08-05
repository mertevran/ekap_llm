from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="İSBAK profil dosyalarına ihale sınıflandırma sinyallerini ekler."
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path("config/isbak"),
    )
    parser.add_argument(
        "--signals",
        type=Path,
        default=Path("tests/data/isbak_profile_signals.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    signals = json.loads(args.signals.read_text(encoding="utf-8"))
    registry_path = args.config_dir / "profile_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))

    updated = 0
    for entry in registry["profiller"]:
        code = entry["profil_kodu"]
        profile_path = args.config_dir / entry["profil_dosyasi"]
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile["ihale_kategori_sinyalleri"] = signals.get(
            code,
            {
                "guclu_terimler": [],
                "destekleyici_terimler": [],
                "negatif_terimler": [],
                "okas_kodlari": [],
                "okas_kod_on_ekleri": [],
            },
        )
        profile_path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        updated += 1

    print(f"Güncellenen profil sayısı: {updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
