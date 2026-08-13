"""
B1 alan terimi istisnası — düşünme kapatılınca ortaya çıkan gerilemenin düzeltmesi.

ÖLÇÜM (07.08.2026): `LLM_DUSUNME=false` ile kaçırma seti 30/30'dan 20/30'a düştü.
On hatanın HEPSİ `uygun -> belirsiz` ve hepsi B1 yanlış tetiklenmesiydi:

    "40 Kalem CCTV Haberleşme, Kontrol ve Kent İzleme Merkezi Sistemi"
    "53 Kalemde Elektronik Denetleme Sistemi Malzeme Alımı"
    "36 kalem Güvenlik Kamera Sistemi ve Fiber Optik Altyapı"

Bunlar belirsiz ifadeler DEĞİL. `Niteliği` alanı başlığı tekrarlıyor, kod başlığı
eliyor, geriye bir şey kalmıyor ve "kanıt yok" deniyor — oysa elenen şeyin kendisi
kanıttı. Düşünme açıkken model kuralı bazen aşıyordu; kapalıyken lafzına uyuyor.

EN KRİTİK TEST: `test_gercekten_belirsiz_ilan_hala_belirsiz`.
İstisna fazla genişlerse B1'i tamamen etkisiz kılar. Raporun 6.3'ündeki gerçek
vaka ("13 Modülden oluşan Dijital İK Yönetim Sistemi") istisnadan SONRA da
tetiklenmeye devam etmeli — modüllerin içeriği gerçekten bilinmiyor.
"""

from __future__ import annotations

import pytest

from app.decision.stage1_kapsam import alan_sozlugu, kalem_listesi_yok_mu
from app.profiles.loader import profilleri_yukle


@pytest.fixture(scope="module")
def sozluk():
    return alan_sozlugu(profilleri_yukle())


def _nitelik(icerik: str) -> str:
    """`_NITELIK_ALANI` deseninin beklediği tablo satırını kurar."""
    return f"| **a)** Niteliği, türü ve miktarı | : | {icerik} |"


# Gerçek koşuda B1'in YANLIŞ tetiklendiği başlıklar (kaçırma seti).
YANLIS_TETIKLENENLER = [
    ("40 Kalem CCTV Haberleşme, Kontrol ve Kent İzleme Merkezi Sistemi Alım ve "
     "Kurulum işi Ayrıntılı bilgiye idari şartnameden ulaşılabilir.",
     "CCTV Haberleşme, Kontrol ve Kent İzleme Merkezi Sistemi Alım ve Kurulum işi"),
    ("53 Kalemde Elektronik Denetleme Sistemi Malzeme Alımı ve Montajı "
     "Ayrıntılı bilgiye idari şartnameden ulaşılabilir.",
     "EDS(ELEKTRONİK DENETLEME SİSTEMİ) MALZEME ALIMI VE MONTAJI İŞİ"),
    ("36 kalem Güvenlik Kamera Sistemi ve Fiber Optik Alt Yapı Donanım Malzemeleri "
     "Ayrıntılı bilgiye idari şartnameden ulaşılabilir.",
     "Terminaller ve Hizmet Binaları Güvenlik Kamera Sistemi ve Fiber Optik Alt Yapı"),
    ("50 Kalem Muhtelif Tür ve Miktarlarda Sinyalizasyon Malzemeleri Alımı "
     "Ayrıntılı bilgiye idari şartnameden ulaşılabilir.",
     "Kavşak Kontrol Cihazı İle Yedek Malzemeler ve Sinyalizasyon Malzemeleri Alımı"),
]


@pytest.mark.parametrize("icerik,adi", YANLIS_TETIKLENENLER)
def test_alan_terimi_varsa_uyari_verilmiyor(icerik, adi, sozluk):
    kapsam = _nitelik(icerik)
    assert kalem_listesi_yok_mu(kapsam, adi) is True, "istisna öncesi tetiklenmeliydi"
    assert kalem_listesi_yok_mu(kapsam, adi, sozluk) is False, "istisna kurtarmalıydı"


def test_gercekten_belirsiz_ilan_hala_belirsiz(sozluk):
    """Rapor 6.3'ün vakası — istisna bunu KURTARMAMALI.

    "13 Modülden oluşan" bir sayaçtır, modüllerin ne olduğu gerçekten bilinmiyor.
    Hiçbir profil terimi geçmediği için istisna açılmaz.
    """
    kapsam = _nitelik(
        "1 Adet 13 Modülden oluşan Dijital İnsan Kaynakları Yönetim Sistemi "
        "Ayrıntılı bilgiye idari şartnameden ulaşılabilir."
    )
    adi = "Dijital İnsan Kaynakları Yönetim Sistemi Hizmet Alımı"
    assert kalem_listesi_yok_mu(kapsam, adi) is True
    assert kalem_listesi_yok_mu(kapsam, adi, sozluk) is True, (
        "İstisna fazla geniş — gerçekten belirsiz ilanı da kurtarıyor."
    )


def test_kapsam_disi_ilan_uyariyi_tetiklemeye_devam_ediyor(sozluk):
    """Öğrenci taşıma: alan terimi yok, istisna açılmamalı."""
    kapsam = _nitelik(
        "181 Günlük Öğrenci Taşıma Hizmet Alım İşi "
        "Ayrıntılı bilgiye idari şartnameden ulaşılabilir."
    )
    adi = "İlköğretim Öğrencilerinin Taşınması Amacıyla Hizmet Alım İşi"
    assert kalem_listesi_yok_mu(kapsam, adi, sozluk) is True


def test_kalem_listesi_zaten_varsa_degismiyor(sozluk):
    """İstisna, kalem listesi OLAN ilanların davranışını etkilememeli."""
    kapsam = _nitelik(
        "Kamera 50 ADET, Switch 10 ADET, Kablo 4000 METRE, NVR 2 ADET, "
        "Direk 25 ADET, Pano 8 ADET"
    )
    adi = "Güvenlik Kamera Sistemi Alımı"
    assert kalem_listesi_yok_mu(kapsam, adi) is False
    assert kalem_listesi_yok_mu(kapsam, adi, sozluk) is False


def test_sozluk_kisa_terimleri_almiyor():
    """'kart', 'yol' gibi kısa parçalar her ilana vurup istisnayı her yerde açar."""
    from app.decision.stage1_kapsam import MIN_ALAN_TERIMI_UZUNLUGU

    s = alan_sozlugu(profilleri_yukle())
    assert s, "sözlük boş"
    assert all(len(t) >= MIN_ALAN_TERIMI_UZUNLUGU - 2 for t in s), (
        "katlama sonrası bile çok kısa terim var"
    )


def test_sozluk_yoksa_eski_davranis(sozluk):
    """`alan_terimleri=None` (bayrak kapalı) -> davranış BİREBİR eskisi."""
    kapsam = _nitelik(YANLIS_TETIKLENENLER[0][0])
    adi = YANLIS_TETIKLENENLER[0][1]
    assert kalem_listesi_yok_mu(kapsam, adi, None) == kalem_listesi_yok_mu(kapsam, adi)


def test_bos_sozluk_istisnayi_acmaz(sozluk):
    kapsam = _nitelik(YANLIS_TETIKLENENLER[0][0])
    adi = YANLIS_TETIKLENENLER[0][1]
    assert kalem_listesi_yok_mu(kapsam, adi, frozenset()) is True
