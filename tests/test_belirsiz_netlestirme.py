"""
`belirsiz` -> `uygun_degil` netleştirme kuralı + koleksiyon tutarlılık doğrulaması.

İkisi de 30.07.2026'da, aynı oturumda ortaya çıkan iki soruna karşılık geldi:
  1) Model 'uygun_degil' demekten kaçınıp 'belirsiz'e sığınıyordu (60 etiketli
     ihalede gerçek 'uygun_degil'lerin %59'u 'belirsiz'e düşüyordu).
  2) Qdrant koleksiyonları sessizce bozulmuştu — aynı ihale iki koleksiyonda birden,
     çelişkili etiketlerle duruyordu ve o günkü dört ölçümü de etkilemişti.
"""

from __future__ import annotations

import csv

import pytest

from app.decision.schemas import KapsamSonucu
from app.decision.stage1_kapsam import belirsizi_netlestir
from app.embedding.embedders import FakeEmbedder
from app.retrieval.profil_retriever import (
    koleksiyonlari_dogrula,
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


def _sonuc(karar, skor):
    return KapsamSonucu(
        gerekce="test", eslesen_paket="X", eslesen_okas=[], ilgi_skoru=skor, karar=karar
    )


# ============================================================================
# Netleştirme kuralı
# ============================================================================


def test_dusuk_skorlu_belirsiz_uygun_degile_netlesiyor():
    yeni, uyarilar = belirsizi_netlestir(_sonuc("belirsiz", 0.42), esik=0.48)
    assert yeni.karar == "uygun_degil"
    assert len(uyarilar) == 1
    assert "0.42" in yeni.gerekce


@pytest.mark.parametrize("skor", [0.48, 0.4898, 0.5149, 0.62])
def test_esik_ustundeki_belirsiz_korunuyor(skor):
    """0.4898 (Millestone, gerçekten belirsiz) ve 0.5149 (Siber Güvenlik SOC,
    gerçekte uygun) eşiğin üstünde — dokunulmamalı."""
    yeni, uyarilar = belirsizi_netlestir(_sonuc("belirsiz", skor), esik=0.48)
    assert yeni.karar == "belirsiz"
    assert not uyarilar


@pytest.mark.parametrize("karar", ["uygun", "uygun_degil"])
def test_belirsiz_olmayan_kararlara_dokunulmuyor(karar):
    yeni, uyarilar = belirsizi_netlestir(_sonuc(karar, 0.1), esik=0.48)
    assert yeni.karar == karar
    assert not uyarilar


def test_varsayilan_kapali():
    """VARSAYILAN 0.0 — açıkça istenmedikçe hiçbir ihale bu kuralla elenmez."""
    from app.pipeline.servis import AnalizAyarlari

    assert AnalizAyarlari().belirsiz_netlestirme_esigi == 0.0
    yeni, uyarilar = belirsizi_netlestir(_sonuc("belirsiz", 0.1), esik=0.0)
    assert yeni.karar == "belirsiz"
    assert not uyarilar


def test_gercek_pozitiflerin_taban_skoru_esigin_ustunde(karar_seti):
    """KAÇIRMA KORUMASI: eşik, ölçülmüş en düşük gerçek pozitif skorunun altında
    kalmalı. İnsan onaylı 33 pozitifin en düşüğü 0.5149; 0.48 güvenli, 0.52 değil."""
    from app.decision.stage1_kapsam import belirsizi_netlestir as f

    ONERILEN_ESIK = 0.48
    OLCULEN_EN_DUSUK_POZITIF = 0.5149
    assert ONERILEN_ESIK < OLCULEN_EN_DUSUK_POZITIF
    korunan, _ = f(_sonuc("belirsiz", OLCULEN_EN_DUSUK_POZITIF), esik=ONERILEN_ESIK)
    assert korunan.karar == "belirsiz", "gerçek pozitif eşik tarafından elenmemeli"


# ============================================================================
# Koleksiyon tutarlılığı
# ============================================================================


def test_temiz_koleksiyonlarda_sorun_yok(tmp_path, karar_seti):
    from scripts.index_profiles import _KOLEKSIYON_ESLEMESI, ornekleri_oku
    from app.database.sqlite_depo import SqliteIhaleDeposu

    kok = karar_seti.parent.parent
    db = kok.parent / "VerilerEtiketliveri" / "ekap.db"
    if not db.exists():
        pytest.skip("yerel veri yok")

    e = FakeEmbedder()
    yol = tmp_path / "qdrant"
    gruplar, _ = ornekleri_oku(karar_seti, SqliteIhaleDeposu(db))
    for etiket, ad in _KOLEKSIYON_ESLEMESI.items():
        with QdrantDeposu(yol, ad) as d:
            ornekleri_indeksle(d, e, gruplar[etiket], sifirla=True)

    assert koleksiyonlari_dogrula(yol, karar_seti) == []


def test_yanlis_koleksiyondaki_ornek_yakalaniyor(tmp_path, karar_seti, depo):
    """30.07'de yaşanan tam senaryo: `belirsiz` örnekler `uygun` koleksiyonunda."""
    from scripts.index_profiles import ornekleri_oku

    e = FakeEmbedder()
    yol = tmp_path / "qdrant"
    gruplar, _ = ornekleri_oku(karar_seti, depo)

    # BOZUK KURULUM — eski (üç-grup öncesi) davranışın taklidi
    with QdrantDeposu(yol, KOLEKSIYON_ORNEK_UYGUN) as d:
        ornekleri_indeksle(d, e, gruplar["uygun"] + gruplar["belirsiz"], sifirla=True)
    with QdrantDeposu(yol, KOLEKSIYON_ORNEK_BELIRSIZ) as d:
        ornekleri_indeksle(d, e, gruplar["belirsiz"], sifirla=True)

    sorunlar = koleksiyonlari_dogrula(yol, karar_seti)
    assert sorunlar, "bozuk kurulum yakalanmadı"
    assert any("yanlış koleksiyonda" in s for s in sorunlar)
    assert any("HEM" in s for s in sorunlar), "çelişkili çift üyelik bildirilmedi"


def test_sifirlama_windows_kilidine_ragmen_koleksiyonu_bosaltiyor(tmp_path, monkeypatch):
    """WINDOWS'A ÖZGÜ SESSİZ BOZULMA — 30.07.2026'da yaşandı, Linux'ta GÖRÜNMEZ.

    qdrant_client'ın gömülü modunda `delete_collection`, koleksiyon klasörünü
    `shutil.rmtree(..., ignore_errors=True)` ile siler ama SQLite bağlantısını
    kapatmaz. Linux'ta açık dosya silinebildiği için sorun çıkmaz; Windows'ta
    silme başarısız olur, hata yutulur, ardından gelen `create_collection` aynı
    dosyayı yeniden açar ve ESKİ NOKTALAR GERİ GELİR.

    Gerçek vaka: `index_profiles.py --sifirla` "+5 kayıt" bastı, koleksiyonda 11
    kayıt kaldı (5 uygun + 6 eski `belirsiz`). `--ornekleri-temizle` ve
    `--belirsiz-yok` da tamamen etkisizdi.

    Bu test rmtree'yi no-op yaparak Windows'u taklit eder, böylece koruma
    (QdrantDeposu._kalintiyi_temizle) Linux CI'da da doğrulanır.
    """
    import qdrant_client.local.qdrant_local as ql

    e = FakeEmbedder()
    yol = tmp_path / "qdrant"
    eski = [{"id": f"T{i}", "baslik": f"eski {i}", "metin": "x"} for i in range(11)]
    yeni = [{"id": f"Y{i}", "baslik": f"yeni {i}", "metin": "x"} for i in range(5)]

    with QdrantDeposu(yol, KOLEKSIYON_ORNEK_UYGUN) as d:
        ornekleri_indeksle(d, e, eski, sifirla=True)
        assert d.sayi() == 11

    monkeypatch.setattr(ql.shutil, "rmtree", lambda *a, **k: None)  # Windows taklidi

    with QdrantDeposu(yol, KOLEKSIYON_ORNEK_UYGUN) as d:
        ornekleri_indeksle(d, e, yeni, sifirla=True)
        assert d.sayi() == 5, "sıfırlama kaçtı — eski kayıtlar hayatta kaldı"
        assert d.son_kalinti == 11, "kalıntı temizliği raporlanmadı"

    # Boşaltma da çalışmalı (--ornekleri-temizle / --belirsiz-yok yolu)
    with QdrantDeposu(yol, KOLEKSIYON_ORNEK_UYGUN) as d:
        ornekleri_indeksle(d, e, [], sifirla=True)
        assert d.sayi() == 0, "--ornekleri-temizle boşaltamadı"


def test_final_sizintisi_yakalaniyor(tmp_path, karar_seti, depo):
    """Final seti kanıt havuzuna girerse ölçüm anlamsızlaşır — bildirilmeli."""
    e = FakeEmbedder()
    yol = tmp_path / "qdrant"
    with open(karar_seti, encoding="utf-8-sig") as f:
        final = [s for s in csv.DictReader(f, delimiter=";")
                 if (s.get("set") or "").strip() == "final"][:2]
    ihaleler = {i.id: i for i in depo.coklu_getir([s["tender_id"] for s in final], alan="id")}
    kayitlar = [{"id": i.id, "baslik": i.adi or "", "metin": "x"} for i in ihaleler.values()]

    with QdrantDeposu(yol, KOLEKSIYON_ORNEK_UYGUN) as d:
        ornekleri_indeksle(d, e, kayitlar, sifirla=True)

    assert any("SIZINTI" in s for s in koleksiyonlari_dogrula(yol, karar_seti))
