"""
Postgres arka ucu — canlı veritabanı bağlantısı.

SADECE OKUMA yapar. Bu dosyada hiçbir INSERT / UPDATE / ALTER ifadesi yoktur;
ihale verisine dokunulmaz, şema değiştirilmez.

`icerik_temiz` diye hazır bir kolon bu şemada YOKTUR ve eklenmeyecektir. Ham
`icerik` okunur ve `IhaleDeposu._ilani_kur()` içinde okuma anında temizlenir.

Yalnızca `DATA_BACKEND=postgres` seçiliyken devreye girer. `psycopg` import'u
tembeldir (fonksiyon içinde yapılır): paket kurulu olmasa bile sqlite arka ucu
sorunsuz çalışmaya devam eder.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.database.base import AKTIF_DURUM, IhaleBulunamadi, IhaleDeposu
from app.domain.models import Ihale

logger = logging.getLogger(__name__)

ZAMAN_DILIMI = "Europe/Istanbul"

# ============================================================================
# TARİH BİÇİMİ — 30.07.2026'da canlı şema görülünce düzeltildi
# ============================================================================
# Canlıda tarihler GERÇEK `timestamp without time zone` kolonları:
#     tenders.ihale_tarihi              -> datetime(2026, 1, 2, 11, 0)
#     tender_announcements.ilan_tarihi  -> datetime(2025, 12, 10, 0, 0)
#
# Yerel SQLite kopyasında ise METİN:
#     ihale_tarihi  -> '01.04.2026 09:00'
#     ilan_tarihi   -> '2026-03-11T00:00:00'
#
# İlk sürüm canlıyı da metin varsayıp regex (`~`) uyguluyordu ve şu hatayla
# patlıyordu: `operator does not exist: timestamp without time zone ~ unknown`.
#
# ÇÖZÜM: regex'i silmek YETMEZ. `Ihale.ihale_tarihi` alanı `str | None` olarak
# tanımlı ve `on_filtre.ihale_tarihi_gecmis_mi()` üzerinde `.strip()` +
# `strptime("%d.%m.%Y %H:%M")` çağırıyor. Postgres bir `datetime` nesnesi
# döndürürse orada AttributeError patlar — ve o fonksiyon yalnızca
# `sadece_aktif=True` iken çağrıldığı için hata TAM TARAMA sırasında ortaya
# çıkar, en kötü an.
#
# Bu yüzden tarihler SQL'de SQLite'ın ürettiği metin biçimine çevriliyor.
# Böylece aşağıdaki tüm kod (domain modeli, ön filtre, ilan önceliği, ölçüm
# script'leri) iki arka uçta BİREBİR AYNI girdiyi görür — planın "bugün test
# edilen kod yolu = yarın üretimde çalışacak kod yolu" ilkesi korunur.
_IHALE_TARIHI_BICIMI = "DD.MM.YYYY HH24:MI"
_ILAN_TARIHI_BICIMI = 'YYYY-MM-DD"T"HH24:MI:SS'

_IHALE_ALANLARI = f"""
    id, ikn, adi, idare_adi, il,
    to_char(ihale_tarihi, '{_IHALE_TARIHI_BICIMI}') AS ihale_tarihi,
    ihale_turu, ihale_usulu, ihale_durumu, kapsam, ihale_yeri, isin_yeri
"""

_ILAN_ALANLARI = f"""
    id, tender_id, ilan_tipi,
    to_char(ilan_tarihi, '{_ILAN_TARIHI_BICIMI}') AS ilan_tarihi,
    baslik, icerik
"""

# Aktif = durum "Katılıma Açık" VE ihale tarihi henüz geçmemiş.
# Kolon zaten timestamp olduğu için doğrudan karşılaştırılıyor; metin çevirme
# ya da regex doğrulama gerekmiyor.
_AKTIF_KOSUL = f"""
    ihale_durumu = %s
    AND ihale_tarihi IS NOT NULL
    AND ihale_tarihi >= (CURRENT_TIMESTAMP AT TIME ZONE '{ZAMAN_DILIMI}')
