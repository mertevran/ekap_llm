"""
SQL ön filtresi testleri.

İKİ KRİTİK TEST:
  · `test_python_ve_sql_ayni_mantik` — SQLite (Python eşleşme) ile Postgres
    (SQL eşleşme) AYNI ihaleyi seçmeli. Ayrışırlarsa iki arka uçta farklı
    havuz taranır ve koşular kıyaslanamaz hale gelir.
  · `test_dogrulayici_*` — LLM'e SQL ürettirilirse çalıştırılmadan önce
    buradan geçer. Canlı veritabanına doğrulanmamış SQL gönderilemez.
"""

from __future__ import annotations

import pytest

from app.decision.sql_filtre import (
    FiltreTanimi,
    eslesme_var_mi,
    profillerden_kur,
    sql_guvenli_mi,
    where_uret,
)
from app.profiles.loader import profilleri_yukle

TANIM = FiltreTanimi(
    terimler=("trafik sinyalizasyonu", "akıllı kavşak", "kamera sistemi"),
    okas_on_ekleri=("34996", "45316"),
)


# ------------------------------------------------------------------- kurulum
def test_profillerden_kur_iki_genislik():
    p = profilleri_yukle()
    dar = profillerden_kur(p, genislik="dar")
    genis = profillerden_kur(p, genislik="genis")
    assert len(dar.terimler) > 0
    # 'genis' anahtar_kelimeler'i kullanır: guclu + destekleyici + genel + ekipman
    assert len(genis.terimler) > len(dar.terimler)
    assert dar.okas_on_ekleri == genis.okas_on_ekleri


def test_negatif_terimler_kendi_profilinde_anahtar_degil():
    """Bir terim, KENDİ profilinde hem negatif hem anahtar olamaz — çelişkidir.

    Profiller ARASI çakışma meşrudur ve dışlanmaz: "geometrik düzenleme" AUS-01
    için negatiftir (yol düzenleme işi) ama PLN-02 için anahtar kelimedir
    (trafik mühendisliği etüdü). Aynı ifade farklı paketlerde farklı anlam
    taşıyabilir; global kesişim aramak yanlış testtir.
    """
    for pr in profilleri_yukle():
        neg = {t.lower() for t in pr.negatif_terimler}
        anah = {t.lower() for t in pr.anahtar_kelimeler}
        assert not (neg & anah), f"{pr.kod} çelişkili: {sorted(neg & anah)}"


def test_katlama_iki_tarafta_ayni():
    """Türkçe katlama Python ve SQL'de AYNI sonucu vermeli.

    `_sql_katla` bir translate() haritası kurar; `katla()` Python tarafını yapar.
    Ayrışırlarsa SQLite ve Postgres farklı havuz tarar.
    """
    from app.decision.sql_filtre import _HEDEF_HARFLER, _KAYNAK_HARFLER, katla

    for kaynak, hedef in zip(_KAYNAK_HARFLER, _HEDEF_HARFLER):
        assert katla(kaynak) == hedef.lower(), f"{kaynak!r} -> {katla(kaynak)!r} != {hedef.lower()!r}"


def test_turkce_i_tuzagi():
    """'AKILLI'.lower() -> 'akilli' (ı DEĞİL i). Düz lower() eşleşmeyi kaçırır."""
    assert "AKILLI KAVŞAK".lower() != "akıllı kavşak"          # tuzağın kendisi
    assert eslesme_var_mi(TANIM, "AKILLI KAVŞAK KONTROL CİHAZI", [])
    assert eslesme_var_mi(TANIM, "Akıllı Kavşak Sistemi", [])
    assert eslesme_var_mi(TANIM, "TRAFİK SİNYALİZASYONU BAKIMI", [])


def test_kisa_terimler_eleniyor():
    """'yol' gibi parçalar her ihaleye vurup filtreyi işlevsiz kılar."""
    t = profillerden_kur([], ek_terimler=["yol", "kart", "sinyalizasyon"])
    assert "sinyalizasyon" in t.terimler
    assert "yol" not in t.terimler


def test_gecersiz_genislik():
    with pytest.raises(ValueError):
        profillerden_kur([], genislik="orta")


