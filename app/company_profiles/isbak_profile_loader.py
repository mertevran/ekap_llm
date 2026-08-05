from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class IsbakProfileLoader:
    """İSBAK ana profili ve kategori alt profillerini yükler."""

    def __init__(self, base_path: str | Path = "config/isbak") -> None:
        self.base_path = Path(base_path)
        self.registry_path = self.base_path / "profile_registry.json"
        self.company_path = self.base_path / "company_master.json"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"Profil dosyası bulunamadı: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"JSON kök değeri nesne olmalıdır: {path}")
        return data

    def load_company_master(self) -> dict[str, Any]:
        return self._read_json(self.company_path)

    def load_registry(self) -> dict[str, Any]:
        return self._read_json(self.registry_path)

    def list_profiles(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        profiles = list(self.load_registry().get("profiller", []))
        if active_only:
            profiles = [p for p in profiles if bool(p.get("aktif", False))]
        return profiles

    def _registry_entry(self, profile_code: str) -> dict[str, Any]:
        normalized = str(profile_code).strip().upper()
        for entry in self.list_profiles(active_only=False):
            if str(entry.get("profil_kodu", "")).upper() == normalized:
                return entry
        raise KeyError(f"Bilinmeyen profil kodu: {profile_code}")

    def load_profile(self, profile_code: str) -> dict[str, Any]:
        entry = self._registry_entry(profile_code)
        return self._read_json(self.base_path / entry["profil_dosyasi"])

    def load_evaluation_rules(self, profile_code: str) -> dict[str, Any]:
        entry = self._registry_entry(profile_code)
        return self._read_json(self.base_path / entry["degerlendirme_kurali_dosyasi"])

    def resolve_profile_codes(
        self,
        primary_code: str,
        secondary_codes: list[str] | None = None,
        *,
        include_supporting_profiles: bool = True,
        recursive_supporting_profiles: bool = True,
    ) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()

        def add(code: str) -> None:
            normalized = str(code).strip().upper()
            if not normalized or normalized in seen:
                return
            self._registry_entry(normalized)
            seen.add(normalized)
            ordered.append(normalized)

        add(primary_code)
        for code in secondary_codes or []:
            add(code)

        if include_supporting_profiles:
            if recursive_supporting_profiles:
                index = 0
                while index < len(ordered):
                    entry = self._registry_entry(ordered[index])
                    for support_code in entry.get("destekleyici_profiller", []):
                        add(support_code)
                    index += 1
            else:
                initial_count = len(ordered)
                for index in range(initial_count):
                    entry = self._registry_entry(ordered[index])
                    for support_code in entry.get("destekleyici_profiller", []):
                        add(support_code)

        return ordered

    def build_evaluation_context(
        self,
        primary_code: str,
        secondary_codes: list[str] | None = None,
        include_supporting_profiles: bool = True,
        recursive_supporting_profiles: bool = True,
        include_supporting_profile_documents: bool = True,
    ) -> dict[str, Any]:
        profile_codes = self.resolve_profile_codes(
            primary_code=primary_code,
            secondary_codes=secondary_codes,
            include_supporting_profiles=include_supporting_profiles,
            recursive_supporting_profiles=recursive_supporting_profiles,
        )
        primary_code_upper = str(primary_code).strip().upper()
        supporting_codes = [c for c in profile_codes if c != primary_code_upper]
        
        if include_supporting_profile_documents:
            loaded_profiles = [self.load_profile(code) for code in profile_codes]
        else:
            loaded_profiles = [self.load_profile(primary_code_upper)]
            
        baglam_politikasi = {
            "birincil_profil_tek_karar_profili": True,
            "destekleyici_profiller_birincil_olamaz": True,
            "destekleyici_profiller_sadece_yardimci_sinyaldir": True,
            "zincirleme_destek_profili_yuklenmedi": True
        }

        return {
            "kurum": self.load_company_master(),
            "birincil_profil_kodu": primary_code_upper,
            "yuklenen_profil_kodlari": profile_codes,
            "profiller": loaded_profiles,
            "birincil_profil": self.load_profile(primary_code_upper),
            "destekleyici_profil_kodlari": supporting_codes,
            "baglam_politikasi": baglam_politikasi,
            "degerlendirme_kurallari": self.load_evaluation_rules(primary_code_upper),
        }

    def validate_all(self) -> list[str]:
        errors: list[str] = []
        entries = self.load_registry().get("profiller", [])
        known_codes = {str(entry.get("profil_kodu", "")).strip().upper() for entry in entries}

        for entry in entries:
            code = str(entry.get("profil_kodu", "")).strip().upper()
            if not code:
                errors.append("Profil kodu boş kayıt bulundu.")
                continue
            try:
                profile = self.load_profile(code)
                if profile.get("profil_kodu") != code:
                    errors.append(f"{code}: profil kodu eşleşmiyor.")

                rules = self.load_evaluation_rules(code)
                if rules.get("profil_kodu") != code:
                    errors.append(f"{code}: kural kodu eşleşmiyor.")

                weights = rules.get("agirlikli_degerlendirme", {}).get("agirliklar_yuzde", {})
                if sum(weights.values()) != 100:
                    errors.append(f"{code}: ağırlıklar 100 etmiyor.")

                for support_code in entry.get("destekleyici_profiller", []):
                    if str(support_code).upper() not in known_codes:
                        errors.append(f"{code}: bilinmeyen destek profili {support_code}.")
            except Exception as exc:
                errors.append(f"{code}: {exc}")

        return errors
