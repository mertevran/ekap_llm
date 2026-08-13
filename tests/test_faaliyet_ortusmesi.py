"""
`faaliyet_ortusmesi` şeması + negatif terim doğrulaması testleri.

EN KRİTİK TEST: `test_genis_semada_faaliyet_ortusmesi_karardan_once`.
Ollama alanları ŞEMA SIRASIYLA üretir; `faaliyet_ortusmesi` `karar`dan sonraya
düşerse model kararını verdikten SONRA örtüşmeyi sınıflandırır ve alan, sonradan
uydurulan bir gerekçeye dönüşür — schemas.py başlığındaki ölçülmüş hata modunun
aynısı. `KapsamSonucuGenis` bu yüzden kalıtımla değil, alanlar kopyalanarak
yazıldı; bu test o kopyanın bozulmadığını garanti eder.
"""

from __future__ import annotations

import pytest

from app.decision.negatif_dogrulama import (
    negatifleri_dogrula,
    ozet,
    prompt_blogu,
)
from app.decision.schemas import KapsamSonucu, KapsamSonucuGenis
from app.decision.stage1_kapsam import kullanici_mesaji
from app.domain.models import Ihale, Ilan, OkasKodu
from app.retrieval.profil_retriever import PaketVurusu


# ------------------------------------------------------------------ şema sırası
def test_genis_semada_faaliyet_ortusmesi_karardan_once():
    alanlar = list(KapsamSonucuGenis.model_fields)
    assert alanlar.index("faaliyet_ortusmesi") < alanlar.index("karar")
    # Gerekçeden HEMEN sonra: önce kanıtı tart, sonra örtüşmeyi sınıflandır.
    assert alanlar.index("gerekce") < alanlar.index("faaliyet_ortusmesi")
    assert alanlar[-1] == "karar"


def test_genis_sema_dar_semanin_alan_sirasini_koruyor():
    dar = list(KapsamSonucu.model_fields)
    genis = [a for a in KapsamSonucuGenis.model_fields if a != "faaliyet_ortusmesi"]
    assert genis == dar, "KapsamSonucuGenis, KapsamSonucu'nun alan sırasından saptı."


def test_dar_semada_yeni_alan_yok():
    """Bayrak kapalıyken eski şema BİREBİR aynı kalmalı."""
    assert "faaliyet_ortusmesi" not in KapsamSonucu.model_fields


def test_gecerli_ortusme_degerleri():
    for d in ("guclu", "kismi", "zayif", "yok"):
        s = KapsamSonucuGenis(
            gerekce="g", faaliyet_ortusmesi=d, ilgi_skoru=0.5, karar="belirsiz"
        )
        assert s.faaliyet_ortusmesi == d
    with pytest.raises(Exception):
        KapsamSonucuGenis(gerekce="g", faaliyet_ortusmesi="cok_guclu",
                          ilgi_skoru=0.5, karar="belirsiz")


# ------------------------------------------------------- negatif terim eşleşmesi
def _paket(kod: str, baslik: str, negatifler: list[str], skor: float = 0.55) -> PaketVurusu:
    return PaketVurusu(kod=kod, baslik=baslik, benzerlik=skor, oncelik="kritik",
                       negatif_terimler=negatifler)


PLN01 = _paket("PLN-01", "Ulaşım Planlama", ["öğrenci taşıma", "personel taşıma hizmeti"])


def test_metinde_gecen_negatif_yakalaniyor():
    metin = "2026-2027 Eğitim Öğretim Yılı öğrenci taşıma hizmet alım işi"
    v = negatifleri_dogrula(metin, [PLN01])
    assert [x.terim for x in v] == ["öğrenci taşıma"]
    assert v[0].paket_kodu == "PLN-01"
    assert "öğrenci taşıma" in v[0].baglam


def test_metinde_gecmeyen_negatif_uydurulmuyor():
    metin = "Sinyalize kavşaklarda trafik düzeninin sağlanması için malzeme alımı"
    assert negatifleri_dogrula(metin, [PLN01]) == []


def test_turkce_buyuk_harf_kacirilmiyor():
    """'İ' tuzağı: düz .lower() bunu kaçırır (bkz. on_filtre.turkce_ascii_kucuk)."""
    metin = "ÖĞRENCİ TAŞIMA HİZMET ALIMI İŞİ"
    v = negatifleri_dogrula(metin, [PLN01])
    assert [x.terim for x in v] == ["öğrenci taşıma"]


def test_kelime_sinirinda_eslesme():
    """'yol' terimi 'yolcu' içinde eşleşmemeli — alt dize araması listeyi bozardı."""
    p = _paket("X", "X", ["yol"])
    assert negatifleri_dogrula("yolcu taşıma araçları kiralanması", [p]) == []
    assert negatifleri_dogrula("yol çizgi boyası alımı", [p]) != []


