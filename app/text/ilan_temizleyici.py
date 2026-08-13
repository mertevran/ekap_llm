"""
İlan metni temizleyici — gürültüyü atıp asıl kapsam tanımını bırakır.

DOĞRULAMA: Bu modülün çıktısı, veritabanında toplu olarak üretilip saklanmış
temizlenmiş metinle 96.218 kaydın 96.218'inde BAYT BAYT AYNI çıktı (fark = 0). Yani
"okuma anında temizle" yaklaşımı, önceden hesaplanıp saklanan sonucun birebir aynısını
verir; canlı veritabanına yeni bir kolon eklemeye gerek yoktur.

ÖNEMLİ: Aktif ihalelerin yaklaşık %99'u "Ön İlan" şablonundan okunur. Ön İlan'ın ham
metni ortalama 6.253 karakter, temizlenmiş hâli 988 karakterdir — yani bu modül
gürültünün %84'ünü atar. Değişiklik yapıp doğrularken "İhale İlanı" değil, ÖN İLAN
örneklerine bakın.


tender_announcements.icerik alanındaki ham EKAP metninden sadece asıl kapsam tanımını
(ihale ne için açılmış, ne alınacak/yaptırılacak) çıkarır; her ilanda neredeyse birebir
tekrarlanan "Katılım ve yeterlik kriterleri", "Teklifler", "Sözleşmenin" gibi bölümleri atar.

Neden gerekli: v1'de kaba karakter kırpması (ilk N karakter) kullanılmıştı — bu bazen
boilerplate'i içeride bırakıyor, bazen asıl kapsamı yarıda kesiyordu (bkz. v1 plan notları,
Dinamik Kavşak örneği). EKAP'ın ilan şablonu çok tutarlı olduğu için gerçek bir ayrıştırıcı
yazmak mümkün ve çok daha güvenilir.

EKAP'ta iki farklı şablon görülüyor (ilan_tipi'ne göre):
- "Ön İlan" / "İhale İlanı": "...- İhale konusu .../3.1 Adı/3.2 Niteliği, türü ve miktarı/
  3.3 Yapılacağı yer..." bölümü, ardından "...- Katılım ve yeterlik kriterleri" boilerplate'i
  başlar.
- "Sonuç İlanı": "...- İhale konusu .../a) Adı b) Yapılacağı yer c) Süresi" bölümü, ardından
  "...- Teklifler" (kaç teklif geldi vb., idari) ve "...- Sözleşmenin" (yüklenici, bedel vb.,
  bizim karar için gerekli değil, hatta post-hoc bilgiyle önyargı riski taşır) gelir.

Her iki şablonda da ortak nokta: "İhale konusu" ifadesi asıl kapsamın başladığı yer.
"""

from __future__ import annotations

import re

# "İhale konusu" ifadesinden sonra, boilerplate başlamadan önce makul bir üst sınır —
# beklenmedik/tanınmayan bir formatla karşılaşılırsa (bitiş deseni bulunamazsa) bu kadarını al.
GUVENLI_UST_SINIR = 3000

_BASLANGIC_DESENI = re.compile(r"\d+\s*[-.]?\s*İhale konusu", re.IGNORECASE)

_BITIS_DESENLERI = [
    re.compile(r"\d+\s*[-.]?\s*Katılım ve yeterlik kriterleri", re.IGNORECASE),
    re.compile(r"\d+\s*[-.]?\s*Teklifler\b", re.IGNORECASE),
]

# Markdown tablo ayraç satırları ("|  |  |  |" ve "| --- | --- | --- |") — bilgi taşımıyor,
# sadece görsel süsleme, temiz metinden atılabilir.
_TABLO_AYRAC_DESENI = re.compile(r"^\s*\|[\s|:-]*\|\s*$", re.MULTILINE)


def ilan_metnini_temizle(icerik: str | None) -> str:
    """Ham `icerik` metninden sadece kapsam tanımını (İhale konusu bölümü) döndürür.

    Boş/None girdide boş string, tanınmayan formatta ise güvenli bir kırpma (ilk
    GUVENLI_UST_SINIR karakter) döner — hiçbir zaman exception fırlatmaz, RAG/LLM
    pipeline'ında toplu işlemede tek bir bozuk kayıt yüzünden çökme olmaması için.
    """
    if not icerik:
        return ""

    baslangic_eslesme = _BASLANGIC_DESENI.search(icerik)
    if not baslangic_eslesme:
        return _tabloyu_temizle(icerik[:GUVENLI_UST_SINIR].strip())

    baslangic = baslangic_eslesme.start()

    bitis = len(icerik)
    for desen in _BITIS_DESENLERI:
        eslesme = desen.search(icerik, baslangic)
        if eslesme and eslesme.start() < bitis:
            bitis = eslesme.start()

    # Bitiş deseni hiç bulunamadıysa (ör. çok kısa/eksik bir ilan), güvenli üst sınırı uygula
    if bitis == len(icerik):
        bitis = min(bitis, baslangic + GUVENLI_UST_SINIR)

    parca = icerik[baslangic:bitis]
    return _tabloyu_temizle(parca).strip()


def _tabloyu_temizle(metin: str) -> str:
    """Sadece süsleme amaçlı markdown tablo ayraç satırlarını atar, gerçek içerik
    taşıyan satırlara (| **3.2.** Niteliği... | : | ... |) dokunmaz."""
    metin = _TABLO_AYRAC_DESENI.sub("", metin)
    metin = re.sub(r"\n{3,}", "\n\n", metin)
    # Bitiş deseninin hemen öncesinde kalan yalnız "**" gibi yarım markdown kalıntıları
    metin = re.sub(r"\*{1,2}\s*$", "", metin.strip())
    return metin.strip()
