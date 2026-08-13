"""
Uçtan uca boru hattı testleri — SAHTE LLM ile.

Amaç: iki aşamalı akışın kendisini (geçiş kapısı, doğrulayıcı entegrasyonu, ikinci
görüş, kaçırma koruması, hata yolları) Ollama olmadan doğrulamak. Model kalitesi
burada ölçülmez — o evaluation/evaluate.py'nin işi. Burada ölçülen şey MİMARİ.
"""

from __future__ import annotations

import pytest

from app.decision.llm_client import LlmHatasi
from app.decision.schemas import (
    KapsamSonucu,
    KapsamSonucuGenis,
    Kriter,
    YeterlilikSonucu,
)
from app.embedding.embedders import FakeEmbedder
from app.pipeline.servis import AnalizAyarlari, AnalizServisi
from app.retrieval.profil_retriever import (
    ProfilRetriever,
    ornekleri_indeksle,
    profilleri_indeksle,
)
from app.retrieval.qdrant_deposu import (
    KOLEKSIYON_ORNEK_BELIRSIZ,
    KOLEKSIYON_ORNEK_RED,
    KOLEKSIYON_ORNEK_UYGUN,
    KOLEKSIYON_PROFIL,
    QdrantDeposu,
)


class SahteIstemci:
    """OllamaIstemcisi arayüzünü taklit eder; şemaya göre önceden belirlenmiş
    yanıtı döndürür ya da istenirse hata fırlatır."""

    def __init__(self, model="sahte:1b", kapsam=None, yeterlilik=None, hata=None):
        self.model = model
        self._kapsam = kapsam
        self._yeterlilik = yeterlilik
        self._hata = hata
        self.cagri_sayisi = 0

    def yapisal_uret(self, *, sistem, kullanici, sema):
        self.cagri_sayisi += 1
        self.son_kullanici_mesaji = kullanici      # prompt denetimi için
        self.son_sema = sema
        if self._hata:
            raise LlmHatasi(self._hata)
        # `FAALIYET_ORTUSMESI=true` iken şema KapsamSonucuGenis olur. Bu dal
        # eklenmezse bayrak açıkken tüm uçtan uca testler AssertionError verir —
        # yani bayrağın açık hali hiç test edilmemiş olurdu.
        if sema in (KapsamSonucu, KapsamSonucuGenis):
            return self._kapsam
        if sema is YeterlilikSonucu:
            return self._yeterlilik
        raise AssertionError(f"beklenmedik şema: {sema}")


def _kapsam(karar="uygun", skor=0.9):
    return KapsamSonucu(
        gerekce="test gerekçesi", eslesen_paket="Trafik Yönetimi ve Sinyalizasyon",
        eslesen_okas=[], ilgi_skoru=skor, karar=karar,
    )


def _yeterlilik(karar="inceleme_gerekli", guven=0.8, kriterler=()):
    return YeterlilikSonucu(
        ozet="test özeti", kriterler=list(kriterler), riskler=[],
        eksik_kanitlar=[], guven=guven, karar=karar,
    )


@pytest.fixture(scope="module")
def retriever():
    """Bellek-içi Qdrant üzerinde gerçek ProfilRetriever — sahte olan sadece LLM.

    Kontrastif örnek koleksiyonları da dolduruluyor: kanıt havuzu BOŞ olursa
    doğrulayıcı K1 ('kanıtsız dogrudan_uygun olamaz') her seferinde tetiklenir ve
    ikinci görüş mantığı hiç test edilemez.
    """
    r = ProfilRetriever(QdrantDeposu.BELLEK, FakeEmbedder())
    profilleri_indeksle(r._depo(KOLEKSIYON_PROFIL), r.embedder, sifirla=True)
    ornekleri_indeksle(
        r._depo(KOLEKSIYON_ORNEK_UYGUN),
        r.embedder,
        [
            {"id": "ornek-1", "baslik": "Akıllı Kavşak Sinyalizasyon Sistemi Kurulumu", "metin": "kavşak sinyal denetleyici"},
            {"id": "ornek-2", "baslik": "Trafik Yönetim Merkezi Yazılımı", "metin": "trafik yönetim merkezi"},
        ],
        sifirla=True,
    )
    ornekleri_indeksle(
        r._depo(KOLEKSIYON_ORNEK_BELIRSIZ),
        r.embedder,
        [{"id": "bel-1", "baslik": "Akıllı Şehir Yönetim Platformu Hizmet Alımı", "metin": "9 kalem platform"}],
        sifirla=True,
    )
    ornekleri_indeksle(
        r._depo(KOLEKSIYON_ORNEK_RED),
        r.embedder,
        [{"id": "red-1", "baslik": "SAP ERP Kullanıcı Lisansı Destek Hizmeti", "metin": "erp lisans"}],
        sifirla=True,
    )
    yield r
    r.kapat()


