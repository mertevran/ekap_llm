"""İSBAK ihale bazlı bilgi getirme katmanı (Yeni Puanlama Mimarisi)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.config.isbak_rag_settings import get_isbak_rag_settings
from app.matching.score_aggregator import ScoreAggregator


class VectorStoreProtocol(Protocol):
    collection_name: str

    def search(
        self, *, query_vector: list[float], limit: int, score_threshold: float | None = None
    ) -> list[dict[str, Any]]: ...
    def collection_exists(self) -> bool: ...


class EmbedderProtocol(Protocol):
    model_name: str
    vector_size: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass
class ChunkEvidence:
    chunk_id: str
    section_id: str
    chunk_title: str
    semantic_score: float
    text: str
    text_preview: str


@dataclass
class ScoreBreakdown:
    max_chunk: float = 0.0
    top_chunks_mean: float = 0.0
    section_diversity: float = 0.0
    okas_support: float = 0.0
    title_support: float = 0.0
    final: float = 0.0
    negative_penalty: float = 0.0


@dataclass
class TenderSearchResult:
    tender_id: str
    ikn: str
    tender_name: str
    chunk_title: str
    primary_profile_code: str
    profile_codes: list[str]
    classification_status: str
    idare_adi: str
    il: str
    ihale_tarihi: str
    ihale_turu: str
    okas_codes: list[str]
    section_ids: list[str]
    scores: ScoreBreakdown
    evidence_chunks: list[ChunkEvidence]


@dataclass
class LlmContext:
    tender_id: str
    ikn: str
    tender_name: str
    profiles: list[str]
    scores: dict[str, float]
    main_information: str
    qualification_requirements: str
    technical_requirements: str
    dates_and_deadlines: str
    okas_codes: list[str]
    evidence_chunks: list[dict[str, str]]


_TOKEN_PATTERN = re.compile(r"[a-zçğışöü0-9]+", re.IGNORECASE | re.UNICODE)
_STOPWORDS = frozenset(
    {
        "ve",
        "veya",
        "ile",
        "için",
        "bir",
        "bu",
        "şu",
        "o",
        "alımı",
        "alım",
        "satın",
        "hizmet",
        "hizmeti",
        "işi",
        "iş",
        "malzeme",
        "malzemesi",
        "ihalesi",
        "ihale",
        "yapım",
        "temini",
        "temin",
        "dahil",
        "kapsamında",
    }
)
_WEAK_TERMS = frozenset(
    {
        "yazılım",
        "donanım",
        "uygulama",
        "veri",
        "bilgi",
        "proje",
        "destek",
        "teknik",
        "bakım",
        "onarım",
    }
)


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text))
    normalized = normalized.translate(str.maketrans({"I": "ı", "İ": "i"}))
    return normalized.lower().replace("\u0307", "").strip()


def _tokenize(text: str) -> list[str]:
    return _TOKEN_PATTERN.findall(_normalize(text))


def _query_terms(query: str) -> tuple[str, ...]:
    tokens = _tokenize(query)
    meaningful = [t for t in tokens if t not in _STOPWORDS and len(t) >= 3]
    return tuple(dict.fromkeys(meaningful or tokens))


def _resolve_tender_name(payload: dict[str, Any]) -> str:
    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("document_title"), str):
        return metadata["document_title"].strip()
    title = payload.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return str(payload.get("ikn") or "—")


def _payload_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _payload_list(payload: dict[str, Any], key: str) -> list[str]:
    metadata = _payload_metadata(payload)
    values = payload.get(key)
    if not isinstance(values, list):
        values = metadata.get(key)
    if not isinstance(values, list):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


class IsbakTenderRetriever:
    def __init__(
        self,
        *,
        embedder: EmbedderProtocol,
        vector_store: VectorStoreProtocol,
        settings: Any | None = None,
    ) -> None:
        self.embedder = embedder
        self.vector_store = vector_store
        self.settings = settings or get_isbak_rag_settings()

    def retrieve(self, query: str, limit: int | None = None) -> list[TenderSearchResult]:
        query = query.strip()
        if not query:
            raise ValueError("Sorgu boş olamaz.")

        cfg = self.settings
        eff_limit = limit if limit is not None else cfg.faiss_max_tenders_per_profile

        if not self.vector_store.collection_exists():
            raise RuntimeError("FAISS indeksi bulunamadı.")

        vectors = self.embedder.embed([query])
        query_vector = vectors[0]
        q_terms = _query_terms(query)

        raw_chunks = self.vector_store.search(
            query_vector=query_vector,
            limit=cfg.faiss_search_top_k,
            score_threshold=None,
        )

        groups = self._group_by_tender(
            raw_chunks=raw_chunks, max_chunks_per_tender=cfg.faiss_max_chunks_per_tender
        )

        results: list[TenderSearchResult] = []
        for tender_key, chunk_list in groups.items():
            result = self._build_result(
                tender_key=tender_key, chunks=chunk_list, query=query, query_terms=q_terms
            )
            if result and result.scores.final >= cfg.minimum_final_score:
                results.append(result)

        results.sort(key=lambda r: r.scores.final, reverse=True)
        return results[:eff_limit]

    def _group_by_tender(
        self, *, raw_chunks: list[dict[str, Any]], max_chunks_per_tender: int
    ) -> dict[str, list[dict[str, Any]]]:
        tender_chunks: dict[str, list[dict[str, Any]]] = {}
        for chunk in raw_chunks:
            payload = dict(chunk.get("payload") or {})
            tender_key = str(payload.get("tender_id") or payload.get("ikn") or "").strip()
            if not tender_key:
                continue
            if tender_key not in tender_chunks:
                tender_chunks[tender_key] = []
            tender_chunks[tender_key].append(chunk)

        groups: dict[str, list[dict[str, Any]]] = {}
        for tender_key, chunks in tender_chunks.items():
            # Deduplicate by chunk_id picking highest score
            sec_map = {}
            for c in chunks:
                sid = str(c.get("payload", {}).get("chunk_id", ""))
                score = float(c.get("score", 0.0))
                if sid not in sec_map or score > sec_map[sid]["score"]:
                    sec_map[sid] = c

            unique_chunks = list(sec_map.values())
            unique_chunks.sort(key=lambda c: float(c.get("score", 0.0)), reverse=True)
            groups[tender_key] = unique_chunks[:max_chunks_per_tender]

        return groups

    def _build_result(
        self,
        *,
        tender_key: str,
        chunks: list[dict[str, Any]],
        query: str,
        query_terms: tuple[str, ...],
        profile_signals: dict[str, Any] | None = None,
    ) -> TenderSearchResult | None:
        if not chunks:
            return None

        signals = profile_signals or {}
        raw_scores = [float(c.get("score", 0.0)) for c in chunks]
        section_types = [
            str(c.get("payload", {}).get("section_type") or "")
            for c in chunks
        ]

        top_payload = chunks[0]["payload"]
        tender_name = _resolve_tender_name(top_payload)

        okas_codes: list[str] = []
        for c in chunks:
            okas_codes.extend(
                _payload_list(dict(c.get("payload") or {}), "okas_codes")
            )

        profile_codes = []
        p_codes = top_payload.get("profile_codes")
        if isinstance(p_codes, list):
            profile_codes = [str(x) for x in p_codes]

        tender_text = "\n".join(
            [
                tender_name,
                *(
                    str(c.get("payload", {}).get("text") or "")
                    for c in chunks
                ),
            ]
        )
        aggregate = ScoreAggregator(self.settings).compute(
            raw_scores=raw_scores,
            section_types=section_types,
            okas_codes=list(dict.fromkeys(okas_codes)),
            query_terms=query_terms,
            tender_name=tender_name,
            profile_okas_prefixes=list(signals.get("okas_kod_on_ekleri") or []),
            strong_terms=list(signals.get("guclu_terimler") or []),
            negative_terms=list(signals.get("negatif_terimler") or []),
            tender_text=tender_text,
        )

        scores = ScoreBreakdown(
            max_chunk=aggregate.max_similarity,
            top_chunks_mean=aggregate.top_similarity_mean,
            section_diversity=aggregate.section_diversity,
            okas_support=aggregate.okas_support,
            title_support=aggregate.title_support,
            final=aggregate.final_score,
            negative_penalty=aggregate.negative_term_penalty,
        )

        evidence_chunks = []
        for c in chunks:
            p = c["payload"]
            full_text = str(p.get("text") or "")
            evidence_chunks.append(
                ChunkEvidence(
                    chunk_id=str(p.get("chunk_id") or ""),
                    section_id=str(p.get("section_id") or ""),
                    chunk_title=str(p.get("title") or ""),
                    semantic_score=round(float(c.get("score", 0.0)), 4),
                    text=full_text,
                    text_preview=full_text[:200],
                )
            )

        metadata = _payload_metadata(top_payload)
        return TenderSearchResult(
            tender_id=str(top_payload.get("tender_id") or tender_key),
            ikn=str(top_payload.get("ikn") or tender_key),
            tender_name=tender_name,
            chunk_title=evidence_chunks[0].chunk_title,
            primary_profile_code=str(top_payload.get("primary_profile_code") or ""),
            profile_codes=profile_codes,
            classification_status=str(top_payload.get("classification_status") or ""),
            idare_adi=_first_text(
                top_payload.get("authority_name"),
                top_payload.get("idare_adi"),
                metadata.get("authority_name"),
                metadata.get("idare_adi"),
            ),
            il=_first_text(top_payload.get("il"), metadata.get("il")),
            ihale_tarihi=_first_text(
                top_payload.get("ihale_tarihi"),
                top_payload.get("tender_date"),
                metadata.get("ihale_tarihi"),
                metadata.get("tender_date"),
            ),
            ihale_turu=_first_text(
                top_payload.get("ihale_turu"),
                top_payload.get("announcement_type"),
                metadata.get("ihale_turu"),
                metadata.get("announcement_type"),
            ),
            okas_codes=list(dict.fromkeys(okas_codes)),
            section_ids=list(dict.fromkeys(section_types)),
            scores=scores,
            evidence_chunks=evidence_chunks,
        )

    def build_llm_context(self, result: TenderSearchResult) -> LlmContext:
        cfg = self.settings
        main_parts, qual_parts, tech_parts, date_parts = [], [], [], []
        evidence = []
        total_chars = 0

        for chunk in result.evidence_chunks:
            if total_chars >= cfg.llm_context_max_chars:
                break

            text = chunk.text
            clean_text = text.strip()
            if not clean_text:
                continue

            remaining = cfg.llm_context_max_chars - total_chars
            if len(clean_text) > remaining:
                clean_text = clean_text[:remaining]
            total_chars += len(clean_text)

            title_norm = _normalize(chunk.chunk_title)
            if any(kw in title_norm for kw in ("yeterlik", "nitelik", "belge", "şart")):
                qual_parts.append(clean_text)
            elif any(kw in title_norm for kw in ("teknik", "şartname", "özellik")):
                tech_parts.append(clean_text)
            elif any(kw in title_norm for kw in ("tarih", "süre", "son", "teslim")):
                date_parts.append(clean_text)
            else:
                main_parts.append(clean_text)

            evidence.append({"chunk_id": chunk.chunk_id, "text": clean_text})

        return LlmContext(
            tender_id=result.tender_id,
            ikn=result.ikn,
            tender_name=result.tender_name,
            profiles=result.profile_codes,
            scores={"final": result.scores.final},
            main_information="\n\n".join(main_parts),
            qualification_requirements="\n\n".join(qual_parts),
            technical_requirements="\n\n".join(tech_parts),
            dates_and_deadlines="\n\n".join(date_parts),
            okas_codes=result.okas_codes,
            evidence_chunks=evidence,
        )
