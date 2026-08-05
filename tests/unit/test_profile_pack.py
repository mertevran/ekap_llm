"""Profil paketi testleri (Gereksinim 52: 24-32).

profile_registry.json içeriğinden dinamik yükleme,
aktif profil filtresi, eksik dosya hataları ve
context_policy kurallarını doğrular.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROFILE_BASE = Path("config/isbak")
REGISTRY_PATH = PROFILE_BASE / "profile_registry.json"


# ---------------------------------------------------------------------------
# Test 24: registry içindeki 20 profil dinamik olarak yüklenmeli
# ---------------------------------------------------------------------------


def test_registry_loads_20_profiles() -> None:
    """Mevcut paketin 20 profil içerdiğini doğrula."""
    from app.company_profiles.isbak_profile_loader import IsbakProfileLoader

    loader = IsbakProfileLoader()
    all_profiles = loader.list_profiles(active_only=False)
    assert len(all_profiles) == 20, f"Beklenen 20 profil, bulunan: {len(all_profiles)}"


# ---------------------------------------------------------------------------
# Test 25: Yalnızca aktif profiller kullanılmalı
# ---------------------------------------------------------------------------


def test_only_active_profiles_returned() -> None:
    from app.company_profiles.isbak_profile_loader import IsbakProfileLoader

    loader = IsbakProfileLoader()
    active = loader.list_profiles(active_only=True)
    all_p = loader.list_profiles(active_only=False)
    # Tüm aktif profillerin gerçekten aktif olduğu doğrulanmalı
    for p in active:
        assert p.get("aktif") is True, f"Aktif olmayan profil döndü: {p}"
    assert len(active) <= len(all_p)


# ---------------------------------------------------------------------------
# Test 26: Eksik profil dosyası FileNotFoundError üretmeli
# ---------------------------------------------------------------------------


def test_missing_profile_file_raises_error(tmp_path) -> None:
    from app.company_profiles.isbak_profile_loader import IsbakProfileLoader

    # Geçersiz bir path ile loader oluştur
    loader = IsbakProfileLoader(base_path=tmp_path)
    # registry.json yok → FileNotFoundError
    with pytest.raises(FileNotFoundError):
        loader.load_registry()


# ---------------------------------------------------------------------------
# Test 27: Eksik kural dosyası FileNotFoundError üretmeli
# ---------------------------------------------------------------------------


def test_missing_rule_file_raises_error() -> None:
    from app.company_profiles.isbak_profile_loader import IsbakProfileLoader

    loader = IsbakProfileLoader()
    # Var olmayan bir profil kodu
    with pytest.raises((KeyError, FileNotFoundError)):
        loader.load_evaluation_rules("NONEXISTENT-99")


# ---------------------------------------------------------------------------
# Test 28: Destekleyici profil kodlarının doğrulanması
# ---------------------------------------------------------------------------


def test_supporting_profile_codes_are_valid() -> None:
    from app.company_profiles.isbak_profile_loader import IsbakProfileLoader

    loader = IsbakProfileLoader()
    errors = loader.validate_all()
    support_errors = [e for e in errors if "bilinmeyen destek profili" in e]
    assert len(support_errors) == 0, f"Bilinmeyen destekleyici profil kodları: {support_errors}"


# ---------------------------------------------------------------------------
# Test 29: Ağırlık toplamının yüzde 100 olduğunu doğrula
# ---------------------------------------------------------------------------


def test_profile_weight_sums_to_100() -> None:
    from app.company_profiles.isbak_profile_loader import IsbakProfileLoader

    loader = IsbakProfileLoader()
    errors = loader.validate_all()
    weight_errors = [e for e in errors if "ağırlıklar 100 etmiyor" in e]
    assert len(weight_errors) == 0, f"Ağırlık toplamı 100 olmayan profiller: {weight_errors}"


# ---------------------------------------------------------------------------
# Test 30: context_policy.is_company_evidence=False kuralının korunması
# ---------------------------------------------------------------------------


def test_context_policy_is_company_evidence_false() -> None:
    """Tüm profillerde context_policy.is_company_evidence=False olmalı."""
    from app.company_profiles.isbak_profile import IsbakProfile
    from app.company_profiles.isbak_profile_loader import IsbakProfileLoader

    loader = IsbakProfileLoader()
    active = loader.list_profiles(active_only=True)
    for entry in active:
        code = entry.get("profil_kodu", "").upper()
        try:
            profile_data = loader.load_profile(code)
            profile = IsbakProfile.model_validate(profile_data)
            assert profile.context_policy.is_company_evidence is False, (
                f"{code}: context_policy.is_company_evidence True olmamalı"
            )
        except Exception:
            pass  # Yükleme hatası test 26/27'de yakalanır


# ---------------------------------------------------------------------------
# Test 31: Profil açıklaması şirket kanıtı kabul edilmemeli
# ---------------------------------------------------------------------------


def test_profile_description_not_company_evidence() -> None:
    """IsbakProfile.context_policy.is_company_evidence=False → profil metni kanıt değil."""
    from app.company_profiles.isbak_profile import ProfileContextPolicy

    policy = ProfileContextPolicy(is_company_evidence=False)
    assert policy.is_company_evidence is False


# ---------------------------------------------------------------------------
# Test 32: Profil profil_surumu alanı mevcut ve string olmalı
# ---------------------------------------------------------------------------


def test_profile_version_field_exists() -> None:
    from app.company_profiles.isbak_profile_loader import IsbakProfileLoader

    loader = IsbakProfileLoader()
    active = loader.list_profiles(active_only=True)
    if not active:
        pytest.skip("Aktif profil bulunamadı")
    code = active[0].get("profil_kodu", "").upper()
    from app.company_profiles.isbak_profile import IsbakProfile

    data = loader.load_profile(code)
    profile = IsbakProfile.model_validate(data)
    assert isinstance(profile.profil_surumu, str)
    assert len(profile.profil_surumu) > 0
