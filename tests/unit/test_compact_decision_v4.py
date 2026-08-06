from __future__ import annotations

import json

from app.decision.ollama_decision_model import (
    COMPACT_DECISION_OUTPUT_SCHEMA,
    DECISION_OUTPUT_SCHEMA,
    OllamaDecisionModel,
)


def _compact_response() -> dict[str, object]:
    return {
        "decision": "uygun",
        "confidence": 0.83,
        "birincil_profil_kodu": "TEST-01",
        "faaliyet_eslesmesi": "guclu",
        "negatif_kapsam_cakismasi": False,
        "gerekceler": ["Kamera sistemi profil faaliyetleriyle örtüşüyor."],
        "faaliyet_belirsizlikleri": [],
        "katilim_belirsizlikleri": [],
        "zorunlu_kriter_sonuclari": [],
        "uygun_kisimlar": [],
        "kullanilan_chunk_idleri": ["chk_1"],
        "kaynak_disinda_bilgi_var_mi": False,
    }


def test_v4_contract_is_shorter_than_v3_contract() -> None:
    assert len(COMPACT_DECISION_OUTPUT_SCHEMA["required"]) < len(
        DECISION_OUTPUT_SCHEMA["required"]
    )
    assert "uygun_kisimlar" in COMPACT_DECISION_OUTPUT_SCHEMA["required"]


def test_v4_uses_compact_schema_and_builds_internal_decision(monkeypatch) -> None:
    payloads: list[dict[str, object]] = []

    class MockResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "response": json.dumps(_compact_response(), ensure_ascii=False),
                "done_reason": "stop",
            }

        @staticmethod
        def raise_for_status() -> None:
            return None

    class MockClient:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            del args

        def post(self, url, json, **kwargs):
            del url, kwargs
            payloads.append(json)
            return MockResponse()

    monkeypatch.setattr("httpx.Client", MockClient)
    model = OllamaDecisionModel(
        name="qwen3.5:4b-q4_K_M",
        prompt_version="isbak_qwen_decision_v4_compact",
    )
    decision = model.analyze(
        tender_id="1",
        ikn="2026/1",
        category_code="TEST-01",
        tender_context="[KAYNAK | chunk_id: chk_1] Kamera sistemi",
        company_context="Kamera sistemi faaliyet profili",
        valid_chunk_ids=["chk_1"],
        primary_profile_code="TEST-01",
    )

    assert payloads[0]["format"] == COMPACT_DECISION_OUTPUT_SCHEMA
    assert decision.decision == "uygun"
    assert decision.uygunluk_gerekceleri
    assert decision.katilim_yeterliligi_durumu == "uygulanamaz"
