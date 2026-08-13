"""
MCP okuma katmanı testleri.

EN KRİTİK TEST: `test_katmanda_hicbir_yazma_yolu_yok`.
Bu katman uzmana ve bir LLM istemcisine açılıyor; yazma yeteneği olsaydı model
üretimli SQL doğrudan canlı veriye dokunabilirdi. Modülün TAMAMI taranıp veri
değiştiren hiçbir komut bulunmadığı doğrulanıyor.

İkincisi `test_serbest_sorgu_zararli_ifadeyi_calistirmiyor`: doğrulayıcının
`serbest_sorgu` yolunda GERÇEKTEN devrede olduğunu gösterir — `sql_guvenli_mi`
tek başına doğru çalışsa da çağrılmazsa işe yaramaz.
"""

from __future__ import annotations

import inspect
import re

import pytest

from app.mcp import sorgular


# ----------------------------------------------------------------- güvenlik
def test_katmanda_hicbir_yazma_yolu_yok():
    """Modülün tamamında veri değiştiren SQL komutu bulunmamalı."""
    kaynak = inspect.getsource(sorgular)
    # Docstring'ler yasak kelimeleri ANLATIYOR; yalnızca kod satırlarına bak.
    kod = "\n".join(
        s for s in kaynak.splitlines()
        if not s.strip().startswith("#")
    )
    kod = re.sub(r'"""[\s\S]*?"""', "", kod)      # docstring'leri çıkar
    for yasak in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "TRUNCATE ", "CREATE "):
        assert yasak.lower() not in kod.lower(), f"MCP katmanında yazma komutu: {yasak}"


def test_karar_hatti_import_edilmiyor():
    """MCP katmanı LLM/retrieval çağırmamalı — karar hattı deterministik kalsın."""
    kaynak = inspect.getsource(sorgular)
    for modul in ("llm_client", "OllamaIstemcisi", "profil_retriever", "stage1_kapsam"):
        assert modul not in kaynak, f"MCP katmanı karar hattına bağlanmış: {modul}"


@pytest.mark.parametrize(
    "ifade",
    [
        "t.adi ILIKE '%x%'; DROP TABLE public.tenders",
        "t.adi ILIKE '%x%' -- yorum",
        "DELETE FROM public.tenders",
        "SELECT * FROM public.tenders",
        "pg_sleep(10)",
        "gizli.sutun = 1",
        "",
    ],
)
def test_serbest_sorgu_zararli_ifadeyi_calistirmiyor(ifade):
    """Doğrulayıcı bu yolda GERÇEKTEN devrede mi — çalıştırmadan reddetmeli."""
    with pytest.raises(sorgular.SorguHatasi) as e:
        sorgular.serbest_sorgu(ifade)
    assert "REDDETTİ" in str(e.value)


def test_limit_ust_sinirla_kisitlaniyor():
    """Sınırsız liste LLM bağlamını doldurur ve maliyeti şişirir."""
    assert sorgular._limitle(999999) == sorgular.MAX_LIMIT
    assert sorgular._limitle(5) == 5
    # None / 0 / negatif = "belirtilmedi" -> varsayılan. İstemciden gelen saçma
    # parametre sorguyu patlatmamalı.
    assert sorgular._limitle(None) == sorgular.VARSAYILAN_LIMIT
    assert sorgular._limitle(0) == sorgular.VARSAYILAN_LIMIT
    assert sorgular._limitle(-3) == sorgular.VARSAYILAN_LIMIT


# ------------------------------------------------------------------ okuma
def test_profil_listesi(depo):
    s = sorgular.profil_listesi()
    assert s["sayi"] == 20
    kodlar = {p["kod"] for p in s["profiller"]}
    assert {"AUS-01", "ENT-02", "TEK-01", "OPS-02"} <= kodlar
    ent04 = next(p for p in s["profiller"] if p["kod"] == "ENT-04")
    assert "pdks" in [t.lower() for t in ent04["guclu_terimler"]]
    # Aşama 2'nin neden kapalı olduğu çıktıda görünmeli
    assert ent04["kapasite_verisi"] == "kurum_ici_dogrulama_gerekli"


