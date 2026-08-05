"""Çift yönlü RAG eşleştirme veri modelleri.

Tüm skorlar 0.0–1.0 arasında doğrulanır.
Karar değerleri yalnızca: uygun, uygun_degil, inceleme_gerekli

Kanıt bütünlüğü:
    - tender_evidence_* alanları yalnızca ihale payload verisi içerir.
    - profile_evidence_* alanları yalnızca profil payload verisi içerir.
    - chunk_matches her ihale–profil parça çiftini ayrı kayıt olarak tutar.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Skor kırılımı
# ---------------------------------------------------------------------------


class MatchScoreBreakdown(BaseModel):
    """Tek bir ihale–profil çiftinin puan kırılımı."""

    max_similarity: float = Field(0.0, ge=0.0, le=1.0)
    top_similarity_mean: float = Field(0.0, ge=0.0, le=1.0)
    section_diversity: float = Field(0.0, ge=0.0, le=1.0)
    okas_support: float = Field(0.0, ge=0.0, le=1.0)
    title_support: float = Field(0.0, ge=0.0, le=1.0)
    strong_term_support: float = Field(0.0, ge=0.0, le=1.0)
    negative_term_penalty: float = Field(0.0, ge=0.0, le=1.0)
    final_score: float = Field(0.0, ge=0.0, le=1.0)

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# İhale–Profil kanıt çifti
# ---------------------------------------------------------------------------


class ChunkMatchEvidence(BaseModel):
    """Bir ihale parçası ile bir profil parçası arasındaki kanıt kaydı.

    Her iki kaynak açık biçimde ayrılır; metinler karışmaz.
    """

    tender_chunk_id: str
    tender_section_type: str = ""
    tender_text: str = ""

    profile_chunk_id: str
    profile_section_type: str = ""
    profile_text: str = ""

    similarity_score: float = Field(..., ge=0.0, le=1.0)

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Profil → İhale eşleşme kaydı
# ---------------------------------------------------------------------------


class ProfileTenderMatch(BaseModel):
    """Profil odaklı aramada tek bir ihale adayı."""

    profile_code: str
    profile_name: str = ""

    tender_id: str
    ikn: str
    tender_name: str = ""
    authority_name: str = ""

    retrieval_rank: int = Field(0, ge=0)
    retrieval_score: float = Field(0.0, ge=0.0, le=1.0)

    score_breakdown: MatchScoreBreakdown = Field(
        default_factory=MatchScoreBreakdown
    )

    evidence_chunk_ids: list[str] = Field(default_factory=list)
    evidence_sections: list[str] = Field(default_factory=list)
    evidence_texts: list[str] = Field(default_factory=list)

    model_config = {"frozen": False}


# ---------------------------------------------------------------------------
# İhale → Profil eşleşme kaydı
# ---------------------------------------------------------------------------


class TenderProfileMatch(BaseModel):
    """İhale odaklı aramada tek bir profil adayı.

    Kanıt bütünlüğü garantisi:
        - tender_evidence_* alanları yalnızca ihale payload verisi içerir.
        - profile_evidence_* alanları yalnızca profil payload verisi içerir.
        - chunk_matches her ihale–profil parça çiftini ayrı olarak saklar.
    """

    tender_id: str
    ikn: str
    tender_name: str = ""
    authority_name: str = ""

    profile_code: str
    profile_name: str = ""

    retrieval_rank: int = Field(0, ge=0)
    retrieval_score: float = Field(0.0, ge=0.0, le=1.0)

    score_breakdown: MatchScoreBreakdown = Field(
        default_factory=MatchScoreBreakdown
    )

    # İhale kanıt alanları — yalnızca ihale payload'ından gelir
    tender_evidence_chunk_ids: list[str] = Field(default_factory=list)
    tender_evidence_sections: list[str] = Field(default_factory=list)
    tender_evidence_texts: list[str] = Field(default_factory=list)

    # Profil kanıt alanları — yalnızca profil payload'ından gelir
    profile_evidence_chunk_ids: list[str] = Field(default_factory=list)
    profile_evidence_sections: list[str] = Field(default_factory=list)
    profile_evidence_texts: list[str] = Field(default_factory=list)

    # İhale–Profil çift kanıtları
    chunk_matches: list[ChunkMatchEvidence] = Field(default_factory=list)

    model_config = {"frozen": False}

    # ------------------------------------------------------------------
    # Geriye uyumluluk property'leri
    # ------------------------------------------------------------------

    @property
    def tender_chunk_ids(self) -> list[str]:
        """Geriye uyumluluk: tender_evidence_chunk_ids takma adı."""
        return self.tender_evidence_chunk_ids

    @property
    def profile_section_ids(self) -> list[str]:
        """Geriye uyumluluk: profile_evidence_sections takma adı."""
        return self.profile_evidence_sections

    # NOT: evidence_texts takma adı kasıtlı olarak tanımlanmadı.
    # İhale ve profil metinleri artık ayrı alanlarda tutulmalıdır.
    # Belirsizliği önlemek için tender_evidence_texts veya
    # profile_evidence_texts kullanın.


# ---------------------------------------------------------------------------
# İhale nihai karar (birden fazla profil birleşimi)
# ---------------------------------------------------------------------------

_VALID_DECISIONS = frozenset({"uygun", "uygun_degil", "inceleme_gerekli"})


class TenderFinalDecision(BaseModel):
    """İhale odaklı modda birden fazla profil kararının birleşimi."""

    tender_id: str
    ikn: str
    tender_name: str = ""
    authority_name: str = ""

    final_decision: str
    final_confidence: float = Field(0.0, ge=0.0, le=1.0)

    primary_profile_code: str = ""
    supporting_profile_codes: list[str] = Field(default_factory=list)
    evaluated_profile_codes: list[str] = Field(default_factory=list)

    best_retrieval_score: float = Field(0.0, ge=0.0, le=1.0)

    positive_reasons: list[str] = Field(default_factory=list)
    negative_reasons: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)

    human_review_required: bool = False
    human_review_reason: str = ""

    primary_model: str = ""
    primary_decision: str = ""
    primary_confidence: float = 0.0
    primary_used_chunk_ids: list[str] = Field(default_factory=list)

    secondary_triggered: bool = False
    secondary_trigger_reasons: list[str] = Field(default_factory=list)
    secondary_model: str = ""
    secondary_succeeded: bool = False
    secondary_decision: str = ""
    secondary_confidence: float = 0.0
    secondary_used_chunk_ids: list[str] = Field(default_factory=list)
    secondary_error_type: str = ""
    secondary_error_message: str = ""

    model_agreement: bool = False
    merge_rule: str = ""
    agreement_status: str = ""
    validation_issues: list[dict[str, Any]] = Field(default_factory=list)
    missing_mandatory_evidence: bool = False
    mandatory_missing_evidence: list[str] = Field(default_factory=list)
    optional_missing_evidence: list[str] = Field(default_factory=list)

    @field_validator("final_decision")
    @classmethod
    def validate_decision(cls, v: str) -> str:
        if v not in _VALID_DECISIONS:
            raise ValueError(
                f"Geçersiz final_decision: {v!r}. "
                f"İzin verilenler: {sorted(_VALID_DECISIONS)}"
            )
        return v

    model_config = {"frozen": False}


# ---------------------------------------------------------------------------
# Özet modeller
# ---------------------------------------------------------------------------


class ProfileMatchingSummary(BaseModel):
    """Profil odaklı bir çalışmanın özeti."""

    run_id: str
    profile_code: str
    profile_name: str = ""
    top_k: int
    minimum_score: float
    total_candidates: int
    returned_candidates: int
    retrieval_only: bool = False
    elapsed_seconds: float = 0.0
    errors: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"frozen": False}


class TenderMatchingSummary(BaseModel):
    """İhale odaklı bir çalışmanın özeti."""

    run_id: str
    tender_id: str
    ikn: str
    tender_name: str = ""
    top_k: int
    minimum_score: float
    total_profiles_evaluated: int
    returned_profiles: int
    retrieval_only: bool = False
    elapsed_seconds: float = 0.0
    errors: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"frozen": False}


class MatchingRunSummary(BaseModel):
    """Toplu çalışmanın genel özeti."""

    run_id: str
    mode: str
    total_tenders: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0
    total_decisions: int = 0
    uygun_count: int = 0
    uygun_degil_count: int = 0
    inceleme_gerekli_count: int = 0
    retrieval_only: bool = False
    elapsed_seconds: float = 0.0
    errors: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"frozen": False}


__all__ = [
    "ChunkMatchEvidence",
    "MatchScoreBreakdown",
    "MatchingRunSummary",
    "ProfileMatchingSummary",
    "ProfileTenderMatch",
    "TenderFinalDecision",
    "TenderMatchingSummary",
    "TenderProfileMatch",
]
