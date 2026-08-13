"""
Offline test paketi — Ollama, GPU ve Postgres GEREKTİRMEZ.

Bu testler boru hattının LLM dışındaki her katmanını kapsar: temizleyici, veri
katmanı, profiller, ön-filtreler, şema alan sırası, retrieval, doğrulayıcı ve
sızıntı güvenliği. LLM'in kendisi sahte bir istemciyle taklit edilir.

    PYTHONPATH=. pytest -q tests/
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest

from app.decision.dogrulayici import dogrula
from app.decision.on_filtre import idare_isbak_mi, ihale_tarihi_gecmis_mi, on_filtrele
from app.decision.schemas import KapsamSonucu, Kriter, YeterlilikSonucu
from app.decision.stage2_yeterlilik import _kanitsiz_kriterleri_dusur
from app.domain.models import Ihale, Ilan, en_iyi_ilan, kapsam_metni
from app.embedding.embedders import FakeEmbedder
from app.profiles.loader import aile_sinyalleri, profil_getir, profilleri_yukle
from app.retrieval.profil_retriever import (
    ornekleri_indeksle,
    profilleri_indeksle,
    sorgu_metni,
)
from app.retrieval.qdrant_deposu import (
    KOLEKSIYON_ORNEK_RED,
    KOLEKSIYON_ORNEK_UYGUN,
    KOLEKSIYON_PROFIL,
    QdrantDeposu,
    nokta_id,
)
from app.text.ilan_temizleyici import ilan_metnini_temizle

# ============================================================================
# İlan temizleyici
# ============================================================================


def test_temizleyici_bos_girdide_patlamaz():
    assert ilan_metnini_temizle(None) == ""
    assert ilan_metnini_temizle("") == ""


def test_temizleyici_boilerplate_atiyor():
    ham = (
        "1- İdarenin adı: X\n2- İhalenin\n"
        "3- İhale konusu mal alımının\n3.1. Adı : AKILLI KAVŞAK SİSTEMİ\n"
        "3.2. Niteliği : 50 adet sinyal denetleyici\n"
        "4- Katılım ve yeterlik kriterleri\n4.1. İsteklilerin ihaleye katılabilmeleri için..."
    )
    temiz = ilan_metnini_temizle(ham)
    assert "AKILLI KAVŞAK" in temiz
    assert "sinyal denetleyici" in temiz
    assert "Katılım ve yeterlik" not in temiz
    assert not temiz.startswith("1- İdarenin")


def test_temizleyici_taninmayan_formatta_da_metin_dondurur():
    """Desen bulunamazsa sessizce boş dönmemeli — veri kaybetmek en kötü sonuç."""
    ham = "Tamamen beklenmedik bir format, hiçbir desen yok. " * 20
    assert len(ilan_metnini_temizle(ham)) > 0


def test_temizleyici_okuma_anindaki_cikti_kayitli_kolonla_ayni(ekap_db):
    """KRİTİK REGRESYON: 'okuma anında temizle' yaklaşımının (Postgres'te kolon
    eklemeden çalışan yol), daha önce toplu üretilip DB'ye yazılmış `icerik_temiz`
    ile aynı sonucu verdiğini doğrular. Bu eşitlik bozulursa, bugün SQLite'la
    ölçtüğümüz sonuçlar yarın Postgres'te tekrarlanmaz."""
    conn = sqlite3.connect(f"file:{ekap_db}?mode=ro", uri=True)
    satirlar = conn.execute(
        "SELECT icerik, icerik_temiz FROM tender_announcements LIMIT 2000"
    ).fetchall()
    conn.close()
    farkli = [1 for ham, kayitli in satirlar if ilan_metnini_temizle(ham) != (kayitli or "")]
    assert not farkli, f"{len(farkli)}/{len(satirlar)} satırda fark var"


# ============================================================================
# Ön filtreler
# ============================================================================


@pytest.mark.parametrize(
    "idare,beklenen",
    [
        ("İSBAK A.Ş.", True),
        ("İSBAK İSTANBUL BİLİŞİM", True),
        ("isbak a.s.", True),
        ("İSTANBUL BÜYÜKŞEHİR BELEDİYESİ", False),
        ("", False),
        (None, False),
    ],
)
def test_isbak_tespiti_turkce_i_tuzagini_atlatiyor(idare, beklenen):
    """'İSBAK'.lower() Python'da 'i̇sbak' (i + birleşen nokta) verir, düz 'isbak'
    aramasıyla EŞLEŞMEZ. Bu test o tuzağı korur."""
    assert idare_isbak_mi(idare) is beklenen


def test_isbak_ihalesi_llm_cagrilmadan_eleniyor():
    ihale = Ihale(id="1", ikn="2026/1", adi="EDS Direk Alımı", idare_adi="İSBAK A.Ş.")
    sonuc = on_filtrele(ihale)
    assert sonuc.devam is False
    assert sonuc.karar == "uygun_degil"
    assert sonuc.kural == "IDARE_ISBAK"


def test_tarih_gecmis_kontrolu():
    simdi = datetime(2026, 7, 28, 12, 0)
    assert ihale_tarihi_gecmis_mi("01.01.2026 10:00", simdi) is True
    assert ihale_tarihi_gecmis_mi("31.12.2026 10:00", simdi) is False
    # Tanınmayan biçimde ELEMEYİZ — kaçırma en pahalı hata.
    assert ihale_tarihi_gecmis_mi("bozuk tarih", simdi) is False
    assert ihale_tarihi_gecmis_mi(None, simdi) is False


def test_tekil_analizde_bitmis_ihale_elenmez():
    """Karar setindeki 35 örneğin çoğu 'Sonuç İlanı Yayımlanmış'. Tekil analizde
    durum filtresi elememelidir, yoksa regresyon setini hiç ölçemeyiz."""
    ihale = Ihale(id="1", ikn="2026/1", adi="X", idare_adi="Y",
                  ihale_durumu="Sonuç İlanı Yayımlanmış")
    assert on_filtrele(ihale, sadece_aktif=False).devam is True
    assert on_filtrele(ihale, sadece_aktif=True).devam is False


# ============================================================================
# Şema alan sırası — kanıtlanmış bir hata modunu koruyor
# ============================================================================


def test_kapsam_semasinda_karar_en_son_gerekce_ilk():
    """Ollama alanları ŞEMA SIRASIYLA üretir. 'karar' başa alınırsa model gerekçe
    üretmeden karara kilitlenir (ölçülmüş bir hata modu)."""
    alanlar = list(KapsamSonucu.model_json_schema()["properties"])
    assert alanlar[0] == "gerekce"
    assert alanlar[-1] == "karar"
    assert alanlar.index("ilgi_skoru") < alanlar.index("karar")


def test_yeterlilik_semasinda_karar_en_son():
    alanlar = list(YeterlilikSonucu.model_json_schema()["properties"])
    assert alanlar[0] == "ozet"
    assert alanlar[-1] == "karar"
    assert alanlar.index("guven") < alanlar.index("karar")


def test_kriter_semasinda_durum_en_son():
    alanlar = list(Kriter.model_json_schema()["properties"])
    assert alanlar[-1] == "durum"
    assert alanlar.index("gerekce") < alanlar.index("durum")


# ============================================================================
# Profiller
# ============================================================================


def test_yirmi_profil_yukleniyor():
    assert len(profilleri_yukle()) == 20


def test_tek01_ampirik_negatif_terimleri_korunuyor():
    """Bu terimler Faz D testlerinde gerçek yanlış eşleşmelerden çıkarıldı
    (kurumsal kaynak planlama yazılımı vakaları). Silinmemeleri gerekir."""
    tek01 = profil_getir("TEK-01")
    assert tek01 is not None
    for terim in ("ERP", "SAP", "CRM", "kurumsal kaynak planlama"):
        assert terim in tek01.negatif_terimler, f"'{terim}' TEK-01'den kaybolmuş"