# ------------------------------------------------------------------ eşleşme
@pytest.mark.parametrize(
    "adi,okas,beklenen",
    [
        ("Trafik Sinyalizasyonu Malzeme Alımı", [], True),          # terim
        ("AKILLI KAVŞAK KONTROL CİHAZI", [], True),                 # büyük harf
        ("Muhtelif Malzeme Alımı", ["3499600"], True),              # OKAS ön eki
        ("Muhtelif Malzeme Alımı", ["453162101"], True),            # 45316 ön eki
        ("Öğrenci Taşıma Hizmeti", [], False),
        ("Gıda Maddesi Alımı", ["15800000"], False),
    ],
)
def test_eslesme(adi, okas, beklenen):
    assert eslesme_var_mi(TANIM, adi, okas) is beklenen


def test_bos_tanim_hicbir_sey_secmez():
    bos = FiltreTanimi()
    assert bos.bos_mu()
    assert where_uret(bos) == ("", [])
    assert eslesme_var_mi(bos, "Trafik Sinyalizasyonu", ["34996000"]) is False


# ---------------------------------------------------- SQL / Python denkliği
def test_where_uret_parametreli():
    """Terimler sorgu METNİNE gömülmemeli — enjeksiyon yüzeyi olmasın."""
    sql, par = where_uret(TANIM, "t")
    assert "trafik sinyalizasyonu" not in sql
    assert "%s" in sql
    assert par[1] == list(TANIM.okas_on_ekleri)
    assert sql.count("(") == sql.count(")")
    # Terimler katlanmış olarak geçirilmeli — SQL tarafı da katlıyor.
    from app.decision.sql_filtre import katla
    assert par[0] == [katla(t) for t in TANIM.terimler]


def test_python_ve_sql_ayni_mantik():
    """SQL katlamalı LIKE ile Python `katla()` içermesi aynı kümeyi seçmeli."""
    from app.decision.sql_filtre import katla

    ornekler = [
        ("Trafik Sinyalizasyonu Yapım İşi", []),
        ("trafik sinyalizasyonu bakımı", []),
        ("TRAFİK SİNYALİZASYONU", []),
        ("AKILLI KAVŞAK", []),
        ("Alakasız İhale", []),
        ("Alakasız", ["34996123"]),
        ("Alakasız", ["99999999"]),
    ]
    for adi, okas in ornekler:
        py = eslesme_var_mi(TANIM, adi, okas)
        # SQL karşılığının simülasyonu: her iki taraf da katlanıp içerme aranır
        sql_terim = any(katla(t) in katla(adi) for t in TANIM.terimler)
        sql_okas = any(str(k).startswith(p) for k in okas for p in TANIM.okas_on_ekleri)
        assert py is (sql_terim or sql_okas), f"ayrışma: {adi!r} {okas}"


# ------------------------------------------------------------- doğrulayıcı
@pytest.mark.parametrize(
    "ifade",
    [
        "t.adi ILIKE '%sinyalizasyon%'",
        "t.ihale_turu = 'Mal' AND t.il = 'İSTANBUL'",
        "EXISTS (SELECT 1 FROM public.tender_okas_codes o WHERE o.tender_id = t.id)",
    ],
)
def test_dogrulayici_gecerli_ifadeleri_kabul_eder(ifade):
    ok, gerekce = sql_guvenli_mi(ifade)
    assert ok, gerekce


@pytest.mark.parametrize(
    "ifade,neden",
    [
        ("t.adi ILIKE '%x%'; DROP TABLE tenders", "noktalı virgül"),
        ("t.adi ILIKE '%x%' -- yorum", "yorum"),
        ("DELETE FROM tenders", "veri değiştiren"),
        ("t.adi ILIKE '%x%' AND (1=1", "dengesiz parantez"),
        ("SELECT * FROM tenders", "tam sorgu"),
        ("pg_sleep(10)", "yasak fonksiyon"),
        ("gizli.sutun = 1", "izinsiz tablo"),
        ("", "boş"),
        ("   ", "boşluk"),
    ],
)
def test_dogrulayici_zararli_ifadeleri_reddeder(ifade, neden):
    ok, _ = sql_guvenli_mi(ifade)
    assert not ok, f"reddedilmeliydi ({neden}): {ifade!r}"


def test_dogrulayici_gerekce_donduruyor():
    ok, gerekce = sql_guvenli_mi("DROP TABLE tenders")
    assert not ok
    assert gerekce and gerekce != "geçti"
