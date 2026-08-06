import os
from unittest.mock import MagicMock, patch

from app.decision.ollama_decision_model import (
    GEMMA_CORRECTION_MSG,
    QWEN_CORRECTION_MSG,
    OllamaDecisionModel,
)


def test_correction_messages_at_module_level():
    assert QWEN_CORRECTION_MSG is not None
    assert GEMMA_CORRECTION_MSG is not None
    assert '"decision":' not in QWEN_CORRECTION_MSG
    assert '"decision":' not in GEMMA_CORRECTION_MSG

def test_no_fixed_biases_in_correction_messages():
    # `"decision": "inceleme_gerekli"` bulunmamalı
    assert '"decision": "inceleme_gerekli"' not in QWEN_CORRECTION_MSG
    assert '"decision": "inceleme_gerekli"' not in GEMMA_CORRECTION_MSG
    assert '"confidence": 0.0' not in QWEN_CORRECTION_MSG
    assert '"confidence": 0.50' not in QWEN_CORRECTION_MSG
    assert '"confidence": 0.85' not in QWEN_CORRECTION_MSG
    assert '"confidence": 0.0' not in GEMMA_CORRECTION_MSG
    assert '"confidence": 0.50' not in GEMMA_CORRECTION_MSG
    assert '"confidence": 0.85' not in GEMMA_CORRECTION_MSG
    assert '"confidence": 0.50' not in QWEN_CORRECTION_MSG
    assert '"confidence": 0.50' not in GEMMA_CORRECTION_MSG

def test_gemma_compact_fallback_does_not_mutate_prompt_version():
    model = OllamaDecisionModel(name="gemma:2b", prompt_version="isbak_gemma_review_v3")
    assert model.prompt_version == "isbak_gemma_review_v3"

    mock_client_context = MagicMock()
    mock_client = MagicMock()

    # First response: truncated (done_reason="length")
    resp1 = MagicMock()
    resp1.status_code = 200
    resp1.json.return_value = {
        "response": '{"decision": "uygun", ',
        "done_reason": "length",
        "eval_count": 100
    }

    # Second response: valid compact response
    resp2 = MagicMock()
    resp2.status_code = 200
    resp2.json.return_value = {
        "response": '{"decision": "uygun", "confidence": 0.8, "birincil_profil_kodu": "TEST", "uygunluk_gerekceleri": [], "uygunsuzluk_gerekceleri": [], "eksik_kanitlar": [], "kritik_belirsizlikler": [], "kullanilan_chunk_idleri": [], "kaynak_disinda_bilgi_var_mi": false, "insan_incelemesi_gerekcesi": ""}',
        "done_reason": "stop",
        "eval_count": 100
    }

    mock_client.post.side_effect = [resp1, resp2]
    mock_client_context.__enter__.return_value = mock_client

    with patch("app.decision.ollama_decision_model.httpx.Client", return_value=mock_client_context):
        decision = model.analyze(
            tender_id="1",
            ikn="1",
            category_code="TEST",
            tender_context="",
            company_context=""
        )

    assert decision.decision == "uygun"
    assert model.prompt_version == "isbak_gemma_review_v3"

def test_scripts_use_correct_prompt():
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

    chain_path = os.path.join(base_dir, "scripts", "run_tender_decision_chain.py")
    with open(chain_path, encoding="utf-8") as f:
        chain_content = f.read()
    assert 'isbak_qwen_decision_v4_compact' in chain_content
    assert 'isbak_gemma_review_v3' not in chain_content

    pipe_path = os.path.join(base_dir, "scripts", "run_matching_pipeline.py")
    with open(pipe_path, encoding="utf-8") as f:
        pipe_content = f.read()
    assert 'isbak_gemma_review_v3' in pipe_content
    assert 'isbak_gemma_review_compact' not in pipe_content

def test_compact_fallback_with_schema_correction(monkeypatch):
    """
    Doğrulanacak Senaryo:
    1. model = isbak_gemma_review_v3
    2. İlk yanıt: done_reason="length", truncated JSON (Compact fallback tetiklenir)
    3. İkinci yanıt: Geçersiz şema (Schema correction tetiklenir)
    4. Üçüncü yanıt: Geçerli karar JSON'u
    Beklentiler:
    - İkinci çağrının promptu compact istemi olmalıdır.
    - Üçüncü çağrının promptu compact istemi + düzeltme mesajı olmalıdır.
    - Ana v3 istemine geri dönülmemelidir.
    - İşlem sonunda model.prompt_version "isbak_gemma_review_v3" olarak kalmalıdır.
    """
    model = OllamaDecisionModel(
        name="gemma:test",
        host="http://localhost:11434",
        prompt_version="isbak_gemma_review_v3"
    )
    model.max_json_corrections = 1

    call_prompts = []

    class MockResponse:
        def __init__(self, data, status_code=200):
            self.data = data
            self.status_code = status_code
        def json(self):
            return self.data
        def raise_for_status(self):
            pass

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, json, **kwargs):
            call_prompts.append(json.get("prompt", ""))
            call_idx = len(call_prompts)

            if call_idx == 1:
                # Truncation error
                return MockResponse({
                    "response": '{"birincil_profil_kodu": "TEST"',
                    "done_reason": "length",
                    "eval_count": 2048
                })
            elif call_idx == 2:
                # Schema error (invalid JSON but not truncated)
                return MockResponse({
                    "response": '{"decision": "geçersiz_bir_deger", "confidence": "yüksek"}',
                    "done_reason": "stop",
                    "eval_count": 100
                })
            else:
                # Success
                return MockResponse({
                    "response": '{"decision": "uygun", "confidence": 0.9, "birincil_profil_kodu": "TEST", "zorunlu_kriter_sonuclari": []}',
                    "done_reason": "stop",
                    "eval_count": 150
                })

    import httpx
    monkeypatch.setattr(httpx, "Client", MockClient)

    decision = model.analyze(
        tender_id="T1",
        ikn="IKN",
        category_code="TEST",
        tender_context="test context",
        company_context="test context",
        primary_profile_code="TEST"
    )

    assert len(call_prompts) == 3

    # 1. İlk çağrı v3 istemi olmalıdır
    assert "İSBAK A.Ş. için çalışan bağımsız ikinci görüş ve karar denetim modelisin" in call_prompts[0]

    # 2. İkinci çağrı compact istemi olmalıdır (ve v3 içeriği barındırmamalıdır)
    assert "Sen bağımsız ikinci görüş modelisin. Uzun rapor yazma." in call_prompts[1]
    assert "İSBAK A.Ş. için çalışan bağımsız ikinci görüş ve karar denetim modelisin" not in call_prompts[1]

    # 3. Üçüncü çağrı compact istemi + düzeltme mesajı olmalıdır
    assert "Sen bağımsız ikinci görüş modelisin. Uzun rapor yazma." in call_prompts[2]
    assert "ÖNCEKİ YANITINIZ GEÇERSİZDİ." in call_prompts[2]
    assert "İSBAK A.Ş. için çalışan bağımsız ikinci görüş ve karar denetim modelisin" not in call_prompts[2]

    # Model versiyonu bozulmamalıdır
    assert model.prompt_version == "isbak_gemma_review_v3"

    # Başarılı dönüş olmalıdır
    assert decision.decision == "uygun"
    assert decision.confidence == 0.9
