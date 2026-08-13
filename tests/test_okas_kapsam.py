"""
OKAS vetosu + nitel yakınlık etiketi testleri.

En kritik test `test_kapsam_disi_liste_hicbir_pozitifi_kesmiyor` — SIZINTI TESTİ.
`KAPSAM_DISI_BOLUMLER` listesi büyütülürse ve yeni bölüm etiketli bir pozitifin
OKAS kodunu kapsıyorsa bu test kırılır. Liste sezgiyle değil veriyle büyütülsün
diye vardır: ilk turda 19 (deri/tekstil) ve 63 (taşımacılık destek) "alakasız"
göründüğü için dışlanacaktı, veri aksini söyledi.
"""

from __future__ import annotations

import csv
import sqlite3

import pytest

from app.decision.okas_kapsam import (
    KAPSAM_DISI_BOLUMLER,
    bolum,
    kapsam_disi_mi,
    tamami_kapsam_disi,
)
from app.decision.stage1_kapsam import kullanici_mesaji, yakinlik_etiketi
from app.domain.models import Ihale, Ilan, OkasKodu
from app.retrieval.profil_retriever import PaketVurusu


# ---------------------------------------------------------------- temel davranış
def test_bolum_ilk_iki_hane():
    assert bolum("60170000") == "60"
    assert bolum(" 33141800 ") == "33"
    assert bolum("") == ""


@pytest.mark.parametrize(
    "kod",
    ["60170000", "60112000", "33141800", "15800000", "09134200", "18100000"],
)
def test_acikca_kapsam_disi_kodlar(kod):
    assert kapsam_disi_mi(kod)


@pytest.mark.parametrize(
    "kod",
    [
        "32333200",  # Kamerasıyla kombine video kayıt cihazları -> ENT-02
        "34990000",  # Kontrol, güvenlik, sinyalizasyon ve ışık  -> AUS-01
        "45316000",  # aydınlatma tesisatı işleri
        "48000000",  # yazılım paketleri
        "31321700",  # sinyal kablosu
        "19522110",  # plastik boru — POZİTİF bir ihalede ikincil kod
        "63710000",  # karayolu taşımacılığı destek — POZİTİFLERDE geçiyor
    ],
)
def test_kapsam_ici_kodlar_veto_yemiyor(kod):
    assert not kapsam_disi_mi(kod)


def test_bos_liste_veto_uretmez():
    """Kanıt yokluğu, kapsam dışılık kanıtı değildir."""
    assert tamami_kapsam_disi([]) is False
    assert tamami_kapsam_disi(["", "  "]) is False


def test_tek_alakali_kod_vetoyu_engeller():
    """TAMAMI kuralı: bir tek ilgili kod bile veto'yu düşürür (kaçırmama)."""
    assert tamami_kapsam_disi(["60170000", "60112000"]) is True
    assert tamami_kapsam_disi(["60170000", "32333200"]) is False


# ------------------------------------------------------------------ sızıntı testi
def test_kapsam_disi_liste_hicbir_pozitifi_kesmiyor(ekap_db, karar_seti):
    """Etiketli POZİTİF ihalelerin hiçbiri kapsam dışı bölümde olmamalı.

    Kaynak: kaçırma seti (30 'uygun') + karar setinin 'uygun' satırları.
    """
    kok = karar_seti.parent
    iknler: list[str] = []
    for yol, filtre in ((kok / "kacirma-seti-v1.csv", None), (karar_seti, "uygun")):
        if not yol.exists():
            continue
        with open(yol, encoding="utf-8-sig") as f:
            for satir in csv.DictReader(f, delimiter=";"):
                if filtre and satir.get("karar") != filtre:
                    continue
                iknler.append(satir["ikn"])

    assert iknler, "Etiketli pozitif bulunamadı — test anlamsız olurdu."

    conn = sqlite3.connect(ekap_db)
    try:
        ihlaller: list[str] = []
        for ikn in iknler:
            kodlar = [
                str(r[0])
                for r in conn.execute(
                    "SELECT o.kod FROM tender_okas_codes o "
                    "JOIN tenders t ON t.id = o.tender_id WHERE t.ikn = ?",
                    (ikn,),
                )
            ]
            if kodlar and tamami_kapsam_disi(kodlar):
                ihlaller.append(f"{ikn} -> {kodlar}")
    finally:
        conn.close()

    assert not ihlaller, (
        "KAPSAM_DISI_BOLUMLER gerçek pozitif eliyor:\n  " + "\n  ".join(ihlaller)
    )


def test_liste_beklenen_bolumleri_iceriyor():
    """Öğrenci taşıma vakasının bölümü listede kalmalı (regresyon koruması)."""
    assert "60" in KAPSAM_DISI_BOLUMLER
    # Sızıntı testinin yakaladığı iki bölüm ASLA eklenmemeli.
    assert "19" not in KAPSAM_DISI_BOLUMLER
    assert "63" not in KAPSAM_DISI_BOLUMLER


