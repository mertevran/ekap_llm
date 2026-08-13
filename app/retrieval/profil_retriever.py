"""
Aşama 1 benzerlik araması — "bu ihale hangi iş paketine benziyor?"

Üç ayrı vektör koleksiyonu kullanılır:
  A) isbak_profiller     — 20 profil. SOYUT kategori tanımları.
  B) isbak_ornek_uygun   — geçmişte gerçekten "uygun" bulunmuş SOMUT ihaleler.
  C) isbak_ornek_red     — yüzeysel benzese de "uygun_degil" bulunmuş SOMUT ihaleler.

C KOLEKSİYONU NEDEN VAR: Ölçüldü — modele SADECE olumlu örnek göstermek, kararı
"uygun" yönüne kaydırıyor ve yanlış alarmları artırıyor. Her aday için en yakın KABUL
EDİLMİŞ ve en yakın REDDEDİLMİŞ gerçek örneği yan yana göstermek, modeli gerçek bir
karşılaştırma yapmaya zorluyor.

SIZINTI GÜVENLİĞİ: B ve C koleksiyonlarına, karar setinin `final` (held-out) kısmındaki
hiçbir ihale GİREMEZ. Bu kural burada değil, koleksiyonu dolduran tarafta (bkz.
scripts/index_profiles.py `--ornek-seti`) uygulanır ve orada test edilir.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.embedding.embedders import Embedder
from app.profiles.loader import profilleri_yukle
from app.retrieval.qdrant_deposu import (
    KOLEKSIYON_ORNEK_BELIRSIZ,
    KOLEKSIYON_ORNEK_RED,
    KOLEKSIYON_ORNEK_UYGUN,
    KOLEKSIYON_PROFIL,
    QdrantDeposu,
    nokta_id,
)

# ÖLÇÜLDÜ (28.07.2026, yerel ekap.db'deki 4.868 AKTİF ihale, temizlenmiş kapsam metni):
#   medyan 810 | ortalama 1.292 | p90 2.918 | p95 4.059 | p99 7.326 | maks 16.286
#   2.000 karakteri aşan: 633 ihale (%13,0)
#   4.000 karakteri aşan: 257 ihale ( %5,3)
#
# Bu sınır önceden 2.000'di ve "neredeyse hiç devreye girmez" varsayılıyordu. O varsayım
# TÜM veritabanının ortalamasına dayanıyordu; oysa veritabanının %65'i kısa "Sonuç İlanı"
# kayıtlarıdır. Üretimde görülecek olan AKTİF ihalelerde sınır %13 oranında devreye
# giriyordu — üstelik kırpılanlar tam olarak uzun ve çok kalemli, yani kararı en zor olan
# ihalelerdi. 4.000'e çıkarıldı: artık aktif ihalelerin %94,7'si tam olarak gömülüyor.
SORGU_ILAN_MAX_KARAKTER = 4000


@dataclass
class PaketVurusu:
    kod: str
    baslik: str
    benzerlik: float
    oncelik: str
    negatif_terimler: list[str] = field(default_factory=list)


@dataclass
class OrnekVurusu:
    id: str
    baslik: str
    benzerlik: float


def sorgu_metni(adi: str, kapsam: str | None = None) -> str:
    """Retrieval için gömülecek metin: başlık + temizlenmiş kapsam.

    NEDEN SADECE BAŞLIK YETMİYOR: Yalnızca başlıkla arama yapıldığında, ilan metninde
    geçen ama başlıkta geçmeyen teknik bileşenler (kamera, yazılım modülleri gibi)
    kaçırılıyordu.
    """
    adi = adi or ""
    if not kapsam:
        return adi
    return f"{adi}\n{kapsam[:SORGU_ILAN_MAX_KARAKTER]}"


def profilleri_indeksle(depo: QdrantDeposu, embedder: Embedder, *, sifirla: bool = False) -> int:
    """20 profili A koleksiyonuna yazar. Deterministik id sayesinde tekrar
    çalıştırmak kopya üretmez, üzerine yazar."""
    profiller = profilleri_yukle()
    depo.hazirla(boyut=embedder.boyut, imza=embedder.imza, sifirla=sifirla)
    if depo.sayi() > 0 and not sifirla:
        return 0
    metinler = [p.embed_metni() for p in profiller]
    return depo.ekle(
        idler=[nokta_id("profil", p.kod) for p in profiller],
        vektorler=embedder.embed(metinler),
        payloadlar=[
            {
                "kod": p.kod,
                "baslik": p.ad,
                "aile": p.aile,
                "oncelik": p.oncelik,
                "negatif_terimler": p.negatif_terimler,
            }
            for p in profiller
        ],
    )


def koleksiyonlari_dogrula(qdrant_yolu, karar_seti_yolu) -> list[str]:
    """Kontrastif koleksiyonların içeriği karar setiyle TUTARLI mı? Sorun listesi döner.

    NEDEN GEREKLİ (30.07.2026'da yaşandı): `storage/` gitignore'da olduğu için
    koleksiyonlar makineler arasında taşınmıyor; her makinede `index_profiles.py`
    en son NE ZAMAN koşulduysa o hâlde kalıyor. Üç-grup ayrımından önceki bir
    indeksleme kalmıştı ve `isbak_ornek_uygun` içinde 5 gerçek `uygun` + 6
    `belirsiz` duruyordu. Üstelik o 6'sı kendi koleksiyonunda da vardı, yani
    aynı ihale modele hem "ETİKET: UYGUN" hem "ETİKET: BELİRSİZ" başlığı altında
    ÇELİŞKİLİ olarak gösteriliyordu.

    Sessizdi: hiçbir hata vermedi, sadece ölçümler bozuldu — o gün alınan dört
    koşunun (kacirma/v4, ayar/v7, triyaj/v6, yanlis_alarm/v1) hepsi bu çelişkili
    RAG ile koştu ve `belirsiz` yığılmasının bir kısmı muhtemelen bundan geldi.

    Bu yüzden içerik artık her `check_setup` çağrısında doğrulanıyor.
    """
    import csv

    from app.retrieval.qdrant_deposu import (
        KOLEKSIYON_ORNEK_BELIRSIZ,
        KOLEKSIYON_ORNEK_RED,
        KOLEKSIYON_ORNEK_UYGUN,
        QdrantDeposu,
    )

    esleme = {
        KOLEKSIYON_ORNEK_UYGUN: "uygun",
        KOLEKSIYON_ORNEK_BELIRSIZ: "belirsiz",
        KOLEKSIYON_ORNEK_RED: "uygun_degil",
    }
    with open(karar_seti_yolu, encoding="utf-8-sig") as f:
        satirlar = list(csv.DictReader(f, delimiter=";"))
    etiket = {s["tender_id"]: (s.get("karar") or "").strip() for s in satirlar}
    final_idler = {
        s["tender_id"] for s in satirlar if (s.get("set") or "").strip() == "final"
    }

    sorunlar: list[str] = []
    icerikler: dict[str, set[str]] = {}

    for ad, beklenen in esleme.items():
        d = QdrantDeposu(qdrant_yolu, ad)
        try:
            if d.sayi() == 0:
                icerikler[ad] = set()
                continue
            noktalar, _ = d._istemci.scroll(collection_name=ad, limit=500, with_payload=True)
        finally:
            d.kapat()

        idler = {p.payload.get("tender_id") for p in noktalar}
        icerikler[ad] = idler

        for p in noktalar:
            tid = p.payload.get("tender_id")
            gercek = etiket.get(tid)
            baslik = (p.payload.get("baslik") or "")[:48]
            if gercek and gercek != beklenen:
                sorunlar.append(
                    f"{ad}: '{baslik}' gerçekte '{gercek}' etiketli — yanlış koleksiyonda"
                )
            if tid in final_idler:
                sorunlar.append(f"{ad}: FINAL SIZINTISI — '{baslik}'")

    adlar = list(esleme)
    for i, a1 in enumerate(adlar):
        for a2 in adlar[i + 1 :]:
            ortak = icerikler.get(a1, set()) & icerikler.get(a2, set())
            if ortak:
                sorunlar.append(
                    f"{len(ortak)} ihale HEM {a1} HEM {a2} içinde — modele çelişkili "
                    f"etiketle gösteriliyor"
                )
    return sorunlar


def ornekleri_indeksle(
    depo: QdrantDeposu, embedder: Embedder, ornekler: list[dict], *, sifirla: bool = False
) -> int:
    """B veya C koleksiyonunu doldurur. `ornekler`: {"id","baslik","metin"} sözlükleri."""
    depo.hazirla(boyut=embedder.boyut, imza=embedder.imza, sifirla=sifirla)
    if not ornekler:
        return 0
    metinler = [f"{o['baslik']}\n{o.get('metin','')}" for o in ornekler]
    return depo.ekle(
        idler=[nokta_id("ornek", depo.koleksiyon, o["id"]) for o in ornekler],
        vektorler=embedder.embed(metinler),
        payloadlar=[
            {"baslik": o["baslik"], "tender_id": o["id"], "kaynak": depo.koleksiyon} for o in ornekler
        ],
    )


class ProfilRetriever:
    """Üç koleksiyonu tek çağrıda sorgular. Olmayan koleksiyonlar sessizce atlanır —
    örnek koleksiyonları henüz doldurulmamışsa sistem yine de çalışır.

    BAĞLANTI ÖMRÜ: koleksiyonlar İLK KULLANIMDA açılır ve retriever kapatılana kadar
    AÇIK KALIR. İlk sürümde her sorgu kendi bağlantısını açıp kapatıyordu; 4.872
    ihalelik toplu taramada bu 14.000+ kez Qdrant açıp kapatmak demekti (gömülü mod
    her açılışta dosya kilidi alır). Bittiğinde `kapat()` çağırın ya da `with`
    kullanın.
    """

    def __init__(self, qdrant_yolu, embedder: Embedder):
        self.qdrant_yolu = qdrant_yolu
        self.embedder = embedder
        self._depolar: dict[str, QdrantDeposu] = {}

    def _depo(self, ad: str) -> QdrantDeposu:
        if ad not in self._depolar:
            self._depolar[ad] = QdrantDeposu(self.qdrant_yolu, ad)
        return self._depolar[ad]

    def kapat(self) -> None:
        for d in self._depolar.values():
            d.kapat()
        self._depolar.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.kapat()

    def paketleri_bul(self, metin: str, k: int = 5) -> list[PaketVurusu]:
        v = self.embedder.embed([metin])[0]
        vuruslar = self._depo(KOLEKSIYON_PROFIL).ara(vektor=v, limit=k)
        return [
            PaketVurusu(
                kod=h.payload.get("kod", ""),
                baslik=h.payload.get("baslik", ""),
                benzerlik=round(h.skor, 4),
                oncelik=h.payload.get("oncelik", "orta"),
                negatif_terimler=list(h.payload.get("negatif_terimler", []) or []),
            )
            for h in vuruslar
        ]

    def _ornek_bul(self, koleksiyon: str, metin: str, k: int, haric: str | None) -> list[OrnekVurusu]:
        d = self._depo(koleksiyon)
        if d.sayi() == 0:
            return []
        v = self.embedder.embed([metin])[0]
        vuruslar = d.ara(vektor=v, limit=k, haric_tender_id=haric)
        return [
            OrnekVurusu(id=h.id, baslik=h.payload.get("baslik", ""), benzerlik=round(h.skor, 4))
            for h in vuruslar
        ]

    def uygun_ornekler(self, metin: str, k: int = 3, haric: str | None = None) -> list[OrnekVurusu]:
        return self._ornek_bul(KOLEKSIYON_ORNEK_UYGUN, metin, k, haric)

    def belirsiz_ornekler(self, metin: str, k: int = 3, haric: str | None = None) -> list[OrnekVurusu]:
        """Daha önce `belirsiz` bulunmuş örnekler — KENDİ grubunda, kendi etiketiyle.

        v1'de bunlar `uygun` koleksiyonuna karıştırılmıştı ve üç hatanın üçünün de
        sebebi buydu (bkz. qdrant_deposu.py ORNEK_KOLEKSIYONLARI notu).
        """
        return self._ornek_bul(KOLEKSIYON_ORNEK_BELIRSIZ, metin, k, haric)

    def red_ornekler(self, metin: str, k: int = 3, haric: str | None = None) -> list[OrnekVurusu]:
        return self._ornek_bul(KOLEKSIYON_ORNEK_RED, metin, k, haric)
