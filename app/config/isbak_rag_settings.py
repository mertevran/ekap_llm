"""İSBAK RAG bileşenleri için merkezi yapılandırma.

Tüm İSBAK özgü ayarlar bu sınıfta toplanmıştır.
Ortam değişkenleri ``ISBAK_`` ön eki ile okunur; örneğin::

    ISBAK_QDRANT_PATH=storage/qdrant_isbak
    ISBAK_COLLECTION_NAME=ekap_isbak_tender_chunks_v1
    ISBAK_SEMANTIC_WEIGHT=0.60

Saf dataclass kullanılır; ek bağımlılık gerektirmez.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# ---------------------------------------------------------------------------
# Sabit varsayılanlar
# ---------------------------------------------------------------------------

_DEFAULT_QDRANT_PATH: str = "storage/qdrant_isbak"
_DEFAULT_COLLECTION_NAME: str = "ekap_isbak_tender_chunks_v1"
_DEFAULT_EMBEDDING_MODEL: str = "BAAI/bge-m3"
_DEFAULT_EMBEDDING_DEVICE: str = "cpu"
_DEFAULT_EMBEDDING_BATCH_SIZE: int = 4

# FAISS İhale indeksi
_DEFAULT_FAISS_TENDER_PATH: str = "storage/faiss"
_DEFAULT_FAISS_TENDER_COLLECTION: str = "ekap_tender_chunks"

# FAISS Profil indeksi
_DEFAULT_FAISS_PROFILE_PATH: str = "storage/faiss_profiles"
_DEFAULT_FAISS_PROFILE_COLLECTION: str = "isbak_company_profiles"

_DEFAULT_FAISS_SEARCH_TOP_K: int = 100
_DEFAULT_FAISS_MAX_TENDERS_PER_PROFILE: int = 30
_DEFAULT_FAISS_MAX_CHUNKS_PER_TENDER: int = 4
_DEFAULT_FAISS_MAX_PROFILES_PER_TENDER: int = 20  # toplam profil sayısı

# Puanlama ağırlıkları
_DEFAULT_WEIGHT_MAX_CHUNK: float = 0.55
_DEFAULT_WEIGHT_TOP_CHUNKS: float = 0.20
_DEFAULT_WEIGHT_SECTION_DIVERSITY: float = 0.10
_DEFAULT_WEIGHT_OKAS: float = 0.10
_DEFAULT_WEIGHT_TITLE: float = 0.05

_DEFAULT_MINIMUM_FINAL_SCORE: float = 0.35

# Maksimum LLM bağlam uzunluğu (karakter)
_DEFAULT_LLM_CONTEXT_MAX_CHARS: int = 8000

# Karar sürümü (şema değişikliklerini takip etmek için)
DECISION_VERSION: str = "v1"

# İSBAK kendi ihalelerini filtrele — yapılandırılabilir liste
_DEFAULT_EXCLUDED_AUTHORITIES: list[str] = [
    "isbak",
    "istanbul bilişim ve akıllı kent teknolojileri",
    "istanbul bilisim ve akilli kent teknolojileri",
    "istanbul bilişim ve akıllı kent teknolojileri a.ş.",
    "istanbul bilişim ve akıllı kent teknolojileri sanayi ve ticaret a.ş.",
]


def _env_str(key: str, default: str) -> str:
    return os.environ.get(f"ISBAK_{key}", default)


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(f"ISBAK_{key}", "")
    if raw.strip():
        try:
            return int(raw.strip())
        except ValueError:
            pass
    return default


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(f"ISBAK_{key}", "")
    if raw.strip():
        try:
            return float(raw.strip())
        except ValueError:
            pass
    return default


@dataclass(frozen=True)
class IsbakRagSettings:
    """İSBAK RAG bileşenleri için tüm yapılandırma parametreleri.

    Örnekleme sırasında herhangi bir alan açıkça verilmezse ortam
    değişkenlerinden okunur; o da yoksa dahili varsayılan kullanılır.

    Kural 3: Bu sınıf yalnızca ``app.config`` paketinden dışa aktarılır.
    """

    # --- Qdrant (geriye uyumluluk) ---
    qdrant_isbak_path: str = field(
        default_factory=lambda: _env_str("QDRANT_PATH", _DEFAULT_QDRANT_PATH)
    )
    collection_name: str = field(
        default_factory=lambda: _env_str("COLLECTION_NAME", _DEFAULT_COLLECTION_NAME)
    )

    # --- FAISS İhale indeksi ---
    faiss_tender_path: str = field(
        default_factory=lambda: _env_str("FAISS_TENDER_PATH", _DEFAULT_FAISS_TENDER_PATH)
    )
    faiss_tender_collection: str = field(
        default_factory=lambda: _env_str(
            "FAISS_TENDER_COLLECTION", _DEFAULT_FAISS_TENDER_COLLECTION
        )
    )

    # --- FAISS Profil indeksi ---
    faiss_profile_path: str = field(
        default_factory=lambda: _env_str("FAISS_PROFILE_PATH", _DEFAULT_FAISS_PROFILE_PATH)
    )
    faiss_profile_collection: str = field(
        default_factory=lambda: _env_str(
            "FAISS_PROFILE_COLLECTION", _DEFAULT_FAISS_PROFILE_COLLECTION
        )
    )

    # --- Gömme modeli ---
    embedding_model: str = field(
        default_factory=lambda: _env_str("EMBEDDING_MODEL", _DEFAULT_EMBEDDING_MODEL)
    )
    embedding_device: str = field(
        default_factory=lambda: _env_str("EMBEDDING_DEVICE", _DEFAULT_EMBEDDING_DEVICE)
    )
    embedding_batch_size: int = field(
        default_factory=lambda: _env_int("EMBEDDING_BATCH_SIZE", _DEFAULT_EMBEDDING_BATCH_SIZE)
    )

    # --- FAISS Arama limitleri ---
    faiss_search_top_k: int = field(
        default_factory=lambda: _env_int("FAISS_SEARCH_TOP_K", _DEFAULT_FAISS_SEARCH_TOP_K)
    )
    faiss_max_tenders_per_profile: int = field(
        default_factory=lambda: _env_int(
            "FAISS_MAX_TENDERS_PER_PROFILE", _DEFAULT_FAISS_MAX_TENDERS_PER_PROFILE
        )
    )
    faiss_max_chunks_per_tender: int = field(
        default_factory=lambda: _env_int(
            "FAISS_MAX_CHUNKS_PER_TENDER", _DEFAULT_FAISS_MAX_CHUNKS_PER_TENDER
        )
    )
    faiss_max_profiles_per_tender: int = field(
        default_factory=lambda: _env_int(
            "FAISS_MAX_PROFILES_PER_TENDER", _DEFAULT_FAISS_MAX_PROFILES_PER_TENDER
        )
    )

    minimum_final_score: float = field(
        default_factory=lambda: _env_float("MINIMUM_FINAL_SCORE", _DEFAULT_MINIMUM_FINAL_SCORE)
    )

    # --- Nihai puan ağırlıkları ---
    weight_max_chunk: float = field(
        default_factory=lambda: _env_float("WEIGHT_MAX_CHUNK", _DEFAULT_WEIGHT_MAX_CHUNK)
    )
    weight_top_chunks: float = field(
        default_factory=lambda: _env_float("WEIGHT_TOP_CHUNKS", _DEFAULT_WEIGHT_TOP_CHUNKS)
    )
    weight_section_diversity: float = field(
        default_factory=lambda: _env_float(
            "WEIGHT_SECTION_DIVERSITY", _DEFAULT_WEIGHT_SECTION_DIVERSITY
        )
    )
    weight_okas: float = field(
        default_factory=lambda: _env_float("WEIGHT_OKAS", _DEFAULT_WEIGHT_OKAS)
    )
    weight_title: float = field(
        default_factory=lambda: _env_float("WEIGHT_TITLE", _DEFAULT_WEIGHT_TITLE)
    )

    # --- LLM bağlamı ---
    llm_context_max_chars: int = field(
        default_factory=lambda: _env_int("LLM_CONTEXT_MAX_CHARS", _DEFAULT_LLM_CONTEXT_MAX_CHARS)
    )

    # --- İSBAK filtresi ---
    excluded_authorities: tuple[str, ...] = field(
        default_factory=lambda: tuple(_DEFAULT_EXCLUDED_AUTHORITIES)
    )

    def __post_init__(self) -> None:
        _vi = self._validate_positive_int
        _vi("faiss_search_top_k", self.faiss_search_top_k)
        _vi("faiss_max_tenders_per_profile", self.faiss_max_tenders_per_profile)
        _vi("faiss_max_chunks_per_tender", self.faiss_max_chunks_per_tender)
        _vi("embedding_batch_size", self.embedding_batch_size)
        _vi("llm_context_max_chars", self.llm_context_max_chars)

        weight_groups = [
            (
                "nihai ağırlıklar",
                {
                    "weight_max_chunk": self.weight_max_chunk,
                    "weight_top_chunks": self.weight_top_chunks,
                    "weight_section_diversity": self.weight_section_diversity,
                    "weight_okas": self.weight_okas,
                    "weight_title": self.weight_title,
                },
            ),
        ]

        for group_name, weights in weight_groups:
            for name, value in weights.items():
                if not 0.0 <= value <= 1.0:
                    raise ValueError(f"{name} 0 ile 1 arasında olmalıdır, alınan: {value}")
            total = sum(weights.values())
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"{group_name} toplamı 1.0 olmalıdır, alınan: {total:.6f}")

        for name, value in {
            "minimum_final_score": self.minimum_final_score,
        }.items():
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} 0 ile 1 arasında olmalıdır, alınan: {value}")

    @property
    def resolved_qdrant_path(self) -> Path:
        """Göreceli yolları proje köküne göre çözer."""
        p = Path(self.qdrant_isbak_path)
        if p.is_absolute():
            return p
        # app/config/ → app/ → proje kökü
        project_root = Path(__file__).resolve().parents[2]
        return (project_root / p).resolve()

    @staticmethod
    def _validate_positive_int(name: str, value: int) -> None:
        if value <= 0:
            raise ValueError(f"{name} pozitif bir tamsayı olmalıdır.")


@lru_cache(maxsize=1)
def get_isbak_rag_settings() -> IsbakRagSettings:
    """Önbelleğe alınmış varsayılan İSBAK RAG ayarlarını döndürür."""
    return IsbakRagSettings()


__all__ = [
    "DECISION_VERSION",
    "IsbakRagSettings",
    "get_isbak_rag_settings",
]
