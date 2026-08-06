from __future__ import annotations

import json

from app.decision.tender_batch import UniqueTenderCandidate
from app.domain import TenderRecord
from app.retrieval.isbak_tender_retriever import (
    ScoreBreakdown,
    TenderSearchResult,
)
from scripts.run_tender_decision_chain import prepare_database_sources


def _candidate(ikn: str) -> UniqueTenderCandidate:
    result = TenderSearchResult(
        tender_id="faiss-id",
        ikn=ikn,
        tender_name="FAISS başlığı",
        chunk_title="Aday",
        primary_profile_code="TEST-01",
        profile_codes=["TEST-01"],
        classification_status="active",
        idare_adi="FAISS idaresi",
        il="İstanbul",
        ihale_tarihi="2026-08-10",
        ihale_turu="Yanlış ilan türü",
        okas_codes=[],
        section_ids=[],
        scores=ScoreBreakdown(final=0.8),
        evidence_chunks=[],
    )
    return UniqueTenderCandidate(
        tender_key=f"ikn:{ikn}",
        candidate=result,
        primary_profile_code="TEST-01",
        supporting_profile_codes=[],
        profile_match_scores={"TEST-01": 0.8},
        profile_matches=[],
    )


class _Repository:
    @staticmethod
    def validate_required_schema():
        return {
            "schema": "public",
            "database_user": "secret-user",
            "tables": {
                "tenders": {},
                "tender_announcements": {},
                "tender_characteristics": {},
                "tender_okas_codes": {},
            },
        }

    @staticmethod
    def get_by_ikns(ikns):
        assert ikns == ["2026/1", "2026/MISSING"]
        return [
            TenderRecord(
                id="db-id",
                ikn="2026/1",
                adi="Gerçek başlık",
                ihale_turu="Mal Alımı",
                ihale_durumu="Aktif",
                kismi_teklif=0,
            )
        ]


def test_database_preflight_is_strict_and_does_not_write_credentials(tmp_path) -> None:
    records, path, failures = prepare_database_sources(
        repository=_Repository(),
        candidates=[_candidate("2026/1"), _candidate("2026/MISSING")],
        report_dir=tmp_path,
        active_status_values=["Aktif"],
        allow_inactive=False,
    )
    report = json.loads(path.read_text(encoding="utf-8"))

    assert records["2026/1"].ihale_turu == "Mal Alımı"
    assert len(failures) == 1
    assert failures[0]["stage"] == "database_source"
    assert report["eligible_tenders"] == 1
    assert "secret-user" not in path.read_text(encoding="utf-8")
    assert report["credentials_written_to_report"] is False
