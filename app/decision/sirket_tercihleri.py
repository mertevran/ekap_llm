"""
Şirket tercihleri — portaldan girilen anahtar kelimeler ve OKAS kodları.

=============================================================================
NEDEN VAR
=============================================================================
Projenin genel planındaki 2. hedef:

    "Sabit (hardcoded) kurallar yerine Satış Ekibinin seçeceği 9.590 Master OKAS
     Kodu ve serbest anahtar kelimeler ile İSBAK ilgi alanlarını DİNAMİK olarak
     belirlemek."

Bugüne kadar SQL ön filtresi yalnızca `app/profiles/data/profiles/*.json`
dosyalarından besleniyordu — 20 iş paketi, 274 terim, dosyada sabit. Satış ekibi
portaldan ("Sistem Ayarları > Şirket Tercihleri") OKAS kodu seçtiğinde filtre
bunu GÖRMÜYORDU.

Bu modül o boşluğu kapatıyor. Backend'in `company_preferences` tablosu
(tek satırlık, `CHECK (id = 1)`) doğrudan okunuyor:

    selected_okas_codes  text[]
    keywords             text[]
    company_summary      text

=============================================================================
ÜÇ KAYNAK MODU — HANGİSİNİN NE ZAMAN KULLANILACAĞI
=============================================================================
    profil    Yalnızca profil dosyaları. ÖLÇÜLMÜŞ: 41 etiketli pozitifin 41'i
              geçiyor, rastgele havuzun %88'i eleniyor. Üretimde güvenli taban.

    tercih    Yalnızca portaldan girilenler. Satış ekibinin seçimi ne ise o.
              Geri çağırma garantisi YOK — tercihler dar girilirse pozitif kaçar.
              Demo ve "seçimim ne getiriyor" denemeleri için.

    birlesik  İkisinin birleşimi. Profillerin ölçülmüş tabanı korunur, tercihler
              ÜSTÜNE ekler. Havuz genişler, kaçırma riski profil modundan düşük.
              ÜRETİM İÇİN ÖNERİLEN.

Not: `tercih` modu profil tabanını devre dışı bıraktığı için `sql_filtre_analizi`
ile geri çağırması AYRI ölçülmelidir. Aynı 41/41 sayısı orada geçerli değildir.

=============================================================================
OKAS KODU BİÇİMİ
=============================================================================
Portal kodları farklı biçimlerde saklayabiliyor (arayüz ekran görüntüsünden):

    "45232420-2"
    "03111300 - Ayçiçeği tohumları"
    "34996"

Hepsinden BAŞTAKİ RAKAM DİZİSİ alınır ve ÖN EK olarak kullanılır. Ön ek
kullanmak bilinçli: `34996` seçen bir kullanıcı `34996100` ve `34996200`
kodlarını da kastediyordur. Tam eşleşme aramak kullanıcının beklemediği
şekilde daraltırdı.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.decision.sql_filtre import (
    MIN_TERIM_UZUNLUGU,
    FiltreTanimi,
    profillerden_kur,
)

KAYNAKLAR = ("profil", "tercih", "birlesik")

# "45232420-2" / "03111300 - Ayçiçeği tohumları" / "34996" -> baştaki rakamlar
_KOD_DESENI = re.compile(r"^\s*(\d+)")


@dataclass(frozen=True)
class SirketTercihi:
    okas_kodlari: tuple[str, ...] = ()
    anahtar_kelimeler: tuple[str, ...] = ()
    ozet: str = ""
    guncelleme: str = ""

    def bos_mu(self) -> bool:
        return not self.okas_kodlari and not self.anahtar_kelimeler


def kod_ayikla(ham: str) -> str:
    """Portalın sakladığı biçimden OKAS ön ekini çıkarır. Bulunamazsa boş."""
    m = _KOD_DESENI.match(str(ham or ""))
    return m.group(1) if m else ""


def tercihleri_oku(ayarlar=None) -> SirketTercihi:
    """`public.company_preferences` tek satırını okur. SADECE OKUMA.

    Tablo yoksa, boşsa ya da veri kaynağı sqlite ise BOŞ tercih döner —
    çağıran taraf özel durum yazmasın, `birlesik` modu sessizce profillere düşsün.
    """
    if ayarlar is None:
        from app.config.settings import ayarlari_al

        ayarlar = ayarlari_al()

    if ayarlar.data_backend != "postgres":
        return SirketTercihi()

    try:
        import psycopg

        with psycopg.connect(ayarlar.postgres_baglanti_dizesi()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT selected_okas_codes, keywords, company_summary, "
                    "to_char(updated_at,'DD.MM.YYYY HH24:MI') "
                    "FROM public.company_preferences ORDER BY id LIMIT 1"
                )
                satir = cur.fetchone()
    except Exception:  # noqa: BLE001 — tablo yoksa/erişilemezse tarama durmasın
        return SirketTercihi()

    if not satir:
        return SirketTercihi()

    kodlar, kelimeler, ozet, guncelleme = satir
    return SirketTercihi(
        okas_kodlari=tuple(k for k in (kod_ayikla(x) for x in (kodlar or [])) if k),
        anahtar_kelimeler=tuple(
            str(x).strip().lower() for x in (kelimeler or [])
            if len(str(x).strip()) >= MIN_TERIM_UZUNLUGU
        ),
        ozet=(ozet or "").strip(),
        guncelleme=guncelleme or "",
    )


def tercihten_kur(tercih: SirketTercihi) -> FiltreTanimi:
    return FiltreTanimi(
        terimler=tuple(sorted(set(tercih.anahtar_kelimeler))),
        okas_on_ekleri=tuple(sorted(set(tercih.okas_kodlari))),
        kaynak="sirket_tercihleri",
    )


def filtre_kur(
    kaynak: str = "birlesik",
    *,
    genislik: str = "genis",
    ayarlar=None,
    profiller=None,
) -> tuple[FiltreTanimi, SirketTercihi]:
    """Seçilen kaynağa göre filtre tanımı üretir.

    Dönüş: (tanım, okunan tercih). Tercih, çağıran tarafın "portalda ne yazıyor"
    diye raporlayabilmesi için ayrıca döner.
    """
    if kaynak not in KAYNAKLAR:
        raise ValueError(f"kaynak {KAYNAKLAR} içinden olmalı, '{kaynak}' verildi.")

    tercih = tercihleri_oku(ayarlar) if kaynak in ("tercih", "birlesik") else SirketTercihi()

    if kaynak == "tercih":
        return tercihten_kur(tercih), tercih

    if profiller is None:
        from app.profiles.loader import profilleri_yukle

        profiller = profilleri_yukle()
    profil_tanim = profillerden_kur(profiller, genislik=genislik)

    if kaynak == "profil" or tercih.bos_mu():
        # Tercih boşsa `birlesik` sessizce profile düşer — portal doldurulmadan
        # tarama başlatıldığında havuzun boşalmaması için.
        return profil_tanim, tercih

    return (
        FiltreTanimi(
            terimler=tuple(sorted(set(profil_tanim.terimler) | set(tercih.anahtar_kelimeler))),
            okas_on_ekleri=tuple(
                sorted(set(profil_tanim.okas_on_ekleri) | set(tercih.okas_kodlari))
            ),
            kaynak=f"birlesik/{genislik}",
        ),
        tercih,
    )
