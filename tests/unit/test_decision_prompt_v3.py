import json

import pytest

from app.decision.ollama_decision_model import PROMPTS


def test_qwen_v3_formatting():
    prompt_template = PROMPTS["isbak_qwen_decision_v3"]
    score_breakdown = {"max_similarity": 0.9}
    valid_chunk_ids = ["chunk-1", "chunk-2"]

    formatted = prompt_template.format(
        tender_id="T1",
        ikn="2024/1",
        category_code="TEST-01",
        matching_mode="profile_to_tender",
        retrieval_score=0.88,
        score_breakdown=json.dumps(score_breakdown),
        valid_chunk_ids=json.dumps(valid_chunk_ids),
        tender_context="[KAYNAK 1] ihale metni",
        company_context="[PROFİL KANITI 1] şirket metni"
    )

    assert "T1" in formatted
    assert "2024/1" in formatted
    assert "TEST-01" in formatted
    assert "profile_to_tender" in formatted
    assert "0.88" in formatted
    assert "max_similarity" in formatted
    assert "chunk-1" in formatted
    assert "[KAYNAK 1]" in formatted
    assert "uygun" in formatted
    assert "uygun_degil" in formatted
    assert "inceleme_gerekli" in formatted
    assert "uygun | uygun_degil | inceleme_gerekli" not in formatted
    assert "{category_code}" not in formatted
    assert '"AUS-01"' not in formatted
    assert '"confidence":' not in formatted
    assert '"decision":' not in formatted
    assert "JSON number" in formatted or "0.0 ile 1.0" in formatted
    assert "JSON string" in formatted
    assert "zorunlu_kriter_sonuclari" in formatted

def test_gemma_v3_formatting():
    prompt_template = PROMPTS["isbak_gemma_review_v3"]
    formatted = prompt_template.format(
        tender_id="T2",
        ikn="2024/2",
        category_code="AUS-02",
        matching_mode="tender_to_profile",
        retrieval_score=0.91,
        score_breakdown="{}",
        valid_chunk_ids="[]",
        tender_context="",
        company_context=""
    )

    assert "bağımsız ikinci görüş" in formatted
    assert "tender_to_profile" in formatted

def test_prompt_keyerror_prevention():
    # Only format variables should be processed. If there are stray `{` or `}`, it will raise KeyError or ValueError
    prompt_template = PROMPTS["isbak_qwen_decision_v3"]
    try:
        prompt_template.format(
            tender_id="T1",
            ikn="2024/1",
            category_code="TEST-01",
            matching_mode="profile_to_tender",
            retrieval_score=0.88,
            score_breakdown="{}",
            valid_chunk_ids="[]",
            tender_context="",
            company_context=""
        )
    except KeyError as e:
        pytest.fail(f"KeyError in formatting: {e}")
