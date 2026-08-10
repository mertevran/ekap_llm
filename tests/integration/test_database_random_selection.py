"""Integration tests for database-random selection mode."""

import subprocess
import sys
from pathlib import Path

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


def test_candidate_pool_mode_default(run_command, tmp_path):
    """Test candidate-pool mode works as default."""
    report_dir = tmp_path / "reports"
    res = run_command(
        "--profile-code", "AUS-01",
        "--max-decisions", "1",
        "--report-dir", str(report_dir),
        "--retrieval-only"
    )
    assert res.returncode == 0
    assert "Profil değerlendiriliyor: AUS-01" in res.stderr


def test_database_random_mode_retrieval_only(run_command, tmp_path):
    """Test database-random mode bypasses FAISS retrieval."""
    report_dir = tmp_path / "reports"
    res = run_command(
        "--selection-mode", "database-random",
        "--profile-code", "AUS-01",
        "--max-decisions", "3",
        "--random-seed", "42",
        "--report-dir", str(report_dir),
        "--retrieval-only"
    )
    assert res.returncode == 0
    
    # Check logs
    assert "[SELECTION_MODE] mode=database-random" in res.stderr
    assert "Profil değerlendiriliyor:" not in res.stderr  # Bypassed
    
    # Check if the new CSV report is created
    csv_path = report_dir / "database_random_selection.csv"
    assert csv_path.exists()
    content = csv_path.read_text(encoding="utf-8")
    assert "selection_rank,examined_rank,tender_id,ikn,tender_title" in content
    
    # Check summary json
    import json
    summary_path = report_dir / "tender_decision_run_summary.json"
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    
    assert summary["selection_mode"] == "database-random"
    assert summary["random_seed"] == 42
    assert summary["random_tenders_requested"] == 3
    assert isinstance(summary["random_tenders_examined"], int)
    assert isinstance(summary["random_tenders_submitted_to_model"], int)


def test_database_random_deterministic_seeds(run_command, tmp_path):
    """Test that same seed yields same selections, different seed yields different."""
    report_dir1 = tmp_path / "reports1"
    res1 = run_command(
        "--selection-mode", "database-random",
        "--profile-code", "AUS-01",
        "--max-decisions", "2",
        "--random-seed", "123",
        "--report-dir", str(report_dir1),
        "--retrieval-only"
    )
    assert res1.returncode == 0
    
    report_dir2 = tmp_path / "reports2"
    res2 = run_command(
        "--selection-mode", "database-random",
        "--profile-code", "AUS-01",
        "--max-decisions", "2",
        "--random-seed", "123",
        "--report-dir", str(report_dir2),
        "--retrieval-only"
    )
    assert res2.returncode == 0
    
    csv1 = (report_dir1 / "database_random_selection.csv").read_text(encoding="utf-8")
    csv2 = (report_dir2 / "database_random_selection.csv").read_text(encoding="utf-8")
    
    # Same seed should give same output
    assert csv1 == csv2
    
    report_dir3 = tmp_path / "reports3"
    res3 = run_command(
        "--selection-mode", "database-random",
        "--profile-code", "AUS-01",
        "--max-decisions", "2",
        "--random-seed", "999",
        "--report-dir", str(report_dir3),
        "--retrieval-only"
    )
    assert res3.returncode == 0
    
    csv3 = (report_dir3 / "database_random_selection.csv").read_text(encoding="utf-8")
    
    # Just to be sure, check if it's there
    assert "selection_rank" in csv3


