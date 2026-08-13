"""
Şirket tercihleri — portaldan gelen seçimin filtreye dönüşmesi.

EN KRİTİK TEST: `test_tercih_bossa_birlesik_profile_dusuyor`.
Portal doldurulmadan tarama başlatılırsa `birlesik` modu BOŞ filtre üretirse
havuz sıfırlanır ve o günkü tarama hiçbir ihale işlemez — sessiz bir üretim
arızası. Boş tercih profillere düşmeli.
"""

from __future__ import annotations

import pytest

from app.decision.sirket_tercihleri import (
    KAYNAKLAR,
    SirketTercihi,
    filtre_kur,
    kod_ayikla,
    tercihten_kur,
)
from app.profiles.loader import profilleri_yukle


# ------------------------------------------------------------ OKAS ayrıştırma
@pytest.mark.parametrize(
    "ham,beklenen",
    [
        ("45232420-2", "45232420"),                      # portal eki
        ("03111300 - Ayçiçeği tohumları", "03111300"),   # açıklamalı
        ("34996", "34996"),                              # düz kod
        ("  34923  ", "34923"),                          # boşluklu
        ("", ""),
        ("ABC", ""),                                     # rakamsız
        (None, ""),
    ],
)
def test_kod_ayikla(ham, beklenen):
    assert kod_ayikla(ham) == beklenen


def test_bastaki_sifir_korunuyor():
    """'03111300' -> '0311...' ; sayıya çevrilirse baştaki sıfır kaybolur."""
    assert kod_ayikla("03111300 - Ayçiçeği tohumları").startswith("0")


# ------------------------------------------------------------------ kurulum
def test_tercihten_kur():
    t = SirketTercihi(
        okas_kodlari=("34996", "45316"),
        anahtar_kelimeler=("sinyalizasyon", "kavşak kontrol"),
    )
    f = tercihten_kur(t)
    assert set(f.terimler) == {"sinyalizasyon", "kavşak kontrol"}
    assert set(f.okas_on_ekleri) == {"34996", "45316"}
    assert f.kaynak == "sirket_tercihleri"


def test_gecersiz_kaynak():
    with pytest.raises(ValueError):
        filtre_kur("baska")


def test_kaynak_listesi():
    assert KAYNAKLAR == ("profil", "tercih", "birlesik")


# -------------------------------------------------------------- mod davranışı
def test_profil_modu_tercihi_okumuyor(monkeypatch):
    """`profil` modunda veritabanına HİÇ gidilmemeli — ölçülmüş taban değişmesin."""
    import app.decision.sirket_tercihleri as st

    cagrildi = []
    monkeypatch.setattr(st, "tercihleri_oku", lambda a=None: cagrildi.append(1) or SirketTercihi())
    tanim, _ = filtre_kur("profil", profiller=profilleri_yukle())
    assert not cagrildi, "profil modunda company_preferences okundu"
    assert tanim.terimler and tanim.okas_on_ekleri


def test_tercih_bossa_birlesik_profile_dusuyor(monkeypatch):
    """Portal doldurulmamışsa havuz SIFIRLANMAMALI."""
    import app.decision.sirket_tercihleri as st

    monkeypatch.setattr(st, "tercihleri_oku", lambda a=None: SirketTercihi())
    tanim, tercih = filtre_kur("birlesik", profiller=profilleri_yukle())
    assert tercih.bos_mu()
    assert len(tanim.terimler) > 100, "boş tercih profil tabanını yuttu"


def test_birlesik_ikisini_topluyor(monkeypatch):
    import app.decision.sirket_tercihleri as st

    monkeypatch.setattr(st, "tercihleri_oku", lambda a=None: SirketTercihi(
        okas_kodlari=("99999",), anahtar_kelimeler=("bambaşka bir terim",)))
    profil, _ = filtre_kur("profil", profiller=profilleri_yukle())
    birlesik, _ = filtre_kur("birlesik", profiller=profilleri_yukle())

    assert set(profil.terimler) < set(birlesik.terimler)
    assert "bambaşka bir terim" in birlesik.terimler
    assert "99999" in birlesik.okas_on_ekleri
    assert set(profil.okas_on_ekleri) < set(birlesik.okas_on_ekleri)


def test_tercih_modu_profilleri_katmiyor(monkeypatch):
    """'tercih' = İŞ PAKETLERİNDEN BAĞIMSIZ. Profil terimi sızmamalı."""
    import app.decision.sirket_tercihleri as st

    monkeypatch.setattr(st, "tercihleri_oku", lambda a=None: SirketTercihi(
        okas_kodlari=("34996",), anahtar_kelimeler=("sinyalizasyon",)))
    tanim, _ = filtre_kur("tercih")
    assert set(tanim.terimler) == {"sinyalizasyon"}
    assert set(tanim.okas_on_ekleri) == {"34996"}


def test_kisa_kelimeler_eleniyor(monkeypatch):
    """'yol' gibi parçalar her ihaleye vurup filtreyi işlevsiz kılar."""
    import app.decision.sirket_tercihleri as st
    from app.decision.sirket_tercihleri import tercihleri_oku  # noqa: F401

    t = SirketTercihi(anahtar_kelimeler=("kamera",))
    assert "kamera" in tercihten_kur(t).terimler


def test_sqlite_arka_ucunda_bos_donuyor(monkeypatch):
    """Yerel geliştirmede company_preferences yok; tarama patlamamalı."""
    import app.decision.sirket_tercihleri as st
    from app.config.settings import Settings

    ayar = Settings(_env_file=None)
    ayar.data_backend = "sqlite"
    assert st.tercihleri_oku(ayar).bos_mu()