def test_negatif_terimler_embed_metnine_girmiyor():
    """Negatif terimi gömmeye katmak, tam o terimin geçtiği UYGUN OLMAYAN ihalelerle
    yüksek benzerlik kurar — retrieval'ı ters yönde bozar."""
    tek01 = profil_getir("TEK-01")
    metin = tek01.embed_metni()
    assert "SAP" not in metin
    assert "ERP" not in metin


def test_aile_sinyalleri_profillerden_turuyor():
    """Yönlendirme, elle yazılmış ayrı bir kelime listesinden değil,
    profil JSON'larından besleniyor — tek doğruluk kaynağı."""
    aileler = aile_sinyalleri()
    assert set(aileler) == {"AUS", "ENT", "TEK", "PLN", "OPS"}
    assert "SAP" in aileler["TEK"]["negatif"]


# ============================================================================
# Retrieval
# ============================================================================


def test_sorgu_metni_baslik_ve_kapsami_birlestiriyor():
    assert sorgu_metni("BAŞLIK", "kapsam metni") == "BAŞLIK\nkapsam metni"
    assert sorgu_metni("BAŞLIK", None) == "BAŞLIK"
    assert sorgu_metni("BAŞLIK", "") == "BAŞLIK"


def test_nokta_id_deterministik():
    """Yeniden indeksleme kopya üretmemeli, üzerine yazmalı."""
    assert nokta_id("profil", "AUS-01") == nokta_id("profil", "AUS-01")
    assert nokta_id("profil", "AUS-01") != nokta_id("profil", "AUS-02")


def test_profil_indeksleme_ve_arama():
    e = FakeEmbedder()
    with QdrantDeposu(QdrantDeposu.BELLEK, KOLEKSIYON_PROFIL) as d:
        assert profilleri_indeksle(d, e, sifirla=True) == 20
        assert d.sayi() == 20
        vurus = d.ara(vektor=e.embed(["akıllı kavşak sinyalizasyon"])[0], limit=5)
        assert len(vurus) == 5
        assert all("kod" in v.payload for v in vurus)


def test_ayni_klasorde_uc_koleksiyon_ayni_anda_acilabiliyor(tmp_path):
    """REGRESYON: Qdrant'ın gömülü modu KLASÖR BAŞINA TEK İSTEMCİ kabul eder.

    İlk sürümde her koleksiyon kendi QdrantClient'ını açıyordu; üç koleksiyon aynı
    klasörde olduğu için ikincisi şu hatayla patlıyordu:
        "Storage folder ... is already accessed by another instance of Qdrant client"
    Bu ancak DOSYA tabanlı modda görülüyor (bellek-içi modda kilit yok), o yüzden
    bu test bilerek tmp_path kullanıyor — sadece :memory: ile test etmek hatayı kaçırır.
    """
    e = FakeEmbedder()
    yol = tmp_path / "qdrant"
    depolar = [
        QdrantDeposu(yol, KOLEKSIYON_PROFIL),
        QdrantDeposu(yol, KOLEKSIYON_ORNEK_UYGUN),
        QdrantDeposu(yol, KOLEKSIYON_ORNEK_RED),
    ]
    try:
        profilleri_indeksle(depolar[0], e, sifirla=True)
        ornekleri_indeksle(
            depolar[1], e, [{"id": "a", "baslik": "Akıllı Kavşak", "metin": "kavşak"}], sifirla=True
        )
        ornekleri_indeksle(
            depolar[2], e, [{"id": "b", "baslik": "SAP ERP Lisans", "metin": "erp"}], sifirla=True
        )
        assert depolar[0].sayi() == 20
        assert depolar[1].sayi() == 1
        assert depolar[2].sayi() == 1
        # Üçü de aynı fiziksel istemciyi paylaşmalı.
        assert depolar[0]._istemci is depolar[1]._istemci is depolar[2]._istemci
    finally:
        for d in depolar:
            d.kapat()


def test_retriever_dosya_modunda_uc_koleksiyonu_sorgulayabiliyor(tmp_path):
    """analyze_tender.py'nin gerçekte izlediği yol: tek retriever, üç koleksiyon,
    dosya tabanlı depo. Kilit hatası tam olarak burada patlamıştı."""
    from app.retrieval.profil_retriever import ProfilRetriever

    e = FakeEmbedder()
    yol = tmp_path / "qdrant"
    with QdrantDeposu(yol, KOLEKSIYON_PROFIL) as d:
        profilleri_indeksle(d, e, sifirla=True)
    with QdrantDeposu(yol, KOLEKSIYON_ORNEK_UYGUN) as d:
        ornekleri_indeksle(d, e, [{"id": "a", "baslik": "Akıllı Kavşak", "metin": "kavşak"}], sifirla=True)
    with QdrantDeposu(yol, KOLEKSIYON_ORNEK_RED) as d:
        ornekleri_indeksle(d, e, [{"id": "b", "baslik": "SAP ERP", "metin": "erp"}], sifirla=True)

    with ProfilRetriever(yol, e) as r:
        assert len(r.paketleri_bul("akıllı kavşak sinyalizasyon", k=5)) == 5
        assert len(r.uygun_ornekler("akıllı kavşak", k=3)) == 1
        assert len(r.red_ornekler("erp lisans", k=3)) == 1


def test_kapat_iki_kez_cagrilabilir(tmp_path):
    """`with` bloğu + elle kapat() üst üste gelebilir; referans sayacı bozulmamalı."""
    d = QdrantDeposu(tmp_path / "qdrant", KOLEKSIYON_PROFIL)
    d.kapat()
    d.kapat()  # patlamamalı
    # Klasör tekrar açılabilmeli (kilit gerçekten bırakılmış olmalı)
    d2 = QdrantDeposu(tmp_path / "qdrant", KOLEKSIYON_PROFIL)
    d2.kapat()


def test_embedder_imza_uyusmazligi_hata_veriyor():
    """Ollama bge-m3 ile sentence-transformers bge-m3 AYNI MODEL ama farklı
    vektörler. Karışırlarsa sonuç sessizce yanlış olur — hata vermek zorunda."""
    e = FakeEmbedder()
    with QdrantDeposu(QdrantDeposu.BELLEK, KOLEKSIYON_PROFIL) as d:
        profilleri_indeksle(d, e, sifirla=True)
        with pytest.raises(ValueError, match="uyumsuz"):
            d.hazirla(boyut=e.boyut, imza="ollama:bge-m3")


# ============================================================================
# Doğrulayıcı (Aşama 2)
# ============================================================================


def _sonuc(karar, kriterler=(), guven=0.9, eksik=()):
    return YeterlilikSonucu(
        ozet="test", kriterler=list(kriterler), riskler=[],
        eksik_kanitlar=list(eksik), guven=guven, karar=karar,
    )


def _kriter(kid, durum, kanit=(), gerekce=""):
    return Kriter(kriter_id=kid, aciklama="", gerekce=gerekce,
                  kanit_idleri=list(kanit), durum=durum)


def test_k1_kanitsiz_dogrudan_uygun_engelleniyor():
    d = dogrula(_sonuc("dogrudan_uygun"), kanit_sayisi=0)
    assert d.zorunlu_karar == "inceleme_gerekli"
    assert "K1_KANITSIZ_UYGUN" in d.uygulanan_kurallar


def test_k3_belirsiz_kriter_varken_dogrudan_uygun_olamaz():
    d = dogrula(_sonuc("dogrudan_uygun", [_kriter("sertifika", "belirsiz")]), kanit_sayisi=3)
    assert d.zorunlu_karar == "inceleme_gerekli"