"""


class PostgresIhaleDeposu(IhaleDeposu):
    def __init__(self, baglanti_dizesi: str, max_deneme: int = 3) -> None:
        self.baglanti_dizesi = baglanti_dizesi
        self.max_deneme = max(1, int(max_deneme))

    def _baglan(self):
        """Bağlantı kurar; GEÇİCİ hatalarda üstel geri çekilmeyle yeniden dener.

        NEDEN GEREKLİ: burada havuzlama YOK — her repository çağrısı yeni bir
        bağlantı açıyor. 4.000 ihalelik bir taramada 4.000+ bağlantı demek.
        66 ihalelik gerçek koşuda 7 ihale (%10,6) `ConnectionTimeout` ile düştü;
        sunucudaki `max_connections` sınırına ya da güvenlik duvarının bağlantı
        hızı sınırlamasına takılmak tam olarak böyle görünür.

        YALNIZCA BAĞLANTI hataları yeniden denenir. Sorgu hataları (sözdizimi,
        yetki, bozuk veri) geçici değildir; onları denemek hatayı gizler ve
        süreyi katlar — `llm_client._istek`'teki zaman aşımı kararının aynı
        gerekçesi.

        Tüm denemeler tükenirse hata OLDUĞU GİBİ fırlatılır. Sessizce yerel
        veriye düşmek YASAK: hangi veriyle karar üretildiği belirsizleşir.
        """
        import time

        import psycopg  # tembel import

        son_hata: Exception | None = None
        for deneme in range(1, self.max_deneme + 1):
            try:
                return psycopg.connect(self.baglanti_dizesi)
            except (psycopg.OperationalError, psycopg.errors.ConnectionTimeout) as e:
                son_hata = e
                if deneme < self.max_deneme:
                    bekleme = 2 ** (deneme - 1)      # 1s, 2s, 4s...
                    logger.warning(
                        "Postgres bağlantısı kurulamadı (deneme %d/%d): %s — %.0f sn sonra tekrar",
                        deneme, self.max_deneme, e, bekleme,
                    )
                    time.sleep(bekleme)

        host = "?"
        for parca in self.baglanti_dizesi.split():
            if parca.startswith("host="):
                host = parca[5:]
        raise ConnectionError(
            f"Postgres'e {self.max_deneme} denemede bağlanılamadı (host={host}): {son_hata}\n"
            f"  · Ağ erişimi:  Test-NetConnection {host} -Port 5432\n"
            f"    'TcpTestSucceeded : False' ise sorun AĞDA — VPN/güvenlik duvarı.\n"
            f"  · Zaman aşımı bütçesi .env'de: DATABASE_CONNECT_TIMEOUT (varsayılan 10)\n"
            f"  · Sunucu tarafı: SHOW max_connections; "
            f"SELECT count(*) FROM pg_stat_activity;"
        ) from son_hata

    def _dict_cursor(self, conn):
        from psycopg.rows import dict_row

        return conn.cursor(row_factory=dict_row)

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

    # ---- Çoklu (4 toplu sorgu, N+1 yok) ----

    def coklu_getir(self, anahtarlar: Sequence[str], *, alan: str = "ikn") -> list[Ihale]:
        if alan not in ("ikn", "id"):
            raise ValueError("alan sadece 'ikn' veya 'id' olabilir.")
        temiz = [str(a).strip() for a in anahtarlar if str(a).strip()]
        if not temiz:
            return []
        tekil = list(dict.fromkeys(temiz))

        with self._baglan() as conn:
            with self._dict_cursor(conn) as cur:
                cur.execute(
                    f"SELECT {_IHALE_ALANLARI} FROM public.tenders "
                    f"WHERE {alan}::text = ANY(%s::text[])",
                    (tekil,),
                )
                ihale_satirlari = cur.fetchall()
                if not ihale_satirlari:
                    return []

                tender_idler = [str(s["id"]) for s in ihale_satirlari]
                ilanlar = self._grupla(
                    cur, tender_idler, "public.tender_announcements", _ILAN_ALANLARI,
                )
                ozellikler = self._grupla(
                    cur, tender_idler, "public.tender_characteristics", "id, tender_id, ozellik"
                )
                okaslar = self._grupla(
                    cur, tender_idler, "public.tender_okas_codes", "id, tender_id, kod, ad"
                )

        indeks = {
            str(s[alan]): self._ihaleyi_kur(
                dict(s),
                ilanlar.get(str(s["id"]), []),
                ozellikler.get(str(s["id"]), []),
                okaslar.get(str(s["id"]), []),
            )
            for s in ihale_satirlari
        }
        return [indeks[a] for a in temiz if a in indeks]

    @staticmethod
    def _grupla(cur, tender_idler: list[str], tablo: str, alanlar: str) -> dict[str, list[dict]]:
        cur.execute(
            f"SELECT {alanlar} FROM {tablo} WHERE tender_id::text = ANY(%s::text[]) "
            f"ORDER BY tender_id::text, id",
            (tender_idler,),
        )
        gruplar: dict[str, list[dict]] = {}
        for satir in cur.fetchall():
            gruplar.setdefault(str(satir["tender_id"]), []).append(dict(satir))
        return gruplar

    # ---- Aktif ihaleler ----

    def aktif_ihale_sayisi(self) -> int:
        with self._baglan() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT COUNT(*) FROM public.tenders WHERE {_AKTIF_KOSUL}", (AKTIF_DURUM,)
                )
                return int(cur.fetchone()[0])

    def aktif_ihale_iknleri(self, limit: int | None = None) -> list[str]:
        if limit is not None and limit <= 0:
            raise ValueError("limit pozitif olmalıdır.")
        sorgu = f"""
            SELECT ikn FROM public.tenders
            WHERE {_AKTIF_KOSUL}
            ORDER BY ihale_tarihi ASC, ikn ASC
        """
        par: list = [AKTIF_DURUM]
        if limit is not None:
            sorgu += " LIMIT %s"
            par.append(limit)
        with self._baglan() as conn:
            with conn.cursor() as cur:
                cur.execute(sorgu, par)
                return [str(r[0]) for r in cur.fetchall()]

    def aktif_iknler_oncelikli(self, tanim, limit: int | None = None) -> list[tuple[str, bool]]:
        """(ikn, filtreyi_gecti) — filtreyi geçenler ÖNCE.

        ELEMEZ. Tüm aktif havuz döner, yalnızca SIRA değişir. Gerekçe
        app/decision/sql_filtre.py başlığında: eleme modu ölçülmüş bir kaçırma
        kapısıdır; sıralama modunda kayıp riski sıfırdır ve adaylar erken çıkar.

        Filtre boşsa normal sıraya düşer — çağıran taraf özel durum yazmasın.
        """
        from app.decision.sql_filtre import where_uret

        parca, filtre_par = where_uret(tanim, "t") if tanim else ("", [])
        if not parca:
            return [(i, False) for i in self.aktif_ihale_iknleri(limit)]

        sorgu = f"""
            SELECT ikn, ({parca}) AS eslesti
            FROM public.tenders t
            WHERE {_AKTIF_KOSUL}
            ORDER BY eslesti DESC, ihale_tarihi ASC, ikn ASC
        """
        par: list = [*filtre_par, AKTIF_DURUM]
        if limit is not None:
            sorgu += " LIMIT %s"
            par.append(limit)
        with self._baglan() as conn:
            with conn.cursor() as cur:
                cur.execute(sorgu, par)
                return [(str(r[0]), bool(r[1])) for r in cur.fetchall()]

    def filtreli_iknler(self, tanim, *, sadece_aktif: bool = True) -> list[str]:
        """SQL ön filtresini GEÇEN ihalelerin İKN'leri.

        `sadece_aktif=False` -> TÜM tablo (geçmiş, iptal, sonuçlanmış dahil).
        Etiketli set kurarken işe yarar: kapsam sorusu ("bu iş İSBAK'ın alanına
        girer mi") ihale tarihinden BAĞIMSIZDIR, dolayısıyla tarihi geçmiş bir
        ihale de geçerli bir etiket örneğidir. Aktif havuzda filtreyi geçen ~450
        ihale varken tüm tabloda ~5.800 var — etiket biriktirmek için 13 kat
        daha geniş kaynak.

        DİKKAT: bu ihalelere TEKLİF VERİLEMEZ. Yalnızca etiketleme/ölçüm için.
        Tarama (`toplu_tarama`) her zaman aktif havuzda kalmalı.
        """
        from app.decision.sql_filtre import where_uret

        parca, filtre_par = where_uret(tanim, "t") if tanim else ("", [])
        if not parca:
            return self.aktif_ihale_iknleri() if sadece_aktif else []

        kosul = f"{parca} AND {_AKTIF_KOSUL}" if sadece_aktif else parca
        par: list = [*filtre_par] + ([AKTIF_DURUM] if sadece_aktif else [])
        with self._baglan() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT ikn FROM public.tenders t WHERE {kosul} ORDER BY ikn", par
                )
                return [str(r[0]) for r in cur.fetchall()]

    def aktif_ihaleler(self, limit: int | None = None) -> list[Ihale]:
        return self.coklu_getir(self.aktif_ihale_iknleri(limit), alan="ikn")