def test_uc_ornek_grubu_da_sonuca_yansiyor(depo, retriever):
    """Üç grup da ayrı ayrı toplanıp çıktıda ayrı alanlarda raporlanmalı —
    böylece hangi kanıtın hangi etiketle gösterildiği sonradan denetlenebilir."""
    ist = SahteIstemci(kapsam=_kapsam("uygun"))
    s = _servis(depo, retriever, ist, asama2_calissin=False).analiz_et(ikn="2025/2196999")

    assert s.benzer_uygun_ornekler, "uygun örnekler toplanmadı"
    assert s.benzer_belirsiz_ornekler, "belirsiz örnekler toplanmadı"
    assert s.benzer_red_ornekler, "red örnekler toplanmadı"
    # Gruplar birbirine karışmamalı
    uygun_idler = {o["id"] for o in s.benzer_uygun_ornekler}
    belirsiz_idler = {o["id"] for o in s.benzer_belirsiz_ornekler}
    assert not (uygun_idler & belirsiz_idler)


def _servis(depo, retriever, istemci, ikincil=None, **ayar):
    return AnalizServisi(
        depo=depo, retriever=retriever, birincil_istemci=istemci,
        ikincil_istemci=ikincil, ayarlar=AnalizAyarlari(**ayar),
    )


# ============================================================================
# BAYRAKLAR AÇIKKEN UÇTAN UCA
# ============================================================================
# 06.08.2026'da fark edildi: tüm uçtan uca testler bayraklar KAPALI çalışıyordu.
# Yeni bayraklar (okas_vetosu, nitel_yakinlik, negatif_dogrula, faaliyet_ortusmesi)
# birim testlerle tek tek doğrulanmıştı ama BORU HATTININ TAMAMI hiçbir zaman
# açık bayrakla koşturulmamıştı — üretimde ilk kez orada çalışıyorlardı.
#
# Somut kanıt: `SahteIstemci.yapisal_uret` yalnızca `KapsamSonucu`'nu tanıyordu.
# `faaliyet_ortusmesi=True` şemayı `KapsamSonucuGenis`e çevirdiği için o dal
# AssertionError veriyordu ve bu hiçbir testte görünmüyordu.

TUM_BAYRAKLAR = dict(
    okas_vetosu=True,
    nitel_yakinlik=True,
    negatif_dogrula=True,
    faaliyet_ortusmesi=True,
    retrieval_min_skor=0.30,
    asama2_calissin=False,
)


def _genis_kapsam(karar="uygun", skor=0.7, ortusme="guclu"):
    return KapsamSonucuGenis(
        gerekce="test gerekçesi", faaliyet_ortusmesi=ortusme,
        eslesen_paket=None, eslesen_okas=[], ilgi_skoru=skor,
        belirsiz_tipi=None, karar=karar,
    )


def test_tum_bayraklar_acikken_boru_hatti_calisiyor(depo, retriever):
    ist = SahteIstemci(kapsam=_genis_kapsam())
    s = _servis(depo, retriever, ist, **TUM_BAYRAKLAR).analiz_et(ikn="2025/2196999")

    assert s.kapsam is not None, "Karar üretilemedi"
    assert s.kapsam.karar == "uygun"
    assert ist.cagri_sayisi == 1


def test_faaliyet_ortusmesi_acikken_genis_sema_gonderiliyor(depo, retriever):
    ist = SahteIstemci(kapsam=_genis_kapsam())
    _servis(depo, retriever, ist, **TUM_BAYRAKLAR).analiz_et(ikn="2025/2196999")
    assert ist.son_sema is KapsamSonucuGenis


