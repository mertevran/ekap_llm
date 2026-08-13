"""
Karar sonuçları deposu — tarama çıktısının yazıldığı yer.

=============================================================================
NEDEN AYRI BİR TABLO
=============================================================================
Canlı şemadaki `llm_rag.tender_index_state` tablosunda boş bir `classification`
kolonu bulunuyor. Yani yer var — ama o tablo BAŞKA bir sürece, indeksleme hattına
aittir; `source_hash`, `chunk_count`, `index_status` gibi alanlarını o süreç yönetir.
Oraya yazmak iki bağımsız hattı birbirine bağlar ve ikisini de kırılgan hâle getirir.

Bu yüzden sonuçlar KENDİ tablomuza yazılır ve o tabloya `tender_id` + `source_hash`
ile bağlanır. `classification` kolonu ileride doldurulacaksa kaynağı bu tablo olur.

=============================================================================
İKİ ARKA UÇ
=============================================================================
Projenin veri katmanındaki desenin aynısı: Postgres varsa oraya, yoksa yerel
SQLite'a. Şema aynı olduğu için sonradan aktarmak tek sorgu. Yazma yetkisi
gelmesini beklemeden taramaya başlanabilir.

DİKKAT — bu modül YAZMA yapar. `app/database/base.py` altındaki `IhaleDeposu`
salt okumadır ve öyle kalıyor; ihale verisine hiçbir şey yazılmıyor. Bu modül
yalnızca KENDİ ürettiğimiz karar satırlarını yazar.

=============================================================================
İHALE BAŞINA TEK SATIR — ÜZERİNE YAZILIR
=============================================================================
`tender_id` UNIQUE'tir. Aynı ihale yeniden tarandığında yeni satır EKLENMEZ,
mevcut satır GÜNCELLENİR. Tablo bir iş kuyruğu; her ihalenin tek ve güncel bir
kararı olmalı.

İNSAN ALANLARI ÜZERİNE YAZILMAZ. `insan_karar`, `insan_notu`, `inceleyen` ve
`inceleme_tarihi` güncellemede KORUNUR — bir ihaleyi etiketleyip sonra yeniden
tarayınca o emek kaybolmaz. Makine kararı tazelenir, insan kararı yerinde kalır.

Ölçüm geçmişi ayrı yerde: `Sonuclar/` altındaki versiyonlu koşu çıktıları.

`insan_karar` sözlüğü `public.tenders.takip_durumu` ile UYUMLU tutuldu —
canlıda kullanılan değerler: 'alınması öneriliyor', 'alınması önerilmiyor',
'inceleniyor'. Yeni bir sözlük uydurmak iki sistemi farklı dile böler.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

TABLO_ADI = "tender_scope_decisions"
SEMA_ADI = "llm_rag"

# `public.tenders.takip_durumu` içinde canlıda gözlenen değerler.
INSAN_KARARLARI = ("alınması öneriliyor", "alınması önerilmiyor", "inceleniyor")

_PG_SEMA = f"""
CREATE SCHEMA IF NOT EXISTS {SEMA_ADI};

CREATE TABLE IF NOT EXISTS {SEMA_ADI}.{TABLO_ADI} (
    id                      bigserial PRIMARY KEY,

    -- ihale kimliği
    tender_id               text NOT NULL,
    ikn                     text NOT NULL,
    adi                     text,
    idare_adi               text,
    il                      text,
    ihale_turu              text,
    ihale_tarihi            text,

    -- AŞAMA 1 kararı
    karar                   text NOT NULL,
    ilgi_skoru              numeric(6,4),
    belirsiz_tipi           text,
    gerekce                 text,
    eslesen_paket           text,
    eslesen_okas            jsonb,

    -- retrieval izlenebilirliği: eşiği sonradan LLM'i tekrar koşmadan
    -- yeniden değerlendirebilmek için ŞART
    retrieval_en_ust_skor   numeric(6,4),
    kullanilan_paketler     jsonb,

    -- karar hangi kanıta dayandı
    ilan_id                 text,
    ilan_tipi               text,
    kapsam_metni_uzunlugu   integer,
    source_hash             text,

    -- üretim bağlamı
    model                   text,
    konfigurasyon           jsonb,
    on_filtre_kurali        text,
    kod_uyarilari           jsonb,
    sure_sn                 numeric(9,2),
    makine                  text,
    olusturuldu             timestamptz NOT NULL DEFAULT now(),

    -- insan geri bildirimi (takip_durumu sözlüğüyle uyumlu)
    insan_karar             text,
    insan_notu              text,
    inceleyen               text,
    inceleme_tarihi         timestamptz
);

