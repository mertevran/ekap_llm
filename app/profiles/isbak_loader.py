import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ProfileValidationError(Exception):
    pass


class IsbakProfileLoader:
    def __init__(self, config_dir: str = "config/isbak"):
        self.config_dir = Path(config_dir)
        self.registry_file = self.config_dir / "profile_registry.json"

        self.registry: dict[str, Any] = {}
        self.active_profiles: dict[str, dict[str, Any]] = {}
        self.validation_errors: list[str] = []
        self.warnings: list[str] = []

    def load_and_validate(self) -> tuple[bool, list[str], list[str]]:
        """
        Loads the profile registry, profiles and rules.
        Validates them based on structural requirements.
        Returns (is_valid, errors, warnings).
        """
        if not self.registry_file.exists():
            self.validation_errors.append(f"Kayıt kütüğü bulunamadı: {self.registry_file}")
            return False, self.validation_errors, self.warnings

        with open(self.registry_file, encoding="utf-8") as f:
            try:
                self.registry = json.load(f)
            except json.JSONDecodeError as e:
                self.validation_errors.append(f"Geçersiz JSON (profile_registry.json): {e}")
                return False, self.validation_errors, self.warnings

        all_registry_codes = set()
        active_codes = set()

        profiller = self.registry.get("profiller", [])

        for reg_prof in profiller:
            code = reg_prof.get("profil_kodu")
            if not code:
                self.validation_errors.append(f"Profil kodu bulunamayan kayıt: {reg_prof}")
                continue

            if code in all_registry_codes:
                self.validation_errors.append(f"Mükerrer profil kodu: {code}")
            all_registry_codes.add(code)

            if reg_prof.get("aktif") is True:
                active_codes.add(code)

        for reg_prof in profiller:
            if not reg_prof.get("aktif"):
                continue

            code = reg_prof["profil_kodu"]

            # 1. Check supporting profiles exist in registry
            supporting = reg_prof.get("destekleyici_profiller", [])
            for supp_code in supporting:
                if supp_code not in all_registry_codes:
                    self.validation_errors.append(
                        f"{code}: Destekleyici profil '{supp_code}' registry içinde bulunamadı."
                    )

            # 2. Check files
            profil_dosyasi = self.config_dir / reg_prof.get("profil_dosyasi", "")
            kural_dosyasi = self.config_dir / reg_prof.get("degerlendirme_kurali_dosyasi", "")

            if not profil_dosyasi.exists():
                self.validation_errors.append(f"{code}: Profil dosyası eksik: {profil_dosyasi}")
                continue

            if not kural_dosyasi.exists():
                self.validation_errors.append(f"{code}: Kural dosyası eksik: {kural_dosyasi}")
                continue

            with open(profil_dosyasi, encoding="utf-8") as f:
                prof_data = json.load(f)
            with open(kural_dosyasi, encoding="utf-8") as f:
                kural_data = json.load(f)

            # 3. Check codes match
            if prof_data.get("profil_kodu") != code:
                self.validation_errors.append(
                    f"{code}: Dosya içindeki profil kodu eşleşmiyor ({prof_data.get('profil_kodu')})."
                )

            if kural_data.get("profil_kodu") != code:
                self.validation_errors.append(
                    f"{code}: Kural dosyasındaki profil kodu eşleşmiyor ({kural_data.get('profil_kodu')})."
                )

            # 4. Check weight sums to 100
            weights = kural_data.get("agirlikli_degerlendirme", {}).get("agirliklar_yuzde", {})
            total_weight = sum(weights.values())
            if total_weight != 100:
                self.validation_errors.append(
                    f"{code}: Ağırlık toplamı %100 değil (Mevcut: %{total_weight})."
                )

            # 5. Check term overlap
            signals = prof_data.get("ihale_kategori_sinyalleri", {})
            guclu = set(signals.get("guclu_terimler", []))
            negatif = set(signals.get("negatif_terimler", []))
            genel = set(signals.get("genel_terimler", []))

            overlap = guclu.intersection(negatif)
            if overlap:
                self.validation_errors.append(
                    f"{code}: Güçlü ve negatif terim çakışması var: {overlap}"
                )

            if genel.intersection(guclu):
                self.warnings.append(f"{code}: Genel terimler güçlü kanıt olarak da listelenmiş.")

            # 6. Check context policy
            context_policy = prof_data.get("context_policy", {})
            if context_policy.get("is_company_evidence", True) is not False:
                self.validation_errors.append(
                    f"{code}: context_policy.is_company_evidence kuralı 'false' olmalıdır."
                )

            # 7. Check Decision Thresholds Logical Validity
            esikler = kural_data.get("karar_esikleri", {})
            uygun_puan = esikler.get("doğrudan_uygun", {}).get("asgari_toplam_puan", 0)
            ilgisiz_puan = esikler.get("ilgisiz", {}).get("toplam_puan_ust_siniri", 0)

            if uygun_puan <= ilgisiz_puan:
                self.validation_errors.append(
                    f"{code}: Mantıksal hata: Uygun eşiği ({uygun_puan}) ilgisiz sınırından ({ilgisiz_puan}) küçük/eşit olamaz."
                )

            self.active_profiles[code] = {
                "registry": reg_prof,
                "profile": prof_data,
                "rule": kural_data,
            }

        logger.info(f"Yükleme tamamlandı. Aktif profil sayısı: {len(self.active_profiles)}")

        is_valid = len(self.validation_errors) == 0
        return is_valid, self.validation_errors, self.warnings

    def get_active_profiles(self) -> dict[str, dict[str, Any]]:
        return self.active_profiles