def test_ihale_ara_ve_getir(depo, ekap_db, monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "sqlite")
    sonuc = sorgular.ihale_ara(terim="kamera", sadece_aktif=False, limit=5)
    assert sonuc["sayi"] > 0
    ilk = sonuc["ihaleler"][0]
    assert "kamera" in ilk["adi"].lower()

    ayrinti = sorgular.ihale_getir(ilk["ikn"])
    assert ayrinti["ikn"] == ilk["ikn"]
    assert ayrinti["kapsam_metni_uzunlugu"] >= 0
    assert isinstance(ayrinti["okas"], list)


def test_ihale_ara_turkce_buyuk_harfi_kaciriyor_mu(depo, monkeypatch):
    """'İ' tuzağı: büyük harfle arayınca da bulmalı."""
    monkeypatch.setenv("DATA_BACKEND", "sqlite")
    a = sorgular.ihale_ara(terim="KAMERA", sadece_aktif=False, limit=5)
    b = sorgular.ihale_ara(terim="kamera", sadece_aktif=False, limit=5)
    assert a["sayi"] == b["sayi"]


def test_ihale_getir_bulunamazsa_hata(depo, monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "sqlite")
    with pytest.raises(sorgular.SorguHatasi):
        sorgular.ihale_getir("9999/9999999")


def test_aday_ihaleler_uyari_iceriyor(depo, monkeypatch):
    """Filtreyi geçmenin karar OLMADIĞI çıktıda yazılı olmalı."""
    monkeypatch.setenv("DATA_BACKEND", "sqlite")
    s = sorgular.aday_ihaleler(limit=5)
    assert s["toplam_gecen"] > 0
    assert "DEĞİLDİR" in s["uyari"]
    assert s["filtre"]["terim_sayisi"] > 0


def test_okas_ara(depo, monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "sqlite")
    s = sorgular.okas_ara("sinyalizasyon", limit=5)
    assert s["sayi"] > 0
    assert any("sinyal" in k["ad"].lower() for k in s["kodlar"])


def test_translate_postgres_semantigiyle_ayni():
    """SQLite'a tanıtılan `translate`, Postgres'inkiyle aynı davranmalı.

    Ayrışırlarsa MCP istemcisine verdiğimiz şema tavsiyesi iki arka uçta farklı
    sonuç üretir ve "hangi veritabanındasın" diye sormak gerekir.
    """
    kaynak, hedef = "İIıĞğŞşÇçÖöÜü", "IIiGgSsCcOoUu"
    assert sorgular._translate("AKILLI KAVŞAK", kaynak, hedef).lower() == "akilli kavsak"
    assert sorgular._translate("ÖĞRENCİ", kaynak, hedef).lower() == "ogrenci"
    assert sorgular._translate(None, kaynak, hedef) is None
    # hedef daha kısaysa fazla karakterler SİLİNİR (Postgres semantiği)
    assert sorgular._translate("abc", "abc", "x") == "x"


def test_serbest_sorgu_translate_ile_calisiyor(depo, monkeypatch):
    """Şema tavsiyesindeki Türkçe katlama deseni gerçekten koşabilmeli."""
    monkeypatch.setenv("DATA_BACKEND", "sqlite")
    ifade = ("lower(translate(t.adi,'İIıĞğŞşÇçÖöÜü','IIiGgSsCcOoUu')) "
             "LIKE '%sinyalizasyon%'")
    s = sorgular.serbest_sorgu(ifade, limit=5)
    assert s["sayi"] > 0
    # Doğrulamada da KATLAMA kullanılmalı: "SİNYALİZASYON".lower() Python'da
    # "si̇nyali̇zasyon" verir (i + U+0307) ve düz `in` kontrolü kaçırır.
    # Aynı tuzak `on_filtre`, `negatif_dogrulama` ve `sql_filtre`de de yaşandı.
    from app.decision.sql_filtre import katla

    assert all("sinyal" in katla(i["adi"]) for i in s["ihaleler"])


def test_sema_bilgisi_kurallari_iceriyor():
    s = sorgular.sema_bilgisi()
    assert "public.tenders" in s["sema"]
    assert any("WHERE" in k for k in s["kurallar"])
    assert any("YASAK" in k for k in s["kurallar"])