def test_k5_karsilanmayan_kriter_ilgisize_sertlestiriyor():
    d = dogrula(
        _sonuc("dogrudan_uygun", [_kriter("x", "karsilanmadi"), _kriter("y", "karsilandi", ["K1"])]),
        kanit_sayisi=3,
    )
    assert d.zorunlu_karar == "ilgisiz", "K5 (ilgisiz) K3'ten daha şiddetli olmalı"


def test_k7_var_olmayan_kanit_referansi_yakalaniyor():
    d = dogrula(_sonuc("inceleme_gerekli", [_kriter("x", "karsilandi", ["K9"])]), kanit_sayisi=3)
    assert "K9" in d.gecersiz_kanit_referanslari
    assert d.gecti is False


def test_k11_dogrulanamayan_kritik_kriter_insana_gidiyor():
    """'ilgisiz' kararı bilgi eksikliğinden geliyorsa bu bir eleme değil, insan işidir."""
    d = dogrula(_sonuc("ilgisiz", [_kriter("mali_yeterlilik", "belirsiz")]), kanit_sayisi=3)
    assert d.zorunlu_karar == "inceleme_gerekli"


def test_k13_kritik_baglam_atlandiginda_tetikleniyor():
    """Önceki sürümde bu bayrak karar hattına hiç ulaşmıyordu; kural ölüydü."""
    d = dogrula(_sonuc("dogrudan_uygun", [_kriter("x", "karsilandi", ["K1"])]),
                kanit_sayisi=3, kritik_baglam_atlandi=True)
    assert "K13_KRITIK_BAGLAM_ATLANDI" in d.uygulanan_kurallar
    assert d.zorunlu_karar == "inceleme_gerekli"


def test_dogrulayici_ayni_karari_zorunlu_diye_isaretlemiyor():
    d = dogrula(_sonuc("inceleme_gerekli"), kanit_sayisi=3)
    assert d.zorunlu_karar is None


def test_s4_kanitsiz_karsilandi_kriteri_analizi_oldurmuyor():
    """Önceki sürümde bu durum TÜM analizi bir istisnayla düşürüyordu. Artık o
    kriter 'belirsiz'e düşürülüp devam ediliyor."""
    ham = _sonuc("dogrudan_uygun", [
        _kriter("a", "karsilandi", []),        # kanıtsız — düşürülmeli
        _kriter("b", "karsilandi", ["K1"]),    # sağlam
    ])
    yeni, uyarilar = _kanitsiz_kriterleri_dusur(ham)
    assert len(uyarilar) == 1
    assert yeni.kriterler[0].durum == "belirsiz"
    assert yeni.kriterler[1].durum == "karsilandi"


# ============================================================================
# Veri katmanı (yerel ekap.db gerektirir)
# ============================================================================


def test_depo_ihaleyi_temiz_metinle_getiriyor(depo):
    ihale = depo.ikn_ile_getir("2025/2196999")
    assert ihale.adi
    assert ihale.ilanlar
    ilan = en_iyi_ilan(ihale)
    assert ilan.icerik_temiz  # okuma anında üretildi
    assert len(ilan.icerik_temiz) < len(ilan.icerik)
    assert kapsam_metni(ihale)


def test_ilan_onceligi_ihale_ilanini_tercih_ediyor():
    ihale = Ihale(id="1", ikn="x", ilanlar=[
        Ilan("a", "1", "Sonuç İlanı", "2026-01-01", None, "s", "s"),
        Ilan("b", "1", "İhale İlanı", "2025-01-01", None, "i", "i"),
    ])
    assert en_iyi_ilan(ihale).ilan_tipi == "İhale İlanı"


def test_ayni_tipte_en_yeni_ilan_seciliyor():
    ihale = Ihale(id="1", ikn="x", ilanlar=[
        Ilan("a", "1", "Ön İlan", "2025-01-01", None, "eski", "eski"),
        Ilan("b", "1", "Ön İlan", "2026-06-01", None, "yeni", "yeni"),
    ])
    assert en_iyi_ilan(ihale).icerik_temiz == "yeni"


def test_aktif_ihale_sayisi_makul(depo):
    n = depo.aktif_ihale_sayisi()
    assert 1000 < n < 20000, f"beklenmedik aktif ihale sayısı: {n}"


# ============================================================================
# Sızıntı güvenliği
# ============================================================================


def test_final_satirlari_ornek_koleksiyonuna_girmiyor(depo, karar_seti):
    """Final seti kutsal: değerlendirilecek örnek, kanıt olarak modele gösterilemez."""
    from scripts.index_profiles import ornekleri_oku

    gruplar, atilan = ornekleri_oku(karar_seti, depo)
    assert atilan > 0

    import csv as _csv

    with open(karar_seti, encoding="utf-8-sig") as f:
        final_idler = {
            s["tender_id"] for s in _csv.DictReader(f, delimiter=";")
            if (s.get("set") or "").strip() == "final"
        }
    indekslenen = {o["id"] for grup in gruplar.values() for o in grup}
    assert not (indekslenen & final_idler), "FINAL SIZINTISI"


def test_belirsiz_ornekler_uygun_grubuna_karismiyor(depo, karar_seti):
    """REGRESYON — v1 koşusundaki üç hatanın da kök sebebi buydu.

    `belirsiz` etiketli örnekler `uygun` koleksiyonuna konuyor ve modele
    "DAHA ÖNCE GERÇEKTEN UYGUN BULUNMUŞ (onaylanmış örnekler)" başlığıyla
    gösteriliyordu. Model de gerekçelerinde bunları aynen "geçmişte uygun
    bulunmuştu" diye alıntıladı ve üç ihaleyi de belirsiz->uygun kaydırdı.
    Her etiket kendi grubunda kalmak ZORUNDA.
    """
    import csv as _csv

    from scripts.index_profiles import ornekleri_oku

    gruplar, _ = ornekleri_oku(karar_seti, depo)

    with open(karar_seti, encoding="utf-8-sig") as f:
        gercek = {
            s["tender_id"]: (s.get("karar") or "").strip()
            for s in _csv.DictReader(f, delimiter=";")
        }

    assert set(gruplar) == {"uygun", "belirsiz", "uygun_degil"}
    for etiket, kayitlar in gruplar.items():
        assert kayitlar, f"'{etiket}' grubu boş — beklenmedik"
        for o in kayitlar:
            assert gercek[o["id"]] == etiket, (
                f"'{o['baslik'][:40]}' gerçekte '{gercek[o['id']]}' ama '{etiket}' grubunda"
            )


def test_prompt_uc_grubu_ayri_etiketle_gosteriyor():
    """Modele gösterilen metinde üç grup da KENDİ etiketiyle görünmeli."""
    from app.decision.stage1_kapsam import kullanici_mesaji
    from app.retrieval.profil_retriever import OrnekVurusu, PaketVurusu

    ihale = Ihale(id="1", ikn="2026/1", adi="Test İhalesi", idare_adi="X Belediyesi")
    paketler = [PaketVurusu(kod="AUS-01", baslik="Trafik", benzerlik=0.7, oncelik="kritik")]
    mesaj = kullanici_mesaji(
        ihale,
        paketler,
        uygun_ornekler=[OrnekVurusu(id="a", baslik="U-ornek", benzerlik=0.8)],
        red_ornekler=[OrnekVurusu(id="c", baslik="R-ornek", benzerlik=0.5)],
        belirsiz_ornekler=[OrnekVurusu(id="b", baslik="B-ornek", benzerlik=0.6)],
    )
    assert 'ETİKET: "UYGUN"' in mesaj
    assert 'ETİKET: "BELİRSİZ"' in mesaj
    assert 'ETİKET: "UYGUN_DEGIL"' in mesaj
    # Belirsiz örnek, uygun başlığının ALTINDA değil kendi bölümünde olmalı
    assert mesaj.index("B-ornek") > mesaj.index('ETİKET: "BELİRSİZ"')
    assert mesaj.index("B-ornek") < mesaj.index('ETİKET: "UYGUN_DEGIL"')


