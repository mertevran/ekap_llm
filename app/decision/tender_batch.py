"""Profil bazlı adayları benzersiz ihale kayıtları altında birleştirir."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.retrieval.isbak_tender_retriever import (
    ChunkEvidence,
    TenderSearchResult,
)


@dataclass(frozen=True)
class ProfileCandidateMatch:
    profile_code: str
    retrieval_rank: int
    candidate: TenderSearchResult


@dataclass(frozen=True)
class UniqueTenderCandidate:
    tender_key: str
    candidate: TenderSearchResult
    primary_profile_code: str
    supporting_profile_codes: list[str]
    profile_match_scores: dict[str, float]
    profile_matches: list[ProfileCandidateMatch]


def tender_identity(candidate: TenderSearchResult) -> str:
    """İhaleyi önce iş anahtarı İKN, yoksa tender_id üzerinden tanımlar."""

    normalized_ikn = "".join(str(candidate.ikn or "").upper().split())
    if normalized_ikn:
        return f"ikn:{normalized_ikn}"
    tender_id = str(candidate.tender_id or "").strip()
    if tender_id:
        return f"id:{tender_id}"
    raise ValueError("Aday ihalenin tender_id ve İKN alanları birlikte boş olamaz.")


def group_unique_tenders(
    matches: list[ProfileCandidateMatch],
    *,
    max_evidence_chunks: int,
) -> list[UniqueTenderCandidate]:
    grouped: dict[str, list[ProfileCandidateMatch]] = {}
    for match in matches:
        key = tender_identity(match.candidate)
        grouped.setdefault(key, []).append(match)

    unique_candidates = [
        _merge_group(
            tender_key=key,
            matches=group,
            max_evidence_chunks=max_evidence_chunks,
        )
        for key, group in grouped.items()
    ]
    unique_candidates.sort(
        key=lambda item: (
            -item.candidate.scores.final,
            item.candidate.ikn,
            item.tender_key,
        )
    )
    return unique_candidates


def _merge_group(
    *,
    tender_key: str,
    matches: list[ProfileCandidateMatch],
    max_evidence_chunks: int,
) -> UniqueTenderCandidate:
    ordered_matches = sorted(
        matches,
        key=lambda item: (
            -item.candidate.scores.final,
            item.retrieval_rank,
            item.profile_code,
        ),
    )
    best = ordered_matches[0]

    best_profile_matches: dict[str, ProfileCandidateMatch] = {}
    for match in ordered_matches:
        code = match.profile_code.strip().upper()
        current = best_profile_matches.get(code)
        if current is None or match.candidate.scores.final > current.candidate.scores.final:
            best_profile_matches[code] = match

    profile_matches = sorted(
        best_profile_matches.values(),
        key=lambda item: (
            -item.candidate.scores.final,
            item.retrieval_rank,
            item.profile_code,
        ),
    )
    primary_code = profile_matches[0].profile_code.strip().upper()
    supporting_codes = [
        match.profile_code.strip().upper()
        for match in profile_matches[1:]
    ]

    evidence_by_id: dict[str, ChunkEvidence] = {}
    for match in ordered_matches:
        for evidence in match.candidate.evidence_chunks:
            chunk_key = evidence.chunk_id.strip() or (
                f"{evidence.section_id}|{evidence.chunk_title}|{evidence.text[:80]}"
            )
            current = evidence_by_id.get(chunk_key)
            if current is None or evidence.semantic_score > current.semantic_score:
                evidence_by_id[chunk_key] = evidence
    merged_evidence = sorted(
        evidence_by_id.values(),
        key=lambda evidence: (-evidence.semantic_score, evidence.chunk_id),
    )[:max_evidence_chunks]

    candidates = [match.candidate for match in ordered_matches]
    merged_candidate = TenderSearchResult(
        tender_id=_first_nonempty(item.tender_id for item in candidates),
        ikn=_first_nonempty(item.ikn for item in candidates),
        tender_name=_first_nonempty(item.tender_name for item in candidates),
        chunk_title=_first_nonempty(item.chunk_title for item in candidates),
        primary_profile_code=primary_code,
        profile_codes=[primary_code, *supporting_codes],
        classification_status=_first_nonempty(
            item.classification_status for item in candidates
        ),
        idare_adi=_first_nonempty(item.idare_adi for item in candidates),
        il=_first_nonempty(item.il for item in candidates),
        ihale_tarihi=_first_nonempty(item.ihale_tarihi for item in candidates),
        ihale_turu=_first_nonempty(item.ihale_turu for item in candidates),
        okas_codes=list(
            dict.fromkeys(
                code
                for item in candidates
                for code in item.okas_codes
                if str(code).strip()
            )
        ),
        section_ids=list(
            dict.fromkeys(
                section
                for evidence in merged_evidence
                if (section := evidence.section_id.strip())
            )
        ),
        scores=best.candidate.scores,
        evidence_chunks=merged_evidence,
    )

    return UniqueTenderCandidate(
        tender_key=tender_key,
        candidate=merged_candidate,
        primary_profile_code=primary_code,
        supporting_profile_codes=supporting_codes,
        profile_match_scores={
            match.profile_code.strip().upper(): match.candidate.scores.final
            for match in profile_matches
        },
        profile_matches=profile_matches,
    )


def _first_nonempty(values: Iterable[Any]) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


__all__ = [
    "ProfileCandidateMatch",
    "UniqueTenderCandidate",
    "group_unique_tenders",
    "tender_identity",
]
