from __future__ import annotations

from app.decision.activity_scope import analyze_positive_scope
from app.decision.models import DecisionValidationContext
from app.matching.scope_terms import is_strong_term


def _context(
    title: str,
    evidence: dict[str, str] | None = None,
    signals: dict[str, list[str] | bool] | None = None,
    okas_codes: list[str] | None = None,
    profile_name: str = "",
    primary_capabilities: list[str] | None = None,
    profile_description: str = "",
    technical_equipment: list[dict[str, Any]] | None = None,
    abbreviations_and_jargon: list[dict[str, Any]] | None = None,
    action_verbs: list[str] | None = None,
) -> DecisionValidationContext:
    return DecisionValidationContext(
        tender_name=title,
        evidence_text_by_chunk=evidence or {},
        profile_signals=signals or {},
        tender_okas_codes=okas_codes or [],
        source_origin="test",
        source_complete=True,
        profile_name=profile_name,
        primary_capabilities=primary_capabilities or [],
        profile_description=profile_description,
        technical_equipment=technical_equipment or [],
        abbreviations_and_jargon=abbreviations_and_jargon or [],
        action_verbs=action_verbs or [],
        source_missing_fields=[],
        partial_offer=False,
        tender_parts=[],
    )


def test_is_strong_term_filters_weak_words():
    # Tek genel kelime
    assert not is_strong_term("kamera")
    assert not is_strong_term("yazılım")
    assert not is_strong_term("bakım")
    
    # 2+ token olan her şey güçlüdür
    assert is_strong_term("kamera sistemi")
    assert is_strong_term("yazılım geliştirme")
    assert is_strong_term("araç bakım")
    
    # Listede olmayan tek kelime
    assert is_strong_term("asfaltlama")


def test_positive_scope_verified_with_strong_term_in_title():
    ctx = _context(
        title="Akıllı kavşak sistemi kurulumu işi",
        signals={"guclu_terimler": ["akıllı kavşak"]},
    )
    res = analyze_positive_scope(ctx)
    assert res.verified is True
    assert "akıllı kavşak" in res.strong_matched_terms
    assert "akıllı kavşak" in res.title_matched_terms


def test_positive_scope_verified_with_strong_term_in_evidence():
    ctx = _context(
        title="Mal alımı",
        evidence={"c1": "plaka tanıma sistemi kameraları alınacaktır"},
        signals={"guclu_terimler": ["plaka tanıma sistemi"]},
    )
    res = analyze_positive_scope(ctx)
    assert res.verified is True
    assert "plaka tanıma sistemi" in res.strong_matched_terms
    assert "c1" in res.evidence_chunk_ids


def test_positive_scope_not_verified_with_weak_standalone_term():
    ctx = _context(
        title="kamera sistemi bakım işi",
        signals={"guclu_terimler": ["kamera", "bakım"]},
    )
    res = analyze_positive_scope(ctx)
    # Kamera ve bakım is_strong_term = False olduğundan verified = False
    assert res.verified is False
    assert not res.strong_matched_terms


def test_okas_support_without_text_support_required():
    ctx = _context(
        title="alakasız iş",
        okas_codes=["48000000"],
        signals={
            "okas_kod_on_ekleri": ["48"],
            "okas_metin_destegi_zorunlu": False
        },
    )
    res = analyze_positive_scope(ctx)
    assert res.okas_supported is True
    assert res.okas_text_support_required is False
    assert res.okas_text_support_verified is False


def test_okas_support_with_text_support_required_but_missing():
    ctx = _context(
        title="alakasız iş",
        okas_codes=["48000000"],
        signals={
            "okas_kod_on_ekleri": ["48"],
            "okas_metin_destegi_zorunlu": True,
            "guclu_terimler": ["yazılım geliştirme"],
        },
    )
    res = analyze_positive_scope(ctx)
    assert res.okas_supported is True
    assert res.okas_text_support_required is True
    assert res.okas_text_support_verified is False


def test_okas_support_with_text_support_required_and_present():
    ctx = _context(
        title="yazılım geliştirme işi",
        okas_codes=["48000000"],
        signals={
            "okas_kod_on_ekleri": ["48"],
            "okas_metin_destegi_zorunlu": True,
            "guclu_terimler": ["yazılım geliştirme"],
        },
    )
    res = analyze_positive_scope(ctx)
    assert res.okas_supported is True
    assert res.okas_text_support_required is True
    assert res.okas_text_support_verified is True