# ------------------------------------------------------------- prompt entegrasyonu
def _ihale(okas: list[tuple[str, str]], kapsam: str, adi: str) -> Ihale:
    return Ihale(
        id="t1",
        ikn="2026/1280806",
        adi=adi,
        idare_adi="MUĞLA İL MİLLİ EĞİTİM MÜDÜRLÜĞÜ",
        il="MUĞLA",
        ihale_tarihi="20.08.2026 10:00",
        ihale_turu="Hizmet",
        ilanlar=[
            Ilan(
                id="i1",
                tender_id="t1",
                ilan_tipi="Ön İlan",
                ilan_tarihi="2026-07-28T00:00:00",
                baslik=adi,
                icerik=kapsam,
                icerik_temiz=kapsam,
            )
        ],
        okas_kodlari=[OkasKodu(k, a) for k, a in okas],
    )


# Gerçek vakanın kalıbı: "Niteliği" alanı başlığı tekrarlayıp şartnameye havale ediyor.
_KANITSIZ_KAPSAM = (
    "| **a)** Niteliği, türü ve miktarı | : | 181 Günlük Öğrenci Taşıma Hizmet Alım İşi "
    "Ayrıntılı bilgiye idari şartnameden ulaşılabilir. |"
)
_ADI = "İlköğretim Öğrencilerinin Taşınması Amacıyla 181 Günlük Hizmet Alım İşi"
_PAKETLER = [PaketVurusu(kod="PLN-01", baslik="Ulaşım Planlama", oncelik="kritik", benzerlik=0.5049)]


def test_okas_vetosu_kanit_uyarisini_susturuyor():
    ihale = _ihale([("60170000", "Yolcu taşıma araçlarının sürücüsü ile kiralanması")],
                   _KANITSIZ_KAPSAM, _ADI)

    acik = kullanici_mesaji(ihale, _PAKETLER, min_skor=0.50, kanit_uyarisini_bastir=False)
    assert "KALEM/MODÜL LİSTESİ YOK" in acik

    veto = kullanici_mesaji(ihale, _PAKETLER, min_skor=0.50, kanit_uyarisini_bastir=True)
    assert "KALEM/MODÜL LİSTESİ YOK" not in veto


def test_kapsam_ici_okas_vetoyu_tetiklemez():
    """Aynı kanıtsız metin, kamera OKAS'ıyla gelirse uyarı KORUNMALI."""
    ihale = _ihale([("32333200", "Kamerasıyla kombine video kayıt cihazları")],
                   _KANITSIZ_KAPSAM, _ADI)
    assert not tamami_kapsam_disi([o.kod for o in ihale.okas_kodlari])


# --------------------------------------------------------------- nitel yakınlık
@pytest.mark.parametrize(
    "skor,beklenen",
    [(0.72, "çok yakın"), (0.60, "çok yakın"), (0.5849, "yakın"), (0.55, "yakın"),
     (0.5049, "orta"), (0.50, "orta"), (0.4999, "uzak"), (0.31, "uzak")],
)
def test_yakinlik_etiketi_bantlari(skor, beklenen):
    assert yakinlik_etiketi(skor) == beklenen


def test_nitel_modda_ham_skor_prompta_gecmiyor():
    """Yankının kaynağı sayının kendisi — prompt'ta hiç görünmemeli."""
    ihale = _ihale([("32333200", "Kamera")], _KANITSIZ_KAPSAM, _ADI)

    sayili = kullanici_mesaji(ihale, _PAKETLER, min_skor=0.50, nitel_yakinlik=False)
    assert "0.5049" in sayili

    nitel = kullanici_mesaji(ihale, _PAKETLER, min_skor=0.50, nitel_yakinlik=True)
    assert "0.5049" not in nitel
    assert "yakınlık: orta" in nitel


def test_esik_altinda_da_ham_skor_sizmiyor():
    """'EŞLEŞME YOK' dalı en yüksek skoru 4 haneyle basıyordu — o da bir yankı kaynağı."""
    dusuk = [PaketVurusu(kod="OPS-02", baslik="Bakım", oncelik="orta", benzerlik=0.4437)]
    ihale = _ihale([("32333200", "Kamera")], _KANITSIZ_KAPSAM, _ADI)

    nitel = kullanici_mesaji(ihale, dusuk, min_skor=0.50, nitel_yakinlik=True)
    assert "0.4437" not in nitel
    assert "İŞ PAKETİ EŞLEŞMESİ YOK" in nitel


def test_varsayilanlar_eski_davranisi_koruyor():
    """Bayraklar verilmezse prompt bit bit eskisiyle aynı olmalı."""
    ihale = _ihale([("60170000", "Yolcu taşıma")], _KANITSIZ_KAPSAM, _ADI)
    varsayilan = kullanici_mesaji(ihale, _PAKETLER, min_skor=0.50)
    acik_yazilmis = kullanici_mesaji(
        ihale, _PAKETLER, min_skor=0.50, nitel_yakinlik=False, kanit_uyarisini_bastir=False
    )
    assert varsayilan == acik_yazilmis
    assert "0.5049" in varsayilan
    assert "KALEM/MODÜL LİSTESİ YOK" in varsayilan