ALTER TABLE {SEMA_ADI}.{TABLO_ADI} ADD COLUMN IF NOT EXISTS belirsiz_tipi text;

CREATE INDEX IF NOT EXISTS {TABLO_ADI}_ikn_idx
    ON {SEMA_ADI}.{TABLO_ADI} (ikn);
CREATE INDEX IF NOT EXISTS {TABLO_ADI}_karar_idx
    ON {SEMA_ADI}.{TABLO_ADI} (karar);
CREATE INDEX IF NOT EXISTS {TABLO_ADI}_insan_idx
    ON {SEMA_ADI}.{TABLO_ADI} (insan_karar);
CREATE INDEX IF NOT EXISTS {TABLO_ADI}_tarih_idx
    ON {SEMA_ADI}.{TABLO_ADI} (olusturuldu DESC);
"""

_SQLITE_SEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLO_ADI} (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tender_id               TEXT NOT NULL,
    ikn                     TEXT NOT NULL,
    adi                     TEXT,
    idare_adi               TEXT,
    il                      TEXT,
    ihale_turu              TEXT,
    ihale_tarihi            TEXT,
    karar                   TEXT NOT NULL,
    ilgi_skoru              REAL,
    belirsiz_tipi           TEXT,
    gerekce                 TEXT,
    eslesen_paket           TEXT,
    eslesen_okas            TEXT,
    retrieval_en_ust_skor   REAL,
    kullanilan_paketler     TEXT,
    ilan_id                 TEXT,
    ilan_tipi               TEXT,
    kapsam_metni_uzunlugu   INTEGER,
    source_hash             TEXT,
    model                   TEXT,
    konfigurasyon           TEXT,
    on_filtre_kurali        TEXT,
    kod_uyarilari           TEXT,
    sure_sn                 REAL,
    makine                  TEXT,
    olusturuldu             TEXT NOT NULL,
    insan_karar             TEXT,
    insan_notu              TEXT,
    inceleyen               TEXT,
    inceleme_tarihi         TEXT
);
CREATE INDEX IF NOT EXISTS {TABLO_ADI}_ikn_idx    ON {TABLO_ADI} (ikn);
CREATE INDEX IF NOT EXISTS {TABLO_ADI}_karar_idx  ON {TABLO_ADI} (karar);
CREATE INDEX IF NOT EXISTS {TABLO_ADI}_insan_idx  ON {TABLO_ADI} (insan_karar);
"""

# Yazma sırasına birebir uyan alan listesi — iki arka uç aynı sırayı kullanır.
ALANLAR = (
    "tender_id", "ikn", "adi", "idare_adi", "il", "ihale_turu", "ihale_tarihi",
    "karar", "ilgi_skoru", "belirsiz_tipi", "gerekce", "eslesen_paket", "eslesen_okas",
    "retrieval_en_ust_skor", "kullanilan_paketler",
    "ilan_id", "ilan_tipi", "kapsam_metni_uzunlugu", "source_hash",
    "model", "konfigurasyon", "on_filtre_kurali", "kod_uyarilari",
    "sure_sn", "makine", "olusturuldu",
)
# UNIQUE indeks ANA ŞEMADAN AYRI. Sebep: `tabloyu_hazirla()` her koşuda çağrılıyor
# ve tabloda tekrar varsa `CREATE UNIQUE INDEX` PATLAR. Ayrı tutulup hatası
# yakalanıyor; kullanıcıya "önce tekrarları temizle" deniyor.
_PG_UNIQUE = (f"CREATE UNIQUE INDEX IF NOT EXISTS {TABLO_ADI}_tender_uidx "
              f"ON {SEMA_ADI}.{TABLO_ADI} (tender_id)")
