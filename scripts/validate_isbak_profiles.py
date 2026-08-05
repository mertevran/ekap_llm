from __future__ import annotations

from app.company_profiles import IsbakProfileLoader


def main() -> int:
    loader = IsbakProfileLoader()
    errors = loader.validate_all()

    if errors:
        print("İSBAK profil doğrulaması başarısız:")
        for error in errors:
            print(f"- {error}")
        return 1

    profiles = loader.list_profiles()
    print("İSBAK profil doğrulaması başarılı.")
    print(f"Aktif profil sayısı: {len(profiles)}")
    print("Profil kodları:", ", ".join(p["profil_kodu"] for p in profiles))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
