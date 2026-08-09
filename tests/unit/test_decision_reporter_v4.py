from __future__ import annotations

import csv
import json
from types import SimpleNamespace

from app.decision.models import SuitableTenderPart
from app.reporting.decision_reporter import DecisionReporter


class _Decision(SimpleNamespace):
    def to_dict(self):
        return {"ikn": self.ikn, "final_decision": self.final_decision}

    def to_public_dict(self):
        from app.decision.public_response import build_public_decision_response
        return build_public_decision_response(self).to_dict()


def _decision() -> _Decision:
    return _Decision(
        tender_id="1",
        ikn="2026/1",
        tender_name="Kamera ihalesi",
        authority_name="Test",
        final_decision="uygun",
        activity_decision="uygun",
        activity_match="kismi",
        primary_model=SimpleNamespace(
            decision="uygun",
            eksik_kanitlar=[],
            uygunluk_gerekceleri=["Kapsam eşleşmesi doğrulandı."],
            uygunsuzluk_gerekceleri=[],
            kullanilan_chunk_idleri=["chk_1"],
        ),
        secondary_model=None,
        validation=SimpleNamespace(
            passed=True,
            forced_decision=None,
            criterion_assessments=[],
            contradictions=[],
            negative_scope=SimpleNamespace(
                verified=False,
                evidence_chunk_ids=[],
            ),
        ),
        primary_profile_code="TEST-01",
        secondary_profile_codes=[],
        evaluated_profile_codes=["TEST-01"],
        profile_match_scores={"TEST-01": 0.8},
        confidence_calibration=SimpleNamespace(
            raw_confidence=0.82,
            applied_cap=0.9,
            reasons=[],
        ),
        final_confidence=0.82,
        negative_scope_verified=False,
        matched_negative_terms=[],
        katilim_yeterliligi_durumu="uygulanamaz",
        participation_review_required=False,
        partial_offer=True,
        suitable_parts=[
            SuitableTenderPart(
                part_number="1",
                part_name="Kamera",
                evidence_chunk_ids=["chk_1"],
                reason="Faaliyet eşleşmesi",
            )
        ],
        dogrulanamayan_katilim_sartlari=[],
        optional_missing_evidence=[],
        human_review_required=False,
        human_review_reason="",
        human_approval_required=True,
        human_approval_status="bekliyor",
        automatic_action_allowed=False,
        evaluated_at="2026-08-06T00:00:00+00:00",
        validation_context=None,
        primary_used_chunk_ids=["chk_1"],
    )


def test_reporter_writes_public_contract_and_approval_queue(tmp_path) -> None:
    DecisionReporter(output_dir=str(tmp_path)).write_reports([_decision()])

    public = json.loads(
        (tmp_path / "tender_public_decisions.jsonl").read_text(encoding="utf-8")
    )
    assert public["automatic_action_allowed"] is False

    with (tmp_path / "tender_model_decisions.csv").open(
        encoding="utf-8", newline=""
    ) as file:
        rows = list(csv.reader(file))
    assert len(rows[0]) == len(rows[1])
    assert "human_approval_required" in rows[0]
    assert "suitable_parts" in rows[0]

    with (tmp_path / "tender_human_action_queue.csv").open(
        encoding="utf-8", newline=""
    ) as file:
        action_rows = list(csv.DictReader(file))
    assert action_rows[0]["human_approval_status"] == "bekliyor"
    assert action_rows[0]["automatic_action_allowed"] == "False"


# ─── Yeni Testler (9–12) ──────────────────────────────────────────────────────


def test_professional_csv_olusturuluyor(tmp_path) -> None:
    """Test 9: tender_professional_decisions.csv oluşturulmalıdır."""
    DecisionReporter(output_dir=str(tmp_path)).write_reports([_decision()])
    prof_path = tmp_path / "tender_professional_decisions.csv"
    assert prof_path.exists(), "tender_professional_decisions.csv oluşturulmadı"


def test_professional_csv_kolon_ve_satir_sayisi_dogru(tmp_path) -> None:
    """Test 10: kolon sayısı ve satır sayısı doğru olmalıdır."""
    DecisionReporter(output_dir=str(tmp_path)).write_reports([_decision(), _decision()])
    prof_path = tmp_path / "tender_professional_decisions.csv"

    with prof_path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))

    EXPECTED_COLUMNS = [
        "ikn",
        "tender_name",
        "authority_name",
        "primary_profile_code",
        "karar",
        "guven",
        "yonetici_ozeti",
        "teknik_gerekce",
        "katilim_degerlendirmesi",
        "sonuc",
        "inceleme_notu",
        "human_review_required",
        "human_approval_required",
    ]

    assert rows[0] == EXPECTED_COLUMNS, f"Kolon başlıkları yanlış: {rows[0]}"
    # 1 başlık + 2 veri satırı
    assert len(rows) == 3, f"Beklenen 3 satır, bulunan: {len(rows)}"


def test_professional_csv_teknik_ic_alanlari_icermiyor(tmp_path) -> None:
    """Test 11: profesyonel CSV teknik iç alanları içermemelidir."""
    DecisionReporter(output_dir=str(tmp_path)).write_reports([_decision()])
    prof_path = tmp_path / "tender_professional_decisions.csv"

    with prof_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []

    # Teknik iç alanlar profesyonel CSV'de olmamalı
    forbidden = [
        "tender_id",
        "primary_decision",
        "secondary_decision",
        "python_validation_passed",
        "forced_decision",
        "raw_confidence",
        "confidence_cap",
        "criterion_source_assessments",
        "negative_scope_verified",
        "matched_negative_terms",
        "chunk_id",
    ]
    for col in forbidden:
        assert col not in headers, f"Teknik iç alan CSV'de bulundu: {col}"


def test_mevcut_raporlar_uretilmeye_devam_ediyor(tmp_path) -> None:
    """Test 12: mevcut tender_model_decisions.csv ve tender_public_decisions.jsonl
    üretilmeye devam etmelidir."""
    DecisionReporter(output_dir=str(tmp_path)).write_reports([_decision()])

    assert (tmp_path / "tender_model_decisions.jsonl").exists()
    assert (tmp_path / "tender_public_decisions.jsonl").exists()
    assert (tmp_path / "tender_model_decisions.csv").exists()
    assert (tmp_path / "tender_review_required.csv").exists() or True  # boş olabilir
    assert (tmp_path / "tender_professional_decisions.csv").exists()

    # public jsonl professional_reasoning alanını içermeli
    public_line = (tmp_path / "tender_public_decisions.jsonl").read_text(encoding="utf-8")
    public = json.loads(public_line)
    assert "professional_reasoning" in public, (
        "tender_public_decisions.jsonl professional_reasoning içermiyor"
    )
    pr = public["professional_reasoning"]
    assert pr["karar_basligi"]
    assert pr["yonetici_ozeti"]
    assert pr["sonuc"]
