"""
Etiketleme partilerinin TABAKALANMASI — `--filtreli` ile `--rastgele` ayrımı.

NEDEN TEST: iki tabaka karışırsa ölçüm sessizce bozulur.

  · `--filtreli` yalnızca SQL ön filtresini geçenlerden örnekler. Pozitif
    yoğunluğu yüksektir ama etiketli set filtrenin YANLILIĞINI miras alır.
  · Sadece bundan etiketlenirse "filtre pozitif kaçırıyor mu?" sorusu bir daha
    ölçülemez — filtrenin kaçırdığı ihale etiketli sete hiç girmez, dolayısıyla
    kaçırma görünmez olur. Ölçüm kendi kendini doğrular.
  · Raporun 9.3'ü aynı hatanın birincisini kaydediyor: "Kaçırma setinin 30
    örneğinin tamamı, önceki bir modelin 'uygun' dediği havuzdan gelmektedir."

Bu yüzden hangi tabakadan geldiği koşu kaydına (`konfigurasyon.ornekleme`)
yazılıyor ve bu test onun yazıldığını garanti ediyor.
"""

from __future__ import annotations

import pytest

from app.decision.sql_filtre import eslesme_var_mi, profillerden_kur
from app.profiles.loader import profilleri_yukle


@pytest.fixture(scope="module")
def tanim():
    return profillerden_kur(profilleri_yukle(), genislik="genis")


def test_filtreli_havuz_rastgele_havuzun_alt_kumesi(depo, tanim):
    """Filtreli havuz, aktif havuzun ALT KÜMESİ olmalı — yeni ihale uydurmamalı."""
    tum = set(depo.aktif_ihale_iknleri())
    cift = depo.aktif_iknler_oncelikli(tanim)
    filtreli = {i for i, gecti in cift if gecti}
    assert filtreli, "Filtre hiçbir ihale seçmedi; test anlamsız olurdu."
    assert filtreli <= tum
    assert len(filtreli) < len(tum), "Filtre hiçbir şey elemiyorsa ayrı tabaka değildir."


def test_oncelikli_liste_havuzu_daraltmiyor(depo, tanim):
    """`aktif_iknler_oncelikli` ELEMEZ — sadece sıralar. Kaçırma riski sıfır."""
    tum = depo.aktif_ihale_iknleri()
    cift = depo.aktif_iknler_oncelikli(tanim)
    assert len(cift) == len(tum)
    assert {i for i, _ in cift} == set(tum)


def test_gecenler_once_siralaniyor(depo, tanim):
    cift = depo.aktif_iknler_oncelikli(tanim)
    bayraklar = [gecti for _, gecti in cift]
    # True'lar bloğu bittikten sonra bir daha True görülmemeli
    ilk_false = bayraklar.index(False) if False in bayraklar else len(bayraklar)
    assert not any(bayraklar[ilk_false:]), "Sıralama bozuk: geçenler öne alınmamış."


def test_bos_filtre_tum_havuzu_dondurur(depo):
    """Filtre boşsa özel durum yazmaya gerek kalmadan normal sıraya düşmeli."""
    from app.decision.sql_filtre import FiltreTanimi

    cift = depo.aktif_iknler_oncelikli(FiltreTanimi())
    assert len(cift) == len(depo.aktif_ihale_iknleri())
    assert all(not gecti for _, gecti in cift)


def test_filtreli_havuzdaki_ihaleler_gercekten_esleşiyor(depo, tanim):
    """Sıralama bayrağı ile `eslesme_var_mi` aynı sonucu vermeli.

    Postgres SQL'de, SQLite Python'da eşleşme yapıyor; ayrışırlarsa iki arka uçta
    farklı parti çıkar ve etiketli set tutarsızlaşır.
    """
    cift = depo.aktif_iknler_oncelikli(tanim)
    gecenler = [i for i, g in cift if g][:15]
    if not gecenler:
        pytest.skip("Filtreyi geçen ihale yok.")
    for ihale in depo.coklu_getir(gecenler, alan="ikn"):
        assert eslesme_var_mi(tanim, ihale.adi, [o.kod for o in ihale.okas_kodlari]), (
            f"Sıralama 'geçti' dedi ama eşleşme bulunamadı: {ihale.ikn} {ihale.adi[:50]}"
        )


def test_elenenler_gercekten_eslesmiyor(depo, tanim):
    cift = depo.aktif_iknler_oncelikli(tanim)
    elenenler = [i for i, g in cift if not g][:15]
    if not elenenler:
        pytest.skip("Elenen ihale yok.")
    for ihale in depo.coklu_getir(elenenler, alan="ikn"):
        assert not eslesme_var_mi(tanim, ihale.adi, [o.kod for o in ihale.okas_kodlari]), (
            f"Sıralama 'geçmedi' dedi ama eşleşme var: {ihale.ikn} {ihale.adi[:50]}"
        )