def test_dusuk_skorda_uygun_karari_kodda_belirsize_dusuyor():
    """Skor/karar tutarlılığı KODDA garanti — model bu kuralı ısrarla çiğniyor.

    Gerçek vakalar: kurumsal yazılım lisansı (0.4846 -> uygun), video yönetim yazılımı
    "Millestone XProtect" (0.49 -> uygun). İki proje, farklı promptlar, aynı hata.
    """
    from app.decision.stage1_kapsam import skor_karar_tutarliligini_zorla

    ham = KapsamSonucu(
        gerekce="zayıf örtüşme var", eslesen_paket="Kamera", eslesen_okas=[],
        ilgi_skoru=0.49, karar="uygun",
    )
    duzeltilmis, uyarilar = skor_karar_tutarliligini_zorla(ham)
    assert duzeltilmis.karar == "belirsiz", "düşük skorlu 'uygun' düşürülmedi"
    assert len(uyarilar) == 1
    assert "0.49" in duzeltilmis.gerekce  # izlenebilirlik: ne olduğu gerekçeye yazılıyor


def test_dusuk_skorda_uygun_degile_DEGIL_belirsize_dusuyor():
    """Düşürme yönü kritik: 'uygun_degil' ihaleyi insandan koparır (kaçırma riski)."""
    from app.decision.stage1_kapsam import skor_karar_tutarliligini_zorla

    ham = KapsamSonucu(gerekce="x", eslesen_paket="y", eslesen_okas=[],
                       ilgi_skoru=0.1, karar="uygun")
    duzeltilmis, _ = skor_karar_tutarliligini_zorla(ham)
    assert duzeltilmis.karar == "belirsiz"
    assert duzeltilmis.karar != "uygun_degil"


@pytest.mark.parametrize(
    "skor,karar",
    [
        (0.5, "uygun"),        # tam eşikte — geçerli
        (0.78, "uygun"),       # gerçek uygun ihalelerin bandı
        (0.45, "belirsiz"),    # zaten belirsiz
        (0.2, "uygun_degil"),  # zaten elenmiş
        (0.9, "uygun_degil"),  # kelime tuzağı: yüksek skor + uygun_degil MEŞRU
    ],
)
def test_gecerli_skor_karar_ciftlerine_dokunulmuyor(skor, karar):
    from app.decision.stage1_kapsam import skor_karar_tutarliligini_zorla

    ham = KapsamSonucu(gerekce="x", eslesen_paket="y", eslesen_okas=[],
                       ilgi_skoru=skor, karar=karar)
    duzeltilmis, uyarilar = skor_karar_tutarliligini_zorla(ham)
    assert duzeltilmis.karar == karar
    assert not uyarilar


def test_prompt_belirsiz_kurallarini_iceriyor():
    """B1 (kanıt yetersizliği) ve B2 (kalem ağırlığı) kuralları prompt'ta olmalı."""
    from app.decision.stage1_kapsam import SISTEM_PROMPTU

    assert "ayrıntılı bilgiye idari şartnameden" in SISTEM_PROMPTU
    assert "EN FAZLA 0.5" in SISTEM_PROMPTU
    assert "KALEM AĞIRLIĞI" in SISTEM_PROMPTU


def test_prompt_ihale_turunun_kapsam_olcutu_olmadigini_soyluyor():
    """ÖLÇÜMLE EKLENDİ — 33 örneklik kaçırma setindeki 3 sert kaçırmanın ÜÇÜ DE
    aynı uydurma kuraldan geliyordu:

        "Mal alımına yönelik ihaleler, İSBAK'ın iş paketlerinin hizmet odaklı
         doğasıyla örtüşmüyor."   (TRAFİK IŞIĞI VE SİNYALİZASYON MALZEMESİ -> uygun_degil)

    Veri bunu çürütüyor: insan onaylı `uygun` ihalelerin tür dağılımı
    14 Mal / 8 Hizmet / 8 Yapım — yani en büyük kategori MAL ALIMI (%47).
    Kök sebep profil verisinde: `urunler_ve_hizmetler` alanı boş, gömülen metin
    sadece yetkinlik sayıyor, model de yokluktan bir dışlama kuralı üretiyor.

    NOT (30.07.2026): set 33 -> 30'a indi. Üç bağımsız siber güvenlik ihalesi
    (SOC/CTI, sızma testi, SIEM) yanlış alarm setine taşındı — İSBAK'ın o işi yok.
    """
    from app.decision.stage1_kapsam import SISTEM_PROMPTU

    assert "İHALE TÜRÜ BİR KAPSAM ÖLÇÜTÜ DEĞİLDİR" in SISTEM_PROMPTU
    assert "KAPSAM DIŞI YAPMAZ" in SISTEM_PROMPTU


def test_uygun_etiketli_ihaleler_her_turu_kapsiyor(karar_seti):
    """Etiketli veri, ihale türünün kapsam ölçütü OLMADIĞINI doğruluyor —
    `uygun` sınıfı Mal/Hizmet/Yapım'ın üçünü de içeriyor."""
    import csv as _csv

    with open(karar_seti, encoding="utf-8-sig") as f:
        turler = {
            (s.get("ihale_turu") or "").strip()
            for s in _csv.DictReader(f, delimiter=";")
            if (s.get("karar") or "").strip() == "uygun"
        }
    assert {"Mal", "Hizmet", "Yapım"} <= turler, (
        f"'uygun' sınıfı sadece {turler} içeriyor — prompt'taki tür kuralı gözden geçirilmeli"
    )


def test_ent02_lisans_negatif_terimleri_GERI_ALINDI():
    """ÖLÇÜMLE GERİ ALINDI — bu bir "yapılmayacaklar" testi.

    v2 koşusunda ENT-02'ye "lisans yenileme / lisans güncelleme / ilave lisans alımı"
    gibi negatif terimler eklenmişti (Millestone XProtect vakasını hedefliyordu).
    SONUÇ GERİ TEPTİ: model "lisans" kelimesini ENT-02'nin dışına, global bir dışlama
    kuralı gibi genelledi ve "YAPAY ZEKA DESTEKLİ YAZILIM GELİŞTİRME PLATFORMU LİSANSI"
    ihalesini `belirsiz` -> `uygun_degil`'e kaydırdı. Gerekçesi bunu açıkça gösteriyordu:
    "İSBAK'ın iş paketlerinde yazılım lisansları (ERP, SAP, CRM vb.) açıkça dışlanmıştır."

    Bu TEHLİKELİ yön: `uygun_degil` denen bir ihale insana hiç ulaşmaz. Terimler geri
    alındı. Tekrar eklenecekse önce ölçülmeli — bu test o yüzden burada duruyor.
    """
    ent02 = profil_getir("ENT-02")
    assert ent02 is not None
    for terim in ("lisans yenileme", "lisans güncelleme", "ilave lisans alımı", "kullanıcı lisansı"):
        assert terim not in ent02.negatif_terimler, (
            f"'{terim}' ENT-02'ye geri eklenmiş — v2'de ölçülüp geri alınmıştı, "
            f"tekrar ekleniyorsa önce ayar setiyle ölçün."
        )


# ============================================================================
# DÜŞÜNME MODU (`think`) — 29.07.2026
# ============================================================================
#
# qwen3 akıl yürütmesini `message.thinking` alanına yazar. Bu alan eklenene kadar
# OKUNMADAN ATILIYORDU: model muhtemelen zaten zincirleme düşünüyordu ama hem ne
# düşündüğünü göremiyorduk hem de modun açık/kapalı olmasının etkisini ölçemiyorduk.
# Aşağıdaki testler üç şeyi garanti eder:
#   1) Varsayılan davranış DEĞİŞMEDİ (think gönderilmez) -> v1-v5 ile kıyas bozulmaz
#   2) Ayar verildiğinde gövdeye doğru geçer
#   3) thinking yakalanır ve son_dusunce'ye konur


