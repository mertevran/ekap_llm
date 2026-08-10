"""Regression test for IKN 2026/1419224 (OPS-02 generic action term issue)."""

from __future__ import annotations

from app.decision.activity_scope import analyze_positive_scope, analyze_negative_scope
from app.decision.models import DecisionValidationContext, ModelDecision
from app.decision.validator import IsbakDeterministicValidator


def _build_context(
    title: str = "Kilis 112 Acil Çağrı Merkezi Müdürlüğü Bakım Onarım İşi",
    evidence: str = "BİNA ONARIMI YAPTIRILACAKTIR",
    is_construction: bool = True,
) -> DecisionValidationContext:
    return DecisionValidationContext(
        tender_name=title,
        evidence_text_by_chunk={"c1": evidence},
        profile_signals={
            "guclu_terimler": ["teknik bakım", "elektronik bakım", "ağ bakımı"],
            "negatif_terimler": ["bina", "inşaat", "yapım", "sıva", "boya", "duvar"],
        },
        tender_okas_codes=["45000000"] if is_construction else ["50332000"],
        source_origin="test",
        source_complete=True,
        profile_name="Bakım, Onarım ve Teknik Destek",
        primary_capabilities=["bakım", "onarım", "teknik destek", "saha desteği"],
        profile_description="Sistem bakım, onarım ve saha destekleri.",
        technical_equipment=[{"name": "elektronik cihaz", "aliases": ["ekipman"]}],
        abbreviations_and_jargon=[],
        action_verbs=["bakım", "onarım", "destek"],
        source_missing_fields=[],
        partial_offer=False,
        tender_parts=[],
    )


def test_ops02_generic_action_terms_do_not_verify_positive_scope():
    """1, 2. Generic action terms alone do not verify positive scope."""
    ctx = _build_context()
    pos_scope = analyze_positive_scope(ctx)
    neg_scope = analyze_negative_scope(ctx)

    # 1. Action terms bulunabilir
    assert "bakım" in pos_scope.matched_action_terms or "onarım" in pos_scope.matched_action_terms

    # 2. Generic action match tek başına positive_scope.verified = True yapmaz
    assert pos_scope.verified is False

    # 5. Construction domain mismatch doğrulanabiliyorsa negative_scope verified üretir
    assert neg_scope.verified is True
    assert neg_scope.scope_type in ["full", "ambiguous"]  # Başlıkta yoksa mevcut kurala göre ambiguous olur


def test_validator_downgrades_unverified_positive_scope():
    """3, 4, 5. Validator forces human review or rejection if positive scope isn't verified."""
    ctx = _build_context()
    
    primary_decision = ModelDecision(
        model_name="mock",
        decision="uygun",
        confidence=0.9,
        birincil_profil_kodu="OPS-02",
        ikincil_profil_kodlari=[],
        uygunluk_gerekceleri=["Bakım onarım işidir, uygundur."],
        uygunsuzluk_gerekceleri=[],
        zorunlu_kriter_sonuclari=[],
        faaliyet_eslesmesi="guclu",
        kullanilan_chunk_idleri=["c1"],
    )

    validator = IsbakDeterministicValidator()
    result = validator.validate_with_context(
        tender_id="tender-1",
        ikn="2026/1419224",
        category_code="OPS-02",
        primary_decision=primary_decision,
        evidence_count=1,
        valid_chunk_ids=["c1"],
        validation_context=ctx,
    )

    # 4. Model uygun üretse bile validator final sonucu en az inceleme_gerekli seviyesine indirir.
    # 5. Burada negatif kapsam da full eşleştiği için (BİNA) uygun_degil üretilebilir.
    assert result.passed is False
    assert result.forced_decision in ["inceleme_gerekli", "uygun_degil"]

    # Hata kodları arasında yeni kural bulunmalı
    issue_codes = {issue.code for issue in result.issues}
    assert "suitable_without_verified_positive_scope" in issue_codes
    assert "suitable_with_verified_negative_scope" in issue_codes


def test_normal_technical_maintenance_tender_not_broken():
    """6. Normal teknik bakım ihalesi bozulmaz."""
    ctx = _build_context(
        title="Network Switch ve Router Cihazlarının Bakım Onarımı",
        evidence="Kurumumuzdaki elektronik cihaz ve ağ ekipmanlarının periyodik bakımı yapılacaktır.",
        is_construction=False,
    )
    pos_scope = analyze_positive_scope(ctx)
    neg_scope = analyze_negative_scope(ctx)

    # Gerçek teknik bakım ihalesinde, ekipman ("elektronik cihaz") ve bağlam eşleştiği için doğrulanmalı
    assert pos_scope.verified is True
    assert pos_scope.evidence_strength in ["supporting", "contextual", "strong"]
    assert neg_scope.verified is False

    primary_decision = ModelDecision(
        model_name="mock",
        decision="uygun",
        confidence=0.9,
        birincil_profil_kodu="OPS-02",
        ikincil_profil_kodlari=[],
        uygunluk_gerekceleri=["Ağ ekipmanları bakım işidir."],
        uygunsuzluk_gerekceleri=[],
        zorunlu_kriter_sonuclari=[],
        faaliyet_eslesmesi="guclu",
        kullanilan_chunk_idleri=["c1"],
    )

    validator = IsbakDeterministicValidator()
    result = validator.validate_with_context(
        tender_id="tender-2",
        ikn="2026/9999999",
        category_code="OPS-02",
        primary_decision=primary_decision,
        evidence_count=1,
        valid_chunk_ids=["c1"],
        validation_context=ctx,
    )

    # Geçerli ihale başarıyla onaylanmalı
    assert result.passed is True
    assert result.forced_decision is None