_SQLITE_UNIQUE = (f"CREATE UNIQUE INDEX IF NOT EXISTS {TABLO_ADI}_tender_uidx "
                  f"ON {TABLO_ADI} (tender_id)")

JSON_ALANLARI = frozenset({"eslesen_okas", "kullanilan_paketler", "konfigurasyon", "kod_uyarilari"})

# Üzerine yazarken DOKUNULMAYAN alanlar. `tender_id` çakışma anahtarı; insan
# alanları ise elle girilmiş emek — makine kararı tazelenirken silinmemeli.
KORUNAN_ALANLAR = ("tender_id", "insan_karar", "insan_notu", "inceleyen", "inceleme_tarihi")
GUNCELLENEN = tuple(a for a in ALANLAR if a not in KORUNAN_ALANLAR)


@dataclass
class KararSatiri:
    tender_id: str
    ikn: str
    karar: str
    adi: str | None = None
    idare_adi: str | None = None
    il: str | None = None
    ihale_turu: str | None = None
    ihale_tarihi: str | None = None
    ilgi_skoru: float | None = None
    belirsiz_tipi: str | None = None
    gerekce: str | None = None
    eslesen_paket: str | None = None
    eslesen_okas: list = field(default_factory=list)
    retrieval_en_ust_skor: float | None = None
    kullanilan_paketler: list = field(default_factory=list)
    ilan_id: str | None = None
    ilan_tipi: str | None = None
    kapsam_metni_uzunlugu: int | None = None
    source_hash: str | None = None
    model: str | None = None
    konfigurasyon: dict = field(default_factory=dict)
    on_filtre_kurali: str | None = None
    kod_uyarilari: list = field(default_factory=list)
    sure_sn: float | None = None
    makine: str | None = None
    olusturuldu: str | None = None

    def degerler(self, *, json_metne_cevir: bool) -> list[Any]:
        if not self.olusturuldu:
            self.olusturuldu = datetime.now(timezone.utc).isoformat(timespec="seconds")
        out: list[Any] = []
        for alan in ALANLAR:
            v = getattr(self, alan)
            if alan in JSON_ALANLARI:
                v = json.dumps(v, ensure_ascii=False) if json_metne_cevir else json.dumps(v, ensure_ascii=False)
            out.append(v)
        return out


class KararDeposu:
    """Ortak arayüz. `olustur()` fabrikası doğru arka ucu seçer."""

    def tabloyu_hazirla(self) -> None: raise NotImplementedError
    def yaz(self, satirlar: list[KararSatiri]) -> int: raise NotImplementedError
    def islenmis_tender_idler(self) -> set[str]: raise NotImplementedError

    def islenmis_iknler(self) -> set[str]:
        """Taranmış İKN'ler — SEÇİM ADIMINDA gerekli.

        `islenmis_tender_idler()` yetmiyor: aday listesi İKN'lerden oluşuyor ve
        tender_id'yi öğrenmek için ihaleyi çekmek gerekiyor. Seçimi İKN üzerinden
        yapmazsak her partide aynı ilk N İKN seçilir, hepsi "zaten taranmış" diye
        atlanır ve 0 satır yazılır.
        """
        raise NotImplementedError
    def sayimlar(self) -> dict[str, int]: raise NotImplementedError
    def incelenmemisler(self, limit: int = 20) -> list[dict]: raise NotImplementedError

    def son_taranmislar(self, limit: int = 5) -> list[dict]:
        """En son taranan N ihalenin GEÇERLİ (en yeni) kararı.

        Konfigürasyon değiştikten sonra "aynı ihalelerde ne değişti" sorusunu
        cevaplamak için. Tabloda tender_id üzerinde UNIQUE olmadığı için yeniden
        tarama eski satırı silmez — öncesi/sonrası karşılaştırılabilir.
        """
        raise NotImplementedError
    def insan_karari_yaz(self, ikn: str, karar: str, not_: str = "", inceleyen: str = "") -> int:
        raise NotImplementedError

    def son_satirlar(self, limit: int) -> list[dict]:
        """En son EKLENEN N satır (tekilleştirilmemiş — silme önizlemesi için)."""
        raise NotImplementedError

    def son_satirlari_sil(self, limit: int) -> int:
        """En son eklenen N satırı siler. SADECE kendi tablomuz; ihale verisine dokunmaz."""
        raise NotImplementedError

    def tekrarlari_temizle(self) -> tuple[int, int]:
        """Aynı `tender_id`'nin eski satırlarını siler, EN YENİSİNİ bırakır.

        UNIQUE indeks eklenmeden önce mevcut tekrarları temizlemek için.
        İnsan etiketi eski satırdaysa YENİ satıra taşınır — silinen satırla
        birlikte kaybolmasın.

        Dönüş: (silinen satır, taşınan insan etiketi)
        """
        raise NotImplementedError

    def kapat(self) -> None: ...

    @property
    def nerede(self) -> str: raise NotImplementedError


