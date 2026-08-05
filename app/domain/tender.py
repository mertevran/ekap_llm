from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TenderAnnouncement(BaseModel):
    id: str
    tender_id: str
    ilan_tipi: str | None = None
    ilan_tarihi: str | None = None
    baslik: str | None = None
    icerik: str | None = None
    created_at: datetime | None = None


class TenderCharacteristic(BaseModel):
    id: int
    tender_id: str
    ozellik: str


class TenderOkasCode(BaseModel):
    id: int
    tender_id: str
    kod: str | None = None
    ad: str | None = None


class TenderRecord(BaseModel):
    id: str
    ikn: str

    adi: str | None = None
    idare_adi: str | None = None
    il: str | None = None
    ihale_tarihi: str | None = None
    ihale_turu: str | None = None
    ihale_usulu: str | None = None
    ihale_durumu: str | None = None
    kapsam: str | None = None

    e_ihale: int | None = None
    kismi_teklif: int | None = None

    ihale_yeri: str | None = None
    isin_yeri: str | None = None
    dokuman_sayisi: int | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    announcements: list[TenderAnnouncement] = Field(default_factory=list)
    characteristics: list[TenderCharacteristic] = Field(default_factory=list)
    okas_codes: list[TenderOkasCode] = Field(default_factory=list)
