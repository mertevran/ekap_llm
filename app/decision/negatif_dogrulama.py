"""
Negatif terim doğrulaması — "bu terim ilanda GERÇEKTEN geçiyor mu?"

=============================================================================
NEDEN VAR
=============================================================================
Profillerin `negatif_terimler` listesi bugün prompt'ta yalnızca bir UYARI olarak
duruyor (`stage1_kapsam::_paket_satiri`):

    (DİKKAT — bu terimler bu paketin KAPSAMINA GİRMEZ: öğrenci taşıma,
     personel taşıma hizmeti, ... İhale metninde bunlardan biri geçiyorsa
     bu paketi 'eslesen_paket' olarak SEÇME)

İki zayıflığı var:

  1. Model deseni METİNDE KENDİ FARK ETMEK zorunda. B1 kuralında bunun
     çalışmadığı ölçüldü ve tespit koda alındı (`kalem_listesi_yok_mu`);
     negatif terimlerde aynı bağımlılık hâlâ duruyor.
  2. Liste 136 terime çıktı. Hepsi her ihalede gösteriliyor, hangisinin O
     ihaleyle ilgili olduğu belirtilmiyor — sinyal gürültüde kayboluyor.

Bu modül eşleşmeyi KOD DÜZEYİNDE yapar: hangi negatif terim, hangi paketin
listesinden, ilanın neresinde geçiyor. Sonuç prompt'a kanıtla sunulur ve koşu
kaydına yazılır.

Bu yaklaşımın işe yaradığı ölçüldü: 30 ihalelik bir koşuda bir öğrenci taşıma
ihalesi tam olarak bu mekanizma sayesinde doğru elendi —

    matched_negative_terms: öğrenci taşıma|personel taşıma hizmeti|yolcu taşıma hizmeti

=============================================================================
NE YAPAR / NE YAPMAZ
=============================================================================
YAPAR : Doğrulanmış terimleri prompt'a ayrı ve somut bir blok olarak koyar.
YAPMAZ: Kararı değiştirmez, paketi elemez, LLM'i atlamaz.

BİLEREK ZORLAMA DEĞİL. Rapor 6.4'teki ölçüm dersi: kanıt yetersizliği uyarısı
zorlama yapılsaydı 20 doğru kararı bozup 4 doğru karar kazandıracaktı. Negatif
terim eşleşmesi de tek başına yeterli kanıt değildir — "öğrenci taşıma" ifadesi
bir trafik yönetimi ihalesinin kapsam metninde de geçebilir ("okul geçitlerinde
öğrenci taşıma güzergâhları"). Karar modelin.

=============================================================================
TÜRKÇE EŞLEŞME
=============================================================================
`on_filtre.turkce_ascii_kucuk` yeniden kullanılıyor. Sebep orada yazılı:
Python'da "İSBAK".lower() -> 'i̇sbak' (i + U+0307), normal 'i' DEĞİL. Düz
`.lower()` ile yapılan karşılaştırma Türkçe metinde sessizce kaçırır.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.decision.on_filtre import turkce_ascii_kucuk

# Terimin ilanda geçtiği yerden gösterilecek bağlam genişliği (karakter).
BAGLAM_KARAKTER = 60
# Bir paket için en fazla kaç doğrulanmış terim gösterilsin. Liste 136 terime
# çıktı; hepsini basmak yeniden gürültü üretir.
PAKET_BASINA_MAX = 4


@dataclass(frozen=True)
class NegatifVurusu:
    paket_kodu: str
    paket_basligi: str
    terim: str
    baglam: str


def _normalize(metin: str) -> str:
    """Türkçe-güvenli küçük harf + boşluk sadeleştirme."""
    return re.sub(r"\s+", " ", turkce_ascii_kucuk(metin or ""))


def _normalize_haritali(metin: str) -> tuple[str, list[int]]:
    """Normalleştirilmiş metin + her karakterin ORİJİNALDEKİ indeksi.

    NEDEN HARİTA GEREKİYOR: eşleşmeyi normalleştirilmiş metinde yapıyoruz
    ("ÖĞRENCİ TAŞIMA" -> "ogrenci tasima"), ama modele gösterilecek ALINTI
    orijinal metinden gelmeli. Aksi halde prompt'a "kanıt" diye bozuk Türkçe
    konur ve kanıt göstermenin amacı ortadan kalkar.

    `turkce_ascii_kucuk` uzunluğu koruMAZ (NFKD + ascii'ye indirgeme bazı
    karakterleri siler), o yüzden düz indeks aritmetiği yapılamaz; eşleme
    karakter karakter kuruluyor.
    """
    parcalar: list[str] = []
    harita: list[int] = []
    onceki_bosluk = False
    for i, ch in enumerate(metin or ""):
        if ch.isspace():
            if onceki_bosluk:
                continue
            parcalar.append(" ")
            harita.append(i)
            onceki_bosluk = True
            continue
        onceki_bosluk = False
        for c in turkce_ascii_kucuk(ch):
            parcalar.append(c)
            harita.append(i)
    return "".join(parcalar), harita


def negatifleri_dogrula(metin: str, paketler) -> list[NegatifVurusu]:
    """İlan metninde GERÇEKTEN geçen negatif terimleri bulur.

    `paketler`: `PaketVurusu` listesi (kod, baslik, negatif_terimler alanları
    kullanılır). Yalnızca prompt'a sunulan paketler taranır — sunulmayan bir
    paketin negatifi modele gösterilmediği için doğrulanması da anlamsızdır.

    Eşleşme KELİME SINIRINDA yapılır. "yol" terimi "yolcu" içinde eşleşmemeli;
    alt dize araması negatif terim listesini kullanılamaz hale getirirdi.
    """
    if not metin:
        return []
    hedef, harita = _normalize_haritali(metin)
    if not hedef:
        return []

    vuruslar: list[NegatifVurusu] = []
    gorulen: set[tuple[str, str]] = set()

    for p in paketler or []:
        paket_sayaci = 0
        for terim in getattr(p, "negatif_terimler", None) or []:
            t = _normalize(terim)
            if not t:
                continue
            anahtar = (getattr(p, "kod", ""), t)
            if anahtar in gorulen:
                continue
            # \b tek kelimede de çok kelimeli terimde de doğru çalışır.
            desen = re.compile(rf"\b{re.escape(t)}\b")
            m = desen.search(hedef)
            if not m:
                continue
            gorulen.add(anahtar)
            # Alıntı ORİJİNAL metinden — modele bozuk Türkçe gösterilmez.
            o_bas = harita[m.start()]
            o_son = harita[m.end() - 1] + 1
            bas = max(0, o_bas - BAGLAM_KARAKTER)
            son = min(len(metin), o_son + BAGLAM_KARAKTER)
            baglam = re.sub(r"\s+", " ", metin[bas:son]).strip()
            vuruslar.append(
                NegatifVurusu(
                    paket_kodu=getattr(p, "kod", ""),
                    paket_basligi=getattr(p, "baslik", ""),
                    terim=terim,
                    baglam=baglam,
                )
            )
            paket_sayaci += 1
            if paket_sayaci >= PAKET_BASINA_MAX:
                break
    return vuruslar


def prompt_blogu(vuruslar: list[NegatifVurusu]) -> str:
    """Doğrulanmış vuruşları prompt'a konacak metne çevirir. Vuruş yoksa boş."""
    if not vuruslar:
        return ""
    satirlar = []
    for v in vuruslar:
        satirlar.append(f'- "{v.terim}"  ({v.paket_basligi})\n    ilanda: …{v.baglam}…')
    return (
        "\n\nDİKKAT — İLANDA GEÇEN DIŞLAMA TERİMLERİ (kod tarafından doğrulandı):\n"
        "Aşağıdaki ifadeler, karşılarında yazan iş paketinin kapsamına GİRMEYEN işleri "
        "tanımlar ve ilan metninde GERÇEKTEN geçtikleri teyit edilmiştir:\n"
        + "\n".join(satirlar)
        + "\nBu, o paketi 'eslesen_paket' olarak seçmemen için güçlü bir gerekçedir. "
        "Ancak TEK BAŞINA yeterli kanıt değildir: terim, işin asıl konusunu değil "
        "yan bir ayrıntısını tarif ediyor olabilir. Metne bak, kararı sen ver."
    )


def ozet(vuruslar: list[NegatifVurusu]) -> str:
    """Koşu kaydına yazılacak tek satırlık özet."""
    if not vuruslar:
        return ""
    return "negatif_dogrulandi: " + " | ".join(f"{v.terim}({v.paket_kodu})" for v in vuruslar)