class PostgresKararDeposu(KararDeposu):
    def __init__(self, baglanti_dizesi: str, max_deneme: int = 3) -> None:
        self._dsn = baglanti_dizesi
        self._max_deneme = max(1, int(max_deneme))

    @property
    def nerede(self) -> str:
        return f"postgres:{SEMA_ADI}.{TABLO_ADI}"

    def _baglan(self):
        """Geçici bağlantı hatalarında yeniden dener.

        BURADA ÖZELLİKLE ÖNEMLİ: `olustur()` bağlantı kurulamazsa SESSİZCE yerel
        SQLite'a düşüyor. Yani tek bir geçici ağ dalgalanması, o partinin tüm
        kararlarının ve insan etiketlerinin YANLIŞ YERE yazılmasına yol açıyor —
        ve kullanıcı bunu ancak `--durum` ile iki depoyu karşılaştırırsa görüyor.
        Yeniden deneme, geri düşmeyi gerçekten kalıcı arızalara saklıyor.
        """
        import time

        import psycopg

        son_hata: Exception | None = None
        for deneme in range(1, self._max_deneme + 1):
            try:
                return psycopg.connect(self._dsn)
            except (psycopg.OperationalError, psycopg.errors.ConnectionTimeout) as e:
                son_hata = e
                if deneme < self._max_deneme:
                    time.sleep(2 ** (deneme - 1))
        raise son_hata  # type: ignore[misc]

    def tabloyu_hazirla(self) -> None:
        with self._baglan() as conn:
            with conn.cursor() as cur:
                cur.execute(_PG_SEMA)
            conn.commit()
        self._unique_dene()

    def _unique_dene(self) -> None:
        """UNIQUE indeksi kurmayı dener; tekrar varsa açık uyarı verir."""
        try:
            with self._baglan() as conn:
                with conn.cursor() as cur:
                    cur.execute(_PG_UNIQUE)
                conn.commit()
        except Exception as e:  # noqa: BLE001
            print(f"  ! tender_id UNIQUE indeksi kurulamadı: {type(e).__name__}")
            print(f"  ! Tabloda aynı ihaleden birden fazla satır var. Üzerine yazma "
                  f"(UPSERT) çalışmaz.")
            print(f"  ! Çözüm:  python scripts/tara_ve_kaydet.py --tekrarlari-temizle")

    def yaz(self, satirlar: list[KararSatiri]) -> int:
        if not satirlar:
            return 0
        yer_tutucu = ", ".join(["%s"] * len(ALANLAR))
        guncelle = ", ".join(f"{a} = EXCLUDED.{a}" for a in GUNCELLENEN)
        sorgu = (
            f"INSERT INTO {SEMA_ADI}.{TABLO_ADI} ({', '.join(ALANLAR)}) "
            f"VALUES ({yer_tutucu}) "
            f"ON CONFLICT (tender_id) DO UPDATE SET {guncelle}"
        )
        with self._baglan() as conn:
            with conn.cursor() as cur:
                cur.executemany(sorgu, [s.degerler(json_metne_cevir=True) for s in satirlar])
            conn.commit()
        return len(satirlar)

    def islenmis_tender_idler(self) -> set[str]:
        return self._tekil_sutun("tender_id")

    def islenmis_iknler(self) -> set[str]:
        return self._tekil_sutun("ikn")

    def _tekil_sutun(self, sutun: str) -> set[str]:
        with self._baglan() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT DISTINCT {sutun} FROM {SEMA_ADI}.{TABLO_ADI}")
                return {str(r[0]) for r in cur.fetchall()}

    def sayimlar(self) -> dict[str, int]:
        with self._baglan() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT karar, COUNT(*) FROM {SEMA_ADI}.{TABLO_ADI} GROUP BY karar")
                d = {str(k): int(n) for k, n in cur.fetchall()}
                cur.execute(
                    f"SELECT COUNT(*) FROM {SEMA_ADI}.{TABLO_ADI} WHERE insan_karar IS NOT NULL"
                )
                d["_insan_etiketli"] = int(cur.fetchone()[0])
                cur.execute(f"SELECT COUNT(DISTINCT tender_id) FROM {SEMA_ADI}.{TABLO_ADI}")
                d["_tekil_ihale"] = int(cur.fetchone()[0])
        return d

    def incelenmemisler(self, limit: int = 20) -> list[dict]:
        from psycopg.rows import dict_row
        with self._baglan() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(f"""
                    SELECT DISTINCT ON (tender_id)
                           ikn, adi, karar, ilgi_skoru, retrieval_en_ust_skor,
                           belirsiz_tipi, eslesen_paket, gerekce, ilan_tipi, olusturuldu
                    FROM {SEMA_ADI}.{TABLO_ADI}
                    WHERE insan_karar IS NULL
                    ORDER BY tender_id, olusturuldu DESC, id DESC, id DESC
                    LIMIT %s
                """, (limit,))
                return [dict(r) for r in cur.fetchall()]

    def tekrarlari_temizle(self) -> tuple[int, int]:
        with self._baglan() as conn:
            with conn.cursor() as cur:
                # 1) insan etiketi eski satırdaysa en yeni satıra taşı
                cur.execute(f"""
                    WITH yeni AS (
                        SELECT DISTINCT ON (tender_id) id, tender_id
                        FROM {SEMA_ADI}.{TABLO_ADI} ORDER BY tender_id, id DESC
                    ), eski_etiket AS (
                        SELECT DISTINCT ON (t.tender_id)
                               t.tender_id, t.insan_karar, t.insan_notu,
                               t.inceleyen, t.inceleme_tarihi
                        FROM {SEMA_ADI}.{TABLO_ADI} t
                        WHERE t.insan_karar IS NOT NULL
                        ORDER BY t.tender_id, t.id DESC
                    )
                    UPDATE {SEMA_ADI}.{TABLO_ADI} h
                    SET insan_karar = e.insan_karar, insan_notu = e.insan_notu,
                        inceleyen = e.inceleyen, inceleme_tarihi = e.inceleme_tarihi
                    FROM yeni y JOIN eski_etiket e ON e.tender_id = y.tender_id
                    WHERE h.id = y.id AND h.insan_karar IS NULL
                """)
                tasinan = cur.rowcount
                # 2) her tender_id icin en yeni ID disindakileri sil
                cur.execute(f"""
                    DELETE FROM {SEMA_ADI}.{TABLO_ADI}
                    WHERE id NOT IN (
                        SELECT MAX(id) FROM {SEMA_ADI}.{TABLO_ADI} GROUP BY tender_id
                    )
                """)
                silinen = cur.rowcount
                # 3) artik UNIQUE indeks kurulabilir
                cur.execute(_PG_UNIQUE)
            conn.commit()
        return silinen, tasinan

    def son_satirlar(self, limit: int) -> list[dict]:
        from psycopg.rows import dict_row
        with self._baglan() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(f"""
                    SELECT id, ikn, adi, karar, ilgi_skoru, insan_karar, olusturuldu
                    FROM {SEMA_ADI}.{TABLO_ADI} ORDER BY id DESC LIMIT %s
                """, (limit,))
                return [dict(r) for r in cur.fetchall()]

    def son_satirlari_sil(self, limit: int) -> int:
        with self._baglan() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    DELETE FROM {SEMA_ADI}.{TABLO_ADI}
                    WHERE id IN (SELECT id FROM {SEMA_ADI}.{TABLO_ADI}
                                 ORDER BY id DESC LIMIT %s)
                """, (limit,))
                n = cur.rowcount
            conn.commit()
        return n

    def son_taranmislar(self, limit: int = 5) -> list[dict]:
        from psycopg.rows import dict_row
        with self._baglan() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(f"""
                    SELECT DISTINCT ON (tender_id)
                           ikn, adi, karar, ilgi_skoru, belirsiz_tipi,
                           retrieval_en_ust_skor, eslesen_paket, olusturuldu
                    FROM {SEMA_ADI}.{TABLO_ADI}
                    ORDER BY tender_id, olusturuldu DESC, id DESC
                    LIMIT %s
                """, (limit,))
                return [dict(r) for r in cur.fetchall()]

    def insan_karari_yaz(self, ikn: str, karar: str, not_: str = "", inceleyen: str = "") -> int:
        with self._baglan() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    UPDATE {SEMA_ADI}.{TABLO_ADI}
                    SET insan_karar = %s, insan_notu = %s, inceleyen = %s, inceleme_tarihi = now()
                    WHERE ikn = %s
                """, (karar, not_ or None, inceleyen or None, ikn))
                n = cur.rowcount
            conn.commit()
        return n