def test_bos_girdiler():
    assert negatifleri_dogrula("", [PLN01]) == []
    assert negatifleri_dogrula("herhangi bir metin", []) == []
    assert negatifleri_dogrula("metin", [_paket("Y", "Y", [])]) == []


def test_paket_basina_ust_sinir():
    """136 terimlik liste prompt'u boğmasın."""
    terimler = [f"terim{i}" for i in range(10)]
    p = _paket("Z", "Z", terimler)
    metin = " ".join(terimler)
    assert len(negatifleri_dogrula(metin, [p])) == 4


def test_prompt_blogu_kanit_iceriyor():
    v = negatifleri_dogrula("okullara ait öğrenci taşıma hizmeti alınacaktır", [PLN01])
    blok = prompt_blogu(v)
    assert "DIŞLAMA TERİMLERİ" in blok
    assert "öğrenci taşıma" in blok
    assert "ilanda:" in blok          # metinden alıntı var
    assert "TEK BAŞINA yeterli kanıt değildir" in blok   # zorlama değil, sinyal
    assert prompt_blogu([]) == ""


def test_ozet_kosu_kaydina_uygun():
    v = negatifleri_dogrula("öğrenci taşıma işi", [PLN01])
    assert ozet(v) == "negatif_dogrulandi: öğrenci taşıma(PLN-01)"
    assert ozet([]) == ""


def test_baglam_orijinal_metinden_aliniyor():
    """Alıntı normalleştirilmiş metinden gelirse modele bozuk Türkçe gösterilir."""
    metin = "2026-2027 Eğitim Öğretim Yılı ÖĞRENCİ TAŞIMA hizmet alım işi"
    v = negatifleri_dogrula(metin, [PLN01])
    assert "ÖĞRENCİ TAŞIMA" in v[0].baglam
    assert "Eğitim Öğretim" in v[0].baglam
    assert "ogrenci" not in v[0].baglam   # ascii'ye indirgenmiş hâli SIZMAMALI


# --------------------------------------------------------- prompt entegrasyonu
def _ihale(kapsam: str, adi: str = "Öğrenci Taşıma Hizmet Alım İşi") -> Ihale:
    return Ihale(
        id="t1", ikn="2026/1", adi=adi, idare_adi="X MEM", il="MUĞLA",
        ihale_tarihi="20.08.2026 10:00", ihale_turu="Hizmet",
        ilanlar=[Ilan(id="i1", tender_id="t1", ilan_tipi="Ön İlan",
                      ilan_tarihi="2026-07-28", baslik=adi,
                      icerik=kapsam, icerik_temiz=kapsam)],
        okas_kodlari=[OkasKodu("60170000", "Yolcu taşıma")],
    )


def test_bayrak_acikken_blok_prompta_giriyor():
    ih = _ihale("İlçemizdeki okullara öğrenci taşıma hizmeti alınacaktır. 42 hat.")
    kapali = kullanici_mesaji(ih, [PLN01], min_skor=0.50)
    acik = kullanici_mesaji(ih, [PLN01], min_skor=0.50, negatif_dogrula=True)
    assert "DIŞLAMA TERİMLERİ" not in kapali
    assert "DIŞLAMA TERİMLERİ" in acik


def test_terim_gecmiyorsa_blok_eklenmiyor():
    ih = _ihale("Sinyalize kavşak kontrol cihazı ve sinyalizasyon malzemesi alımı",
                adi="Sinyalizasyon Malzemesi Alımı")
    acik = kullanici_mesaji(ih, [PLN01], min_skor=0.50, negatif_dogrula=True)
    assert "DIŞLAMA TERİMLERİ" not in acik


def test_esik_alti_paketlerin_negatifleri_kullanilmiyor():
    """Prompt'a sunulmayan paketin negatifini doğrulamak anlamsız."""
    dusuk = _paket("PLN-01", "Ulaşım Planlama", ["öğrenci taşıma"], skor=0.31)
    ih = _ihale("öğrenci taşıma hizmeti")
    acik = kullanici_mesaji(ih, [dusuk], min_skor=0.50, negatif_dogrula=True)
    assert "DIŞLAMA TERİMLERİ" not in acik


def test_varsayilanlar_eski_prompti_koruyor():
    ih = _ihale("öğrenci taşıma hizmeti alınacaktır")
    assert kullanici_mesaji(ih, [PLN01], min_skor=0.50) == kullanici_mesaji(
        ih, [PLN01], min_skor=0.50, negatif_dogrula=False,
        nitel_yakinlik=False, kanit_uyarisini_bastir=False,
    )
