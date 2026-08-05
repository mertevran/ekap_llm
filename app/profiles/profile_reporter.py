import csv
import hashlib
import json
from pathlib import Path

from app.profiles.isbak_loader import IsbakProfileLoader


class ProfileReporter:
    def __init__(self, config_dir: str = "config/isbak", output_dir: str = "reports"):
        self.config_dir = Path(config_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _compute_sha256(self, file_path: Path) -> str:
        if not file_path.exists():
            return ""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def generate_all_reports(self) -> None:
        loader = IsbakProfileLoader(str(self.config_dir))
        is_valid, errors, warnings = loader.load_and_validate()

        # 1. Validation Report
        validation_report = {
            "is_valid": is_valid,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "errors": errors,
            "warnings": warnings,
            "active_profile_count": len(loader.active_profiles),
        }
        with open(self.output_dir / "isbak_profile_validation.json", "w", encoding="utf-8") as f:
            json.dump(validation_report, f, indent=2, ensure_ascii=False)

        # 2. Registry CSV
        with open(
            self.output_dir / "isbak_profile_registry.csv", "w", encoding="utf-8", newline=""
        ) as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "profil_kodu",
                    "profil_adi",
                    "profil_ailesi",
                    "aktif",
                    "destekleyici_sayisi",
                    "kural_dosyasi",
                ]
            )
            for reg_prof in loader.registry.get("profiller", []):
                writer.writerow(
                    [
                        reg_prof.get("profil_kodu", ""),
                        reg_prof.get("profil_adi", ""),
                        reg_prof.get("profil_ailesi", ""),
                        reg_prof.get("aktif", ""),
                        len(reg_prof.get("destekleyici_profiller", [])),
                        reg_prof.get("degerlendirme_kurali_dosyasi", ""),
                    ]
                )

        # 3. Missing Fields (Company Master)
        company_master_path = self.config_dir / "company_master.json"
        missing_fields = []
        if company_master_path.exists():
            with open(company_master_path, encoding="utf-8") as f:
                cm = json.load(f)

            fields_to_check = [
                "ortak_belgeler",
                "ortak_personel_profilleri",
                "ortak_tamamlanan_projeler",
                "ortak_is_deneyim_belgeleri",
                ("ortak_kurumsal_bilgiler", "toplam_calisan_sayisi"),
                ("ortak_kurumsal_bilgiler", "toplam_muhendis_sayisi"),
                ("ortak_kurumsal_bilgiler", "mali_yeterlilik"),
                ("ortak_kurumsal_bilgiler", "operasyonel_kapasite"),
            ]

            for field in fields_to_check:
                if isinstance(field, tuple):
                    parent, child = field
                    val = cm.get(parent, {}).get(child)
                    field_name = f"{parent}.{child}"
                else:
                    val = cm.get(field)
                    field_name = field

                is_empty = False
                if val is None:
                    is_empty = True
                elif isinstance(val, list) and len(val) == 0:
                    is_empty = True
                elif isinstance(val, dict) and val.get("veri_durumu") == "eksik":
                    is_empty = True

                if is_empty:
                    missing_fields.append((field_name, "Boş veya eksik veri"))

        with open(
            self.output_dir / "isbak_profile_missing_fields.csv", "w", encoding="utf-8", newline=""
        ) as f:
            writer = csv.writer(f)
            writer.writerow(["field", "status"])
            for mf in missing_fields:
                writer.writerow(mf)

        # 4. Rule Validation Report
        rule_report = {}
        for code, data in loader.active_profiles.items():
            rule = data["rule"]
            rule_report[code] = {
                "zorunlu_kural_sayisi": len(rule.get("zorunlu_kapi_kurallari", [])),
                "agirliklar": rule.get("agirlikli_degerlendirme", {}).get("agirliklar_yuzde", {}),
                "karar_esikleri": rule.get("karar_esikleri", {}),
            }
        with open(
            self.output_dir / "isbak_profile_rule_validation.json", "w", encoding="utf-8"
        ) as f:
            json.dump(rule_report, f, indent=2, ensure_ascii=False)

        # 5. Profile Relationships
        relationships = {}
        for code, data in loader.active_profiles.items():
            relationships[code] = data["registry"].get("destekleyici_profiller", [])
        with open(self.output_dir / "isbak_profile_relationships.json", "w", encoding="utf-8") as f:
            json.dump(relationships, f, indent=2, ensure_ascii=False)

        # Manifest
        manifest = {}
        manifest["profile_registry.json"] = self._compute_sha256(
            self.config_dir / "profile_registry.json"
        )
        manifest["company_master.json"] = self._compute_sha256(
            self.config_dir / "company_master.json"
        )

        for code, data in loader.active_profiles.items():
            prof_file = data["registry"].get("profil_dosyasi")
            kural_file = data["registry"].get("degerlendirme_kurali_dosyasi")
            if prof_file:
                manifest[prof_file] = self._compute_sha256(self.config_dir / prof_file)
            if kural_file:
                manifest[kural_file] = self._compute_sha256(self.config_dir / kural_file)

        with open(self.output_dir / "isbak_profile_manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    reporter = ProfileReporter()
    reporter.generate_all_reports()
    print("Raporlar başarıyla üretildi.")
