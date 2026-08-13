"""
Veri erişim arayüzü — uygulamanın geri kalanı SADECE bunu görür.

NEDEN BU KATMAN VAR: Sistem hem yerel bir `ekap.db` (SQLite) kopyasıyla hem de canlı
bir Postgres sunucusuyla çalışabilmelidir. İkisi de aşağıdaki `IhaleDeposu` arayüzünü
uygular; geçiş yapmak için `.env` içindeki `DATA_BACKEND` satırını değiştirmek yeter,
başka hiçbir kod değişmez. Karar veren kod hangisinin bağlı olduğunu asla bilmez.

ORTAK DAVRANIŞ (her iki arka uç için burada, tek yerde tanımlı):
Temizlenmiş metni hazır tutan `icerik_temiz` alanı canlı Postgres'te YOKTUR. Bu yüzden
her iki arka uç da ham `icerik`'i okur ve `_ilani_kur()` ile OKUMA ANINDA temizler.
SQLite kopyasında böyle hazır bir kolon bulunsa bile BİLEREK KULLANILMAZ: kullanılsaydı
yerelde test edilen kod yolu, üretimde çalışan kod yolundan farklı olurdu ve testler
gerçeği ölçmezdi. İki yolun aynı sonucu verdiği 96.218 kayıtta doğrulanmıştır.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from app.domain.models import AKTIF_IHALE_DURUMU, Ihale, Ilan, OkasKodu, Ozellik
from app.text.ilan_temizleyici import ilan_metnini_temizle


class IhaleBulunamadi(LookupError):
    def __init__(self, anahtar: str) -> None:
        self.anahtar = anahtar
        super().__init__(f"İhale bulunamadı: {anahtar}")


class IhaleDeposu(ABC):
    """İhale okuma arayüzü. SADECE OKUMA — hiçbir uygulama bu katmandan yazma yapmaz."""

    @abstractmethod
    def ikn_ile_getir(self, ikn: str) -> Ihale: ...

    @abstractmethod
    def id_ile_getir(self, tender_id: str) -> Ihale: ...

    @abstractmethod
    def coklu_getir(self, anahtarlar: Sequence[str], *, alan: str = "ikn") -> list[Ihale]: ...

    @abstractmethod
    def aktif_ihale_iknleri(self, limit: int | None = None) -> list[str]:
        """Sadece İKN listesi — ilan/OKAS gövdesi ÇEKİLMEZ.

        `aktif_ihaleler()` 4.872 ihaleyi ilanlarıyla belleğe alıyor (~30MB). Rastgele
        örnekleme gibi "önce seç, sonra getir" akışlarında bu israf; bu metot
        seçim adımını ucuzlatır. `coklu_getir` ile birlikte kullanılır.
        """
        ...

    @abstractmethod
    def aktif_ihaleler(self, limit: int | None = None) -> list[Ihale]: ...

    @abstractmethod
    def aktif_ihale_sayisi(self) -> int: ...

    # ---- Her iki arka ucun paylaştığı kurma mantığı ----

    @staticmethod
    def _ilani_kur(satir: dict[str, Any]) -> Ilan:
        """Ham DB satırından Ilan üretir ve icerik_temiz'i OKUMA ANINDA hesaplar."""
        ham = satir.get("icerik")
        return Ilan(
            id=str(satir.get("id", "")),
            tender_id=str(satir.get("tender_id", "")),
            ilan_tipi=satir.get("ilan_tipi"),
            ilan_tarihi=satir.get("ilan_tarihi"),
            baslik=satir.get("baslik"),
            icerik=ham,
            icerik_temiz=ilan_metnini_temizle(ham),
        )

    @classmethod
    def _ihaleyi_kur(
        cls,
        satir: dict[str, Any],
        ilan_satirlari: Sequence[dict[str, Any]] = (),
        ozellik_satirlari: Sequence[dict[str, Any]] = (),
        okas_satirlari: Sequence[dict[str, Any]] = (),
    ) -> Ihale:
        return Ihale(
            id=str(satir["id"]),
            ikn=str(satir["ikn"]),
            adi=satir.get("adi"),
            idare_adi=satir.get("idare_adi"),
            il=satir.get("il"),
            ihale_tarihi=satir.get("ihale_tarihi"),
            ihale_turu=satir.get("ihale_turu"),
            ihale_usulu=satir.get("ihale_usulu"),
            ihale_durumu=satir.get("ihale_durumu"),
            kapsam=satir.get("kapsam"),
            ihale_yeri=satir.get("ihale_yeri"),
            isin_yeri=satir.get("isin_yeri"),
            ilanlar=[cls._ilani_kur(s) for s in ilan_satirlari],
            ozellikler=[Ozellik(ozellik=s["ozellik"]) for s in ozellik_satirlari if s.get("ozellik")],
            okas_kodlari=[
                OkasKodu(kod=str(s.get("kod") or ""), ad=str(s.get("ad") or "")) for s in okas_satirlari
            ],
        )


# Aktif ihale tanımı — her iki arka uçta da aynı olmalı.
# Not: Postgres'te ayrıca `ihale_tarihi` geçmemiş olma koşulu da SQL'e konuyor
# doğrudan SQL'e konur. SQLite'ta tarih metni aynı formatta (DD.MM.YYYY HH:MM)
# olduğu için aynı kontrol Python tarafında yapılır — bkz. app/decision/on_filtre.py.
AKTIF_DURUM = AKTIF_IHALE_DURUMU


def depo_olustur(ayarlar=None) -> IhaleDeposu:
    """Fabrika: .env'deki DATA_BACKEND'e göre doğru arka ucu döndürür.

    Bağımlılıklar TEMBEL yüklenir — psycopg kurulu olmasa da sqlite arka ucu çalışır
    (ve tersi). Böylece bugün Postgres sürücüsü olmadan geliştirme yapılabiliyor.
    """
    if ayarlar is None:
        from app.config.settings import ayarlari_al

        ayarlar = ayarlari_al()

    if ayarlar.data_backend == "sqlite":
        from app.database.sqlite_depo import SqliteIhaleDeposu

        return SqliteIhaleDeposu(ayarlar.sqlite_yolu)

    if ayarlar.data_backend == "postgres":
        from app.database.postgres_depo import PostgresIhaleDeposu

        return PostgresIhaleDeposu(
            ayarlar.postgres_baglanti_dizesi(),
            max_deneme=ayarlar.database_max_deneme,
        )

    raise ValueError(f"Bilinmeyen DATA_BACKEND: {ayarlar.data_backend}")
