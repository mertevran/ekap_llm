"""
SQL ön filtresi — profil sinyallerinden PARAMETRELİ `WHERE` ifadesi üretir.

=============================================================================
NE YAPAR, NE YAPMAZ — ÖNCE BUNU OKU
=============================================================================
YAPAR : Aktif ihale havuzunu, profillerdeki anahtar terimler ve OKAS ön ekleriyle
        veritabanı seviyesinde daraltır/sıralar. Milisaniye mertebesinde.
YAPMAZ: İhale BAŞINA süreyi kısaltmaz. LLM'e ulaşan bir ihale CPU'da yine
        ~1.743 saniye sürer. Değişen tek şey KAÇ ihalenin LLM'e ulaştığıdır.

=============================================================================
ÖLÇÜLDÜ (06.08.2026) — ELEME MODU BİR KAÇIRMA KAPISIDIR
=============================================================================
190 terim + 25 OKAS ön ekiyle kurulan filtre, etiketli 41 pozitife karşı:

    POZİTİF GERİ ÇAĞIRMA : 35/41  (%85)   <-- 6 pozitif KAYBEDİLİYOR
    HAVUZ DARALMASI      : %88  (400 rastgele aktif -> 49)

Kaçanlar İSBAK'ın çekirdek işi:
    EDS (ELEKTRONİK DENETLEME SİSTEMİ) MALZEME ALIMI VE MONTAJI
    İstanbul Geneli Sinyalizasyon Sistemlerinin Bakım-Onarımı
    Akıllı Ulaşım Sistemlerinin Bakım Onarımı ile Ulaşım Yönetim Merkezi
    Karayolları 12 (Erzurum) Akıllı Ulaşım Sistemleri
    Trafik Düzenlemesi ve Hibrit Sinyalizasyon Yapılması
    Akıllı Ulaşım Sistemleri Saha Unsurları Tedarik, Montaj

Sebep: terim listelerinde "elektronik denetleme sistemi" / "akıllı ulaşım
sistemleri" TAM İFADE olarak yok. İlan metnini de aramaya katmak geri çağırmayı
HİÇ değiştirmedi (35/41) — sorun aramanın dar olması değil, kelime eşleşmesinin
yanlış araç olması. Raporun 2. bölümü bunu zaten söylüyordu:
"Anahtar kelime araması iki yönlü hata üretir."

Kıyas: `sert_on_filtre_skoru=0.44` kabul edilmişti çünkü ölçümde pozitif kaybı
SIFIRDI. %15 kaçırma kabul edilemez.

=============================================================================
BU YÜZDEN VARSAYILAN MOD "SIRALAMA"
=============================================================================
    SIRALAMA (varsayılan) : Filtre ELEMEZ, tarama SIRASINI belirler. Geçenler
                            önce taranır. Kaçırma riski SIFIR, adaylar erken
                            çıkar, kesilen koşuda değerli sonuç elde kalır.
    ELEME    (opt-in)     : Geçmeyen ihale hiç taranmaz. %88 tasarruf ama
                            ölçülmüş %15 kaçırma. Geri çağırma 41/41 olmadan
                            AÇILMAMALI.

`evaluation/sql_filtre_analizi.py` bu iki sayıyı LLM çağırmadan yeniden ölçer;
terim listeleri genişletildikçe tekrar koşulmalıdır.

=============================================================================
GÜVENLİK
=============================================================================
Üretilen SQL PARAMETRELİDİR: terimler sorgu metnine GÖMÜLMEZ, `%s` yer
tutucularıyla ayrı geçirilir. Yani enjeksiyon yüzeyi yok. Bir LLM'e SQL
ürettirilecekse (text-to-SQL) `sql_guvenli_mi()` doğrulayıcısından geçmek
ZORUNDA — bu proje "ihale verisine yazma yok" ilkesiyle kurulu.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.decision.on_filtre import turkce_ascii_kucuk

# Terimin anlamlı sayılması için en az uzunluk. "yol", "kart" gibi kısa
# parçalar her ihaleye vurur ve filtreyi işlevsiz kılar.
MIN_TERIM_UZUNLUGU = 4

# `odakli` modda kabul edilen en kısa OKAS ön eki.
#
# NEDEN VAR: profillerdeki ön eklerin bir kısmı ön ek değil, koca CPV BÖLÜMÜ —
# "30" tüm bilgisayar donanımı, "31" tüm elektrikli ekipman, "72" tüm BT
# hizmetleri, "713" tüm mühendislik hizmetleri. Raporun 9.4'ü bunu zaten
# yazıyor: "Profil ön ekleri seyrektir ve bazıları iki hanelidir."
#
# ÖLÇÜLDÜ (06.08.2026, tüm tablo 47.001 ihale): gürültünün baskın kaynağı bunlar.
#     "31"  963 ihale (Adaptörler)     "72"  238 (İnternet hizmetleri)
#     "30"  951 ihale (Monitörler)     "48"  141 (Lisans yazılımı)
#     "713" 750 ihale (Elektrik proje)
# Eklediğimiz alan ön ekleri ise 38-52 arası vuruş yapıyor.
MIN_ON_EK_UZUNLUGU = 5

# ============================================================================
# TÜRKÇE KATLAMA — İKİ ARKA UÇTA AYNI OLMAK ZORUNDA
# ============================================================================
# Python'da "AKILLI KAVŞAK".lower() -> "akilli kavşak" (I -> i, 'ı' DEĞİL).
# Yani düz `.lower()` ile "akıllı kavşak" terimi hiç eşleşmez. Bu tuzak bu
# projede `on_filtre.turkce_ascii_kucuk` yazılırken zaten yaşanmıştı; aynı
# hataya burada ikinci kez düşüldü ve test yakaladı.
#
# Postgres tarafında `ILIKE` de aynı sorunu yaşar: veritabanı harmanlaması
# 'İ' ile 'i'yi eşitlemez. Bu yüzden SQL'de de ELLE katlama yapılıyor —
# iki arka uç aynı kümeyi seçmeli, aksi halde koşular kıyaslanamaz.
#
# `translate()` haritası `turkce_ascii_kucuk`un ürettiğiyle birebir aynı
# sonucu vermeli; `tests/test_sql_filtre.py::test_katlama_iki_tarafta_ayni`
# bunu karakter karakter doğrular.
_KAYNAK_HARFLER = "İIıĞğŞşÇçÖöÜüÂâÎîÛû"
_HEDEF_HARFLER = "IIiGgSsCcOoUuAaIiUu"


def katla(metin: str) -> str:
    """Türkçe-güvenli katlama. SQL `translate(...)` ile aynı sonucu vermeli."""
    return turkce_ascii_kucuk(metin or "")


def _sql_katla(ifade: str) -> str:
    """`katla()`nın SQL karşılığı."""
    return f"lower(translate({ifade}, '{_KAYNAK_HARFLER}', '{_HEDEF_HARFLER}'))"


@dataclass(frozen=True)
class FiltreTanimi:
    """Profillerden türetilmiş ham sinyaller."""

    terimler: tuple[str, ...] = ()
    okas_on_ekleri: tuple[str, ...] = ()
    kaynak: str = "profiller"

    def bos_mu(self) -> bool:
        return not self.terimler and not self.okas_on_ekleri


def profillerden_kur(profiller, *, genislik: str = "genis", ek_terimler=None) -> FiltreTanimi:
    """`app.profiles.loader.Profil` listesinden filtre tanımı çıkarır.

    Üç mod var ve HANGİSİNİN KULLANILACAĞI AMACA GÖRE DEĞİŞİR:

        mod       terim kaynağı        OKAS ön ekleri    geri çağırma   havuz*
        dar       guclu_terimler       hepsi                 26/41       —
        genis     anahtar_kelimeler    hepsi                 41/41    5.809
        odakli    anahtar_kelimeler    yalnızca >=5 hane     39/41    2.867
        (*) tüm tabloda (47.001 ihale) filtreyi geçen sayısı, 06.08.2026 ölçümü

    ELEME için `genis` ZORUNLU — kaçırma bu projenin en pahalı hatası ve yalnızca
    `genis` 41/41 veriyor.

    ETİKETLEME için `odakli` daha verimli: havuz yarıya iniyor ve pozitif
    yoğunluğu belirgin artıyor, çünkü gürültüyü üreten iki haneli CPV bölümleri
    (30, 31, 32, 48, 72, 713) dışarıda kalıyor. Etiketlemede eleme yapılmadığı
    için 39/41'lik geri çağırma sorun değildir — hiçbir ihale kaybolmuyor,
    yalnızca ÖNCE hangilerine bakılacağı seçiliyor.

    `negatif_terimler` HİÇBİR MODDA ALINMAZ — onlar dışlama içindir, aday
    getirme için değil. Karıştırılırsa filtre tam ters çalışır.

    Ölçüm: `python evaluation/sql_filtre_analizi.py --genislik dar|genis|odakli`
    """
    if genislik not in ("dar", "genis", "odakli"):
        raise ValueError("genislik 'dar', 'genis' ya da 'odakli' olmalı.")

    terimler: set[str] = set()
    on_ekler: set[str] = set()
    for p in profiller or []:
        kaynak = p.guclu_terimler if genislik == "dar" else p.anahtar_kelimeler
        for t in kaynak or []:
            t = str(t).strip().lower()
            if len(t) >= MIN_TERIM_UZUNLUGU:
                terimler.add(t)
        for on_ek in p.okas_on_ekleri or []:
            on_ek = str(on_ek).strip()
            if on_ek and (genislik != "odakli" or len(on_ek) >= MIN_ON_EK_UZUNLUGU):
                on_ekler.add(on_ek)

    for t in ek_terimler or []:
        t = str(t).strip().lower()
        if len(t) >= MIN_TERIM_UZUNLUGU:
            terimler.add(t)
    return FiltreTanimi(tuple(sorted(terimler)), tuple(sorted(on_ekler)), kaynak=f"profiller/{genislik}")


def where_uret(tanim: FiltreTanimi, tablo_takma_adi: str = "t") -> tuple[str, list]:
    """(sql_parcasi, parametreler) döndürür. Terimler SORGUYA GÖMÜLMEZ.

    Üretilen ifade: ihale adı terimlerden birini içeriyor VEYA OKAS kodu
    ön eklerden biriyle başlıyor.
    """
    if tanim.bos_mu():
        return "", []

    t = tablo_takma_adi
    parcalar: list[str] = []
    parametreler: list = []

    if tanim.terimler:
        # unnest(array) — tek parametre, tek tarama. Terim sayısı 274 olduğu için
        # tek tek OR yazmak sorguyu okunmaz ve yavaş yapardı.
        # ILIKE DEĞİL, elle katlanmış LIKE: Türkçe 'İ/I/ı' harmanlama tuzağı
        # (bkz. modül başlığındaki katlama bölümü).
        parcalar.append(
            f"EXISTS (SELECT 1 FROM unnest(%s::text[]) AS k "
            f"WHERE {_sql_katla(t + '.adi')} LIKE '%%'||k||'%%')"
        )
        parametreler.append([katla(x) for x in tanim.terimler])

    if tanim.okas_on_ekleri:
        parcalar.append(
            "EXISTS (SELECT 1 FROM public.tender_okas_codes o "
            f"WHERE o.tender_id = {t}.id "
            "AND EXISTS (SELECT 1 FROM unnest(%s::text[]) AS p WHERE o.kod LIKE p||'%%'))"
        )
        parametreler.append(list(tanim.okas_on_ekleri))

    return "(" + " OR ".join(parcalar) + ")", parametreler


def eslesme_var_mi(tanim: FiltreTanimi, adi: str, okas_kodlari=None) -> bool:
    """SQL'in Python karşılığı — ölçüm ve SQLite arka ucu için.

    `where_uret` ile AYNI mantığı uygulamalı; `tests/test_sql_filtre.py`
    ikisinin aynı sonucu verdiğini kontrol eder.
    """
    h = katla(adi)
    if any(katla(t) in h for t in tanim.terimler):
        return True
    for kod in okas_kodlari or []:
        k = str(kod)
        if any(k.startswith(p) for p in tanim.okas_on_ekleri):
            return True
    return False


# ============================================================================
# TEXT-TO-SQL DOĞRULAYICISI
# ============================================================================
# Bir LLM'e `WHERE` ifadesi ürettirilecekse çalıştırmadan ÖNCE buradan geçmeli.
# Model iyi niyetli olsa bile üretilen metin doğrulanmadan canlı veritabanına
# gönderilemez.
_YASAK = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|"
    r"vacuum|call|do|merge|commit|rollback|set|pg_sleep|pg_read_file|"
    r"pg_ls_dir|lo_import|lo_export|dblink)\b",
    re.I,
)
# İzin verilen tablo/sütun adları. Model bunların dışına çıkarsa reddedilir.
IZINLI_SUTUNLAR = frozenset({
    "t", "o", "n", "adi", "ikn", "idare_adi", "il", "ihale_turu", "ihale_usulu",
    "ihale_durumu", "ihale_tarihi", "kapsam", "tender_id", "id", "kod", "ad",
    "ilan_tipi", "ilan_tarihi", "icerik_temiz", "baslik",
    "public", "tenders", "tender_okas_codes", "tender_announcements",
})


def sql_guvenli_mi(ifade: str) -> tuple[bool, str]:
    """LLM'in ürettiği `WHERE` ifadesi çalıştırılabilir mi?

    Dönüş: (güvenli_mi, gerekçe). Şüphede REDDET — bu bir karar kapısı değil,
    bir güvenlik kapısı; yanlış negatifin maliyeti düşük.
    """
    if not ifade or not ifade.strip():
        return False, "boş ifade"
    s = ifade.strip()
    if ";" in s:
        return False, "';' içeriyor — zincirlenmiş ifade riski"
    if "--" in s or "/*" in s:
        return False, "SQL yorumu içeriyor"
    m = _YASAK.search(s)
    if m:
        return False, f"yasak anahtar kelime: {m.group(0)}"
    if s.count("(") != s.count(")"):
        return False, "parantezler dengesiz"
    # `select` yalnızca alt sorgu olarak serbest; başta olamaz.
    if re.match(r"^\s*select\b", s, re.I):
        return False, "tam sorgu değil, SADECE WHERE ifadesi bekleniyor"
    # Tanımlayıcı denetimi: nokta ile ayrılmış her ad izin listesinde olmalı.
    for ad in re.findall(r"\b([a-zA-Z_][a-zA-Z_0-9]*)\s*\.", s):
        if ad.lower() not in IZINLI_SUTUNLAR:
            return False, f"izin verilmeyen tablo/takma ad: {ad}"
    return True, "geçti"


# Text-to-SQL istenirse modele verilecek şema. Üç tablo — küçük ve sabit.
SEMA_TANIMI = """public.tenders           (id, ikn, adi, idare_adi, il, ihale_turu,
                          ihale_usulu, ihale_durumu, ihale_tarihi, kapsam)
public.tender_announcements (id, tender_id, ilan_tipi, ilan_tarihi, baslik, icerik_temiz)
public.tender_okas_codes    (id, tender_id, kod, ad)

Takma adlar: tenders -> t, tender_okas_codes -> o, tender_announcements -> n
SADECE `WHERE` ifadesinin GÖVDESİNİ üret. SELECT/FROM/ORDER BY YAZMA.
Noktalı virgül, yorum satırı ve veri değiştiren hiçbir komut KULLANMA."""
