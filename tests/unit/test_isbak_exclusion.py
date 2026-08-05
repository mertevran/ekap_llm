"""İSBAK kendi ihalesi dışlama testleri (Gereksinim 52: 9-15).

is_isbak_tender() fonksiyonunun çeşitli idare adı varyasyonlarını
doğru şekilde tanıdığını ve gerçek olmayan eşleşmeleri
yanlışlıkla dışlamadığını doğrular.
"""

from __future__ import annotations

import pytest

from app.indexing.active_tender_indexer import is_isbak_tender, is_tender_empty

# ---------------------------------------------------------------------------
# Test 9: "İSBAK A.Ş." dışlanmalı
# ---------------------------------------------------------------------------


def test_isbak_as_is_excluded() -> None:
    assert is_isbak_tender("İSBAK A.Ş.") is True


# ---------------------------------------------------------------------------
# Test 10: "İstanbul Bilişim ve Akıllı Kent Teknolojileri A.Ş." dışlanmalı
# ---------------------------------------------------------------------------


def test_istanbul_bilisim_akilli_kent_is_excluded() -> None:
    assert is_isbak_tender("İstanbul Bilişim ve Akıllı Kent Teknolojileri A.Ş.") is True


# ---------------------------------------------------------------------------
# Test 11: Noktalama ve şirket eki farklılıklarının eşleşmesi
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "idare_adi",
    [
        "ISBAK A.Ş.",
        "İsbak Anonim Şirketi",
        "isbak a.ş",
        "İSBAK AŞ",
        "İSBAK Sanayi ve Ticaret A.Ş.",
        "istanbul bilişim ve akıllı kent teknolojileri",
    ],
)
def test_isbak_variant_spellings_are_excluded(idare_adi: str) -> None:
    assert is_isbak_tender(idare_adi) is True, f"Dışlanmadı: {idare_adi!r}"


# ---------------------------------------------------------------------------
# Test 12: Benzer fakat farklı kurumlar yanlışlıkla dışlanmamalı
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "idare_adi",
    [
        "İstanbul Büyükşehir Belediyesi",
        "İstanbul Su ve Kanalizasyon İdaresi",
        "Ankara Bilişim A.Ş.",
        "İstanbul Ulaşım A.Ş.",
        "Türkiye Bilişim Vakfı",
        "İstanbul Sağlık A.Ş.",
        "Bilişim Teknolojileri A.Ş.",
        "İstanbul Bilişim A.Ş.",
    ],
)
def test_similar_but_different_authorities_not_excluded(idare_adi: str) -> None:
    assert is_isbak_tender(idare_adi) is False, f"Yanlış dışlandı: {idare_adi!r}"


# ---------------------------------------------------------------------------
# Test 13 & 14: None / boş idare_adi dışlanmamalı
# ---------------------------------------------------------------------------


def test_none_authority_not_excluded() -> None:
    assert is_isbak_tender(None) is False  # type: ignore[arg-type]


def test_empty_authority_not_excluded() -> None:
    assert is_isbak_tender("") is False


# ---------------------------------------------------------------------------
# Test 15: is_tender_empty() davranışı
# ---------------------------------------------------------------------------


class _FakeTender:
    def __init__(
        self, adi="", kapsam="", announcements=None, characteristics=None, okas_codes=None
    ):
        self.adi = adi
        self.kapsam = kapsam
        self.announcements = announcements or []
        self.characteristics = characteristics or []
        self.okas_codes = okas_codes or []


def test_tender_with_adi_is_not_empty() -> None:
    tender = _FakeTender(adi="Sinyalizasyon Alımı")
    assert is_tender_empty(tender) is False


def test_tender_with_kapsam_is_not_empty() -> None:
    tender = _FakeTender(kapsam="Trafik yönetim sistemi kapsamında.")
    assert is_tender_empty(tender) is False


class _FakeAnnouncement:
    def __init__(self, icerik=""):
        self.icerik = icerik


class _FakeCharacteristic:
    def __init__(self, ozellik=""):
        self.ozellik = ozellik


class _FakeOkas:
    def __init__(self, ad=""):
        self.ad = ad


def test_tender_with_announcement_is_not_empty() -> None:
    tender = _FakeTender(announcements=[_FakeAnnouncement(icerik="İlan metni.")])
    assert is_tender_empty(tender) is False


def test_completely_empty_tender_is_empty() -> None:
    tender = _FakeTender()
    assert is_tender_empty(tender) is True


def test_whitespace_only_tender_is_empty() -> None:
    tender = _FakeTender(adi="   ", kapsam="  ")
    assert is_tender_empty(tender) is True
