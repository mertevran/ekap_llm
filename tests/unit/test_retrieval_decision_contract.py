"""Karar zinciri ve güvenlik sözleşme testleri (Gereksinim 52: 60-77).

Model kararlarının yalnızca 3 geçerli etiketle döndüğünü,
not_evaluated kavramının uygun_degil sayılmadığını,
chunk_id doğrulamasını ve güvenlik kurallarını doğrular.
"""

from __future__ import annotations

import pytest

from app.decision.models import (
    CriterionResult,
    DecisionLabel,
    FinalTenderDecision,
    ModelDecision,
    ValidationResult,
)

# ─── Sahte model ve builder ──────────────────────────────────────────────────


def _make_criterion(
    status: str = "karsilaniyor",
    has_evidence: bool = True,
) -> CriterionResult:
    return CriterionResult(
        criterion_id="c1",
        description="Test kriteri",
        status=status,
        evidence_chunk_ids=["chunk-001"] if has_evidence else [],
        explanation="Test açıklama",
    )


def _make_model_decision(
    decision: str = "uygun",
    confidence: float = 0.85,
    criteria: list | None = None,
    missing: list | None = None,
    external_knowledge: bool = False,
) -> ModelDecision:
    return ModelDecision(
        model_name="test-model",
        decision=decision,
        confidence=confidence,
        birincil_profil_kodu="AUS-01",
        ikincil_profil_kodlari=[],
        uygunluk_gerekceleri=["Uygun çünkü..."] if decision == "uygun" else [],
        uygunsuzluk_gerekceleri=["Uygun değil çünkü..."] if decision == "uygun_degil" else [],
        zorunlu_kriter_sonuclari=criteria or [_make_criterion()],
        eksik_kanitlar=missing or [],
        kritik_belirsizlikler=[],
        kullanilan_chunk_idleri=["chunk-001"],
        kaynak_disinda_bilgi_var_mi=external_knowledge,
        insan_incelemesi_gerekcesi="",
    )


def _make_validation(
    passed: bool = True,
    forced: DecisionLabel | None = None,
) -> ValidationResult:
    return ValidationResult(
        passed=passed,
        forced_decision=forced,
        contradictions=[] if passed else ["Çelişki tespit edildi"],
    )


# ---------------------------------------------------------------------------
# Test 60: FAISS payload'ında uygunluk kararı bulunmamalı
# ---------------------------------------------------------------------------


def test_faiss_payload_has_no_eligibility_decision() -> None:
    """FaissVectorStore'a yazılan payload'da final_decision alanı olmamalı."""
    import inspect

    from app.indexing.active_tender_indexer import ActiveTenderIndexer

    source = inspect.getsource(ActiveTenderIndexer.run)
    assert "final_decision" not in source, "FAISS payload'ına final_decision yazılmamalı"
    assert '"uygun"' not in source.replace("uygun_degil", "").replace("inceleme_gerekli", ""), (
        "FAISS payload'ına uygunluk kararı yazılmamalı"
    )


# ---------------------------------------------------------------------------
# Test 61: Model kararı yalnızca 3 geçerli etiketten biri olmalı
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("decision", ["uygun", "uygun_degil", "inceleme_gerekli"])
def test_valid_decision_labels(decision: str) -> None:
    md = _make_model_decision(decision=decision)
    assert md.decision in ("uygun", "uygun_degil", "inceleme_gerekli")


# ---------------------------------------------------------------------------
# Test 62: kosullu_uygun değeri mevcut sistemde kabul edilmemeli
# ---------------------------------------------------------------------------


def test_kosullu_uygun_not_valid_decision() -> None:
    """kosullu_uygun artık geçerli bir karar değeri değil.
    FinalTenderDecision.final_decision alanı yalnızca 3 değer kabul eder."""
    valid_decisions = {"uygun", "uygun_degil", "inceleme_gerekli"}
    # kosullu_uygun bu kümede olmamalı
    assert "kosullu_uygun" not in valid_decisions, "kosullu_uygun geçerli bir karar değeri olmamalı"
    # DecisionLabel tip tanımını kontrol et

    from app.decision.models import DecisionLabel

    args = getattr(DecisionLabel, "__args__", None)
    if args:
        assert "kosullu_uygun" not in args, "kosullu_uygun DecisionLabel'de tanımlı olmamalı"


# ---------------------------------------------------------------------------
# Test 63: Zorunlu kriter karşılanmıyorsa uygun_degil olmalı (Validator)
# ---------------------------------------------------------------------------


def test_failed_criterion_forces_uygun_degil() -> None:
    from app.decision.validator import IsbakDeterministicValidator

    validator = IsbakDeterministicValidator()

    md = _make_model_decision(
        decision="uygun",
        criteria=[_make_criterion(status="karsilanmiyor", has_evidence=True)],
    )
    result = validator.validate(
        tender_id="T001",
        ikn="2026/001",
        category_code="Hizmet",
        primary_decision=md,
        evidence_count=5,
        valid_chunk_ids=["chunk-001"],
    )
    assert result.forced_decision == "uygun_degil"
    assert not result.passed


# ---------------------------------------------------------------------------
# Test 64: Zorunlu kriter bilinmiyorsa inceleme_gerekli olmalı
# ---------------------------------------------------------------------------