class SqliteKararDeposu(KararDeposu):
    def __init__(self, yol: Path) -> None:
        self._yol = Path(yol)
        self._yol.parent.mkdir(parents=True, exist_ok=True)

    @property
    def nerede(self) -> str:
        return f"sqlite:{self._yol}"

    def _baglan(self):
        conn = sqlite3.connect(self._yol)
        conn.row_factory = sqlite3.Row
        return conn

    def tabloyu_hazirla(self) -> None:
        with self._baglan() as conn:
            conn.executescript(_SQLITE_SEMA)
            # SQLite'ta "ADD COLUMN IF NOT EXISTS" yok — mevcut tabloya sonradan
            # eklenen alanlar için tek tek denenir.
            mevcut = {r[1] for r in conn.execute(f"PRAGMA table_info({TABLO_ADI})")}
            for ad, tur in (("belirsiz_tipi", "TEXT"),):
                if ad not in mevcut:
                    conn.execute(f"ALTER TABLE {TABLO_ADI} ADD COLUMN {ad} {tur}")
            try:
                conn.execute(_SQLITE_UNIQUE)
            except sqlite3.IntegrityError:
                print("  ! tender_id UNIQUE indeksi kurulamadı — tabloda tekrar var.")
                print("  ! Çözüm:  python scripts/tara_ve_kaydet.py --tekrarlari-temizle")

    def yaz(self, satirlar: list[KararSatiri]) -> int:
        if not satirlar:
            return 0
        guncelle = ", ".join(f"{a} = excluded.{a}" for a in GUNCELLENEN)
        sorgu = (
            f"INSERT INTO {TABLO_ADI} ({', '.join(ALANLAR)}) "
            f"VALUES ({', '.join(['?'] * len(ALANLAR))}) "
            f"ON CONFLICT(tender_id) DO UPDATE SET {guncelle}"
        )
        with self._baglan() as conn:
            conn.executemany(sorgu, [s.degerler(json_metne_cevir=True) for s in satirlar])
        return len(satirlar)

    def islenmis_tender_idler(self) -> set[str]:
        return self._tekil_sutun("tender_id")

    def islenmis_iknler(self) -> set[str]:
        return self._tekil_sutun("ikn")

    def _tekil_sutun(self, sutun: str) -> set[str]:
        with self._baglan() as conn:
            return {str(r[0]) for r in conn.execute(f"SELECT DISTINCT {sutun} FROM {TABLO_ADI}")}

    def sayimlar(self) -> dict[str, int]:
        with self._baglan() as conn:
            d = {str(r[0]): int(r[1]) for r in
                 conn.execute(f"SELECT karar, COUNT(*) FROM {TABLO_ADI} GROUP BY karar")}
            d["_insan_etiketli"] = int(conn.execute(
                f"SELECT COUNT(*) FROM {TABLO_ADI} WHERE insan_karar IS NOT NULL").fetchone()[0])
            d["_tekil_ihale"] = int(conn.execute(
                f"SELECT COUNT(DISTINCT tender_id) FROM {TABLO_ADI}").fetchone()[0])
        return d

    def incelenmemisler(self, limit: int = 20) -> list[dict]:
        with self._baglan() as conn:
            satirlar = conn.execute(f"""
                SELECT ikn, adi, karar, ilgi_skoru, retrieval_en_ust_skor,
                       eslesen_paket, gerekce, ilan_tipi, olusturuldu
                FROM {TABLO_ADI} t
                WHERE insan_karar IS NULL
                  AND id = (SELECT MAX(id) FROM {TABLO_ADI} WHERE tender_id = t.tender_id)
                ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in satirlar]

    def tekrarlari_temizle(self) -> tuple[int, int]:
        with self._baglan() as conn:
            im = conn.execute(f"""
                UPDATE {TABLO_ADI} SET
                  insan_karar     = (SELECT e.insan_karar    FROM {TABLO_ADI} e
                                     WHERE e.tender_id = {TABLO_ADI}.tender_id
                                       AND e.insan_karar IS NOT NULL ORDER BY e.id DESC LIMIT 1),
                  insan_notu      = (SELECT e.insan_notu     FROM {TABLO_ADI} e
                                     WHERE e.tender_id = {TABLO_ADI}.tender_id
                                       AND e.insan_karar IS NOT NULL ORDER BY e.id DESC LIMIT 1),
                  inceleyen       = (SELECT e.inceleyen      FROM {TABLO_ADI} e
                                     WHERE e.tender_id = {TABLO_ADI}.tender_id
                                       AND e.insan_karar IS NOT NULL ORDER BY e.id DESC LIMIT 1),
                  inceleme_tarihi = (SELECT e.inceleme_tarihi FROM {TABLO_ADI} e
                                     WHERE e.tender_id = {TABLO_ADI}.tender_id
                                       AND e.insan_karar IS NOT NULL ORDER BY e.id DESC LIMIT 1)
                WHERE insan_karar IS NULL
                  AND id IN (SELECT MAX(id) FROM {TABLO_ADI} GROUP BY tender_id)
                  AND EXISTS (SELECT 1 FROM {TABLO_ADI} e
                              WHERE e.tender_id = {TABLO_ADI}.tender_id
                                AND e.insan_karar IS NOT NULL)
            """)
            tasinan = im.rowcount
            im = conn.execute(
                f"DELETE FROM {TABLO_ADI} WHERE id NOT IN "
                f"(SELECT MAX(id) FROM {TABLO_ADI} GROUP BY tender_id)")
            silinen = im.rowcount
            conn.execute(_SQLITE_UNIQUE)
            return silinen, tasinan

    def son_satirlar(self, limit: int) -> list[dict]:
        with self._baglan() as conn:
            return [dict(r) for r in conn.execute(
                f"""SELECT id, ikn, adi, karar, ilgi_skoru, insan_karar, olusturuldu
                    FROM {TABLO_ADI} ORDER BY id DESC LIMIT ?""", (limit,))]

    def son_satirlari_sil(self, limit: int) -> int:
        with self._baglan() as conn:
            im = conn.execute(
                f"""DELETE FROM {TABLO_ADI} WHERE id IN
                    (SELECT id FROM {TABLO_ADI} ORDER BY id DESC LIMIT ?)""", (limit,))
            return im.rowcount

    def son_taranmislar(self, limit: int = 5) -> list[dict]:
        with self._baglan() as conn:
            # TEKİLLEŞTİRME `id` ÜZERİNDEN: `olusturuldu` saniye hassasiyetinde ve
            # aynı partide yazılan satırlar aynı damgayı taşıyor — MAX(olusturuldu)
            # ile aynı ihalenin iki sürümü birden dönüyordu.
            satirlar = conn.execute(f"""
                SELECT ikn, adi, karar, ilgi_skoru, belirsiz_tipi,
                       retrieval_en_ust_skor, eslesen_paket, olusturuldu
                FROM {TABLO_ADI} t
                WHERE id = (SELECT MAX(id) FROM {TABLO_ADI} WHERE tender_id = t.tender_id)
                ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in satirlar]

    def insan_karari_yaz(self, ikn: str, karar: str, not_: str = "", inceleyen: str = "") -> int:
        with self._baglan() as conn:
            im = conn.execute(
                f"UPDATE {TABLO_ADI} SET insan_karar=?, insan_notu=?, inceleyen=?, "
                f"inceleme_tarihi=? WHERE ikn=?",
                (karar, not_ or None, inceleyen or None,
                 datetime.now(timezone.utc).isoformat(timespec="seconds"), ikn),
            )
            return im.rowcount


