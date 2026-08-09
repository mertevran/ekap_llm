from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

ALLOWED_QWEN_MODELS = {"qwen3.5:4b-q4_K_M"}
ALLOWED_GEMMA_MODELS = {"gemma4:e2b-it-q4_K_M"}


class Settings(BaseSettings):
    app_name: str = "EKAP İSBAK Tek Modelli RAG"
    app_env: str = "development"
    log_level: str = "INFO"

    database_host: str = ""
    database_port: int = 5433
    database_name: str = ""
    database_user: str = ""
    database_password: str = ""
    database_sslmode: str = ""
    database_connect_timeout_seconds: int = 10
    database_statement_timeout_ms: int = 60_000
    database_application_name: str = "ekap-isbak-decision"

    qdrant_path: Path = Field(default=Path("storage/qdrant"))
    model_cache_path: Path = Field(default=Path("storage/model_cache"))

    embedding_model: str = "BAAI/bge-m3"
    embedding_device: str = "cpu"
    embedding_batch_size: int = 4
    embedding_normalize: bool = True

    qwen_model: str = "qwen3.5:4b-q4_K_M"
    gemma_model: str = "gemma4:e2b-it-q4_K_M"
    phi_model: str = "phi4-mini:latest"

    ollama_connect_timeout_seconds: int = 10
    ollama_decision_timeout_seconds: int = 3600
    ollama_max_attempts: int = 3
    ollama_retry_backoff_seconds: float = 2.0

    gemma_decision_num_ctx: int = 12288
    qwen_decision_num_ctx: int = 8192

    gemma_decision_num_predict: int = 1100
    qwen_decision_num_predict: int = 512

    ollama_num_thread: int = 4
    ollama_num_batch: int = 32

    max_json_corrections: int = 0

    max_tender_context_chars: int = 30000
    max_company_context_chars: int = 5000
    automatic_positive_decisions_enabled: bool = False
    max_runtime_swap_growth_mb: int = 512

    active_tender_status_values: list[str] = Field(
        default_factory=lambda: ["İhale İlanı Yayımlanmış, Katılıma Açık"]
    )

    @field_validator("qwen_model")
    @classmethod
    def validate_qwen_model(cls, v: str) -> str:
        if v not in ALLOWED_QWEN_MODELS:
            if "qwen2" in v.lower() or "qwen3:4b" in v.lower():
                raise ValueError(
                    f"Eski model adları (Qwen2, qwen3:4b vb.) reddedildi. İzin verilenler: {ALLOWED_QWEN_MODELS}"
                )
            raise ValueError(
                f"İzin verilmeyen Qwen modeli: {v}. İzin verilenler: {ALLOWED_QWEN_MODELS}"
            )
        return v

    @field_validator("gemma_model")
    @classmethod
    def validate_gemma_model(cls, v: str) -> str:
        if v not in ALLOWED_GEMMA_MODELS:
            if "gemma2" in v.lower() or "gemma3" in v.lower():
                raise ValueError(
                    f"Eski model adları (Gemma2, gemma3:4b vb.) reddedildi. İzin verilenler: {ALLOWED_GEMMA_MODELS}"
                )
            raise ValueError(
                f"İzin verilmeyen Gemma modeli: {v}. İzin verilenler: {ALLOWED_GEMMA_MODELS}"
            )
        return v

    @field_validator(
        "gemma_decision_num_ctx",
        "qwen_decision_num_ctx",
        "gemma_decision_num_predict",
        "qwen_decision_num_predict",
        "ollama_num_thread",
        "ollama_num_batch",
        "max_tender_context_chars",
        "max_company_context_chars",
        "database_connect_timeout_seconds",
        "database_statement_timeout_ms",
        "max_runtime_swap_growth_mb",
    )
    @classmethod
    def validate_positive_integers(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(
                f"Değer pozitif bir tam sayı olmalıdır. Alınan: {v}"
            )
        return v

    @field_validator("max_json_corrections")
    @classmethod
    def validate_json_corrections(cls, v: int) -> int:
        if v < 0:
            raise ValueError(
                f"max_json_corrections negatif olamaz. Alınan: {v}"
            )
        return v

    ollama_base_url: str = "http://localhost:11434"

    output_dir: Path = Field(default=Path("outputs"))
    report_dir: Path = Field(default=Path("reports"))
    log_dir: Path = Field(default=Path("logs"))

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def resolve_paths(self) -> None:
        path_fields = [
            "qdrant_path",
            "model_cache_path",
            "output_dir",
            "report_dir",
            "log_dir",
        ]

        for field_name in path_fields:
            current_path = getattr(self, field_name)

            if not current_path.is_absolute():
                current_path = PROJECT_ROOT / current_path

            setattr(self, field_name, current_path.resolve())

    def ensure_directories(self) -> None:
        directories = [
            self.qdrant_path,
            self.model_cache_path,
            self.output_dir,
            self.report_dir,
            self.log_dir,
        ]

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.resolve_paths()
    settings.ensure_directories()

    logger.info(f"Sistem Başlangıcı - Qwen Modeli: {settings.qwen_model}")

    return settings