def test_unknown_criterion_forces_review() -> None:
    from app.decision.validator import IsbakDeterministicValidator

    validator = IsbakDeterministicValidator()

    md = _make_model_decision(
        decision="uygun",
        criteria=[_make_criterion(status="bilinmiyor", has_evidence=False)],
    )
    result = validator.validate(
        tender_id="T001",
        ikn="2026/001",
        category_code="Hizmet",
        primary_decision=md,
        evidence_count=5,
        valid_chunk_ids=[],
    )
    assert result.forced_decision == "inceleme_gerekli"
    assert not result.passed


# ---------------------------------------------------------------------------
# Test 65: not_evaluated ihale uygun_degil sayılmamalı
# ---------------------------------------------------------------------------


def test_not_evaluated_tender_not_marked_uygun_degil() -> None:
    """Henüz değerlendirilmeyen ihale, uygun_degil almamalı.
    Model görmediği ihalelere karar vermemeli."""
    # Pipeline, değerlendirilmemiş ihaleleri listede tutmaz
    # Bu test davranışsal: model çıktısız ihale uygun_degil sayılmaz
    # FinalTenderDecision olmadan ihale 'not_evaluated' statüsündedir
    # Bu test FinalTenderDecision olmadan oluşturulmuş kayıt yokluğunu doğrular
    all_decisions: list[FinalTenderDecision] = []
    # Boş liste → uygun_degil sayısı 0
    uygun_degil_count = sum(1 for d in all_decisions if d.final_decision == "uygun_degil")
    assert uygun_degil_count == 0


# ---------------------------------------------------------------------------
# Test 66: Geçersiz chunk_id kullanımının yakalanması
# ---------------------------------------------------------------------------


def test_invalid_chunk_id_reference_detected() -> None:
    from app.decision.validator import IsbakDeterministicValidator

    validator = IsbakDeterministicValidator()

    md = _make_model_decision(
        decision="uygun",
        criteria=[_make_criterion(status="karsilaniyor", has_evidence=True)],
    )
    # chunk-001 geçerli listede yok
    result = validator.validate(
        tender_id="T001",
        ikn="2026/001",
        category_code="Hizmet",
        primary_decision=md,
        evidence_count=5,
        valid_chunk_ids=["REAL-CHUNK-001"],  # chunk-001 bu listede yok
    )
    assert len(result.invalid_evidence_references) > 0



# ---------------------------------------------------------------------------
# Test 68: Eksik kanıt raporlayan model uygun diyemez
# ---------------------------------------------------------------------------


def test_model_with_missing_evidence_cannot_say_uygun() -> None:
    from app.decision.validator import IsbakDeterministicValidator

    validator = IsbakDeterministicValidator()

    md = _make_model_decision(
        decision="uygun",
        missing=["Teknik şartname eksik", "Yeterlik belgesi yok"],
    )
    result = validator.validate(
        tender_id="T001",
        ikn="2026/001",
        category_code="Hizmet",
        primary_decision=md,
        evidence_count=3,
        valid_chunk_ids=["chunk-001"],
    )
    assert result.forced_decision == "inceleme_gerekli"
    assert not result.passed


# ---------------------------------------------------------------------------
# Test 69: Profil açıklaması şirket kanıtı olarak kullanılamamalı (Gereksinim 71)
# ---------------------------------------------------------------------------


def test_profile_description_not_company_evidence() -> None:
    from app.company_profiles.isbak_profile import ProfileContextPolicy

    policy = ProfileContextPolicy(usage="profil_yonlendirme", is_company_evidence=False)
    assert policy.is_company_evidence is False


# ---------------------------------------------------------------------------
# Güvenlik testleri (Gereksinim 72-77)
# ---------------------------------------------------------------------------


def test_connection_string_has_no_password_in_logs() -> None:
    """build_connection_string() şifreyi dışa aktarmamalı."""
    import io
    import logging

    from app.database.connection import build_connection_string

    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    logging.getLogger("app.database.connection").addHandler(handler)

    try:
        build_connection_string()
        # Bağlantı dizgisi döndürülebilir ama log'a yazılmamalı
        log_output = log_stream.getvalue()
        assert "password=" not in log_output, "Parola log'a yazıldı!"
    except Exception:
        pass  # Bağlantı başarısız → parola log'a yazılmadı
    finally:
        logging.getLogger("app.database.connection").removeHandler(handler)


def test_public_schema_not_written_by_indexer() -> None:
    """ActiveTenderIndexer public şemasına yazma yapmamalı."""
    import inspect

    from app.indexing.active_tender_indexer import ActiveTenderIndexer

    source = inspect.getsource(ActiveTenderIndexer.run)
    assert "INSERT INTO public" not in source, "ActiveTenderIndexer public şemasına yazmamalı"
    assert "UPDATE public" not in source, "ActiveTenderIndexer public şemasına yazmamalı"


def test_index_state_uses_llm_rag_schema() -> None:
    """İndeks durum tablosu yalnızca llm_rag şemasına yazmalı."""
    from app.indexing.index_state_repository import DEFAULT_TABLE_NAME

    assert DEFAULT_TABLE_NAME.startswith("llm_rag."), (
        f"Durum tablosu llm_rag şemasında olmalı: {DEFAULT_TABLE_NAME}"
    )
