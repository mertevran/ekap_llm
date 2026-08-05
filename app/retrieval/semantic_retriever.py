from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Protocol


class VectorStoreProtocol(Protocol):
    def search(
        self,
        *,
        query_vector: list[float],
        limit: int,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]: ...


class EmbedderProtocol(Protocol):
    def embed(
        self,
        texts: list[str],
    ) -> list[list[float]]: ...


@dataclass(frozen=True)
class RetrievalConfig:
    final_limit: int = 10
    candidate_limit: int = 40

    minimum_semantic_score: float = 0.55
    minimum_final_score: float = 0.50

    minimum_lexical_score: float = 0.30
    minimum_semantic_score_with_lexical: float = 0.49
    minimum_final_score_with_lexical: float = 0.44

    max_chunks_per_tender: int = 2

    semantic_weight: float = 0.75
    lexical_weight: float = 0.25

    title_weight: float = 0.70
    text_weight: float = 0.30

    def __post_init__(self) -> None:
        if self.final_limit <= 0:
            raise ValueError("final_limit pozitif olmalıdır.")

        if self.candidate_limit < self.final_limit:
            raise ValueError("candidate_limit, final_limit değerinden küçük olamaz.")

        if self.max_chunks_per_tender <= 0:
            raise ValueError("max_chunks_per_tender pozitif olmalıdır.")

        values = {
            "minimum_semantic_score": self.minimum_semantic_score,
            "minimum_final_score": self.minimum_final_score,
            "minimum_lexical_score": self.minimum_lexical_score,
            "minimum_semantic_score_with_lexical": self.minimum_semantic_score_with_lexical,
            "minimum_final_score_with_lexical": self.minimum_final_score_with_lexical,
            "semantic_weight": self.semantic_weight,
            "lexical_weight": self.lexical_weight,
            "title_weight": self.title_weight,
            "text_weight": self.text_weight,
        }

        for name, value in values.items():
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} 0 ile 1 arasında olmalıdır.")

        if abs(self.semantic_weight + self.lexical_weight - 1.0) > 1e-9:
            raise ValueError("semantic_weight ve lexical_weight toplamı 1 olmalıdır.")

        if abs(self.title_weight + self.text_weight - 1.0) > 1e-9:
            raise ValueError("title_weight ve text_weight toplamı 1 olmalıdır.")


