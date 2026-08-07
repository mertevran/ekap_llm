"""Qwen SQL niyet kodlayıcısının kural ve çıktı testleri."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.sql_encoder.encoder import OllamaTenderIntentEncoder
from app.sql_encoder.models import TenderSearchIntent


def _encoder() -> OllamaTenderIntentEncoder:
    return OllamaTenderIntentEncoder(
        name="qwen3.5:4b-q4_K_M",
        host="http://localhost:11434",
        timeout_seconds=2,
        max_attempts=1,
        backoff_seconds=0,
        num_ctx=4_096,
        num_predict=300,
        num_thread=4,
        num_batch=32,
        max_request_chars=2_000,
    )


def test_write_intent_is_rejected_before_model_call() -> None:
    with pytest.raises(ValueError, match="yalnız veri okumaya"):
        _encoder().encode("Tüm ihaleleri sil")


def test_explicit_words_override_incorrect_model_fields() -> None:
    corrected = _encoder()._apply_explicit_request_rules(
        "Aktif durumdaki 5 rastgele ihaleyi getir.",
        TenderSearchIntent(status="inactive", limit=44, random_order=False),
    )

    assert corrected.status == "active"
    assert corrected.limit == 5
    assert corrected.random_order is True


def test_explicit_limit_cannot_exceed_contract() -> None:
    with pytest.raises(ValidationError):
        _encoder()._apply_explicit_request_rules(
            "999 ihale getir",
            TenderSearchIntent(),
        )


def test_structured_qwen_response_is_validated_and_rules_are_reapplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "response": json.dumps(
                    {
                        "status": "inactive",
                        "limit": 40,
                        "random_order": False,
                        "city": None,
                        "tender_type": None,
                        "authority": None,
                        "keyword": None,
                        "okas_code_prefix": None,
                        "tender_date_from": None,
                        "tender_date_to": None,
                    }
                ),
                "done_reason": "stop",
            }

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            self.payload: dict[str, object] | None = None

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(self, _url: str, *, json: dict[str, object]) -> FakeResponse:
            self.payload = json
            return FakeResponse()

    monkeypatch.setattr("app.sql_encoder.encoder.httpx.Client", FakeClient)

    intent = _encoder().encode("Aktif 5 rastgele ihaleyi getir")

    assert intent == TenderSearchIntent(
        status="active",
        limit=5,
        random_order=True,
    )

