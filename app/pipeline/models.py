from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.decision.models import FinalTenderDecision


@dataclass
class DiagnosticStep:
    name: str
    start_time: datetime
    end_time: datetime | None = None
    duration_seconds: float = 0.0
    success: bool = True
    error_type: str | None = None
    error_desc: str | None = None


@dataclass
class EvidenceRecord:
    ikn: str
    chunk_id: str
    section_id: str | None
    source_table: str
    source_record_ids: list[str]
    profile_code: str
    retrieval_score: float
    text_excerpt: str
    semantic_score: float = 0.0
    lexical_score: float = 0.0
    profile_score: float = 0.0
    metadata_score: float = 0.0
    selection_score_source: str = ""


@dataclass
class AnalysisResult:
    analysis_id: str
    ikn: str
    tender_title: str
    tender_category: str | None
    decision: FinalTenderDecision | None = None
    evidence: list[EvidenceRecord] = field(default_factory=list)
    diagnostics: list[DiagnosticStep] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Yeni eklenen yapılandırılmış profil ve şirket alanları
    primary_profile_code: str | None = None
    secondary_profile_codes: list[str] = field(default_factory=list)
    supporting_profile_codes: list[str] = field(default_factory=list)
    matched_capabilities: list[str] = field(default_factory=list)
    company_evidence: list[EvidenceRecord] = field(default_factory=list)
    historical_evidence: list[EvidenceRecord] = field(default_factory=list)
