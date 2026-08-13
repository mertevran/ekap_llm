"""
Alan modelleri — hangi veri kaynağından gelirse gelsin (SQLite ya da Postgres)
uygulamanın geri kalanının gördüğü TEK tip budur.

Kolon adları canlı Postgres şemasıyla (veritabani_export/databaseekap.sql) birebir aynı.
TEK İSTİSNA: `Ilan.icerik_temiz` — bu kolon canlı Postgres'te YOKTUR, repository
katmanı ham `icerik`'i okuduktan sonra OKUMA ANINDA üretir (bkz. app/text/ilan_temizleyici.py
ve app/database/base.py). Böylece bugün SQLite'la test edilen kod yolu, yarın Postgres'te
çalışacak kod yolunun BİREBİR AYNISI olur.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OkasKodu:
    kod: str
    ad: str

    def __str__(self) -> str:
        return f"{self.kod} - {self.ad}" if self.ad else self.kod


@dataclass(frozen=True)
class Ozellik:
    ozellik: str


@dataclass(frozen=True)
class Ilan:
    id: str
    tender_id: str
    ilan_tipi: str | None
    ilan_tarihi: str | None
    baslik: str | None
    icerik: str | None
    icerik_temiz: str = ""  # TÜRETİLMİŞ — DB'den gelmez, okuma anında üretilir


@dataclass(frozen=True)
class Ihale:
    id: str
    ikn: str
    adi: str | None = None
    idare_adi: str | None = None
    il: str | None = None
    ihale_tarihi: str | None = None
    ihale_turu: str | None = None
    ihale_usulu: str | None = None
    ihale_durumu: str | None = None
    kapsam: str | None = None
    ihale_yeri: str | None = None
    isin_yeri: str | None = None
    ilanlar: list[Ilan] = field(default_factory=list)
    ozellikler: list[Ozellik] = field(default_factory=list)
    okas_kodlari: list[OkasKodu] = field(default_factory=list)

    # ---- Türetilmiş yardımcılar ----

    def okas_metni(self) -> list[str]:
        return [str(o) for o in self.okas_kodlari]

    def ozellik_metni(self) -> str:
        return "\n".join(o.ozellik for o in self.ozellikler if o.ozellik)


# İlan tipi önceliği — ihale açılmadan önceki kapsam tanımı karar için asıl değerli olan.
# Sonuç İlanı ihale BİTTİKTEN sonra yayınlanır (kazanan, sözleşme bedeli); kapsamı içerir
# ama post-hoc bilgiyle önyargı riski taşır, o yüzden daha düşük öncelikli.
#
# ÖLÇÜM NOTU: yerel veritabanı kopyasında aktif (Katılıma Açık) 4.872 ihalenin
# 4.867'sinde SADECE "Ön İlan" var, yalnızca 135'inde "İhale İlanı" var. Yani üretimde
# kararların ~%99'u "Ön İlan" metniyle verilecek. Aşağıdaki sıra doğru çalışıyor
# (İhale İlanı yoksa Ön İlan'a düşüyor) ama temizleyicinin asıl doğrulanması gereken
# şablon "Ön İlan"dır.
ILAN_TIPI_ONCELIGI: dict[str, int] = {
    "İhale İlanı": 0,
    "Ön İlan": 1,
    "Sonuç İlanı": 2,
    "İptal İlanı": 3,
}

# Yalnızca bu durumdaki ihaleler teklif verilebilir durumdadır (plan, deterministik ön-filtre).
AKTIF_IHALE_DURUMU = "İhale İlanı Yayımlanmış, Katılıma Açık"


def en_iyi_ilan(ihale: Ihale) -> Ilan | None:
    """Bir ihalenin duyuruları arasından karar için en bilgilendirici olanı seçer.
    Öncelik: İhale İlanı > Ön İlan > Sonuç İlanı > İptal İlanı.
    Aynı tipten birden fazla varsa en yeni tarihli olan."""
    if not ihale.ilanlar:
        return None
    en_iyi_oncelik = min(ILAN_TIPI_ONCELIGI.get(i.ilan_tipi or "", 99) for i in ihale.ilanlar)
    adaylar = [i for i in ihale.ilanlar if ILAN_TIPI_ONCELIGI.get(i.ilan_tipi or "", 99) == en_iyi_oncelik]
    adaylar.sort(key=lambda i: i.ilan_tarihi or "", reverse=True)
    return adaylar[0]


def kapsam_metni(ihale: Ihale) -> str:
    """Karar ve retrieval için kullanılacak temizlenmiş kapsam metni.
    icerik_temiz boşsa (temizleyicinin desen bulamadığı nadir durum) ham icerik'e düşer —
    sessizce veri kaybetmemek için."""
    ilan = en_iyi_ilan(ihale)
    if ilan is None:
        return ""
    return ilan.icerik_temiz or (ilan.icerik or "")