def olustur(ayarlar=None, *, zorla: Literal["postgres", "sqlite", None] = None) -> KararDeposu:
    """Postgres'e yazmayı DENER; olmazsa yerel SQLite'a düşer.

    Düşme sessiz DEĞİLDİR — hangi arka uca yazıldığı ekrana basılır ve her
    koşunun konfigürasyonuna kaydedilir. "Nereye yazdı?" sorusu sonradan
    cevaplanamaz hale gelmesin.
    """
    if ayarlar is None:
        from app.config.settings import ayarlari_al
        ayarlar = ayarlari_al()

    yerel_yol = Path(ayarlar.qdrant_yolu).parent / "karar_sonuclari.db"

    if zorla == "sqlite":
        d = SqliteKararDeposu(yerel_yol)
        d.tabloyu_hazirla()
        return d

    if zorla == "postgres" or ayarlar.data_backend == "postgres":
        try:
            d = PostgresKararDeposu(
                ayarlar.postgres_baglanti_dizesi(),
                max_deneme=getattr(ayarlar, "database_max_deneme", 3),
            )
            d.tabloyu_hazirla()
            return d
        except Exception as e:  # noqa: BLE001
            if zorla == "postgres":
                raise
            print(f"  ! Postgres'e tablo açılamadı ({type(e).__name__}: {e})")
            print(f"  ! Yerel SQLite'a düşülüyor: {yerel_yol}")
            print(f"  ! Yazma yetkisi gelince aynı şemaya aktarılabilir.")
            # Geri düşme SESSİZ OLMAMALI: bu partinin kararları ve insan
            # etiketleri CANLIYA DEĞİL yerel dosyaya yazılacak. Fark edilmezse
            # ölçüm seti ikiye bölünür ve "kaç etiketim var" sorusu yanlış
            # cevaplanır.
            print("  !")
            print("  ! DİKKAT: bu partide yazacağın HER ŞEY (kararlar + etiketler)")
            print("  !         canlıya DEĞİL yukarıdaki yerel dosyaya gidecek.")
            print("  !         Karşılaştır:  --durum   ve   --durum --yerel")
            if "timeout" in str(e).lower():
                print("  !         Zaman aşımı = paketler düşüyor (ağ/VPN/güvenlik duvarı),")
                print("  !         kimlik doğrulama sorunu DEĞİL.")

    d = SqliteKararDeposu(yerel_yol)
    d.tabloyu_hazirla()
    return d
