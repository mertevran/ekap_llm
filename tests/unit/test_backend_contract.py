from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.integration.backend_contract import (
    BackendContractError,
    final_decision_to_backend_item,
    validate_backend_item,
)


def _decision(final_decision: str = "inceleme_gerekli") -> SimpleNamespace:
    return SimpleNamespace(
        tender_id="t-1",
        ikn="2026/1234567",
        tender_name="Test ihalesi",
        authority_name="Test idaresi",
        primary_profile_code="AUS-04",
        secondary_profile_codes=["TEK-01"],
        evaluated_profile_codes=["AUS-04", "TEK-01"],
        profile_match_scores={"AUS-04": 0.81, "TEK-01": 0.67},
        final_decision=final_decision,
        final_confidence=0.74,
        activity_decision="uygun",
        activity_match="kismi",
        katilim_yeterliligi_durumu="dogrulanmadi",
        human_review_required=True,
        human_review_reason="Katılım belgeleri doğrulanmalı.",
        human_approval_required=False,
        human_approval_status="gerekli_degil",
        automatic_action_allowed=False,
        negative_scope_verified=False,
        matched_negative_terms=[],
        evaluated_at="2026-08-20T08:00:00+00:00",
        primary_model=SimpleNamespace(
            uygunluk_gerekceleri=["Faaliyet kapsamı kısmen eşleşiyor."],
            uygunsuzluk_gerekceleri=[],
        ),
        validation=SimpleNamespace(
            passed=True,
            forced_decision=None,
            issues=[],
            criterion_assessments=[],
        ),
        suitable_parts=[],
        dogrulanamayan_katilim_sartlari=[],
        partial_offer=False,
    )


def test_inceleme_gerekli_backend_sinirinda_belirsiz_olur() -> None:
    item = final_decision_to_backend_item(_decision())
    assert item["karar"] == "belirsiz"
    assert item["notlar"]["original_decision"] == "inceleme_gerekli"


def test_backend_contract_tum_beklenen_alanlari_uretir() -> None:
    item = final_decision_to_backend_item(_decision(), duration_seconds=12.5)
    assert set(item) == {
        "ikn",
        "tender_id",
        "karar",
        "ilgi_skoru",
        "en_ust_benzerlik",
        "eslesen_paket",
        "gerekce",
        "on_filtre",
        "notlar",
        "sure_sn",
    }
    assert item["ikn"] == "2026/1234567"
    assert item["ilgi_skoru"] == pytest.approx(0.74)
    assert item["en_ust_benzerlik"] == pytest.approx(0.81)
    assert item["eslesen_paket"] == "AUS-04"
    assert item["sure_sn"] == pytest.approx(12.5)
    assert item["gerekce"]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("uygun", "uygun"),
        ("uygun_degil", "uygun_degil"),
        ("inceleme_gerekli", "belirsiz"),
    ],
)
def test_karar_mapping(source: str, expected: str) -> None:
    item = final_decision_to_backend_item(_decision(source))
    assert item["karar"] == expected


def test_validate_backend_item_bos_ikn_reddeder() -> None:
    with pytest.raises(BackendContractError, match="ikn"):
        validate_backend_item(
            {
                "ikn": "",
                "karar": "uygun",
                "ilgi_skoru": 0.5,
                "en_ust_benzerlik": None,
                "sure_sn": None,
            }
        )


def test_validate_backend_item_yeni_karar_degerini_reddeder() -> None:
    with pytest.raises(BackendContractError, match="karar"):
        validate_backend_item(
            {
                "ikn": "2026/1",
                "karar": "inceleme_gerekli",
                "ilgi_skoru": 0.5,
                "en_ust_benzerlik": None,
                "sure_sn": None,
            }
        )


def test_validate_backend_item_skor_araligini_korur() -> None:
    with pytest.raises(BackendContractError, match="ilgi_skoru"):
        validate_backend_item(
            {
                "ikn": "2026/1",
                "karar": "uygun",
                "ilgi_skoru": 1.2,
                "en_ust_benzerlik": None,
                "sure_sn": None,
            }
        )
