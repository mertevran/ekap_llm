import pytest

from app.company_profiles.isbak_profile_loader import IsbakProfileLoader
from app.decision.models import DecisionValidationContext
from app.decision.activity_scope import analyze_positive_scope

def test_tek03_profile_loading_and_positive_scope():
    loader = IsbakProfileLoader()
    profile_data = loader.load_profile("TEK-03")
    
    # Simulate how run_tender_decision_chain creates the context
    validation_context = DecisionValidationContext(
        tender_name="Fiber Optik Alt Yapı Revizyon İşi",
        tender_type="hizmet_alimi",
        tender_okas_codes=[],
        evidence_text_by_chunk={
            "chunk_1": "Saha genelinde fiber optik kablolama ve altyapı revizyonu yapılacaktır."
        },
        profile_signals=profile_data.get("ihale_kategori_sinyalleri", {}),
        retrieval_score=0.9,
        profile_name=profile_data.get("profil_adi", ""),
        primary_capabilities=profile_data.get("birincil_yetkinlikler", []),
        profile_description=profile_data.get("description_expanded", ""),
        technical_equipment=profile_data.get("technical_equipment", []),
        abbreviations_and_jargon=profile_data.get("abbreviations_and_jargon", []),
        action_verbs=profile_data.get("action_verbs", []),
    )
    
    # Assert fields are populated
    assert validation_context.profile_name == "Haberleşme ve Ağ Altyapısı"
    assert len(validation_context.primary_capabilities) > 0
    assert "fiber optik" in validation_context.profile_description.lower()
    
    equipment_names = [eq.get("name") for eq in validation_context.technical_equipment]
    assert "Fiber Optik Kablo" in equipment_names
    
    # Test positive scope
    positive_scope = analyze_positive_scope(validation_context)
    assert positive_scope.verified is True

def test_tek04_profile_loading_and_positive_scope():
    loader = IsbakProfileLoader()
    profile_data = loader.load_profile("TEK-04")
    
    # Simulate how run_tender_decision_chain creates the context
    validation_context = DecisionValidationContext(
        tender_name="SİBER GÜVENLİK SİSTEMİ",
        tender_type="mal_alimi",
        tender_okas_codes=[],
        evidence_text_by_chunk={
            "chunk_1": "Kurum geneli siber güvenlik sistemi, sızma testi ve firewall altyapısı."
        },
        profile_signals=profile_data.get("ihale_kategori_sinyalleri", {}),
        retrieval_score=0.9,
        profile_name=profile_data.get("profil_adi", ""),
        primary_capabilities=profile_data.get("birincil_yetkinlikler", []),
        profile_description=profile_data.get("description_expanded", ""),
        technical_equipment=profile_data.get("technical_equipment", []),
        abbreviations_and_jargon=profile_data.get("abbreviations_and_jargon", []),
        action_verbs=profile_data.get("action_verbs", []),
    )
    
    # Assert fields are populated
    assert validation_context.profile_name == "Bilgi ve Siber Güvenlik"
    assert len(validation_context.primary_capabilities) > 0
    assert "siber güvenlik" in validation_context.profile_description.lower()
    
    # Test positive scope
    positive_scope = analyze_positive_scope(validation_context)
    assert positive_scope.verified is True