class SemanticRetriever:
    """
    Semantic search (anlamsal arama) ile lexical search
    (sözcüksel arama) puanlarını birleştirir.
    """

    _TOKEN_PATTERN = re.compile(
        r"[a-z0-9çğıöşü]+",
        re.IGNORECASE,
    )

    _STOPWORDS = {
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
        "hizmeti",
        "hizmet",
        "işi",
        "iş",
        "malzemesi",
        "malzeme",
        "ihalesi",
        "ihale",
    }

    def __init__(
        self,
        *,
        embedder: EmbedderProtocol,
        vector_store: VectorStoreProtocol,
        config: RetrievalConfig | None = None,
    ) -> None:
        self.embedder = embedder
        self.vector_store = vector_store
        self.config = config or RetrievalConfig()

    def retrieve(
        self,
        query: str,
    ) -> list[dict[str, Any]]:
        normalized_query = query.strip()

        if not normalized_query:
            raise ValueError("Sorgu boş olamaz.")

        vectors = self.embedder.embed([normalized_query])

        if len(vectors) != 1:
            raise RuntimeError("Sorgu için tek bir gömme vektörü üretilemedi.")

        candidates = self.vector_store.search(
            query_vector=vectors[0],
            limit=self.config.candidate_limit,
            score_threshold=None,
        )

        query_terms = self._query_terms(normalized_query)

        rescored = self._rescore_candidates(
            candidates=candidates,
            query_terms=query_terms,
        )

        return self._select_diverse_results(rescored)

    def _rescore_candidates(
        self,
        *,
        candidates: list[dict[str, Any]],
        query_terms: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        rescored: list[dict[str, Any]] = []

        for candidate in candidates:
            semantic_score = float(candidate.get("score", 0.0))

            payload = dict(candidate.get("payload") or {})

            title_overlap = self._term_overlap(
                query_terms=query_terms,
                value=self._payload_title(payload),
            )

            text_overlap = self._term_overlap(
                query_terms=query_terms,
                value=self._payload_text(payload),
            )

            lexical_score = (
                self.config.title_weight * title_overlap + self.config.text_weight * text_overlap
            )

            final_score = (
                self.config.semantic_weight * semantic_score
                + self.config.lexical_weight * lexical_score
            )

            passes_semantic_rule = (
                semantic_score >= self.config.minimum_semantic_score
                and final_score >= self.config.minimum_final_score
            )

            passes_lexical_rule = (
                lexical_score >= self.config.minimum_lexical_score
                and semantic_score >= self.config.minimum_semantic_score_with_lexical
                and final_score >= self.config.minimum_final_score_with_lexical
            )

            if not (passes_semantic_rule or passes_lexical_rule):
                continue

            acceptance_rule = "semantic" if passes_semantic_rule else "lexical_supported"

            rescored.append(
                {
                    "id": str(candidate.get("id", "")),
                    "score": semantic_score,
                    "semantic_score": semantic_score,
                    "lexical_score": lexical_score,
                    "title_overlap": title_overlap,
                    "text_overlap": text_overlap,
                    "final_score": final_score,
                    "acceptance_rule": acceptance_rule,
                    "payload": payload,
                }
            )

        rescored.sort(
            key=lambda item: (
                item["final_score"],
                item["semantic_score"],
            ),
            reverse=True,
        )

        return rescored

    def _select_diverse_results(
        self,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        counts_by_ikn: dict[str, int] = {}

        for candidate in candidates:
            payload = candidate["payload"]

            ikn = str(payload.get("ikn") or "__missing_ikn__").strip()

            current_count = counts_by_ikn.get(
                ikn,
                0,
            )

            if current_count >= self.config.max_chunks_per_tender:
                continue

            selected.append(candidate)

            counts_by_ikn[ikn] = current_count + 1

            if len(selected) >= self.config.final_limit:
                break

        return selected

    def _query_terms(
        self,
        query: str,
    ) -> tuple[str, ...]:
        tokens = self._tokenize(self._normalize(query))

        meaningful_terms = [
            token for token in tokens if (token not in self._STOPWORDS and len(token) >= 3)
        ]

        if not meaningful_terms:
            meaningful_terms = tokens

        # Tekrarlı terimleri ilk görülme sırasını koruyarak kaldırır.
        return tuple(dict.fromkeys(meaningful_terms))

    def _term_overlap(
        self,
        *,
        query_terms: tuple[str, ...],
        value: str,
    ) -> float:
        if not query_terms:
            return 0.0

        value_tokens = set(self._tokenize(self._normalize(value)))

        matched_terms = {term for term in query_terms if term in value_tokens}

        return len(matched_terms) / len(query_terms)

    @classmethod
    def _tokenize(
        cls,
        value: str,
    ) -> list[str]:
        return cls._TOKEN_PATTERN.findall(value)

    @staticmethod
    def _normalize(
        value: str,
    ) -> str:
        normalized = unicodedata.normalize(
            "NFKC",
            str(value),
        )

        # Türkçe büyük harfleri genel küçük harf dönüşümünden önce düzeltir.
        normalized = normalized.translate(
            str.maketrans(
                {
                    "I": "ı",
                    "İ": "i",
                }
            )
        )

        normalized = normalized.lower()

        # Önceden oluşmuş olabilecek birleşik nokta işaretini temizler.
        normalized = normalized.replace(
            "\u0307",
            "",
        )

        return normalized.strip()

    @staticmethod
    def _payload_title(
        payload: dict[str, Any],
    ) -> str:
        title = payload.get("title")

        if isinstance(title, str):
            return title

        metadata = payload.get("metadata")

        if isinstance(metadata, dict):
            document_title = metadata.get("document_title")

            if isinstance(
                document_title,
                str,
            ):
                return document_title

        return ""

    @staticmethod
    def _payload_text(
        payload: dict[str, Any],
    ) -> str:
        text = payload.get("text")

        if isinstance(text, str):
            return text

        return ""
