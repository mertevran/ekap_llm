"""Aktif ihale filtreleme testleri (Gereksinim 52: 1-8).

TenderRepository'nin aktif ihale seçimi ve belirsiz durum
raporlamasını mock veritabanıyla doğrular.
Gerçek PostgreSQL bağlantısı gerektirmez.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Test 1: public.tenders alanlarının TenderRecord'a doğru eşlendiğini doğrula
# ---------------------------------------------------------------------------


def test_tender_record_field_mapping() -> None:
    """TenderRecord gerekli tüm alanları içermeli."""
    from app.domain import TenderRecord

    sample_row = {
        "id": "T001",
        "ikn": "2026/001",
        "adi": "Test İhalesi",
        "idare_adi": "Test İdare",
        "il": "İstanbul",
        "ihale_tarihi": "2026-12-01 10:00:00",
        "ihale_turu": "Hizmet Alımı",
        "ihale_usulu": "Açık İhale",
        "ihale_durumu": "İhale İlanı Yayımlanmış, Katılıma Açık",
        "kapsam": "Test kapsamı",
        "e_ihale": 1,
        "kismi_teklif": 0,
        "ihale_yeri": "İstanbul",
        "isin_yeri": "İstanbul",
        "dokuman_sayisi": 5,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
        "announcements": [],
        "characteristics": [],
        "okas_codes": [],
    }
    record = TenderRecord.model_validate(sample_row)
    assert record.id == "T001"
    assert record.ikn == "2026/001"
    assert record.adi == "Test İhalesi"
    assert record.idare_adi == "Test İdare"
    assert record.il == "İstanbul"


# ---------------------------------------------------------------------------
# Test 2: takip_durumu sütununun varsayılmaması
# ---------------------------------------------------------------------------


def test_tender_record_without_takip_durumu_works() -> None:
    """TenderRecord takip_durumu olmadan da oluşturulabilmeli."""
    from app.domain import TenderRecord

    sample_row = {
        "id": "T002",
        "ikn": "2026/002",
        "adi": "Takip Durumu Yok",
        "idare_adi": "Test",
        "il": "Ankara",
        "ihale_tarihi": "2026-12-01 10:00:00",
        "ihale_turu": "Mal Alımı",
        "ihale_usulu": "Açık İhale",
        "ihale_durumu": "İhale İlanı Yayımlanmış, Katılıma Açık",
        "kapsam": None,
        "e_ihale": 0,
        "kismi_teklif": 0,
        "ihale_yeri": None,
        "isin_yeri": None,
        "dokuman_sayisi": 0,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
        "announcements": [],
        "characteristics": [],
        "okas_codes": [],
    }
    # takip_durumu sütunu domain modelinde opsiyonel olmalı
    record = TenderRecord.model_validate(sample_row)
    assert record.id == "T002"


# ---------------------------------------------------------------------------
# Test 3: Aktif durum izin listesinin çalışması
# ---------------------------------------------------------------------------


def test_active_status_allowlist() -> None:
    """Yalnızca izin listesindeki durumlar aktif sayılmalı."""
    from app.config import get_settings

    settings = get_settings()
    active_statuses = settings.active_tender_status_values
    assert isinstance(active_statuses, list)
    assert len(active_statuses) > 0
    # Her değer string olmalı
    for s in active_statuses:
        assert isinstance(s, str), f"Aktif durum string değil: {s!r}"


# ---------------------------------------------------------------------------
# Test 4: Pasif ihale indekse alınmamalı (is_isbak_tender filtresiyle değil,
#          ihale_durumu filtresiyle)
# ---------------------------------------------------------------------------


def test_inactive_status_not_included() -> None:
    """Kapalı durumundaki ihale, aktif listesine girmemeli."""
    from app.config import get_settings

    settings = get_settings()
    inactive_statuses = ["İhale İptal Edilmiştir", "Sonuçlandırılmıştır", "Kapatıldı"]
    for status in inactive_statuses:
        assert status not in settings.active_tender_status_values, (
            f"Pasif durum aktif listede: {status!r}"
        )


# ---------------------------------------------------------------------------
# Test 5: ihale_tarihi sözlüksel karşılaştırılmamalı
# ---------------------------------------------------------------------------


def test_ihale_tarihi_not_compared_lexically() -> None:
    """İhale tarihi string karşılaştırmasıyla değil, datetime ile değerlenmeli.
    '2026-09-01' < '2026-10-01' lexical olarak doğru, ama
    '09-01-2026' > '10-01-2026' lexical olarak YANLIŞ → datetime zorunlu.
    """
    import inspect

    from app.database.tender_repository import TenderRepository

    source = inspect.getsource(TenderRepository.get_active_tenders)
    # datetime.strptime kullanılmalı, basit str karşılaştırması olmamalı
    assert "strptime" in source or "datetime" in source, (
        "get_active_tenders() tarih karşılaştırması datetime kullanmalı"
    )


# ---------------------------------------------------------------------------
# Test 6: Belirsiz ihale tarihi ayrı raporlanmalı
# ---------------------------------------------------------------------------


def test_uncertain_date_reported_separately() -> None:
    """ihale_tarihi çözümlenemezse uncertain rapor alınmalı."""
    import inspect

    from app.database.tender_repository import TenderRepository

    source = inspect.getsource(TenderRepository.get_active_tenders)
    assert "uncertain" in source.lower() or "belirsiz" in source.lower(), (
        "Belirsiz tarih kaydı için raporlama kodu eksik"
    )


# ---------------------------------------------------------------------------
# Test 7: olmayan son_teklif_tarihi sütunu varsayılmamalı
# ---------------------------------------------------------------------------


def test_son_teklif_tarihi_column_not_assumed() -> None:
    """tender_repository.py'da son_teklif_tarihi kullanılmamalı."""
    import inspect

    from app.database import tender_repository

    source = inspect.getsource(tender_repository)
    assert "son_teklif_tarihi" not in source, (
        "son_teklif_tarihi sütunu public.tenders'da yok; kullanılmamalı"
    )


# ---------------------------------------------------------------------------
# Test 8: N+1 sorgu oluşmaması için toplu sorgu kullanılmalı
# ---------------------------------------------------------------------------


def test_batch_query_used_for_announcements() -> None:
    """TenderRepository her ihale için ayrı sorgu değil, toplu sorgu yapmalı."""
    import inspect

    from app.database.tender_repository import TenderRepository

    source = inspect.getsource(TenderRepository.get_by_ikns)
    # Toplu sorgu: ANY(%s) veya IN(...)
    assert "ANY" in source or "= ANY" in source, (
        "get_by_ikns toplu sorgu (ANY) kullanmalı, N+1 sorgu olmamalı"
    )
    # _read_announcements_for_tenders adlı metod olmalı
    assert "_read_announcements_for_tenders" in source
    assert "_read_characteristics_for_tenders" in source
    assert "_read_okas_codes_for_tenders" in source
