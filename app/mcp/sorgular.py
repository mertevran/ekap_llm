"""
MCP araçlarının iş mantığı — SADECE OKUMA, MCP'den bağımsız.

=============================================================================
NEDEN MCP'DEN AYRI BİR MODÜL
=============================================================================
Buradaki fonksiyonlar `mcp` paketine hiç bağlı değil; düz Python. `scripts/
mcp_sunucu.py` yalnızca ince bir sarmalayıcı. Sebep: MCP çalışma zamanı
olmadan test edilebilsinler. Projenin test disiplini, bir katmanın ancak
çalıştırılabildiği ortamda doğrulanabilmesini kabul etmiyor.

=============================================================================
KARAR HATTINA DOKUNMAZ — BU BİR TASARIM SINIRIDIR
=============================================================================
Bu katman VERİ GETİRİR, KARAR ÜRETMEZ. LLM çağırmaz, retrieval yapmaz,
`llm_rag.tender_scope_decisions` tablosuna YAZMAZ.

Gerekçe (rapor 4.3 ve 7): sistemin ölçülebilirliği, karar hattının SABİT beş
adımlı ve deterministik olmasına dayanıyor. Bir ajanın kendi sorgularını yazıp
kendi bağlamını toplaması bu garantiyi bitirir — aynı ihale iki koşuda farklı
karar alabilir ve 205 regresyon testi anlamsızlaşır. Ayrıca CPU'da ihale başına
süre 29 dakikadan ~2 saate çıkar (her ajan turu bir LLM çağrısı).

Bu yüzden MCP burada UZMANIN ARACI: soru sorar, ihale listesi alır. Kararı
isterse mevcut hatta verir (`analyze_tender.py <İKN>`), ama hat değişmez.

=============================================================================
"SQL ENCODER" BURADA
=============================================================================
`serbest_sorgu()` bir `WHERE` ifadesi alır ve çalıştırır. İfadeyi ÜRETEN taraf
MCP istemcisindeki modeldir (Claude vb.) — yani text-to-SQL yeteneği istemciden
gelir, bizim hattımıza ikinci bir model eklenmez.

Üretilen her ifade çalıştırılmadan ÖNCE `sql_filtre.sql_guvenli_mi()`
doğrulayıcısından geçer: noktalı virgül, SQL yorumu, veri değiştiren komutlar,
izin listesi dışı tablo/takma ad, tam sorgu ve dengesiz parantez reddedilir.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config.settings import ayarlari_al
from app.database.base import AKTIF_DURUM, depo_olustur
from app.decision.sql_filtre import (
    SEMA_TANIMI,
    profillerden_kur,
    sql_guvenli_mi,
)
from app.domain.models import en_iyi_ilan, kapsam_metni
from app.profiles.loader import profilleri_yukle

# Tek seferde dönülecek en fazla satır. Sorgu sonucu bir LLM'in bağlamına
# giriyor; sınırsız liste hem pencereyi doldurur hem maliyeti şişirir.
VARSAYILAN_LIMIT = 20
MAX_LIMIT = 200
# İlan metni kırpma sınırı — tam metin `ihale_getir` ile ayrıca istenebilir.
OZET_METIN_KARAKTER = 600


class SorguHatasi(RuntimeError):
    """Kullanıcıya gösterilecek, beklenen hata (geçersiz SQL, bulunamadı...)."""


def _translate(metin: str | None, kaynak: str, hedef: str) -> str | None:
    """Postgres `translate(string, from, to)` fonksiyonunun SQLite karşılığı.

    `kaynak`taki her karakter, `hedef`teki aynı konumdaki karakterle değiştirilir.
    `hedef` daha kısaysa fazla karakterler SİLİNİR — Postgres semantiği budur.
    """
    if metin is None:
        return None
    harita = {k: (hedef[i] if i < len(hedef) else "") for i, k in enumerate(kaynak)}
    return "".join(harita.get(c, c) for c in metin)


def _limitle(limit: int | None) -> int:
    """Satır sayısını [1, MAX_LIMIT] aralığına sıkıştırır.

    `None` ve `0` "belirtilmedi" sayılır -> varsayılana düşer. Bu ÖRTÜK bir
    davranıştı (`0 or 20` Python'da 20 verir) ve test yakaladı; artık açıkça
    yazılı. Negatif değer de varsayılana düşer — bir MCP istemcisinin gönderdiği
    saçma parametre yüzünden sorgu patlamasın.
    """
    if limit is None or int(limit) <= 0:
        return VARSAYILAN_LIMIT
    return min(int(limit), MAX_LIMIT)


@dataclass(frozen=True)
class _Baglanti:
    """Arka uçtan bağımsız ham SELECT çalıştırıcı. SADECE OKUMA."""

    postgres: bool

    def calistir(self, pg_sql: str, sqlite_sql: str, par: list) -> list[dict]:
        ayarlar = ayarlari_al()
        if self.postgres:
            import psycopg

            with psycopg.connect(ayarlar.postgres_baglanti_dizesi()) as conn:
                with conn.cursor() as cur:
                    cur.execute(pg_sql, par)
                    kolonlar = [d[0] for d in cur.description]
                    return [dict(zip(kolonlar, r)) for r in cur.fetchall()]
        import sqlite3

        conn = sqlite3.connect(ayarlari_al().sqlite_yolu)
        conn.row_factory = sqlite3.Row
        # `translate()` Postgres'e özgü; SQLite'ta yok. Türkçe katlama için
        # ÜRETİLEN SQL iki arka uçta da AYNI olmalı, yoksa MCP istemcisine
        # "hangi veritabanındasın" diye sormak gerekir ve şema tavsiyesi
        # ikiye bölünür. Planın ilkesi: bugün test edilen kod yolu = yarın
        # üretimde çalışacak kod yolu.
        conn.create_function("translate", 3, _translate)
        try:
            return [dict(r) for r in conn.execute(sqlite_sql, par)]
        finally:
            conn.close()


def _baglanti() -> _Baglanti:
    return _Baglanti(postgres=ayarlari_al().data_backend == "postgres")


def _ihale_ozet(satir: dict) -> dict:
    return {
        "ikn": satir.get("ikn"),
        "adi": (satir.get("adi") or "").replace("\n", " ").strip(),
        "idare": (satir.get("idare_adi") or "").replace("\n", " ").strip(),
        "il": satir.get("il"),
        "tur": satir.get("ihale_turu"),
        "tarih": str(satir.get("ihale_tarihi") or ""),
        "durum": satir.get("ihale_durumu"),
    }


# ============================================================================
# ARAÇLAR
# ============================================================================


def ihale_ara(
    terim: str | None = None,
    il: str | None = None,
    ihale_turu: str | None = None,
    sadece_aktif: bool = True,
    limit: int = VARSAYILAN_LIMIT,
) -> dict[str, Any]:
    """İhale adında metin arar; il / tür / aktiflik ile daraltır.

    Türkçe büyük-küçük harf duyarsız (İ/I/ı tuzağı için `sql_filtre.katla` ile
    aynı katlama SQL tarafında uygulanır).
    """
    n = _limitle(limit)
    kosullar_pg, kosullar_lt, par = [], [], []

    if terim:
        kosullar_pg.append(
            "lower(translate(t.adi,'İIıĞğŞşÇçÖöÜü','IIiGgSsCcOoUu')) LIKE %s")
        kosullar_lt.append("LOWER(t.adi) LIKE ?")
        from app.decision.sql_filtre import katla

        par.append(f"%{katla(terim)}%")
    if il:
        kosullar_pg.append("upper(t.il) = upper(%s)")
        kosullar_lt.append("UPPER(t.il) = UPPER(?)")
        par.append(il)
    if ihale_turu:
        kosullar_pg.append("t.ihale_turu = %s")
        kosullar_lt.append("t.ihale_turu = ?")
        par.append(ihale_turu)
    if sadece_aktif:
        kosullar_pg.append("t.ihale_durumu = %s")
        kosullar_lt.append("t.ihale_durumu = ?")
        par.append(AKTIF_DURUM)

    w_pg = " AND ".join(kosullar_pg) or "TRUE"
    w_lt = " AND ".join(kosullar_lt) or "1=1"
    satirlar = _baglanti().calistir(
        f"SELECT t.ikn, t.adi, t.idare_adi, t.il, t.ihale_turu, t.ihale_durumu, "
        f"to_char(t.ihale_tarihi,'DD.MM.YYYY HH24:MI') AS ihale_tarihi "
        f"FROM public.tenders t WHERE {w_pg} ORDER BY t.ihale_tarihi DESC LIMIT {n}",
        f"SELECT t.ikn, t.adi, t.idare_adi, t.il, t.ihale_turu, t.ihale_durumu, "
        f"t.ihale_tarihi FROM tenders t WHERE {w_lt} ORDER BY t.ikn DESC LIMIT {n}",
        par,
    )
    return {"sayi": len(satirlar), "ihaleler": [_ihale_ozet(s) for s in satirlar]}


def ihale_getir(ikn: str, tam_metin: bool = False) -> dict[str, Any]:
    """Tek ihalenin künyesi + TEMİZLENMİŞ ilan metni + OKAS kodları.

    Temizlenmiş metin, karar modeline giden metnin BİREBİR aynısıdır
    (`domain.kapsam_metni`) — uzman "model ne gördü" sorusunu buradan cevaplar.
    """
    depo = depo_olustur(ayarlari_al())
    ihaleler = depo.coklu_getir([ikn], alan="ikn")
    if not ihaleler:
        raise SorguHatasi(f"İhale bulunamadı: {ikn}")
    ih = ihaleler[0]
    ilan = en_iyi_ilan(ih)
    metin = kapsam_metni(ih) or ""
    return {
        "ikn": ih.ikn,
        "adi": (ih.adi or "").replace("\n", " ").strip(),
        "idare": ih.idare_adi,
        "il": ih.il,
        "tur": ih.ihale_turu,
        "tarih": ih.ihale_tarihi,
        "durum": ih.ihale_durumu,
        "okas": [{"kod": o.kod, "ad": o.ad} for o in ih.okas_kodlari],
        "secilen_ilan": {"tipi": ilan.ilan_tipi, "tarihi": ilan.ilan_tarihi} if ilan else None,
        "ilan_sayisi": len(ih.ilanlar),
        "kapsam_metni": metin if tam_metin else metin[:OZET_METIN_KARAKTER],
        "kapsam_metni_uzunlugu": len(metin),
    }


def aday_ihaleler(
    limit: int = VARSAYILAN_LIMIT,
    sadece_aktif: bool = True,
    genislik: str = "genis",
) -> dict[str, Any]:
    """SQL alan filtresini geçen ihaleler — "incelemeye değer" havuzu.

    Bu bir KARAR DEĞİLDİR. Filtre kelime ve OKAS eşleşmesidir; ölçüldü:
    41 etiketli pozitifin 41'i geçiyor (genis), rastgele havuzun %88'i eleniyor.
    Ama geçen listenin büyük kısmı gürültüdür — en çok vuran terim "yedek parça".
    """
    tanim = profillerden_kur(profilleri_yukle(), genislik=genislik)
    depo = depo_olustur(ayarlari_al())
    iknler = depo.filtreli_iknler(tanim, sadece_aktif=sadece_aktif)
    n = _limitle(limit)
    secilen = depo.coklu_getir(iknler[:n], alan="ikn")
    return {
        "filtre": {
            "genislik": genislik,
            "terim_sayisi": len(tanim.terimler),
            "okas_on_eki": len(tanim.okas_on_ekleri),
            "kapsam": "aktif" if sadece_aktif else "tüm tablo",
        },
        "toplam_gecen": len(iknler),
        "donen": len(secilen),
        "ihaleler": [
            {
                "ikn": i.ikn,
                "adi": (i.adi or "").replace("\n", " ").strip(),
                "idare": i.idare_adi,
                "tur": i.ihale_turu,
                "okas": [o.kod for o in i.okas_kodlari],
            }
            for i in secilen
        ],
        "uyari": "Filtreyi geçmek 'uygun' demek DEĞİLDİR — bu bir aday havuzudur.",
    }


def okas_ara(terim: str, limit: int = VARSAYILAN_LIMIT) -> dict[str, Any]:
    """OKAS kataloğunda ada göre arama. Hangi kodun ne anlama geldiğini bulmak için."""
    n = _limitle(limit)
    from app.decision.sql_filtre import katla

    par = [f"%{katla(terim)}%"]
    satirlar = _baglanti().calistir(
        "SELECT o.kod, o.ad, count(*) AS ihale_sayisi FROM public.tender_okas_codes o "
        "WHERE lower(translate(o.ad,'İIıĞğŞşÇçÖöÜü','IIiGgSsCcOoUu')) LIKE %s "
        f"GROUP BY o.kod, o.ad ORDER BY ihale_sayisi DESC LIMIT {n}",
        "SELECT o.kod, o.ad, COUNT(*) AS ihale_sayisi FROM tender_okas_codes o "
        "WHERE LOWER(o.ad) LIKE ? GROUP BY o.kod, o.ad "
        f"ORDER BY ihale_sayisi DESC LIMIT {n}",
        par,
    )
    return {"sayi": len(satirlar), "kodlar": satirlar}


def profil_listesi() -> dict[str, Any]:
    """İSBAK'ın 20 iş paketi — kod, ad, aile, öncelik, güçlü terimler."""
    return {
        "sayi": len(profilleri_yukle()),
        "profiller": [
            {
                "kod": p.kod,
                "ad": p.ad,
                "aile": p.aile,
                "oncelik": p.oncelik,
                "guclu_terimler": p.guclu_terimler,
                "negatif_terimler": p.negatif_terimler,
                "okas_on_ekleri": p.okas_on_ekleri,
                "kapasite_verisi": p.veri_durumu,
            }
            for p in profilleri_yukle()
        ],
        "not": "belgeler/tamamlanan_projeler alanları BOŞ — Aşama 2 bu yüzden kapalı.",
    }


def karar_gecmisi(ikn: str | None = None, limit: int = VARSAYILAN_LIMIT) -> dict[str, Any]:
    """Daha önce taranmış ihalelerin kararları ve varsa insan etiketi."""
    from app.database.karar_deposu import olustur as karar_deposu_olustur

    depo = karar_deposu_olustur(ayarlari_al())
    n = _limitle(limit)
    satirlar = depo.son_taranmislar(500 if ikn else n)
    if ikn:
        satirlar = [s for s in satirlar if s.get("ikn") == ikn][:n]
    return {
        "depo": depo.nerede,
        "sayi": len(satirlar),
        "kararlar": [
            {
                "ikn": s.get("ikn"),
                "adi": (s.get("adi") or "")[:90],
                "karar": s.get("karar"),
                "ilgi_skoru": s.get("ilgi_skoru"),
                "eslesen_paket": s.get("eslesen_paket"),
                "gerekce": (s.get("gerekce") or "")[:300],
                "insan_karar": s.get("insan_karar"),
                "insan_notu": s.get("insan_notu"),
            }
            for s in satirlar
        ],
    }


def serbest_sorgu(where_ifadesi: str, limit: int = VARSAYILAN_LIMIT) -> dict[str, Any]:
    """Doğrulanmış bir `WHERE` ifadesiyle ihale arar — "SQL encoder" ucu.

    İfadeyi ÜRETEN taraf MCP istemcisindeki modeldir. Burada yalnızca DENETLENİP
    çalıştırılır. Reddedilirse gerekçesiyle birlikte hata döner ki istemci
    düzeltebilsin.

    Kullanılabilir takma adlar ve sütunlar için `sema_bilgisi()` aracına bakın.
    """
    ok, gerekce = sql_guvenli_mi(where_ifadesi)
    if not ok:
        raise SorguHatasi(
            f"SQL güvenlik denetimi REDDETTİ: {gerekce}\n"
            f"Yalnızca WHERE gövdesi bekleniyor. Şema için `sema_bilgisi` aracını kullanın."
        )
    n = _limitle(limit)
    try:
        satirlar = _baglanti().calistir(
            f"SELECT t.ikn, t.adi, t.idare_adi, t.il, t.ihale_turu, t.ihale_durumu, "
            f"to_char(t.ihale_tarihi,'DD.MM.YYYY HH24:MI') AS ihale_tarihi "
            f"FROM public.tenders t WHERE {where_ifadesi} LIMIT {n}",
            f"SELECT t.ikn, t.adi, t.idare_adi, t.il, t.ihale_turu, t.ihale_durumu, "
            f"t.ihale_tarihi FROM tenders t WHERE {where_ifadesi} LIMIT {n}",
            [],
        )
    except Exception as e:  # noqa: BLE001 — sorgu hatası kullanıcıya döner
        raise SorguHatasi(f"Sorgu çalıştırılamadı: {type(e).__name__}: {e}") from e
    return {
        "where": where_ifadesi,
        "sayi": len(satirlar),
        "ihaleler": [_ihale_ozet(s) for s in satirlar],
    }


def sema_bilgisi() -> dict[str, Any]:
    """`serbest_sorgu` için tablo/sütun şeması ve kurallar."""
    return {
        "sema": SEMA_TANIMI,
        "aktif_durum_degeri": AKTIF_DURUM,
        "kurallar": [
            "Yalnızca WHERE gövdesi üretin; SELECT/FROM/ORDER BY yazmayın.",
            "Noktalı virgül, SQL yorumu (-- ya da /*) ve veri değiştiren komut YASAK.",
            "Türkçe metin araması için: "
            "lower(translate(t.adi,'İIıĞğŞşÇçÖöÜü','IIiGgSsCcOoUu')) LIKE '%terim%'",
        ],
    }
