import json
import tempfile
from pathlib import Path

import pytest

from app.company_profiles.isbak_profile_loader import IsbakProfileLoader


@pytest.fixture
def temp_profile_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        base_path = Path(tmpdir)

        # company_master.json
        (base_path / "company_master.json").write_text(json.dumps({"name": "ISBAK"}), encoding="utf-8")

        # registry
        registry = {
            "profiller": [
                {
                    "profil_kodu": "ENT-05",
                    "aktif": True,
                    "profil_dosyasi": "ent_05.json",
                    "degerlendirme_kurali_dosyasi": "ent_05_rules.json",
                    "destekleyici_profiller": ["OPS-01"]
                },
                {
                    "profil_kodu": "OPS-01",
                    "aktif": True,
                    "profil_dosyasi": "ops_01.json",
                    "degerlendirme_kurali_dosyasi": "ops_01_rules.json",
                    "destekleyici_profiller": ["TEK-04"]
                },
                {
                    "profil_kodu": "TEK-04",
                    "aktif": True,
                    "profil_dosyasi": "tek_04.json",
                    "degerlendirme_kurali_dosyasi": "tek_04_rules.json",
                    "destekleyici_profiller": []
                }
            ]
        }
        (base_path / "profile_registry.json").write_text(json.dumps(registry), encoding="utf-8")

        # Profiles
        (base_path / "ent_05.json").write_text(json.dumps({"profil_kodu": "ENT-05", "data": "ent"}), encoding="utf-8")
        (base_path / "ops_01.json").write_text(json.dumps({"profil_kodu": "OPS-01", "data": "ops"}), encoding="utf-8")
        (base_path / "tek_04.json").write_text(json.dumps({"profil_kodu": "TEK-04", "data": "tek"}), encoding="utf-8")

        # Rules
        rule_content = {"agirlikli_degerlendirme": {"agirliklar_yuzde": {"a": 100}}}
        (base_path / "ent_05_rules.json").write_text(json.dumps({"profil_kodu": "ENT-05", **rule_content}), encoding="utf-8")
        (base_path / "ops_01_rules.json").write_text(json.dumps({"profil_kodu": "OPS-01", **rule_content}), encoding="utf-8")
        (base_path / "tek_04_rules.json").write_text(json.dumps({"profil_kodu": "TEK-04", **rule_content}), encoding="utf-8")

        yield base_path

def test_resolve_recursive_false(temp_profile_dir):
    loader = IsbakProfileLoader(base_path=temp_profile_dir)
    # A. recursive_supporting_profiles=False testi
    codes = loader.resolve_profile_codes("ENT-05", include_supporting_profiles=True, recursive_supporting_profiles=False)
    assert "ENT-05" in codes
    assert "OPS-01" in codes
    assert "TEK-04" not in codes

def test_resolve_recursive_true(temp_profile_dir):
    loader = IsbakProfileLoader(base_path=temp_profile_dir)
    # B. recursive_supporting_profiles=True testi
    codes = loader.resolve_profile_codes("ENT-05", include_supporting_profiles=True, recursive_supporting_profiles=True)
    assert "ENT-05" in codes
    assert "OPS-01" in codes
    assert "TEK-04" in codes

def test_build_evaluation_context_no_documents(temp_profile_dir):
    loader = IsbakProfileLoader(base_path=temp_profile_dir)
    # C. include_supporting_profile_documents=False testi
    context = loader.build_evaluation_context(
        "ENT-05", 
        include_supporting_profiles=True,
        recursive_supporting_profiles=False,
        include_supporting_profile_documents=False
    )
    assert context["birincil_profil_kodu"] == "ENT-05"
    assert len(context["profiller"]) == 1
    assert context["profiller"][0]["profil_kodu"] == "ENT-05"

    # destekleyici_profil_kodlari doğrudan destekçileri içermeli
    assert "OPS-01" in context["destekleyici_profil_kodlari"]
    assert "TEK-04" not in context["destekleyici_profil_kodlari"]

def test_render_company_context(temp_profile_dir):
    from scripts.run_tender_decision_chain import render_company_context
    loader = IsbakProfileLoader(base_path=temp_profile_dir)

    context_str, rules, sec_codes = render_company_context(loader, "ENT-05")

    ctx = json.loads(context_str)
    assert ctx["birincil_profil_kodu"] == "ENT-05"
    assert ctx["birincil_profil"]["profil_kodu"] == "ENT-05"
    assert "OPS-01" in ctx["destekleyici_profil_kodlari"]

    # D. OPS-01 tam nesnesi olmamalı
    assert len(ctx["profiller"]) == 1
    assert ctx["profiller"][0]["profil_kodu"] == "ENT-05"

    # TEK-04 hiçbir şekilde olmamalı
    assert "TEK-04" not in ctx["yuklenen_profil_kodlari"]

    # E. secondary_codes yalnızca doğrudan destekleyiciyi içermeli
    assert "OPS-01" in sec_codes
    assert "ENT-05" not in sec_codes
    assert "TEK-04" not in sec_codes
