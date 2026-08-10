"""Integration tests for faiss-distant selection mode."""

import subprocess
import sys
from pathlib import Path
import json

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_RUN_SCRIPT = _PROJECT_ROOT / "scripts" / "run_tender_decision_chain.py"


@pytest.fixture
def run_command():
    """Helper to run the script in a subprocess."""
    def _run(*args, **kwargs):
        cmd = [sys.executable, str(_RUN_SCRIPT)] + list(args)
        import os
        env = os.environ.copy()
        env["PYTHONPATH"] = str(_PROJECT_ROOT)
        return subprocess.run(
            cmd,
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            env=env,
            **kwargs,
        )
    return _run


def test_faiss_distant_argparse(run_command, tmp_path):
    """Test that argparse accepts --selection-mode faiss-distant and --distant-pool-size."""
    report_dir = tmp_path / "reports"
    res = run_command(
        "--selection-mode", "faiss-distant",
        "--distant-pool-size", "10",
        "--profile-code", "AUS-01",
        "--max-decisions", "1",
        "--report-dir", str(report_dir),
        "--retrieval-only"
    )
    # Even if it fails due to missing FAISS, it shouldn't fail due to argparse
    assert "error: argument --selection-mode: invalid choice: 'faiss-distant'" not in res.stderr
    assert res.returncode in (0, 1) # 1 if faiss is empty, but argparse is ok


def test_faiss_distant_logic(tmp_path):
    """Mock test verifying faiss-distant logic directly via imported main."""
    from unittest.mock import patch, MagicMock
    from scripts.run_tender_decision_chain import main
    import numpy as np
    
    report_dir = tmp_path / "mock_reports"
    report_dir.mkdir()
    
    test_args = [
        "script_name",
        "--selection-mode", "faiss-distant",
        "--distant-pool-size", "10",
        "--profile-code", "PROFILE_A,PROFILE_B",
        "--max-decisions", "2",
        "--random-seed", "42",
        "--report-dir", str(report_dir),
        "--retrieval-only"
    ]
    
    with patch("sys.argv", test_args):
        with patch("scripts.run_tender_decision_chain.FaissVectorStore") as mock_fvs_cls, \
             patch("scripts.run_tender_decision_chain.ProfileVectorTenderMatcher") as mock_matcher_cls, \
             patch("scripts.run_tender_decision_chain.selected_profiles") as mock_sel_prof:
            
            mock_sel_prof.return_value = ["PROFILE_A", "PROFILE_B"]
            
            # Setup Tender Store (FAISS)
            mock_store = MagicMock()
            mock_store.collection_exists.return_value = True
            mock_store.index.ntotal = 5
            mock_store.count.return_value = 5
            # 5 tenders in FAISS
            # Tender 1 is ISBAK (skipped)
            # Tender 2 is very distant
            # Tender 3 is very close to PROFILE_A
            # Tender 4 is distant but closer than Tender 2
            # Tender 5 is duplicate of Tender 4
            
            mock_store.payloads = {
                100: {"ikn": "2024/111", "tender_id": "1", "idare_adi": "İSBAK İSTANBUL BİLİŞİM VE AKILLI KENT TEKNOLOJİLERİ A.Ş.", "text": "isbak"},
                200: {"ikn": "2024/222", "tender_id": "2", "idare_adi": "TEST IDARE", "text": "distant_1"},
                300: {"ikn": "2024/333", "tender_id": "3", "idare_adi": "TEST IDARE", "text": "close_1"},
                400: {"ikn": "2024/444", "tender_id": "4", "idare_adi": "TEST IDARE", "text": "distant_2"},
                500: {"ikn": "2024/444", "tender_id": "4", "idare_adi": "TEST IDARE", "text": "distant_2_duplicate"},
            }
            
            def mock_index_reconstruct(pid):
                if pid == 100: return np.array([0.0, 0.0, 0.0], dtype=np.float32)
                if pid == 200: return np.array([0.0, 0.0, 1.0], dtype=np.float32) # Distant from A and B
                if pid == 300: return np.array([1.0, 0.0, 0.0], dtype=np.float32) # Close to A
                if pid == 400: return np.array([0.0, 0.5, 0.5], dtype=np.float32) # Distant, but closer than 200
                if pid == 500: return np.array([0.0, 0.5, 0.5], dtype=np.float32)
                return np.zeros(3, dtype=np.float32)
                
            mock_store.index.reconstruct.side_effect = mock_index_reconstruct
            mock_fvs_cls.return_value = mock_store
            
            # Setup Matcher Profiles
            mock_matcher = MagicMock()
            mock_matcher._load_profile_meta.side_effect = lambda code: {"name": f"Name {code}", "strong_terms": [], "negative_terms": []}
            mock_matcher._profile_entries.side_effect = lambda code: [(f"{code}_1", {"text": "dummy"})]
            mock_matcher._query_terms_for_subclass.return_value = ("term",)
            
            # PROFILE_A is [1.0, 0.0, 0.0]
            # PROFILE_B is [0.0, 1.0, 0.0]
            def mock_reconstruct_profile_vector(faiss_id):
                if "PROFILE_A" in faiss_id:
                    return np.array([1.0, 0.0, 0.0], dtype=np.float32)
                else:
                    return np.array([0.0, 1.0, 0.0], dtype=np.float32)
                    
            mock_matcher._reconstruct_profile_vector.side_effect = mock_reconstruct_profile_vector
            mock_matcher_cls.return_value = mock_matcher
            
            result = main()
            assert result == 0
            
            # Check summary
            summary_path = report_dir / "tender_decision_run_summary.json"
            assert summary_path.exists()
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            
            assert summary["selection_mode"] == "faiss-distant"
            assert summary["distant_pool_size"] == 10
            assert summary["distant_tenders_examined"] == 4 # 4 unique ikns (111, 222, 333, 444)
            assert summary["distant_tenders_selected"] == 2
            
            csv_path = report_dir / "faiss_distant_selection.csv"
            assert csv_path.exists()
            import csv
            rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
            
            # ISBAK is skipped
            assert any(r["ikn"] == "2024/111" and r["status"] == "skipped" for r in rows)
            
            # Selected should be Tender 2 (most distant) and Tender 4 (next distant). Tender 3 is skipped because of max_decisions = 2
            selected_rows = [r for r in rows if r["status"] == "selected"]
            assert len(selected_rows) == 2
            
            # Since best_score is ASC, 2024/222 should be rank 1
            assert selected_rows[0]["ikn"] == "2024/222"
            assert selected_rows[0]["selection_rank"] == "1"
            
            assert selected_rows[1]["ikn"] == "2024/444"
            assert selected_rows[1]["selection_rank"] == "2"
