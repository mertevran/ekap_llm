"""Unit testler — PublicDecisionResponse ve ProfessionalDecisionReasoning."""
from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from app.decision.models import (
    DecisionValidationContext,
    ModelDecision,
    ValidationResult,
)
from app.decision.public_response import (
    ProfessionalDecisionReasoning,
    PublicDecisionResponse,
    build_public_decision_response,
    _karar_basligi,
    _yonetici_ozeti,
    _teknik_gerekce,
    _katilim_degerlendirmesi,
    _sonuc,
    _inceleme_notu,
)
from app.decision.isbak_decision_pipeline import IsbakDecisionPipeline


# ─── Ortak Test Yardımcıları ────────────────────────────────────────────────


class _Model:
    name = "qwen-test"

    def __init__(self, decision="uygun", activity_match="guclu", uygunsuzluk=None):
        self._decision = decision
        self._activity_match = activity_match
        self._uygunsuzluk = uygunsuzluk or []

    def analyze(self, **kwargs):
        return ModelDecision(
            model_name=self.name,
            decision=self._decision,
            confidence=0.84,
            birincil_profil_kodu="ENT-05",
            ikincil_profil_kodlari=[],
            uygunluk_gerekceleri=["Yazılım bakım kapsamı doğrulanmıştır."],
            uygunsuzluk_gerekceleri=self._uygunsuzluk,
            zorunlu_kriter_sonuclari=[],
            faaliyet_eslesmesi=self._activity_match,
            kullanilan_chunk_idleri=["chk_1", "chk_2"],
        )


class _Validator:
    def __init__(self, forced_decision=None):
        self._forced = forced_decision

    def validate_with_context(self, **kwargs):
        # Pipeline'ın validation override bloğunu tetiklemek için
        # forced_decision="inceleme_gerekli" → has_blocking_issue=True gerekir.
        # forced_decision="uygun_degil" → verified_rejection=True gerekir.
        if self._forced == "inceleme_gerekli":
            return ValidationResult(
                passed=False,
                forced_decision="inceleme_gerekli",
                has_blocking_issue=True,
                human_review_required=True,
            )
        if self._forced == "uygun_degil":
            return ValidationResult(
                passed=False,
                forced_decision="uygun_degil",
                has_blocking_issue=True,
                verified_rejection=True,
            )
        return ValidationResult(passed=True, forced_decision=None)


def _make_pipeline(decision="uygun", activity_match="guclu", uygunsuzluk=None, forced=None):
    return IsbakDecisionPipeline(
        primary_model=_Model(decision, activity_match, uygunsuzluk),
        validator=_Validator(forced_decision=forced),
    )


def _run_pipeline(pipeline, tender_name="Test İhalesi", ikn="2026/1"):
    context = DecisionValidationContext(
        tender_name=tender_name,
        evidence_text_by_chunk={
            f"chk_{i}": f"Kanıt metni {i}" * 10
            for i in range(1, 5)
        },
    )
    return pipeline.run(
        tender_id="T001",
        ikn=ikn,
        tender_name=tender_name,
        authority_name="Test İdaresi",
        category_code="ENT-05",
        primary_profile_code="ENT-05",
        secondary_profile_codes=[],
        tender_context="bağlam",
        company_context="profil",
        evaluation_rules={},
        evidence_count=4,
        valid_chunk_ids=[f"chk_{i}" for i in range(1, 5)],
        validation_context=context,
    )


# ─── Mevcut Test — Korunuyor ─────────────────────────────────────────────────


def test_public_response_is_limited_and_has_two_sentence_summary():
    pipeline = _make_pipeline(decision="uygun", activity_match="guclu")
    result = _run_pipeline(pipeline)
    public = result.to_public_dict()
    assert public["decision"] == "uygun"
    assert len(public["matched_evidences"]) <= 3
    assert all(len(item["excerpt"]) <= 200 for item in public["matched_evidences"])
    assert public["decision_summary"].count(".") <= 2


# ─── Yeni Testler ─────────────────────────────────────────────────────────────


def test_uygun_karari_profesyonel_metin_uretiyor():
    """Test 1: uygun kararı için profesyonel metin üretilir."""
    pipeline = _make_pipeline(decision="uygun", activity_match="guclu")
    result = _run_pipeline(pipeline)
    public = result.to_public_dict()

    assert "professional_reasoning" in public
    pr = public["professional_reasoning"]
    assert pr["karar_basligi"] == "UYGUN"
    assert pr["yonetici_ozeti"]
    assert pr["teknik_gerekce"]
    assert pr["katilim_degerlendirmesi"]
    assert pr["sonuc"]


def test_uygun_degil_profesyonel_metin_uretiyor():
    """Test 2: uygun_degil kararı için profesyonel metin üretilir."""
    pipeline = _make_pipeline(
        decision="uygun_degil",
        activity_match="zayif",
        uygunsuzluk=["Kapsam ihale profiliyle uyuşmamaktadır."],
        forced="uygun_degil",
    )
    result = _run_pipeline(pipeline)
    public = result.to_public_dict()
    pr = public["professional_reasoning"]

    assert pr["karar_basligi"] == "UYGUN DEĞİL"
    assert pr["yonetici_ozeti"]
    assert pr["sonuc"]
    assert "ENT-05" in pr["sonuc"]