def test_faaliyet_ortusmesi_kapaliyken_dar_sema_gonderiliyor(depo, retriever):
    ayar = {**TUM_BAYRAKLAR, "faaliyet_ortusmesi": False}
    ist = SahteIstemci(kapsam=_kapsam("uygun"))
    _servis(depo, retriever, ist, **ayar).analiz_et(ikn="2025/2196999")
    assert ist.son_sema is KapsamSonucu


def test_faaliyet_ortusmesi_kosu_kaydina_yaziliyor(depo, retriever):
    ist = SahteIstemci(kapsam=_genis_kapsam(ortusme="kismi"))
    s = _servis(depo, retriever, ist, **TUM_BAYRAKLAR).analiz_et(ikn="2025/2196999")
    assert any("faaliyet_ortusmesi=kismi" in n for n in s.notlar)


def test_ham_ilgi_skoru_her_zaman_kaydediliyor(depo, retriever):
    """Kod düzeltmeden ÖNCEKİ skor; eşik ayarı LLM'siz yapılabilsin diye."""
    ist = SahteIstemci(kapsam=_genis_kapsam(skor=0.83))
    s = _servis(depo, retriever, ist, **TUM_BAYRAKLAR).analiz_et(ikn="2025/2196999")
    assert any("ham_ilgi_skoru=0.83" in n for n in s.notlar)


def test_nitel_yakinlik_acikken_ham_skor_prompta_sizmiyor(depo, retriever):
    """Yankının kaynağı sayının kendisi — prompt'ta 4 haneli skor görünmemeli."""
    import re

    ist = SahteIstemci(kapsam=_genis_kapsam())
    _servis(depo, retriever, ist, **TUM_BAYRAKLAR).analiz_et(ikn="2025/2196999")
    assert not re.search(r"benzerlik: 0\.\d{3,}", ist.son_kullanici_mesaji)
    assert "yakınlık:" in ist.son_kullanici_mesaji


def test_bayraklar_kapaliyken_eski_davranis(depo, retriever):
    """Hepsi kapalıyken prompt eski hâlinde: ham skor görünür, nitel etiket yok."""
    ist = SahteIstemci(kapsam=_kapsam("uygun"))
    _servis(depo, retriever, ist, asama2_calissin=False,
            retrieval_min_skor=0.30).analiz_et(ikn="2025/2196999")
    assert "benzerlik:" in ist.son_kullanici_mesaji
    assert "yakınlık:" not in ist.son_kullanici_mesaji


# ============================================================================


def test_kapsam_disi_ihalede_asama2_hic_calismiyor(depo, retriever):
    """Maliyet tasarrufunun kanıtı: 'uygun_degil' kararında ikinci LLM çağrısı yok."""
    ist = SahteIstemci(kapsam=_kapsam("uygun_degil", 0.1))
    s = _servis(depo, retriever, ist).analiz_et(ikn="2025/2196999")

    assert s.kapsam.karar == "uygun_degil"
    assert s.yeterlilik is None
    assert ist.cagri_sayisi == 1, "Aşama 2 çağrılmamalıydı"
    assert "asama2_atlandi" in " ".join(s.notlar)


def test_uygun_ihalede_iki_asama_da_calisiyor(depo, retriever):
    ist = SahteIstemci(
        kapsam=_kapsam("uygun"),
        yeterlilik=_yeterlilik("inceleme_gerekli", 0.8,
                              [Kriter(kriter_id="sertifika", gerekce="kayıt yok", durum="belirsiz")]),
    )
    s = _servis(depo, retriever, ist).analiz_et(ikn="2025/2196999")

    assert s.kapsam.karar == "uygun"
    assert s.yeterlilik is not None
    assert ist.cagri_sayisi == 2
    assert s.insan_incelemesi_gerekli is True
    assert s.dogrulama is not None


def test_asama1_uygun_iken_asama2_ilgisiz_diyemez(depo, retriever):
    """KAÇIRMA KORUMASI — mimarinin en önemli güvenlik kuralı.
    Aşama 1 'bu bizim alanımız' demişse, Aşama 2 ihaleyi sessizce eleyemez;
    en fazla insana götürür."""
    ist = SahteIstemci(
        kapsam=_kapsam("uygun", 0.95),
        yeterlilik=_yeterlilik("ilgisiz", 0.9),
    )
    s = _servis(depo, retriever, ist).analiz_et(ikn="2025/2196999")

    assert s.yeterlilik.karar == "inceleme_gerekli", "kaçırma koruması devreye girmedi"
    assert s.insan_incelemesi_gerekli is True
    assert any("kaçırma riski" in r for r in s.sebepler)