def _sahte_ollama(istemci, yanit_icerik, yanit_dusunce=""):
    """`_istek`i değiştirmeden gövdeyi yakalar; ağa hiç çıkılmaz."""
    yakalanan = {}

    def _sahte(httpx, govde):
        yakalanan.update(govde)
        return yanit_icerik, yanit_dusunce

    istemci._istek = _sahte  # type: ignore[method-assign]
    return yakalanan


_GECERLI_KAPSAM = json.dumps({
    "gerekce": "test", "eslesen_paket": None, "eslesen_okas": [],
    "ilgi_skoru": 0.5, "karar": "belirsiz",
}, ensure_ascii=False)


def test_dusunme_varsayilani_think_GONDERMEZ():
    """GERİYE DÖNÜK UYUMLULUK — varsayılan `None` ise anahtar hiç eklenmemeli.

    Bu bozulursa v1-v5 koşularıyla yapılan her kıyas geçersiz olur: model
    farklı bir üretim modunda çalışıyor demektir.
    """
    from app.decision.llm_client import OllamaIstemcisi

    ist = OllamaIstemcisi(model="test")
    assert ist.dusunme is None
    govde = _sahte_ollama(ist, _GECERLI_KAPSAM)
    ist.yapisal_uret(sistem="s", kullanici="k", sema=KapsamSonucu)
    assert "think" not in govde


@pytest.mark.parametrize("deger", [True, False])
def test_dusunme_ayari_govdeye_gecer(deger):
    from app.decision.llm_client import OllamaIstemcisi

    ist = OllamaIstemcisi(model="test", dusunme=deger)
    govde = _sahte_ollama(ist, _GECERLI_KAPSAM)
    ist.yapisal_uret(sistem="s", kullanici="k", sema=KapsamSonucu)
    assert govde["think"] is deger


def test_dusunce_yakalanir_ve_her_cagrida_sifirlanir():
    from app.decision.llm_client import OllamaIstemcisi

    ist = OllamaIstemcisi(model="test", dusunme=True)
    assert ist.son_dusunce == ""

    _sahte_ollama(ist, _GECERLI_KAPSAM, "önce şunu düşündüm")
    ist.yapisal_uret(sistem="s", kullanici="k", sema=KapsamSonucu)
    assert ist.son_dusunce == "önce şunu düşündüm"

    # Düşünce üretmeyen bir sonraki çağrı ESKİ düşünceyi taşımamalı — aksi halde
    # ölçümde bir ihalenin akıl yürütmesi başka bir ihaleye atfedilir.
    _sahte_ollama(ist, _GECERLI_KAPSAM, "")
    ist.yapisal_uret(sistem="s", kullanici="k", sema=KapsamSonucu)
    assert ist.son_dusunce == ""


def test_aktif_ihale_iknleri_aktif_ihalelerle_ayni_sirada(depo):
    """`aktif_ihale_iknleri` hafif yol; `aktif_ihaleler` ile AYNI kümeyi AYNI sırada vermeli.

    Triyaj koşusu "önce İKN'leri çek, örnekle, sonra sadece seçilenleri getir"
    yapıyor. İki yol ayrışırsa örneklem sessizce başka bir evrenden çekilir.
    """
    iknler = depo.aktif_ihale_iknleri()
    ihaleler = depo.aktif_ihaleler()
    assert iknler == [i.ikn for i in ihaleler]

    # limit iki yolda da aynı ön eki vermeli
    assert depo.aktif_ihale_iknleri(limit=1) == iknler[:1]


@pytest.mark.parametrize(
    "deger, beklenen",
    [("", None), ("   ", None), ("false", False), ("true", True)],
)
def test_llm_dusunme_bos_deger_none_olur(monkeypatch, deger, beklenen):
    """`.env`'de `LLM_DUSUNME=` (boş) yazmak çalışmalı — dokümante edilmiş varsayılan.

    pydantic-settings boş satırı `''` veriyor; `bool | None` bunu çeviremeyip
    ValidationError atıyordu ve `.env.example`'daki "boş bırakın" talimatı
    doğrudan çöküyordu.
    """
    from app.config.settings import Settings

    monkeypatch.setenv("LLM_DUSUNME", deger)
    assert Settings(_env_file=None).llm_dusunme is beklenen


# ============================================================================
# RETRIEVAL EŞİĞİ / PAKET KURALLARI — 29.07.2026
# ============================================================================
#
# 29.07 triyajı: 10 rastgele aktif ihalenin 6'sına `uygun` dendi (cami inşaatı,
# okul onarımı, öğrenci taşıma servisi). Kök sebep `paketleri_bul`un eşiksiz
# çalışması: benzerlik ne olursa olsun en yakın 5 paket prompt'a "İLGİLİ İŞ
# PAKETLERİ" başlığıyla yazılıyordu — yani modele o ihalenin bir iş paketiyle
# ilgili OLDUĞU söyleniyordu.
#
# esik_analizi.py (300 rastgele + 44 insan onaylı `uygun`): pozitiflerin minimumu
# 0.5149, rastgele ihalelerin medyanı 0.463. 0.47 eşiği rastgelenin %59'unu eler,
# pozitif kaybı %0.


def _paket(kod, baslik, benzerlik):
    from app.retrieval.profil_retriever import PaketVurusu

    return PaketVurusu(kod=kod, baslik=baslik, benzerlik=benzerlik, oncelik="orta")


def _ihale_kus():
    return Ihale(id="t1", ikn="2026/1", adi="Test İhalesi", idare_adi="X Belediyesi")


def test_esik_altinda_ilgili_is_paketleri_basligi_KULLANILMAZ():
    """Eşiği hiçbir paket geçmiyorsa prompt ilgililik İDDİA ETMEMELİ."""
    from app.decision.stage1_kapsam import kullanici_mesaji

    paketler = [_paket("AUS-01", "Trafik Yönetimi", 0.40), _paket("OPS-02", "Bakım", 0.39)]
    mesaj = kullanici_mesaji(_ihale_kus(), paketler, min_skor=0.47)

    assert "İŞ PAKETİ EŞLEŞMESİ YOK" in mesaj
    assert "İLGİLİ İŞ PAKETLERİ" not in mesaj
    # Paketler yine de gösterilir (şeffaflık) ama "ilgili" denmez.
    assert "Trafik Yönetimi" in mesaj
    assert "İLGİLİ OLDUKLARI ANLAMINA GELMEZ" in mesaj


def test_esigi_gecen_paket_varsa_eski_davranis():
    from app.decision.stage1_kapsam import kullanici_mesaji

    paketler = [_paket("AUS-01", "Trafik Yönetimi", 0.62), _paket("OPS-02", "Bakım", 0.39)]
    mesaj = kullanici_mesaji(_ihale_kus(), paketler, min_skor=0.47)

    assert "İLGİLİ İŞ PAKETLERİ" in mesaj
    assert "İŞ PAKETİ EŞLEŞMESİ YOK" not in mesaj


def test_min_skor_sifir_eski_davranisi_AYNEN_korur():
    """GERİYE DÖNÜK UYUMLULUK — eşiksiz prompt, önceki koşularla kıyas bozulmasın."""
    from app.decision.stage1_kapsam import kullanici_mesaji

    paketler = [_paket("AUS-01", "Trafik Yönetimi", 0.11)]
    assert "İLGİLİ İŞ PAKETLERİ" in kullanici_mesaji(_ihale_kus(), paketler, min_skor=0.0)