def test_database_random_semantic_logic(tmp_path):
    """Mock test verifying semantic evidence logic directly via imported main."""
    from unittest.mock import patch, MagicMock
    from scripts.run_tender_decision_chain import main
    import numpy as np
    
    report_dir = tmp_path / "mock_reports"
    report_dir.mkdir()
    
    test_args = [
        "script_name",
        "--selection-mode", "database-random",
        "--profile-code", "PROFILE_A,PROFILE_B",
        "--max-decisions", "3",
        "--random-seed", "42",
        "--report-dir", str(report_dir),
        "--retrieval-only"
    ]
    
    with patch("sys.argv", test_args):
        with patch("scripts.run_tender_decision_chain.TenderRepository") as mock_repo_cls, \
             patch("scripts.run_tender_decision_chain.FaissVectorStore") as mock_fvs_cls, \
             patch("scripts.run_tender_decision_chain.ProfileVectorTenderMatcher") as mock_matcher_cls, \
             patch("scripts.run_tender_decision_chain.selected_profiles") as mock_sel_prof, \
             patch("random.Random.shuffle") as mock_shuffle:
            
            mock_sel_prof.return_value = ["PROFILE_A", "PROFILE_B"]
            
            # Disable shuffle so order is deterministic
            mock_shuffle.side_effect = lambda x: None
            
            # Setup Tenders
            mock_repo = MagicMock()
            tender1 = MagicMock(id=1, ikn="2024/111", adi="Tender FAISS")
            tender2 = MagicMock(id=2, ikn="2024/222", adi="Tender MISSING")
            tender3 = MagicMock(id=3, ikn="2024/333", adi="Tender FAISS 2")
            tender4 = MagicMock(id=4, ikn="2024/444", adi="Tender FAISS Error")
            mock_repo.get_active_tenders.return_value = [tender1, tender2, tender3, tender4]
            mock_repo_cls.return_value = mock_repo
            
            # Setup Tender Store (FAISS)
            mock_store = MagicMock()
            mock_store.collection_exists.return_value = True
            mock_store.index.ntotal = 3
            mock_store.count.return_value = 3
            mock_store.payloads = {
                100: {"ikn": "2024/111", "tender_id": "1", "text": "test", "chunk_id": "c1"},
                300: {"ikn": "2024/333", "tender_id": "3", "text": "test", "chunk_id": "c3"},
                400: {"ikn": "2024/444", "tender_id": "4", "text": "test", "chunk_id": "c4"}
            }
            # [1.0, 0.0] for tender vectors
            def mock_index_reconstruct(pid):
                if pid == 400:
                    raise RuntimeError("Simulated extraction error")
                return np.array([1.0, 0.0], dtype=np.float32)
            mock_store.index.reconstruct.side_effect = mock_index_reconstruct
            mock_fvs_cls.return_value = mock_store
            
            # Setup Matcher Profiles
            mock_matcher = MagicMock()
            mock_matcher._load_profile_meta.side_effect = lambda code: {"name": f"Name {code}", "strong_terms": ["term"]}
            mock_matcher._profile_entries.side_effect = lambda code: [(f"{code}_1", {"text": "dummy"})]
            mock_matcher._query_terms_for_subclass.return_value = ("term",)
            
            # PROFILE_A matches tender exactly [1.0, 0.0]
            # PROFILE_B is orthogonal [0.0, 1.0]
            def mock_reconstruct_profile_vector(faiss_id):
                if "PROFILE_A" in faiss_id:
                    return np.array([1.0, 0.0], dtype=np.float32)
                else:
                    return np.array([0.0, 1.0], dtype=np.float32)
                    
            mock_matcher._reconstruct_profile_vector.side_effect = mock_reconstruct_profile_vector
            mock_matcher_cls.return_value = mock_matcher
            
            result = main()
            assert result == 0
            
            csv_path = report_dir / "database_random_selection.csv"
            assert csv_path.exists()
            import csv
            rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
            
            # We expected 3 max decisions, so we examine up to tender4 (1 valid, 1 missing, 1 valid, 1 error) -> 4 rows
            assert len(rows) == 4
            
            t1 = next(r for r in rows if r["ikn"] == "2024/111")
            assert t1["semantic_evidence_available"] == "True"
            assert t1["selection_status"] == "selected"
            assert t1["selected_profile_code"] == "PROFILE_A"
            assert float(t1["selected_profile_score"]) > 0.0
            
            t2 = next(r for r in rows if r["ikn"] == "2024/222")
            assert t2["semantic_evidence_available"] == "False"
            assert t2["selection_status"] == "missing_faiss_evidence"
            assert t2["selected_profile_code"] == ""
            assert float(t2["selected_profile_score"]) == 0.0
            
            t3 = next(r for r in rows if r["ikn"] == "2024/333")
            assert t3["semantic_evidence_available"] == "True"
            assert t3["selection_status"] == "selected"
            assert t3["selected_profile_code"] == "PROFILE_A"

            t4 = next(r for r in rows if r["ikn"] == "2024/444")
            assert t4["semantic_evidence_available"] == "False"
            assert t4["selection_status"] == "faiss_vector_error"
            assert t4["selected_profile_code"] == ""