def test_inceleme_gerekli_profesyonel_metin_uretiyor():
    """Test 3: inceleme_gerekli kararı için profesyonel metin üretilir."""
    pipeline = _make_pipeline(
        decision="uygun",
        activity_match="kismi",
        forced="inceleme_gerekli",
    )
    result = _run_pipeline(pipeline)
    public = result.to_public_dict()
    pr = public["professional_reasoning"]

    assert pr["karar_basligi"] == "İNCELEME GEREKLİ"
    assert pr["yonetici_ozeti"]
    assert pr["sonuc"]


def test_final_inceleme_ise_uygun_degil_ifadesi_olusmuyor():
    """Test 4: final karar inceleme_gerekli ise primary model uygun_degil olsa bile
    sonuçta kesin olumsuz ifade oluşmamalıdır."""
    pipeline = _make_pipeline(
        decision="uygun_degil",      # primary model uygun_degil dedi
        activity_match="belirsiz",
        forced="inceleme_gerekli",  # ama python validator inceleme_gerekli'ye taşıdı
    )
    result = _run_pipeline(pipeline)
    assert result.final_decision == "inceleme_gerekli"

    public = result.to_public_dict()
    pr = public["professional_reasoning"]

    # Sonuç alanında "uygun değildir" / "takip edilmesi önerilmemektedir" olmamalı
    sonuc_lower = pr["sonuc"].lower()
    assert "takip edilmesi önerilmemektedir" not in sonuc_lower
    assert "uygun değil" not in sonuc_lower

    # Karar başlığı doğru olmalı
    assert pr["karar_basligi"] == "İNCELEME GEREKLİ"


def test_eksik_katilim_bilgisi_faaliyet_uyumsuzlugu_gibi_yazilmiyor():
    """Test 5: eksik katılım bilgisi, faaliyet uyumsuzluğu gibi
    karar_basligi veya yonetici_ozeti'ne yansımamalıdır."""
    pipeline = _make_pipeline(decision="uygun", activity_match="guclu")
    result = _run_pipeline(pipeline)
    public = result.to_public_dict()
    pr = public["professional_reasoning"]

    # katilim_degerlendirmesi ayrı alanda olmalı
    assert "katilim_degerlendirmesi" in public["professional_reasoning"]

    # yonetici_ozeti faaliyet uyumunu ele almalı, katılım eksikliğini değil
    ozet = pr["yonetici_ozeti"].lower()
    assert "faaliyet" in ozet or "teknik uyum" in ozet
    # karar başlığı faaliyet kararını yansıtmalı
    assert pr["karar_basligi"] == "UYGUN"


def test_uygun_kararinda_insan_onayi_bilgisi_korunuyor():
    """Test 6: uygun kararında human_approval_required bilgisi sonuç metnine yansır."""
    pipeline = _make_pipeline(decision="uygun", activity_match="guclu")
    result = _run_pipeline(pipeline)
    public = result.to_public_dict()

    # human_approval_required doğrudan public sözleşmede mevcut olmalı
    assert "human_approval_required" in public

    # Eğer insan onayı gerekiyorsa sonuç buna değinmeli
    if public["human_approval_required"]:
        pr = public["professional_reasoning"]
        assert "onay" in pr["sonuc"].lower() or "inceleme" in pr["sonuc"].lower()


def test_profesyonel_metin_karakter_sinirlarini_gecmiyor():
    """Test 7: profesyonel metin alanları belirlenen karakter sınırlarını aşmamalıdır."""
    LIMITS = {
        "yonetici_ozeti": 400,
        "teknik_gerekce": 700,
        "katilim_degerlendirmesi": 500,
        "sonuc": 400,
        "inceleme_notu": 300,
    }
    for karar, activity in [
        ("uygun", "guclu"),
        ("uygun_degil", "zayif"),
    ]:
        forced = "uygun_degil" if karar == "uygun_degil" else None
        pipeline = _make_pipeline(decision=karar, activity_match=activity, forced=forced)
        result = _run_pipeline(pipeline)
        public = result.to_public_dict()
        pr = public["professional_reasoning"]
        for field_name, limit in LIMITS.items():
            value = pr.get(field_name, "")
            assert len(value) <= limit, (
                f"{field_name} limit {limit} aşıldı: {len(value)} karakter "
                f"(karar={karar})"
            )


def test_public_sozlesmedeki_eski_alanlar_korunuyor():
    """Test 8: mevcut public sözleşme alanları korunmalıdır."""
    pipeline = _make_pipeline(decision="uygun", activity_match="guclu")
    result = _run_pipeline(pipeline)
    public = result.to_public_dict()

    required_fields = [
        "tender_id",
        "ikn",
        "tender_name",
        "authority_name",
        "decision",
        "activity_decision",
        "activity_match",
        "participation_status",
        "confidence",
        "decision_summary",  # eski alan — kaldırılmamış olmalı
        "primary_profile_code",
        "secondary_profile_codes",
        "kismi_teklif",
        "uygun_kisimlar",
        "matched_evidences",
        "unmet_requirements",
        "conflicts",
        "human_review_required",
        "human_review_reason",
        "human_approval_required",
        "human_approval_status",
        "automatic_action_allowed",
        "professional_reasoning",  # yeni alan
    ]
    for field_name in required_fields:
        assert field_name in public, f"Eksik alan: {field_name}"