def test_tek03_realistic_evidence_match():
    # Gerçek TEK-03 profili JSON sözleşmesi ve gerçekçi kanıt
    ctx = _context(
        title="Fiber Optik Alt Yapı Revizyon İşi",
        evidence={"chunk1": "Bu iş kapsamında haberleşme altyapısı yenilenecektir."},
        signals={
            "guclu_terimler": ["haberleşme altyapısı", "ağ altyapısı"],
            "okas_kod_on_ekleri": ["324", "325"],
        }
    )
    res = analyze_positive_scope(ctx)
    assert res.verified is True
    assert "haberleşme altyapısı" in res.strong_matched_terms


def test_tek04_missing_term_rejection():
    # TEK-04 profili JSON sözleşmesi ("siber güvenlik" güçlü terimlerde yok)
    ctx = _context(
        title="SİBER GÜVENLİK SİSTEMİ",
        evidence={"chunk1": "Kurum siber güvenlik sistemi alacaktır."},
        signals={
            "guclu_terimler": [
                "delil kayıt bütünlüğü", 
                "güvenli veri aktarımı", 
                "erişim kontrolü ve yetkilendirme", 
                "sistem yedekleme"
            ],
            "destekleyici_terimler": ["yetkilendirme", "güvenlik günlüğü", "sayısal imza"],
        }
    )
    res = analyze_positive_scope(ctx)
    # Siber güvenlik ifadesi profilin güçlü terimlerinde YOKTUR.
    # Profil eksikliğinden dolayı eşleşmez, kod hatası değildir.
    assert res.verified is False
    assert not res.strong_matched_terms


def test_pln03_contextual_match():
    ctx = _context(
        title="Coğrafi Bilgi Sistemi Yazılım Geliştirme",
        profile_name="Coğrafi Bilgi Sistemleri",
        primary_capabilities=["Coğrafi Veri Analizi", "CBS Yazılım Geliştirme"],
        signals={"guclu_terimler": ["cbs"]}
    )
    res = analyze_positive_scope(ctx)
    assert res.verified is True
    assert "cbs yazılım geliştirme" in res.primary_capability_matches or res.evidence_strength in ["strong", "contextual"]

def test_aus01_contextual_match():
    ctx = _context(
        title="Akıllı Kavşak Sinyalizasyon Sistemi",
        profile_name="Akıllı Ulaşım Sistemleri",
        technical_equipment=[{"name": "Sinyalizasyon Cihazı", "aliases": []}],
        signals={"guclu_terimler": ["akıllı ulaşım"]}
    )
    res = analyze_positive_scope(ctx)
    assert res.verified is True
    assert "Sinyalizasyon Cihazı" in res.equipment_matches

def test_ent04_contextual_match():
    ctx = _context(
        title="Kartlı Geçiş ve PDKS Sistemi",
        profile_description="Personel devam kontrol sistemleri (PDKS) ve kartlı geçiş.",
        signals={"guclu_terimler": ["elektronik güvenlik"]}
    )
    res = analyze_positive_scope(ctx)
    assert res.verified is True
    assert res.evidence_strength == "contextual"

def test_ops02_negative_priority():
    ctx = _context(
        title="Araç Bakım Onarım Hizmeti",
        action_verbs=["bakım", "onarım"],
        signals={"negatif_terimler": ["araç bakım onarım"]}
    )
    res = analyze_positive_scope(ctx)
    # This just tests positive scope. Positive scope shouldn't be verified just by action verbs.
    assert res.verified is False

def test_tek02_no_generic_okas_only_match():
    ctx = _context(
        title="Dizüstü Bilgisayar Alımı",
        okas_codes=["30210000"],
        signals={"okas_kod_on_ekleri": ["302"]}
    )
    res = analyze_positive_scope(ctx)
    # Only OKAS shouldn't verify
    assert res.verified is False

def test_generic_equipment_without_context_rejected():
    ctx = _context(
        title="Güvenlik Kamerası Alımı",
        technical_equipment=[{"name": "kamera", "aliases": []}]
    )
    res = analyze_positive_scope(ctx)
    assert res.verified is False
