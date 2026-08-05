from pathlib import Path

from app.company_profiles import IsbakProfileLoader

BASE = Path("config/isbak")


def test_all_profile_files_are_valid():
    loader = IsbakProfileLoader(BASE)
    assert loader.validate_all() == []


def test_registry_contains_twenty_profiles():
    loader = IsbakProfileLoader(BASE)
    assert len(loader.list_profiles()) == 20


def test_supporting_profiles_are_loaded_once():
    loader = IsbakProfileLoader(BASE)
    codes = loader.resolve_profile_codes("AUS-02")
    assert codes[0] == "AUS-02"
    assert len(codes) == len(set(codes))
    assert "TEK-01" in codes
    assert "OPS-02" in codes


def test_evaluation_context_keeps_primary_profile():
    loader = IsbakProfileLoader(BASE)
    context = loader.build_evaluation_context(
        "AUS-01",
        secondary_codes=["PLN-02"],
    )
    assert context["birincil_profil_kodu"] == "AUS-01"
    assert context["degerlendirme_kurallari"]["profil_kodu"] == "AUS-01"
    assert "PLN-02" in context["yuklenen_profil_kodlari"]


def test_profile_weights_sum_to_one_hundred():
    loader = IsbakProfileLoader(BASE)
    for profile in loader.list_profiles():
        rules = loader.load_evaluation_rules(profile["profil_kodu"])
        weights = rules["agirlikli_degerlendirme"]["agirliklar_yuzde"]
        assert sum(weights.values()) == 100