def test_isbak_kendi_ihalesinde_model_hic_cagrilmiyor(depo, retriever):
    from app.domain.models import Ihale

    ist = SahteIstemci(kapsam=_kapsam("uygun"))
    ihale = Ihale(id="x", ikn="2026/9", adi="EDS Direk Alımı", idare_adi="İSBAK A.Ş.")
    s = _servis(depo, retriever, ist).ihaleyi_analiz_et(ihale)

    assert ist.cagri_sayisi == 0, "deterministik kural LLM'e sorulmamalı"
    assert s.kapsam.karar == "uygun_degil"
    assert s.on_filtre_kurali == "IDARE_ISBAK"


def test_asama1_llm_hatasi_belirsize_dusuyor_uygun_degile_degil(depo, retriever):
    """Model çöktüğünde ihaleyi ELEMEK en pahalı hata olurdu. 'belirsiz' + insan."""
    ist = SahteIstemci(hata="model şemaya uymayan çıktı verdi")
    s = _servis(depo, retriever, ist).analiz_et(ikn="2025/2196999")

    assert s.kapsam.karar == "belirsiz"
    assert s.insan_incelemesi_gerekli is True
    assert "asama1_llm_hatasi" in s.notlar


def test_modeller_celisince_insana_gidiyor(depo, retriever):
    birincil = SahteIstemci("birincil", kapsam=_kapsam("uygun"),
                            yeterlilik=_yeterlilik("dogrudan_uygun", 0.5,
                                                   [Kriter(kriter_id="x", gerekce="ok",
                                                           kanit_idleri=["K1"], durum="karsilandi")]))
    ikincil = SahteIstemci("ikincil", yeterlilik=_yeterlilik("ilgisiz", 0.9))
    s = _servis(depo, retriever, birincil, ikincil).analiz_et(ikn="2025/2196999")

    assert ikincil.cagri_sayisi == 1, "düşük güvende ikinci görüş çağrılmalıydı"
    assert s.insan_incelemesi_gerekli is True
    assert s.ikincil_gorus is not None


def test_yuksek_guven_ve_temiz_dogrulamada_ikinci_gorus_cagrilmiyor(depo, retriever):
    birincil = SahteIstemci("birincil", kapsam=_kapsam("uygun"),
                            yeterlilik=_yeterlilik("dogrudan_uygun", 0.95,
                                                   [Kriter(kriter_id="x", gerekce="ok",
                                                           kanit_idleri=["K1"], durum="karsilandi")]))
    ikincil = SahteIstemci("ikincil", yeterlilik=_yeterlilik("dogrudan_uygun", 0.9))
    _servis(depo, retriever, birincil, ikincil).analiz_et(ikn="2025/2196999")

    assert ikincil.cagri_sayisi == 0, "gereksiz yere ikinci model çağrıldı (maliyet)"


def test_asama2_kapali_kipinde_sadece_kapsam_uretiliyor(depo, retriever):
    """evaluate.py bu kipi kullanıyor — 35 örneklik kapsam setiyle kıyas için."""
    ist = SahteIstemci(kapsam=_kapsam("uygun"))
    s = _servis(depo, retriever, ist, asama2_calissin=False).analiz_et(ikn="2025/2196999")

    assert s.kapsam is not None
    assert s.yeterlilik is None
    assert ist.cagri_sayisi == 1


def test_sonuc_json_serilestirilebiliyor(depo, retriever):
    """Rapor/servis katmanı için: tüm çıktı JSON'a dönebilmeli."""
    import json

    ist = SahteIstemci(kapsam=_kapsam("uygun"), yeterlilik=_yeterlilik())
    s = _servis(depo, retriever, ist).analiz_et(ikn="2025/2196999")
    metin = json.dumps(s.model_dump(), ensure_ascii=False)
    assert '"ikn"' in metin and '"kapsam"' in metin
