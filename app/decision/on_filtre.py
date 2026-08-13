"""
Deterministik ön-filtreler — LLM ÇAĞRILMADAN ÖNCE, kodla garanti altına alınan kurallar.

NEDEN MODELE SORULMUYOR: Bunlar basit, kesin ve tartışmasız kurallardır ("ihaleyi
şirketin kendisi açmış", "son teklif tarihi geçmiş" gibi). Modele bırakıldıklarında
ÇİĞNENDİKLERİ ölçüldü — bir örnekte model, girdideki bilgiyle doğrudan çelişen bir
gerekçe uydurdu. Kesin olan bir şeyi olasılıksal bir modele sormak hem yanlış cevap
riski hem de boşa harcanan saniyeler demektir.

Her filtre `OnFiltreSonucu` döndürür; `karar` doluysa boru hattı orada durur.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime

from app.domain.models import AKTIF_IHALE_DURUMU, Ihale


@dataclass(frozen=True)
class OnFiltreSonucu:
    devam: bool
    karar: str | None = None      # "uygun_degil" ya da None
    kural: str | None = None
    gerekce: str = ""


def turkce_ascii_kucuk(metin: str) -> str:
    """Türkçe büyük/küçük harf tuzağını atlatır.

    Python'da "İSBAK".lower() -> 'i̇sbak' (i + U+0307 birleşen nokta), normal 'i' DEĞİL.
    Bu yüzden düz `"isbak" in metin.lower()` kontrolü İSBAK'ı KAÇIRIR. Önce Türkçe
    harfler elle çevriliyor, sonra aksanlar ascii'ye indirgeniyor.
    """
    metin = metin.replace("İ", "I").replace("ı", "i")
    return unicodedata.normalize("NFKD", metin).encode("ascii", "ignore").decode("ascii").lower()


# ŞİRKETE ÖZEL: Sistemin kullanıldığı şirketin adı, idare adında aranan biçimiyle.
# Küçük harfli ve aksansız (ascii) yazılmalıdır — karşılaştırma
# `turkce_ascii_kucuk()` ile normalize edilmiş metin üzerinde yapılır.
# Sistemi başka bir şirkete uyarlıyorsanız DEĞİŞTİRİLMESİ GEREKEN yer burasıdır.
SIRKET_ADI_ANAHTARI = "isbak"


def idare_isbak_mi(idare_adi: str | None) -> bool:
    """İhaleyi açan idare, sistemin kullanıldığı şirketin kendisi mi?"""
    return bool(idare_adi) and SIRKET_ADI_ANAHTARI in turkce_ascii_kucuk(idare_adi)


def ihale_tarihi_gecmis_mi(ihale_tarihi: str | None, simdi: datetime | None = None) -> bool:
    """`DD.MM.YYYY HH:MM` biçimindeki ihale tarihi geçmiş mi?

    Biçim tanınmıyorsa False döner — belirsizlikte ELEMEYİZ (kaçırma en pahalı hata).
    """
    if not ihale_tarihi:
        return False
    try:
        t = datetime.strptime(ihale_tarihi.strip(), "%d.%m.%Y %H:%M")
    except ValueError:
        return False
    return t < (simdi or datetime.now())


def on_filtrele(
    ihale: Ihale,
    *,
    sadece_aktif: bool = False,
    simdi: datetime | None = None,
) -> OnFiltreSonucu:
    """Sırayla deterministik kuralları uygular.

    `sadece_aktif=False` (varsayılan): tek bir ihale elle analiz edilirken durum/tarih
    kontrolü BİLGİ amaçlıdır, elemez — kullanıcı bitmiş bir ihaleyi de inceleyebilmeli
    (karar setindeki 35 örneğin çoğu "Sonuç İlanı Yayımlanmış" durumda).
    `sadece_aktif=True`: toplu tarama için — sadece teklife açık ihaleler geçer.
    """
    # Kural 1 — İSBAK kendi ihalesine teklif veremez. Her zaman geçerli, her zaman kesin.
    if idare_isbak_mi(ihale.idare_adi):
        return OnFiltreSonucu(
            devam=False,
            karar="uygun_degil",
            kural="IDARE_ISBAK",
            gerekce=(
                f"İhaleyi açan idare İSBAK'ın kendisi ({ihale.idare_adi}) — İSBAK kendi "
                f"ihalesine teklif veremez. Kod seviyesinde otomatik karar, model çağrılmadı."
            ),
        )

    if sadece_aktif:
        # Kural 2 — teklife açık değil.
        if ihale.ihale_durumu != AKTIF_IHALE_DURUMU:
            return OnFiltreSonucu(
                devam=False,
                kural="DURUM_AKTIF_DEGIL",
                gerekce=f"İhale durumu '{ihale.ihale_durumu}' — teklife açık değil, atlandı.",
            )
        # Kural 3 — ihale tarihi geçmiş.
        if ihale_tarihi_gecmis_mi(ihale.ihale_tarihi, simdi):
            return OnFiltreSonucu(
                devam=False,
                kural="TARIH_GECMIS",
                gerekce=f"İhale tarihi ({ihale.ihale_tarihi}) geçmiş, atlandı.",
            )

    return OnFiltreSonucu(devam=True)
