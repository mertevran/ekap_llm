from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TenderSection(BaseModel):
    section_id: str
    tender_id: str
    ikn: str

    source_table: str
    source_record_ids: list[str] = Field(default_factory=list)

    title: str
    text: str

    metadata: dict[str, Any] = Field(default_factory=dict)


class TenderDocument(BaseModel):
    tender_id: str
    ikn: str
    title: str

    sections: list[TenderSection] = Field(default_factory=list)


class TenderChunk(BaseModel):
    chunk_id: str
    tender_id: str
    ikn: str

    section_id: str
    source_table: str
    source_record_ids: list[str] = Field(default_factory=list)

    title: str
    text: str

    chunk_index: int
    char_start: int
    char_end: int

    metadata: dict[str, Any] = Field(default_factory=dict)


class TenderChunkPackage(BaseModel):
    document: TenderDocument
    chunks: list[TenderChunk] = Field(default_factory=list)
