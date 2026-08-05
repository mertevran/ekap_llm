from __future__ import annotations

from pydantic import BaseModel, Field


class EmbeddedChunk(BaseModel):
    chunk_id: str
    text: str

    vector: list[float] = Field(default_factory=list)
    vector_size: int

    model_name: str

    tender_id: str
    ikn: str
    section_id: str
    source_table: str
    source_record_ids: list[str] = Field(default_factory=list)