def test_sadece_yatay_paket_eslesirse_uyari_eklenir():
    """OPS-*/TEK-* 'nasıl' paketleridir, 'ne' değil.

    Ölçüldü: en yüksek skorlu 15 rastgele ihalenin 5'inde en yakın paket OPS-02 —
    ambulans bakımı, bina bakımı, römorkör sörveyi, laboratuvar işletmesi.
    """
    from app.decision.stage1_kapsam import kullanici_mesaji

    yatay = [_paket("OPS-02", "Bakım, Onarım ve Teknik Destek", 0.57),
             _paket("TEK-01", "Yazılım, Veri ve Sistem Bütünleştirme", 0.52)]
    mesaj = kullanici_mesaji(_ihale_kus(), yatay, min_skor=0.47, destekleyici_kurali=True)
    assert "YALNIZCA YATAY PAKET EŞLEŞMESİ" in mesaj

    # Bir ALAN paketi de eşleşiyorsa uyarı ÇIKMAMALI.
    karisik = yatay + [_paket("AUS-01", "Trafik Yönetimi", 0.60)]
    assert "YALNIZCA YATAY PAKET EŞLEŞMESİ" not in kullanici_mesaji(
        _ihale_kus(), karisik, min_skor=0.47, destekleyici_kurali=True
    )

    # Kural kapalıyken hiç eklenmemeli.
    assert "YALNIZCA YATAY PAKET EŞLEŞMESİ" not in kullanici_mesaji(
        _ihale_kus(), yatay, min_skor=0.47, destekleyici_kurali=False
    )


def test_uydurma_paket_yakalanir_ve_uygun_belirsize_duser():
    """GERÇEK VAKA: cami inşaatına `eslesen_paket="Bina İşleri"` + `uygun`.

    "Bina İşleri" 20 profilin hiçbiri değil — model paketi uydurdu.
    """
    from app.decision.stage1_kapsam import paket_uydurmasini_yakala

    paketler = [_paket("AUS-01", "Trafik Yönetimi", 0.44)]
    sonuc = KapsamSonucu(
        gerekce="g", eslesen_paket="Bina İşleri", eslesen_okas=[],
        ilgi_skoru=0.8, karar="uygun",
    )
    yeni, uyarilar = paket_uydurmasini_yakala(sonuc, paketler)

    assert yeni.karar == "belirsiz", "uydurma paketle 'uygun' ayakta kalmamalı"
    assert uyarilar and "uydurma paket" in uyarilar[0]
    # DÜŞÜRME YÖNÜ: 'uygun_degil' DEĞİL — insan incelemesinden koparmıyoruz.
    assert yeni.karar != "uygun_degil"


def test_gecerli_paket_adina_dokunulmaz():
    from app.decision.stage1_kapsam import paket_uydurmasini_yakala

    paketler = [_paket("AUS-01", "Trafik Yönetimi ve Sinyalizasyon", 0.62)]
    sonuc = KapsamSonucu(
        gerekce="g", eslesen_paket="  trafik yönetimi VE sinyalizasyon ", eslesen_okas=[],
        ilgi_skoru=0.8, karar="uygun",
    )
    yeni, uyarilar = paket_uydurmasini_yakala(sonuc, paketler)
    assert yeni.karar == "uygun" and not uyarilar, "boşluk/büyük-küçük harf farkı hata sayılmamalı"


def test_oncelik_eki_uydurma_sayilmaz():
    """REGRESYON — prompt paketi "başlık [kritik] (benzerlik: ..)" diye yazıyor.

    Model başlığı ÖNCELİK EKİYLE kopyaladığında ilk sürüm bunu "uydurma paket"
    sayıp iki DOĞRU kararı 'belirsiz'e düşürdü:
      - ayar/v5   "Akıllı Ulaşım Sistemlerinin Bakım Onarımı" (gerçek: uygun)
      - triyaj/v4 "İzmir Adnan Menderes Hv. Çevre Güvenlik Kamera Sistemi"
    """
    from app.decision.stage1_kapsam import paket_uydurmasini_yakala

    paketler = [_paket("ENT-02", "Kamera, Video Analitik ve Güvenlik Sistemleri", 0.53)]
    for yazilan in (
        "Kamera, Video Analitik ve Güvenlik Sistemleri [kritik]",
        "Kamera, Video Analitik ve Güvenlik Sistemleri [orta]",
        "Kamera, Video Analitik ve Güvenlik Sistemleri",
    ):
        sonuc = KapsamSonucu(
            gerekce="g", eslesen_paket=yazilan, eslesen_okas=[],
            ilgi_skoru=0.8, karar="uygun",
        )
        yeni, uyarilar = paket_uydurmasini_yakala(sonuc, paketler)
        assert yeni.karar == "uygun" and not uyarilar, f"'{yazilan}' uydurma sayıldı"

    # Gerçek uydurma HÂLÂ yakalanmalı.
    sonuc = KapsamSonucu(
        gerekce="g", eslesen_paket="Bina İşleri [kritik]", eslesen_okas=[],
        ilgi_skoru=0.8, karar="uygun",
    )
    yeni, uyarilar = paket_uydurmasini_yakala(sonuc, paketler)
    assert yeni.karar == "belirsiz" and uyarilar


# ============================================================================
# TRİYAJ v8 (seed 13) — YOL/KALDIRIM YANLIŞ ALARMI
# ============================================================================
#
# "İLÇEMİZ MUHTELİF CADDE VE SOKAKLARDA YOL VE KALDIRIM YAPIM İŞİ"
#   -> karar `uygun`, skor 0.5237, eşleşen paket "Ulaşım Planlama ve Talep Yönetimi"
#
# Modelin gerekçesi iki ayrı arızayı gösterdi:
#   "...bu skor 0.5'in üzerinde olduğu için 'uygun' olarak değerlendirilir."
#
#   (1) Prompt'taki "0.5 altındaysa asla uygun olamaz" kuralı TEK YÖNLÜ. Model
#       tersine çevirip karar kuralı olarak kullandı.
#   (2) Beton parke yol imalatı PLN-01 ile eşleşti. PLN-01 planlama/modelleme
#       yapar, zemin imal etmez. AUS-01'de bu vaka için negatif terimler VAR
#       ama negatifler paket başına gösterildiği için PLN-01 seçilince
#       devreye girmediler.


def test_prompt_skor_esiginin_TERSI_gecerli_degil_diyor():
    """(1) numaralı arıza. Model "skor > 0.5 -> uygun" çıkarımı yapmamalı."""
    from app.decision.stage1_kapsam import SISTEM_PROMPTU

    assert "BU KURAL TEK YÖNLÜDÜR" in SISTEM_PROMPTU
    assert "CÜMLESİNİ KURMA" in SISTEM_PROMPTU
    # Eski tek yönlü kural DA durmalı — kod tarafındaki zorlayıcının eşi.
    assert "0.5'in altındaysa karar ASLA \"uygun\" olamaz" in SISTEM_PROMPTU


def test_prompt_fiziksel_altyapi_yapimini_kapsam_disi_sayiyor():
    """(2) numaralı arızanın prompt tarafı."""
    from app.decision.stage1_kapsam import SISTEM_PROMPTU

    assert "FİZİKSEL ALTYAPI YAPIM" in SISTEM_PROMPTU
    for ornek in ("kaldırım", "bordür", "cephe", "çatı"):
        assert ornek in SISTEM_PROMPTU, f"'{ornek}' örneği prompt'tan düşmüş"
    # Paket başlığındaki kelime benzerliğine karşı açık uyarı
    assert "ALDANMA" in SISTEM_PROMPTU


def test_pln01_yol_yapim_negatifleri_var():
    """(2) numaralı arızanın profil tarafı — ikinci savunma hattı.

    PLN-01 planlama paketidir; yol/kaldırım İMALATI onun kapsamına girmez.
    """
    pln01 = profil_getir("PLN-01")
    assert pln01 is not None
    for terim in ("yol yapım işi", "kaldırım yapımı", "beton parke", "asfaltlama"):
        assert terim in pln01.negatif_terimler, (
            f"'{terim}' PLN-01'den düşmüş — triyaj/v8'deki yol yapım yanlış alarmı geri gelir"
        )


