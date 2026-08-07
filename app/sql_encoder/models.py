"""SQL Encoder katmanının tipli giriş ve çıkış sözleşmeleri."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TenderStatus = Literal["active", "inactive", "all"]


class TenderSearchIntent(BaseModel):
    """Doğal dil isteğinin güvenli ve sınırlı arama karşılığı."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: TenderStatus = "active"
    limit: int = Field(default=5, ge=1, le=500)
    random_order: bool = False
    city: str | None = Field(default=None, max_length=120)
    tender_type: str | None = Field(default=None, max_length=160)
    authority: str | None = Field(default=None, max_length=240)
    keyword: str | None = Field(default=None, max_length=240)
    okas_code_prefix: str | None = Field(default=None, max_length=32)
    tender_date_from: date | None = None
    tender_date_to: date | None = None

    @field_validator(
        "city",
        "tender_type",
        "authority",
        "keyword",
        "okas_code_prefix",
        mode="before",
    )
    @classmethod
    def empty_text_is_none(cls, value: Any) -> Any:
        if value is None:
            return None
        normalized = " ".join(str(value).split())
        return normalized or None

    @field_validator("okas_code_prefix")
    @classmethod
    def validate_okas_prefix(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"[0-9.\-/]+", value):
            raise ValueError(
                "okas_code_prefix yalnızca rakam, nokta, tire ve eğik çizgi içerebilir."
            )
        return value

    @model_validator(mode="after")
    def validate_date_range(self) -> TenderSearchIntent:
        if (
            self.tender_date_from is not None
            and self.tender_date_to is not None
            and self.tender_date_from > self.tender_date_to
        ):
            raise ValueError("tender_date_from, tender_date_to değerinden sonra olamaz.")
        return self


class SqlValidationReport(BaseModel):
    """Bir SQL sorgusuna uygulanan güvenlik denetiminin sonucu."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    errors: list[str] = Field(default_factory=list)
    query_hash: str = ""
    statement_type: str = ""
    tables: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    functions: list[str] = Field(default_factory=list)
    placeholder_count: int = 0
    join_count: int = 0
    ast_node_count: int = 0


class GeneratedSql(BaseModel):
    """Niyet çıkarımı sonrasında Python tarafından derlenen SQL."""

    model_config = ConfigDict(extra="forbid")

    natural_language_request: str
    encoder_model: str
    intent: TenderSearchIntent
    sql: str
    parameters: list[Any]
    validation: SqlValidationReport
    effective_limit: int
    random_seed: int | None = None


class ReadonlyQueryResult(BaseModel):
    """Salt okunur sorgunun sınırlandırılmış sonucu."""

    model_config = ConfigDict(extra="forbid")

    query_hash: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    max_rows: int
    elapsed_ms: float
    transaction_read_only: bool = True


class TenderSelection(BaseModel):
    """Karar zincirine aktarılacak doğrudan SQL Encoder seçimi."""

    model_config = ConfigDict(extra="forbid")

    natural_language_request: str
    encoder_model: str
    sql: str
    parameters: list[Any]
    random_seed: int | None
    selected_ikns: list[str]
    rows: list[dict[str, Any]]
    schema_tables: list[str]
    query_hash: str


__all__ = [
    "GeneratedSql",
    "TenderSelection",
    "ReadonlyQueryResult",
    "SqlValidationReport",
    "TenderSearchIntent",
    "TenderStatus",
]
