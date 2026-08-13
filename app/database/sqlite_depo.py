"""
SQLite arka ucu — bugünkü yerel test için (VerilerEtiketliveri/ekap.db).

Sorgular canlı Postgres şemasıyla (databaseekap.sql) aynı kolon adlarını kullanır;
tek fark `public.` ön eki ve parametre yer tutucusu (? yerine %s). Bu sayede iki
arka ucun döndürdüğü `Ihale` nesneleri ayırt edilemez.

BİLEREK: `icerik_temiz` kolonu bu dosyada HİÇ okunmuyor (ekap.db'de var olmasına
rağmen). Ham `icerik` okunup base.IhaleDeposu._ilani_kur() ile temizleniyor —
üretimde çalışacak kod yolunun aynısı test edilsin diye.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from app.database.base import AKTIF_DURUM, IhaleBulunamadi, IhaleDeposu
from app.domain.models import Ihale

_IHALE_ALANLARI = """
    id, ikn, adi, idare_adi, il, ihale_tarihi, ihale_turu,
    ihale_usulu, ihale_durumu, kapsam, ihale_yeri, isin_yeri
"""


class SqliteIhaleDeposu(IhaleDeposu):
    def __init__(self, db_yolu: str | Path) -> None:
        self.db_yolu = Path(db_yolu)
        if not self.db_yolu.exists():
            raise FileNotFoundError(
                f"{self.db_yolu} bulunamadı. .env içindeki SQLITE_YOLU'nu kontrol edin."
            )

    def _baglan(self) -> sqlite3.Connection:
        # read-only aç — bu katman hiçbir koşulda yazmamalı.
        conn = sqlite3.connect(f"file:{self.db_yolu}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- Tekil ----

    def ikn_ile_getir(self, ikn: str) -> Ihale:
        return self._tekil_getir("ikn", ikn)

    def id_ile_getir(self, tender_id: str) -> Ihale:
        return self._tekil_getir("id", tender_id)

    def _tekil_getir(self, alan: str, deger: str) -> Ihale:
        deger = (deger or "").strip()
        if not deger:
            raise ValueError(f"{alan} boş olamaz.")
        sonuc = self.coklu_getir([deger], alan=alan)
        if not sonuc:
            raise IhaleBulunamadi(deger)
        return sonuc[0]

    # ---- Çoklu (N+1 sorgu yok: 4 toplu sorgu) ----

    def coklu_getir(self, anahtarlar: Sequence[str], *, alan: str = "ikn") -> list[Ihale]:
        if alan not in ("ikn", "id"):
            raise ValueError("alan sadece 'ikn' veya 'id' olabilir.")

        temiz = [str(a).strip() for a in anahtarlar if str(a).strip()]
        if not temiz:
            return []
        tekil = list(dict.fromkeys(temiz))  # sırayı koruyarak tekrarları at

        with self._baglan() as conn:
            yer = ",".join("?" * len(tekil))
            ihale_satirlari = [
                dict(r)
                for r in conn.execute(
                    f"SELECT {_IHALE_ALANLARI} FROM tenders WHERE {alan} IN ({yer})", tekil
                )
            ]
            if not ihale_satirlari:
                return []

            tender_idler = [s["id"] for s in ihale_satirlari]
            ilanlar = self._grupla(conn, tender_idler, "tender_announcements",
                                   "id, tender_id, ilan_tipi, ilan_tarihi, baslik, icerik")
            ozellikler = self._grupla(conn, tender_idler, "tender_characteristics",
                                      "id, tender_id, ozellik")
            okaslar = self._grupla(conn, tender_idler, "tender_okas_codes",
                                   "id, tender_id, kod, ad")

        indeks = {
            s[alan]: self._ihaleyi_kur(
                s, ilanlar.get(s["id"], []), ozellikler.get(s["id"], []), okaslar.get(s["id"], [])
            )
            for s in ihale_satirlari
        }
        return [indeks[a] for a in temiz if a in indeks]

    @staticmethod
    def _grupla(conn, tender_idler: list[str], tablo: str, alanlar: str) -> dict[str, list[dict]]:
        gruplar: dict[str, list[dict]] = {}
        # SQLite'ın değişken sınırı (varsayılan 999) için parçalara böl.
        for i in range(0, len(tender_idler), 500):
            parca = tender_idler[i : i + 500]
            yer = ",".join("?" * len(parca))
            for r in conn.execute(
                f"SELECT {alanlar} FROM {tablo} WHERE tender_id IN ({yer})", parca
            ):
                d = dict(r)
                gruplar.setdefault(d["tender_id"], []).append(d)
        return gruplar

    # ---- Aktif ihaleler ----

    def aktif_ihale_sayisi(self) -> int:
        with self._baglan() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM tenders WHERE ihale_durumu = ?", (AKTIF_DURUM,)
            ).fetchone()[0]

    def aktif_ihale_iknleri(self, limit: int | None = None) -> list[str]:
        if limit is not None and limit <= 0:
            raise ValueError("limit pozitif olmalıdır.")
        with self._baglan() as conn:
            sorgu = "SELECT ikn FROM tenders WHERE ihale_durumu = ? ORDER BY ikn"
            par: list = [AKTIF_DURUM]
            if limit is not None:
                sorgu += " LIMIT ?"
                par.append(limit)
            return [str(r["ikn"]) for r in conn.execute(sorgu, par)]

    def aktif_iknler_oncelikli(self, tanim, limit: int | None = None) -> list[tuple[str, bool]]:
        """(ikn, filtreyi_gecti) — filtreyi geçenler ÖNCE. ELEMEZ.

        Postgres tarafı bunu tek SQL'de yapıyor. SQLite'ta `ILIKE ANY(array)`
        yok; iki hafif sütun (ikn, adi) + OKAS kodları çekilip eşleşme Python'da
        yapılıyor. `sql_filtre.eslesme_var_mi` her iki arka uçta da AYNI mantığı
        uygular — testle bağlı.
        """
        from app.decision.sql_filtre import eslesme_var_mi

        with self._baglan() as conn:
            satirlar = list(conn.execute(
                "SELECT id, ikn, adi FROM tenders WHERE ihale_durumu = ? ORDER BY ikn",
                [AKTIF_DURUM],
            ))
            okaslar: dict[str, list[str]] = {}
            for r in conn.execute(
                "SELECT o.tender_id, o.kod FROM tender_okas_codes o "
                "JOIN tenders t ON t.id = o.tender_id WHERE t.ihale_durumu = ?",
                [AKTIF_DURUM],
            ):
                okaslar.setdefault(str(r["tender_id"]), []).append(str(r["kod"]))

        cift = [
            (str(r["ikn"]), bool(tanim) and eslesme_var_mi(tanim, r["adi"], okaslar.get(str(r["id"]), [])))
            for r in satirlar
        ]
        cift.sort(key=lambda x: (not x[1], x[0]))
        return cift[:limit] if limit else cift

    def filtreli_iknler(self, tanim, *, sadece_aktif: bool = True) -> list[str]:
        """SQL ön filtresini geçen İKN'ler. `sadece_aktif=False` -> TÜM tablo.

        Gerekçe postgres_depo'daki ikizinde. Burada eşleşme Python'da yapılıyor;
        `sql_filtre.eslesme_var_mi` iki arka uçta da aynı mantığı uygular.
        """
        from app.decision.sql_filtre import eslesme_var_mi

        if not tanim:
            return self.aktif_ihale_iknleri() if sadece_aktif else []

        with self._baglan() as conn:
            if sadece_aktif:
                satirlar = list(conn.execute(
                    "SELECT id, ikn, adi FROM tenders WHERE ihale_durumu = ? ORDER BY ikn",
                    [AKTIF_DURUM],
                ))
            else:
                satirlar = list(conn.execute(
                    "SELECT id, ikn, adi FROM tenders ORDER BY ikn"))
            okaslar: dict[str, list[str]] = {}
            for r in conn.execute("SELECT tender_id, kod FROM tender_okas_codes"):
                okaslar.setdefault(str(r["tender_id"]), []).append(str(r["kod"]))

        return [
            str(r["ikn"]) for r in satirlar
            if eslesme_var_mi(tanim, r["adi"], okaslar.get(str(r["id"]), []))
        ]

    def aktif_ihaleler(self, limit: int | None = None) -> list[Ihale]:
        """Teklife açık ihaleleri döndürür.

        NOT: Postgres arka ucu 'ihale_tarihi geçmemiş' koşulunu da SQL'e koyuyor.
        SQLite'ta tarih `DD.MM.YYYY HH:MM` metni olarak tutulduğu ve yerel kopya
        donmuş bir anlık görüntü olduğu için burada tarih filtresi UYGULANMAZ —
        tarih kontrolü ortak ön-filtrede (app/decision/on_filtre.py) yapılır, iki
        arka uçta da aynı davranışı görürsünüz.
        """
        return self.coklu_getir(self.aktif_ihale_iknleri(limit), alan="ikn")