def test_prompt_ilgi_skorunun_retrievaldan_AYRI_oldugunu_soyluyor():
    """ÖLÇÜMLE EKLENDİ (triyaj, seed 13, iki koşu arası).

    Prompt'a "sana verilen benzerlik sayıları ... İSBAK'ın işi olup olmadığını
    ölçmez" cümlesi eklenince model bunu KENDİ `ilgi_skoru` alanına uyguladı ve
    retrieval skorunu oraya kopyaladı:

        7 GRUP 8 KALEM BİLGİSAYAR VE BİLGİSAYAR MALZEMESİ ALIMI
          önce : uygun_degil, ilgi_skoru 0.0      (modelin kendi yargısı)
          sonra: uygun_degil, ilgi_skoru 0.5349   (retrieval skorunun kopyası)

    Aynı ihale, aynı karar, skor uçtu. Bu önemli çünkü BELIRSIZ_NETLESTIRME_ESIGI
    tam o alanı okuyor — alan yankıya dönerse eşik ölçtüğümüz şeyi ölçmez.
    """
    from app.decision.stage1_kapsam import SISTEM_PROMPTU

    assert "SENİN KENDİ YARGINDIR" in SISTEM_PROMPTU
    assert "KOPYALAMA" in SISTEM_PROMPTU
    # "uygun_degil + yüksek skor" çelişkisi açıkça yasaklanmalı
    assert "kendi kendiyle çelişir" in SISTEM_PROMPTU
    # Ama tek yönlü eşik kuralı ve tersinin yasağı DA durmalı
    assert "TERSİ GEÇERLİ DEĞİLDİR" in SISTEM_PROMPTU


def test_b1_vakalari_netlestirme_esigiyle_supurulebiliyor_BILINEN_SORUN():
    """BİLİNEN ETKİLEŞİM — henüz çözülmedi, bu test durumu KAYIT ALTINA ALIR.

    B1 kuralı: kanıt yetersizse karar `belirsiz` ZORUNLU ve ilgi_skoru EN FAZLA 0.5.
    `BELIRSIZ_NETLESTIRME_ESIGI=0.50` ise skor < 0.50 olan her `belirsiz`i
    `uygun_degil`e çevirir.

    Sonuç: "bilmiyorum, insana sor" sinyali sessizce "reddet"e dönüşüyor. Aktif
    ihalelerin 4.867/4.872'si "Ön İlan"dan okunuyor ve Ön İlan tipik olarak kalem
    listesi vermiyor — yani B1 sık tetiklenmeli.

    ÇÖZÜM ÖNERİSİ: şemaya `belirsiz_tipi` alanı (kanit_yetersiz | zayif_ortusme).
    Netleştirme yalnızca `zayif_ortusme`yi süpürsün. Ölçülmeden uygulanmamalı.
    """
    from app.decision.stage1_kapsam import belirsizi_netlestir

    b1 = KapsamSonucu(
        gerekce="Kalemler sayılmamış, ayrıntı şartnamede — B1 gereği belirsiz.",
        eslesen_paket="Yazılım, Veri ve Sistem Bütünleştirme", eslesen_okas=[],
        ilgi_skoru=0.45, karar="belirsiz",
    )
    yeni, uyarilar = belirsizi_netlestir(b1, 0.50)
    assert yeni.karar == "uygun_degil", "davranış değişti — testin gerekçesi güncellenmeli"
    assert uyarilar, "en azından uyarı bırakmalı ki izlenebilir olsun"


# ============================================================================
# belirsiz_tipi — B1 × netleştirme çakışmasının çözümü (30.07.2026)
# ============================================================================
#
# SORUN: B1 kuralı "kanıt yetersizse karar `belirsiz` ZORUNLU ve ilgi_skoru EN
# FAZLA 0.5" diyor. `BELIRSIZ_NETLESTIRME_ESIGI=0.50` ise `belirsiz` + skor<0.50
# olan her kararı `uygun_degil`e çeviriyordu. Yani B1'in ürettiği HER karar
# eşiğin tam menziline düşüyor, "bilmiyorum, insana sor" sinyali sessizce
# "reddet"e dönüşüyordu.
#
# GERÇEK VAKA: "Dijital İnsan Kaynakları Yönetim Sistemi Hizmet Alımı"
# (2026/1121504, BKM). İlan yalnızca "1 Adet 13 Modülden oluşan … Ayrıntılı
# bilgiye idari şartnameden ulaşılabilir" diyor. Modüllerin ne olduğu bilinmiyor.
# Model kararsız DEĞİL — elinde veri yok. Bu ihale insana gitmeliydi.
#
# ÖLÇEK: aktif ihalelerin %99'u "Ön İlan"dan okunuyor ve Ön İlan kalem listesi
# vermiyor. Tek bir ihalenin sorunu değil, kuyruğun tamamının.


def test_belirsiz_tipi_semada_karardan_ONCE():
    """'Kanıtım yeterli mi' sorusu 'hüküm ne' sorusundan ÖNCE sorulmalı.

    Ollama alanları şema sırasıyla üretiyor. `karar` yine EN SON — o koruyucu
    kural (test_kapsam_semasinda_karar_en_son_gerekce_ilk) bozulmadı.
    """
    alanlar = list(KapsamSonucu.model_json_schema()["properties"])
    assert alanlar[0] == "gerekce"
    assert alanlar[-1] == "karar"
    assert alanlar.index("belirsiz_tipi") < alanlar.index("karar")
    assert alanlar.index("ilgi_skoru") < alanlar.index("belirsiz_tipi")


def _belirsiz(tip, skor=0.45, karar="belirsiz"):
    return KapsamSonucu(
        gerekce="g", eslesen_paket="X", eslesen_okas=[],
        ilgi_skoru=skor, belirsiz_tipi=tip, karar=karar,
    )


def test_kanit_yetersiz_ASLA_netlestirilmez():
    """En kritik davranış: kanıt yokluğu reddetme sebebi DEĞİLDİR."""
    from app.decision.stage1_kapsam import belirsizi_netlestir

    yeni, uyarilar = belirsizi_netlestir(_belirsiz("kanit_yetersiz"), 0.50)
    assert yeni.karar == "belirsiz", "kanıt yetersizliği sessizce 'uygun_degil'e çevrildi"
    assert uyarilar, "kural devreye girmediyse bile iz bırakmalı"
    assert "kanit_yetersiz" in uyarilar[0]


def test_zayif_ortusme_netlestirilmeye_DEVAM_ediyor():
    """Ölçüldü: bu çevirmenin 13/13'ü doğruydu. Davranış korunmalı."""
    from app.decision.stage1_kapsam import belirsizi_netlestir

    yeni, uyarilar = belirsizi_netlestir(_belirsiz("zayif_ortusme"), 0.50)
    assert yeni.karar == "uygun_degil"
    assert uyarilar


def test_belirsiz_tipi_bossa_ESKI_DAVRANIS_korunur():
    """GERİYE DÖNÜK UYUM: model alanı doldurmadıysa netleştirme eskisi gibi çalışır.

    Yalnızca AÇIK `kanit_yetersiz` sinyali kuralı durdurur; sessiz bir davranış
    değişikliği olmaz.
    """
    from app.decision.stage1_kapsam import belirsizi_netlestir

    assert belirsizi_netlestir(_belirsiz(None), 0.50)[0].karar == "uygun_degil"


