import argparse
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def run_migration(profiles_dir: Path, seed_file: Path, dry_run: bool, force: bool) -> None:
    if not profiles_dir.exists():
        logger.error(f"Profiller dizini bulunamadı: {profiles_dir}")
        return

    if not seed_file.exists():
        logger.error(f"Seed dosyası bulunamadı: {seed_file}")
        return

    seed_data = json.loads(seed_file.read_text(encoding="utf-8"))

    modified_count = 0
    for file_path in profiles_dir.glob("*.json"):
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            profile_code = data.get("profil_kodu")
            if not profile_code or profile_code not in seed_data:
                logger.warning(f"Seed verisi bulunamadı: {profile_code} ({file_path.name})")
                continue

            seed = seed_data[profile_code]
            changed = False

            if data.get("profil_surumu") != "1.1.0":
                data["profil_surumu"] = "1.1.0"
                changed = True

            # Context policy
            if "context_policy" not in data or force:
                data["context_policy"] = {
                    "usage": "profil_yonlendirme_ve_bilgi_getirme",
                    "is_company_evidence": False,
                }
                changed = True

            # Diğer alanlar
            fields = [
                "description_expanded",
                "abbreviations_and_jargon",
                "technical_equipment",
                "action_verbs",
            ]
            for field in fields:
                if field not in data or force or not data.get(field):
                    data[field] = seed.get(field, [] if field != "description_expanded" else "")
                    changed = True

            if changed:
                modified_count += 1
                if dry_run:
                    logger.info(f"[DRY-RUN] {profile_code} güncellenecek.")
                else:
                    file_path.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    logger.info(f"{profile_code} başarıyla 1.1.0'a güncellendi.")
            else:
                logger.info(
                    f"{profile_code} zaten güncel veya veri ezilmek istenmiyor (--force kullanın)."
                )

        except Exception as e:
            logger.error(f"Hata {file_path.name}: {e}")

    if dry_run:
        logger.info(
            f"Dry-run bitti. {modified_count} dosya güncellenecek. Gerçekten uygulamak için --apply kullanın."
        )
    else:
        logger.info(f"Geçiş tamamlandı. {modified_count} dosya güncellendi.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="İSBAK Profillerini 1.1.0 sürümüne geçirir.")
    parser.add_argument("--apply", action="store_true", help="Değişiklikleri kaydeder.")
    parser.add_argument(
        "--force", action="store_true", help="Mevcut alanları tohum(seed) verisiyle ezer."
    )
    parser.add_argument(
        "--profiles-dir",
        type=str,
        default="config/isbak/profiles",
        help="Profillerin bulunduğu dizin.",
    )
    parser.add_argument(
        "--seed-file",
        type=str,
        default="data/profiles_seed_13_2.json",
        help="Seed dosyasının yolu.",
    )

    args = parser.parse_args()

    dry_run = not args.apply
    profiles_dir = Path(args.profiles_dir)
    seed_file = Path(args.seed_file)

    run_migration(profiles_dir, seed_file, dry_run, args.force)
