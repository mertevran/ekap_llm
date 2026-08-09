import pytest

from app.pipeline.isbak_tender_analysis_service import (
    format_company_context,
    format_company_context_compact,
)

@pytest.fixture
def sample_context_dict():
    return {
        "birincil_profil_kodu": "YAZILIM",
        "yuklenen_profil_kodlari": ["YAZILIM", "BAKIM", "SISTEM"],
        "kurum": {
            "dogrulanmis_yetkinlikler": [
                "C# ile masaüstü geliştirme.",
                "C# ile masaüstü geliştirme.",  # duplicate
                "Veritabanı yönetimi.",
                "veritabanı yönetimi.",  # not exact duplicate (different case)
                " C# ile masaüstü geliştirme."  # not exact duplicate (leading space)
            ],
            "dogrulanmis_belgeler": [
                "ISO 9001",
                "ISO 27001",
                "ISO 9001"  # duplicate
            ],
            "eksik_bilgiler": [
                "Yeterli personel yok.",
                "Yeterli personel yok." # duplicate
            ]
        }
    }


def test_compact_company_context_removes_exact_duplicates_and_preserves_order(sample_context_dict):
    legacy = format_company_context(sample_context_dict)
    compact, stats = format_company_context_compact(sample_context_dict)

    assert "PROFİL: YAZILIM" in compact
    assert "EK PROFİLLER: BAKIM, SISTEM" in compact
    
    # Exact duplicate removed, others preserved
    assert stats["verified_capabilities_original_count"] == 5
    assert stats["verified_capabilities_compact_count"] == 4
    assert stats["exact_duplicate_capabilities_removed"] == 1
    
    assert stats["verified_documents_original_count"] == 3
    assert stats["verified_documents_compact_count"] == 2
    assert stats["exact_duplicate_documents_removed"] == 1
    
    assert stats["missing_info_count"] == 2 # original count is 2

    # Check compact contents
    lines = compact.split("\n")
    assert "DOĞRULANMIŞ YETKİNLİKLER:" in lines
    assert "DOĞRULANMIŞ BELGELER:" in lines
    assert "EKSİKLER:" in lines
    
    # Check that legacy is larger than compact
    assert len(compact) < len(legacy)


def test_compact_company_context_omits_empty_sections():
    context_dict = {
        "birincil_profil_kodu": "DONANIM",
        "yuklenen_profil_kodlari": ["DONANIM"],
        "kurum": {
            "dogrulanmis_yetkinlikler": [],
            "dogrulanmis_belgeler": ["TSE Belgesi"],
            "eksik_bilgiler": []
        }
    }
    
    compact, stats = format_company_context_compact(context_dict)
    
    assert "PROFİL: DONANIM" in compact
    assert "EK PROFİLLER:" not in compact
    assert "DOĞRULANMIŞ YETKİNLİKLER:" not in compact
    assert "DOĞRULANMIŞ BELGELER:" in compact
    assert "EKSİKLER:" not in compact
    
    assert stats["verified_capabilities_original_count"] == 0
    assert stats["verified_capabilities_compact_count"] == 0
    
    assert stats["verified_documents_original_count"] == 1
    assert stats["verified_documents_compact_count"] == 1

def test_qwen_compact_company_context_settings(monkeypatch):
    from app.config import get_settings
    
    monkeypatch.setenv("QWEN_COMPACT_COMPANY_CONTEXT", "true")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.qwen_compact_company_context is True
    
    monkeypatch.setenv("QWEN_COMPACT_COMPANY_CONTEXT", "false")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.qwen_compact_company_context is False

def test_real_profiles_compact_integrity():
    from app.company_profiles.isbak_profile_loader import IsbakProfileLoader
    
    loader = IsbakProfileLoader()
    profiles_to_test = ["OPS-02", "AUS-03", "PLN-02", "TEK-03", "TEK-04"]
    
    for code in profiles_to_test:
        ctx = loader.build_evaluation_context(code)
        legacy = format_company_context(ctx)
        compact, stats = format_company_context_compact(ctx)
        
        # Test basic header lengths
        assert len(compact) > 100, f"{code} compact context is too small!"
        
        def check_all_unique(items, text):
            if isinstance(items, str):
                assert items in text, f"Missing string: {items}"
            elif isinstance(items, list):
                seen = set()
                unique_items = []
                for item in items:
                    item_str = str(item)
                    if item_str not in seen:
                        seen.add(item_str)
                        unique_items.append(item_str)
                for item in unique_items:
                    assert item in text, f"Missing list item: {item}"
            elif isinstance(items, dict):
                for k, v in items.items():
                    if v:
                        assert str(v) in text, f"Missing dict value for {k}: {v}"

        # Extract fields to verify
        primary = ctx.get("birincil_profil", {})
        
        profil_adi = primary.get("profil_adi", "")
        if profil_adi:
            check_all_unique(profil_adi, compact)
            
        yetkinlikler = primary.get("birincil_yetkinlikler", [])
        if yetkinlikler:
            check_all_unique(yetkinlikler, compact)
            
        desc = primary.get("description_expanded", "")
        if desc:
            check_all_unique(desc, compact)
            
        equipment = primary.get("technical_equipment", [])
        if equipment:
            check_all_unique(equipment, compact)

        abbreviations = primary.get("abbreviations_and_jargon", [])
        if abbreviations:
            check_all_unique(abbreviations, compact)

        verbs = primary.get("action_verbs", [])
        if verbs:
            check_all_unique(verbs, compact)

        products = primary.get("urunler_ve_hizmetler", [])
        if products:
            check_all_unique(products, compact)

        technologies = primary.get("teknolojiler", [])
        if technologies:
            check_all_unique(technologies, compact)

        capacities = primary.get("kapasite_sinirlari", {})
        if capacities:
            check_all_unique(capacities, compact)

        signals = primary.get("ihale_kategori_sinyalleri", {})
        if isinstance(signals, dict):
            guclu = signals.get("guclu_terimler", [])
            if guclu:
                check_all_unique(guclu, compact)
                
            destekleyici = signals.get("destekleyici_terimler", [])
            if destekleyici:
                check_all_unique(destekleyici, compact)
                
            negatif = signals.get("negatif_terimler", [])
            if negatif:
                check_all_unique(negatif, compact)

            okas = signals.get("okas_kodlari", [])
            if okas:
                check_all_unique(okas, compact)

            on_ekler = signals.get("okas_kod_on_ekleri", [])
            if on_ekler:
                check_all_unique(on_ekler, compact)