def test_netlestirme_esik_ustune_ve_diger_kararlara_dokunmaz():
    from app.decision.stage1_kapsam import belirsizi_netlestir

    # eşiğin üstünde: dokunulmaz
    assert belirsizi_netlestir(_belirsiz("zayif_ortusme", 0.55), 0.50)[0].karar == "belirsiz"
    # karar 'belirsiz' değilse: dokunulmaz
    assert belirsizi_netlestir(_belirsiz("kanit_yetersiz", 0.45, "uygun"), 0.50)[0].karar == "uygun"
    # eşik kapalıysa (0.0): dokunulmaz
    assert belirsizi_netlestir(_belirsiz("zayif_ortusme"), 0.0)[0].karar == "belirsiz"


def test_prompt_b1_kuralini_belirsiz_tipine_bagliyor():
    from app.decision.stage1_kapsam import SISTEM_PROMPTU

    assert "kanit_yetersiz" in SISTEM_PROMPTU
    assert "zayif_ortusme" in SISTEM_PROMPTU
    # B1'in "modülden oluşan" kalıbı da eklendi (İK sistemi vakası)
    assert "modülden oluşan" in SISTEM_PROMPTU


# ============================================================================
# B1 tespiti KODA ALINDI — kalem listesi var mı? (30.07.2026)
# ============================================================================
#
# B1 prompt'ta yazılıydı ama modelin deseni METİNDE KENDİ FARK ETMESİNE bağlıydı
# ve fark etmiyordu. Gerçek vaka: "Dijital İnsan Kaynakları Yönetim Sistemi"
# (2026/1121504) — "1 Adet 13 Modülden oluşan ... ayrıntı şartnamede". Model B1'i
# uygulamadı, başlıktan çıkarım yapıp `uygun_degil` dedi. Oysa en yakın paket
# ENT-04 (PDKS) 0.5541 ile eşiği geçmişti ve 13 modülün içinde personel devam
# takibi olması muhtemeldi.
#
# Tespit ÜÇ şeyi eler, geriye anlamlı bilgi kalıyor mu diye bakar:
#   1. "Ayrıntılı bilgiye ... şartnameden ulaşılabilir" havalesi
#   2. İHALE BAŞLIĞI — `Niteliği` alanı çoğu ilanda başlığı aynen tekrarlıyor
#   3. Sayaç kalıpları ("4 Kalem", "13 Modülden oluşan")


def _nitelik(icerik: str) -> str:
    return f"3- İhale konusu\n\n| **3.2.** Niteliği, türü ve miktarı | : | {icerik} |\n"


def test_kalem_listesi_yok_gercek_vakalar():
    """Modülleri/kalemleri saymayan ilan yakalanmalı."""
    from app.decision.stage1_kapsam import kalem_listesi_yok_mu

    # İK sistemi — 2026/1121504'ün gerçek metni
    ik = _nitelik(
        "1 Adet 13 Modülden oluşan İnsan Kaynakları Yönetim Sistemi Hizmet Alımı  "
        "Ayrıntılı bilgiye EKAP'ta yer alan ihale dokümanı içinde bulunan idari "
        "şartnameden ulaşılabilir."
    )
    assert kalem_listesi_yok_mu(ik, "Dijital İnsan Kaynakları Yönetim Sistemi Hizmet Alımı")


def test_baslik_tekrari_icerik_sanilmiyor():
    """REGRESYON: `Niteliği` alanı başlığı AYNEN tekrarlayınca 'içerik var'
    sanılıyordu. İBB Mikroservis ihalesi (2026/948244) bu yüzden kaçmıştı."""
    from app.decision.stage1_kapsam import kalem_listesi_yok_mu

    adi = "İBB Mikroservis ve Modüler Uygulama Altyapısı Bakım, Güncelleme ve Geliştirme Hizmeti İşi"
    metin = _nitelik(f"4 Kalem - {adi}  Ayrıntılı bilgiye EKAP'ta yer alan ihale "
                     f"dokümanı içinde bulunan idari şartnameden ulaşılabilir.")
    assert kalem_listesi_yok_mu(metin, adi), "başlık tekrarı içerik sayıldı"


def test_gercek_kalem_listesi_yakalanMAZ():
    """Kalemler sayılıyorsa uyarı VERİLMEMELİ — yanlış tetiklenen bir 'kanıt yok'
    uyarısı modeli gereksiz yere `belirsiz`e iter."""
    from app.decision.stage1_kapsam import kalem_listesi_yok_mu

    # 2026/1103261'in gerçek metni — kalemler tek tek sayılmış
    esp = _nitelik(
        "7 GRUP 8 KALEM BİLGİSAYAR VE BİLGİSAYAR MALZEMESİ ALIMI ( ESP32-WROOM-32E IoT "
        "GELİŞTİRME EKRAN KARTI 50 ADET, PFGA BOARD 10 ADET, RASPERRY PI 20 ADET, "
        "BİLGİSAYAR 50 ADET, BİLGİSAYAR KASASI ONBOARD VE MONİTÖR 100 ADET, "
        "BİLGİSAYAR KASASI EKRAN KARTLI 50 ADET VE TÜMLEŞİK BİLGİSAYAR 45 ADET)"
    )
    assert not kalem_listesi_yok_mu(esp, "7 GRUP 8 KALEM BİLGİSAYAR VE BİLGİSAYAR MALZEMESİ ALIMI")

    led = _nitelik(
        "12 Kalem Led Aydınlatma Hammadde Alımı Yapılacaktır Alüminyum Enjeksiyon Kasa "
        "Takımları Led Modül Kartları Sabit Akım Sürücüleri Led Driverlar"
    )
    assert not kalem_listesi_yok_mu(led, "Aydınlatma Malzemeleri Alımı")


def test_kalem_tespiti_belirsizlikte_sinyal_uretmez():
    """Desen bulunamazsa False — yanlış pozitif, doğru negatiften pahalı."""
    from app.decision.stage1_kapsam import kalem_listesi_yok_mu

    assert not kalem_listesi_yok_mu("", "X")
    assert not kalem_listesi_yok_mu(None, "X")
    assert not kalem_listesi_yok_mu("Hiç tanınmayan bir format", "X")


def test_kanit_uyarisi_SADECE_esigi_gecen_paket_varken_verilir():
    """Eşiği geçen paket yoksa eksik kalem listesi kararı DEĞİŞTİRMEZ.

    Akaryakıt alımında kalemleri bilmemek sonucu etkilemez. Örtüşme ihtimali
    varken etkiler — İK sisteminin 13 modülünün içinde PDKS olabilir.
    ÖLÇÜLDÜ: uyarı 300 rastgele ihalenin %11'inde tetikleniyor.
    """
    from app.decision.stage1_kapsam import kullanici_mesaji
    import app.decision.stage1_kapsam as S

    adi = "Dijital İnsan Kaynakları Yönetim Sistemi Hizmet Alımı"
    metin = _nitelik("1 Adet 13 Modülden oluşan İnsan Kaynakları Yönetim Sistemi Hizmet Alımı  "
                     "Ayrıntılı bilgiye idari şartnameden ulaşılabilir.")
    ihale = Ihale(id="t", ikn="2026/1", adi=adi, idare_adi="BKM")

    orijinal = S.kapsam_metni
    S.kapsam_metni = lambda _: metin
    try:
        gecen = kullanici_mesaji(ihale, [_paket("ENT-04", "Geçiş Kontrolü", 0.5541)], min_skor=0.50)
        gecmeyen = kullanici_mesaji(ihale, [_paket("ENT-04", "Geçiş Kontrolü", 0.44)], min_skor=0.50)
    finally:
        S.kapsam_metni = orijinal

    assert "KALEM/MODÜL LİSTESİ YOK" in gecen
    assert "kanit_yetersiz" in gecen
    assert "KALEM/MODÜL LİSTESİ YOK" not in gecmeyen

    # İstisna maddesi durmalı — yoksa fiziksel altyapı düzeltmesi geri teper
    assert "FİZİKSEL ALTYAPI YAPIM İŞLERİ" in gecen
