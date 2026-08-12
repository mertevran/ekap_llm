import os
from unittest.mock import MagicMock, patch

from app.decision.ollama_decision_model import (
    QWEN_CORRECTION_MSG,
    OllamaDecisionModel,
)


def test_correction_messages_at_module_level():
    assert QWEN_CORRECTION_MSG is not None
    assert '"decision":' not in QWEN_CORRECTION_MSG

def test_no_fixed_biases_in_correction_messages():
    # `"decision": "inceleme_gerekli"` bulunmamalı
    assert '"decision": "inceleme_gerekli"' not in QWEN_CORRECTION_MSG
    assert '"confidence": 0.0' not in QWEN_CORRECTION_MSG
    assert '"confidence": 0.50' not in QWEN_CORRECTION_MSG
    assert '"confidence": 0.85' not in QWEN_CORRECTION_MSG
    assert '"confidence": 0.50' not in QWEN_CORRECTION_MSG



def test_scripts_use_correct_prompt():
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

    chain_path = os.path.join(base_dir, "scripts", "run_tender_decision_chain.py")
    with open(chain_path, encoding="utf-8") as f:
        chain_content = f.read()
    assert 'isbak_qwen_decision_v4_compact' in chain_content

    pipe_path = os.path.join(base_dir, "scripts", "run_matching_pipeline.py")
    with open(pipe_path, encoding="utf-8") as f:
        pipe_content = f.read()
    assert 'isbak_qwen_decision_v3' in pipe_content


