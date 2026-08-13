"""
Karar şemaları — iki aşama, iki taksonomi, tek birleşik çıktı.

=============================================================================
ALAN SIRASI BİLİNÇLİDİR — BU BÖLÜMÜ DEĞİŞTİRMEDEN ÖNCE OKUYUN
=============================================================================
Ollama'nın yapılandırılmış çıktısı (format=<json şeması>) alanları ŞEMADA TANIMLANDIĞI
SIRAYLA üretir. Bu, model için "önce neyi düşüneceği" demektir.

ÖNCEKİ SÜRÜMDE `karar` ilk alandı ve model hiçbir gerekçe veya skor üretmeden karara
kilitleniyordu; gerekçe, kararın SEBEBİ değil, sonradan uydurulan bir açıklaması
oluyordu. Ölçülmüş gerçek vaka: bir kurumsal yazılım lisansı ihalesinde model
ilgi_skoru=0.4846 üretmişken karar="uygun" dedi — yani kendi sistem promptundaki
"0.5 altındaysa asla uygun olamaz" kuralını açıkça çiğnedi.

Bu yüzden `karar` artık EN SON alandır: model önce gerekçesini yazar, sonra bir skora
karar verir, kategori kararını en sonda ve bu akıl yürütmeye dayanarak verir.
Aşama 2 şeması da aynı ilkeye göre sıralanmıştır.
=============================================================================
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# ============================================================================
# AŞAMA 1 — KAPSAM:  "Bu iş İSBAK'ın alanına giriyor mu?"
# ============================================================================

KapsamKarari = Literal["uygun", "belirsiz", "uygun_degil"]

# `belirsiz` kararının SEBEBİ. Aynı etikette iki farklı olgu toplanıyordu ve
# ikisi FARKLI İŞLEM gerektiriyor:
#
#   kanit_yetersiz  İlan işin ne olduğunu söylemiyor ("13 modülden oluşan …",
#                   "ayrıntı şartnamede"). Model kararsız DEĞİL — elinde veri yok.
#                   İNSANA GİTMELİ; kod bunu asla `uygun_degil`e çeviremez.
#
#   zayif_ortusme   İlan yeterince açık AMA İSBAK'ın alanıyla örtüşme zayıf.
#                   Model gerçekten kararsız. Düşük skorda `uygun_degil`e
#                   netleştirilebilir — ölçüldü, 13/13 doğru çıktı.
#
# GERÇEK VAKA (30.07): "Dijital İnsan Kaynakları Yönetim Sistemi Hizmet Alımı"
# ilanı yalnızca "1 Adet 13 Modülden oluşan … Ayrıntılı bilgiye idari şartnameden
# ulaşılabilir" diyor. B1 kuralı gereği `belirsiz` olmalıydı ve B1 skoru zaten
# 0.5 ile sınırlıyor — yani netleştirme eşiğinin (0.50) tam menziline düşüyor.
# Sonuç: "bilmiyorum, insana sor" sinyali sessizce "reddet"e dönüşüyordu.
#
# ÖLÇEK: aktif ihalelerin %99'u "Ön İlan"dan okunuyor ve Ön İlan kalem listesi
# vermiyor. Yani bu tek bir ihalenin sorunu değil, kuyruğun tamamının.
BelirsizTipi = Literal["kanit_yetersiz", "zayif_ortusme"]


class KapsamSonucu(BaseModel):
    """Aşama 1 çıktısı. ALAN SIRASI ÖNEMLİ (bkz. modül başlığı)."""

    gerekce: str = Field(
        min_length=1,
        description="Kararın Türkçe gerekçesi — İLK üretilen alan, karardan ÖNCE yazılır. 1-3 cümle.",
    )
    eslesen_paket: Optional[str] = Field(
        default=None,
        description=(
            "İLGİLİ İŞ PAKETLERİ listesinden en çok örtüşen paketin TAM başlığı. "
            "karar 'uygun' ya da 'belirsiz' ise ZORUNLU. Sadece 'uygun_degil' ise ve "
            "hiçbir paketle gerçek örtüşme yoksa null bırakılabilir."
        ),
    )
    eslesen_okas: list[str] = Field(
        default_factory=list,
        description="Girdideki OKAS kodlarından kararla doğrudan ilgili olanlar.",
    )
    ilgi_skoru: float = Field(
        ge=0.0, le=1.0, description="0-1 arası örtüşme skoru — gerekçeye dayanır, karardan ÖNCE üretilir."
    )
    belirsiz_tipi: Optional[BelirsizTipi] = Field(
        default=None,
        description=(
            "SADECE karar 'belirsiz' olacaksa doldur, diğer hâllerde null. "
            "'kanit_yetersiz' = ilan işin ne olduğunu söylemiyor, elinde veri yok. "
            "'zayif_ortusme' = ilan açık ama İSBAK'ın alanıyla örtüşme zayıf. "
            "KARARDAN ÖNCE üretilir: 'kanıtım yeterli mi' sorusu 'hüküm ne' sorusundan önce gelir."
        ),
    )
    karar: KapsamKarari = Field(
        description="EN SON üretilen alan. Yukarıdaki gerekce ve ilgi_skoru ile TUTARLI olmalı."
    )


# ============================================================================
# FAALİYET ÖRTÜŞMESİ — İKİNCİ VE BAĞIMSIZ EKSEN (06.08.2026)
# ============================================================================
# `ilgi_skoru` "modelin kendi örtüşme yargısı" olsun diye tasarlandı. Olmadı:
# ÖLÇÜLDÜ (kaçırma seti v7) 30 kararın 18'inde `ilgi_skoru`, prompt'ta sunulan
# retrieval benzerliğinin BİREBİR kopyasıydı. Sayıyı prompt'tan gizleyerek
# (`nitel_yakinlik`) girdi tarafını kapattık; bu alan ÇIKTI tarafını kapatıyor.
# Kategorik bir alanın kopyalayacağı bir sayı yoktur.
#
# Asıl kazanç ayrı bir EKSEN olması. Bugün tek bir `ilgi_skoru` iki farklı soruyu
# taşımak zorunda:
#       "iş örtüşüyor mu?"          ve       "kanıtım yeterli mi?"
# İkisi bağımsız olabilir ve olduğunda mevcut şema bunu ifade edemiyor:
#
#   2026/121357 "Sondaj ve Workover Kuleleri Kamera Sistemi"
#       iş örtüşüyor (CCTV + video analitik = ENT-02'nin birincil yetkinliği)
#       bağlam örtüşmüyor (petrol sahası, ATEX/IECEx sertifikalı ex-proof cihaz)
#   -> faaliyet_ortusmesi="kismi" + belirsiz_tipi="zayif_ortusme" bunu söyleyebilir;
#      tek bir skor söyleyemez.
#
# Bu alan `ilgi_skoru`nun YERİNE değil YANINA konur; ikisi aynı koşuda yan yana
# ölçülebilsin diye. Kategorik olduğu için modelin arama skorundan kopyalaması da
# mümkün değildir.
FaaliyetOrtusmesi = Literal["guclu", "kismi", "zayif", "yok"]


class KapsamSonucuGenis(BaseModel):
    """`KapsamSonucu` + `faaliyet_ortusmesi`. `FAALIYET_ORTUSMESI=true` iken kullanılır.

    NEDEN KALITIM YOK: pydantic miras alınan alanları ÖNCE, yenileri SONRA sıralar.
    `KapsamSonucu`'ndan türetseydik `faaliyet_ortusmesi` `karar`dan SONRA üretilirdi —
    yani modelin kararını verdikten sonra örtüşmeyi sınıflandırması gerekirdi. Bu,
    modül başlığındaki hatanın aynısı olurdu. Alan sırası burada kopyalanarak
    korunuyor; iki sınıfın sırası `tests/test_faaliyet_ortusmesi.py` ile bağlı.
    """

    gerekce: str = Field(
        min_length=1,
        description="Kararın Türkçe gerekçesi — İLK üretilen alan, karardan ÖNCE yazılır. 1-3 cümle.",
    )
    faaliyet_ortusmesi: FaaliyetOrtusmesi = Field(
        description=(
            "İhalenin İŞİ, İSBAK'ın iş paketleriyle ne kadar örtüşüyor? Bu SADECE işin "
            "kendisiyle ilgilidir; ilanın yeterince açık olup olmadığıyla DEĞİL. "
            "'guclu' = iş, bir paketin birincil yetkinliğinin ta kendisi. "
            "'kismi' = iş o alana giriyor ama bağlamı/koşulları farklı (ör. kamera sistemi "
            "ama patlayıcı ortam sertifikası isteniyor). "
            "'zayif' = yalnızca kelime düzeyinde benzerlik var. "
            "'yok' = hiçbir paketle ilgisi yok. "
            "Sana verilen benzerlik/yakınlık bilgisini KOPYALAMA — bu senin kendi yargın."
        ),
    )
    eslesen_paket: Optional[str] = Field(
        default=None,
        description=(
            "İLGİLİ İŞ PAKETLERİ listesinden en çok örtüşen paketin TAM başlığı. "
            "karar 'uygun' ya da 'belirsiz' ise ZORUNLU. Sadece 'uygun_degil' ise ve "
            "hiçbir paketle gerçek örtüşme yoksa null bırakılabilir."
        ),
    )
    eslesen_okas: list[str] = Field(
        default_factory=list,
        description="Girdideki OKAS kodlarından kararla doğrudan ilgili olanlar.",
    )
    ilgi_skoru: float = Field(
        ge=0.0, le=1.0, description="0-1 arası örtüşme skoru — gerekçeye dayanır, karardan ÖNCE üretilir."
    )
    belirsiz_tipi: Optional[BelirsizTipi] = Field(
        default=None,
        description=(
            "SADECE karar 'belirsiz' olacaksa doldur, diğer hâllerde null. "
            "'kanit_yetersiz' = ilan işin ne olduğunu söylemiyor, elinde veri yok. "
            "'zayif_ortusme' = ilan açık ama İSBAK'ın alanıyla örtüşme zayıf. "
            "KARARDAN ÖNCE üretilir: 'kanıtım yeterli mi' sorusu 'hüküm ne' sorusundan önce gelir."
        ),
    )
    karar: KapsamKarari = Field(
        description="EN SON üretilen alan. Yukarıdaki gerekce ve ilgi_skoru ile TUTARLI olmalı."
    )


# ============================================================================
# AŞAMA 2 — YETERLİLİK:  "Bu ihalenin şartlarını karşılıyor muyuz?"
# ============================================================================

YeterlilikKarari = Literal["dogrudan_uygun", "inceleme_gerekli", "ilgisiz"]
KriterDurumu = Literal["karsilandi", "karsilanmadi", "belirsiz"]


class Kriter(BaseModel):
    kriter_id: str
    aciklama: str = ""
    gerekce: str = Field(default="", description="Bu kriterin neden bu durumda olduğu.")
    kanit_idleri: list[str] = Field(
        default_factory=list,
        description=(
            "Bu kriteri destekleyen KANIT listesindeki referanslar (ör. 'K1'). "
            "durum 'karsilandi' ise kanıt beklenir — yoksa kriter 'belirsiz'e düşürülür."
        ),
    )
    durum: KriterDurumu = Field(description="EN SON üretilen alan — yukarıdaki gerekçeyle tutarlı olmalı.")


class YeterlilikSonucu(BaseModel):
    """Aşama 2 ham model çıktısı (doğrulayıcıdan GEÇMEDEN önce)."""

    ozet: str = Field(min_length=1, description="Değerlendirmenin Türkçe özeti — İLK üretilen alan.")
    kriterler: list[Kriter] = Field(default_factory=list)
    riskler: list[str] = Field(default_factory=list)
    eksik_kanitlar: list[str] = Field(default_factory=list)
    guven: float = Field(ge=0.0, le=1.0, description="0-1 arası güven — karardan ÖNCE üretilir.")
    karar: YeterlilikKarari = Field(description="EN SON üretilen alan.")


class DogrulamaSonucu(BaseModel):
    """Deterministik kural doğrulayıcısının çıktısı (app/decision/dogrulayici.py)."""

    gecti: bool
    zorunlu_karar: Optional[YeterlilikKarari] = None
    celiskiler: list[str] = Field(default_factory=list)
    eksik_zorunlu_kanit: list[str] = Field(default_factory=list)
    gecersiz_kanit_referanslari: list[str] = Field(default_factory=list)
    uygulanan_kurallar: list[str] = Field(default_factory=list)
    uyarilar: list[str] = Field(default_factory=list)


# ============================================================================
# BİRLEŞİK ÇIKTI
# ============================================================================


class ModelKarari(BaseModel):
    model_adi: str
    sonuc: YeterlilikSonucu


class AnalizSonucu(BaseModel):
    """Bir ihalenin uçtan uca analiz sonucu — iki katman tek gövdede."""

    tender_id: str
    ikn: str
    adi: str
    idare_adi: str = ""
    ihale_durumu: str = ""

    # Aşama 0
    on_filtre_kurali: Optional[str] = None

    # Aşama 1 — kapsam
    kapsam: Optional[KapsamSonucu] = None
    kullanilan_paketler: list[dict] = Field(default_factory=list)
    benzer_uygun_ornekler: list[dict] = Field(default_factory=list)
    benzer_belirsiz_ornekler: list[dict] = Field(default_factory=list)
    benzer_red_ornekler: list[dict] = Field(default_factory=list)

    # Aşama 2 — yeterlilik
    yeterlilik: Optional[YeterlilikSonucu] = None
    ikincil_gorus: Optional[ModelKarari] = None
    dogrulama: Optional[DogrulamaSonucu] = None
    insan_incelemesi_gerekli: bool = False
    sebepler: list[str] = Field(default_factory=list)

    # İzlenebilirlik
    birincil_model: str = ""
    sureler_sn: dict[str, float] = Field(default_factory=dict)
    notlar: list[str] = Field(default_factory=list)

    def ozet_satiri(self) -> str:
        k = self.kapsam.karar if self.kapsam else (self.on_filtre_kurali or "-")
        y = self.yeterlilik.karar if self.yeterlilik else "-"
        return f"{self.ikn} | kapsam={k} | yeterlilik={y} | insan={self.insan_incelemesi_gerekli}"
