from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", type=Path, default=Path("config/isbak"))
    parser.add_argument(
        "--patch",
        type=Path,
        default=Path("tests/data/isbak_profile_signal_patch_v2.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    registry = json.loads(
        (args.config_dir / "profile_registry.json").read_text(encoding="utf-8")
    )
    patch = json.loads(args.patch.read_text(encoding="utf-8"))
    updated = 0

    for entry in registry["profiller"]:
        code = entry["profil_kodu"]
        if code not in patch:
            continue
        profile_path = args.config_dir / entry["profil_dosyasi"]
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        current = dict(profile.get("ihale_kategori_sinyalleri", {}))
        current.update(patch[code])
        profile["ihale_kategori_sinyalleri"] = current
        profile_path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        updated += 1

    print(f"V2 sinyal yaması uygulanan profil sayısı: {updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
