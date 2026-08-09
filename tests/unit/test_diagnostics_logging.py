import logging
from unittest.mock import patch
from app.decision.ollama_decision_model import OllamaDecisionModel
from app.pipeline.exceptions import DecisionServiceError
import pytest

class MockResponse:
    def __init__(self, data):
        self.data = data
        self.status_code = 200
    def json(self): return self.data
    def raise_for_status(self): pass

class MockClient:
    def __init__(self, *args, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def post(self, url, json, **kwargs):
        return MockResponse({
            "response": '{"decision": "uygun", "confidence": 0.9, "birincil_profil_kodu": "TEST", "zorunlu_kriter_sonuclari": [], "faaliyet_eslesmesi": "guclu", "negatif_kapsam_cakismasi": false, "uygunluk_gerekceleri": ["gerekçe"]}',
            "done_reason": "stop",
            "done": True,
            "eval_count": 100,
            "prompt_eval_count": 50,
            "total_duration": 5000000000,
            "load_duration": 1000000000,
            "prompt_eval_duration": 2000000000,
            "eval_duration": 2000000000,
        })

def test_diagnostics_logs_all_metrics(caplog):
    model = OllamaDecisionModel(name="test-model", host="http://localhost", timeout_seconds=10)
    
    with patch("app.decision.ollama_decision_model.httpx.Client", MockClient):
        with caplog.at_level(logging.INFO):
            model.analyze(
                tender_id="T1", ikn="I1", category_code="TEST", 
                tender_context="ctx", company_context="ctx", 
                primary_profile_code="TEST"
            )
            
    diagnostics_logs = [record.message for record in caplog.records if "[DIAGNOSTICS]" in record.message]
    assert len(diagnostics_logs) > 0
    log = diagnostics_logs[0]
    
    assert "load_duration=1000000000" in log
    assert "prompt_eval_duration=2000000000" in log
    assert "eval_duration=2000000000" in log
    assert "total_duration=5000000000" in log
    assert "prompt_eval_count=50" in log
    assert "eval_count=100" in log
    
    # 50 tokens / 2.0s = 25.0
    assert "prompt_tokens_per_second=25.00" in log
    # 100 tokens / 2.0s = 50.0
    assert "generation_tokens_per_second=50.00" in log

def test_diagnostics_logs_with_zero_durations(caplog):
    class ZeroDurationClient(MockClient):
        def post(self, url, json, **kwargs):
            return MockResponse({
                "response": '{"decision": "uygun", "confidence": 0.9, "birincil_profil_kodu": "TEST", "zorunlu_kriter_sonuclari": [], "faaliyet_eslesmesi": "guclu", "negatif_kapsam_cakismasi": false, "uygunluk_gerekceleri": ["gerekçe"]}',
                "done_reason": "stop",
                "done": True,
                "eval_count": 100,
                "prompt_eval_count": 50,
                "total_duration": 0,
                "load_duration": 0,
                "prompt_eval_duration": 0,
                "eval_duration": 0,
            })
            
    model = OllamaDecisionModel(name="test-model", host="http://localhost", timeout_seconds=10)
    
    with patch("app.decision.ollama_decision_model.httpx.Client", ZeroDurationClient):
        with caplog.at_level(logging.INFO):
            model.analyze(
                tender_id="T1", ikn="I1", category_code="TEST", 
                tender_context="ctx", company_context="ctx", 
                primary_profile_code="TEST"
            )
            
    diagnostics_logs = [record.message for record in caplog.records if "[DIAGNOSTICS]" in record.message]
    assert len(diagnostics_logs) > 0
    log = diagnostics_logs[0]
    
    # zero duration shouldn't throw division by zero error
    assert "prompt_tokens_per_second=0.00" in log
    assert "generation_tokens_per_second=0.00" in log
