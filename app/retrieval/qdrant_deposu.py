"""
Qdrant vektör deposu — benzer içerik aramasının motoru.

NEDEN QDRANT: Projenin ilk sürümlerinde Chroma ve FAISS denendi; prototip için
yeterliydiler ama üretimde şunlar gerekiyor: metadata'ya göre filtreleme, deterministik
ve tekil nokta kimliği, ihale bazında silip yeniden yazabilme (artımlı güncelleme) ve
gömülü mod ile sunucu modu arasında kod değiştirmeden geçebilme.

GÜVENLİK KİLİDİ: Her koleksiyon, hangi embedder imzasıyla kurulduğunu kaydeder. Farklı
bir imzayla sorgu gelirse SESSİZCE YANLIŞ SONUÇ ÜRETMEK yerine hata verilir. Ollama'nın
bge-m3'ü ile sentence-transformers'ın bge-m3'ü aynı model olmalarına rağmen birbiriyle
uyumsuz vektörler üretir (bkz. app/embedding/embedders.py).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from uuid import NAMESPACE_URL, uuid5

# ---------------------------------------------------------------------------
# İSTEMCİ PAYLAŞIMI — Qdrant'ın gömülü modu KLASÖR BAŞINA TEK İSTEMCİ kabul eder.
#
# Depolama klasörü üzerinde işletim sistemi seviyesinde özel (exclusive) bir kilit
# alınır. Aynı süreç içinde bile ikinci bir QdrantClient(path=...) açmaya çalışmak
# şu hatayı verir:
#     "Storage folder ... is already accessed by another instance of Qdrant client"
#
# Üç koleksiyonumuz (profiller, uygun örnekler, red örnekler) AYNI klasörde durduğu
# için her biri kendi istemcisini açamaz. Aşağıdaki önbellek, yol başına tek istemci
# tutar ve referans sayar; son kullanan kapatınca istemci gerçekten kapanır.
#
# `:memory:` modu bu kısıta tabi değildir (dosya kilidi yok), o yüzden önbelleğe
# alınmaz — testlerin birbirinin durumunu görmemesi için de bu tercih edilir.
# ---------------------------------------------------------------------------

_ISTEMCILER: dict[str, list] = {}  # mutlak yol -> [istemci, referans_sayisi]
_ISTEMCI_KILIDI = threading.Lock()


def _istemci_al(yol: Path):
    from qdrant_client import QdrantClient

    anahtar = str(yol.resolve())
    with _ISTEMCI_KILIDI:
        kayit = _ISTEMCILER.get(anahtar)
        if kayit is not None:
            kayit[1] += 1
            return kayit[0]
        try:
            istemci = QdrantClient(path=anahtar)
        except RuntimeError as e:
            if "already accessed" in str(e):
                raise RuntimeError(
                    f"Qdrant depolama klasörü başka bir SÜREÇ tarafından kilitli:\n"
                    f"  {anahtar}\n\n"
                    f"Muhtemel sebep: aynı anda çalışan başka bir script (ör. arka planda "
                    f"kalmış bir index_profiles.py / check_setup.py) ya da çökmüş bir Python "
                    f"süreci. Açık Python pencerelerini kapatıp tekrar deneyin."
                ) from e
            raise
        _ISTEMCILER[anahtar] = [istemci, 1]
        return istemci


def _istemci_birak(yol: Path) -> None:
    anahtar = str(yol.resolve())
    with _ISTEMCI_KILIDI:
        kayit = _ISTEMCILER.get(anahtar)
        if kayit is None:
            return
        kayit[1] -= 1
        if kayit[1] <= 0:
            del _ISTEMCILER[anahtar]
            kapat = getattr(kayit[0], "close", None)
            if callable(kapat):
                try:
                    kapat()
                except Exception:  # noqa: BLE001 — kapatma hatası akışı durdurmasın
                    pass

# Koleksiyon adları
KOLEKSIYON_PROFIL = "isbak_profiller"
KOLEKSIYON_ORNEK_UYGUN = "isbak_ornek_uygun"
KOLEKSIYON_ORNEK_BELIRSIZ = "isbak_ornek_belirsiz"
KOLEKSIYON_ORNEK_RED = "isbak_ornek_red"
KOLEKSIYON_GECMIS_IHALE = "ekap_gecmis_ihaleler"

# Üç örnek koleksiyonu, üç ayrı etiket. HEPSİ AYRI DURMAK ZORUNDA.
#
# 28.07.2026 — v1 koşusunda (%80, 12/15) üç hatanın ÜÇÜ DE `belirsiz -> uygun` çıktı.
# Sebep: `belirsiz` örnekler `isbak_ornek_uygun` koleksiyonuna konuyordu ve modele
# "DAHA ÖNCE GERÇEKTEN UYGUN BULUNMUŞ (onaylanmış örnekler)" başlığıyla gösteriliyordu.
# 11 kaydın 6'sı yanlış etiketliydi. Model de gerekçelerinde bunları aynen alıntıladı:
#   "Geçmişte benzer güvenlik sistemleri ihaleleri 'uygun' olarak değerlendirildiğinden..."
# — oysa alıntıladığı örnek `belirsiz` etiketliydi. Model doğru akıl yürütüyordu,
# kanıt yanlış etiketliydi. Artık üç grup ayrı ve doğru adıyla gösteriliyor.
ORNEK_KOLEKSIYONLARI = (
    KOLEKSIYON_ORNEK_UYGUN,
    KOLEKSIYON_ORNEK_BELIRSIZ,
    KOLEKSIYON_ORNEK_RED,
)


def nokta_id(*parcalar: str) -> str:
    """Deterministik nokta kimliği — aynı kaynak her zaman aynı id'yi üretir,
    böylece yeniden indeksleme kopya yaratmaz, üzerine yazar."""
    return str(uuid5(NAMESPACE_URL, "|".join(str(p) for p in parcalar)))


@dataclass
class Vurus:
    id: str
    skor: float          # kosinüs benzerliği (1.0 = birebir aynı yön)
    payload: dict[str, Any]


class QdrantDeposu:
    BELLEK = ":memory:"

    def __init__(self, yol: str | Path, koleksiyon: str) -> None:
        from qdrant_client import QdrantClient  # tembel

        self.koleksiyon = koleksiyon
        # Bellek-içi mod: testler ve kalıcı diske yazamayan ortamlar için.
        # Qdrant'ın gömülü modu kalıcılık için SQLite kullanır; bazı ağ/bağlı
        # dosya sistemleri gereken kilitlemeyi desteklemez ("disk I/O error").
        # Bu durumda QDRANT_YOLU=:memory: verilebilir (veri kalıcı olmaz).
        self.bellek_ici = str(yol) == self.BELLEK
        self._kapandi = False
        # sifirla=True koşusunda silinmeye direnen nokta sayısı (bkz. _kalintiyi_temizle).
        # 0'dan büyükse Windows'un dosya kilidi yüzünden sıfırlama sessizce kaçmış demektir.
        self.son_kalinti = 0
        if self.bellek_ici:
            self.yol = Path(".")
            self._istemci = QdrantClient(location=self.BELLEK)
            self._imzalar: dict[str, dict] = {}
        else:
            self.yol = Path(yol)
            self.yol.mkdir(parents=True, exist_ok=True)
            # Paylaşılan istemci — aynı klasördeki diğer koleksiyonlarla ortak.
            self._istemci = _istemci_al(self.yol)

    def kapat(self) -> None:
        """Bu koleksiyonun istemci üzerindeki payını bırakır.

        Dosya tabanlı modda istemci PAYLAŞILDIĞI için burada körü körüne close()
        çağrılamaz — aynı klasördeki diğer koleksiyonlar hâlâ kullanıyor olabilir.
        Referans sayacı sıfırlanınca gerçekten kapanır. Çift kapatmaya karşı da
        korumalı (with bloğu + elle kapat çağrısı üst üste gelebilir).
        """
        if self._kapandi:
            return
        self._kapandi = True
        if self.bellek_ici:
            kapat = getattr(self._istemci, "close", None)
            if callable(kapat):
                kapat()
        else:
            _istemci_birak(self.yol)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.kapat()

    def __del__(self):
        # Yorumlayıcı kapanırken çöp toplayıcı QdrantClient'ı yıkarken
        # "Exception ignored while calling deallocator" uyarısı basıyordu — modüller
        # o sırada zaten boşaltılmış oluyor. İşlevsel bir sorun değil ama çıktıyı
        # kirletiyor ve gerçek hata sanılıyor. Sessizce yutuyoruz.
        try:
            self.kapat()
        except Exception:  # noqa: BLE001
            pass

    def var_mi(self) -> bool:
        return self._istemci.collection_exists(self.koleksiyon)

    def sayi(self) -> int:
        if not self.var_mi():
            return 0
        return int(self._istemci.count(collection_name=self.koleksiyon, exact=True).count)

    # ---- Kurulum ----

    def hazirla(self, *, boyut: int, imza: str, sifirla: bool = False) -> None:
        from qdrant_client.http import models

        if sifirla and self.var_mi():
            self._istemci.delete_collection(self.koleksiyon)

        if not self.var_mi():
            self._istemci.create_collection(
                collection_name=self.koleksiyon,
                vectors_config=models.VectorParams(size=boyut, distance=models.Distance.COSINE),
            )
            self._imza_yaz(imza, boyut)
            if sifirla:
                self.son_kalinti = self._kalintiyi_temizle()
            return

        # Var olan koleksiyon: boyut ve imza uyuşuyor mu?
        mevcut = self._imza_oku()
        if mevcut is None:
            return  # eski/imzasız koleksiyon — dokunma
        if mevcut["imza"] != imza:
            raise ValueError(
                f"'{self.koleksiyon}' koleksiyonu '{mevcut['imza']}' embedder'ıyla kuruldu, "
                f"şimdi '{imza}' ile sorgulanıyor. Aynı model olsalar bile vektörler uyumsuz. "
                f"Ya .env'deki EMBEDDING_BACKEND'i geri alın ya da koleksiyonu sifirla=True "
                f"ile yeniden kurun."
            )
        if mevcut["boyut"] != boyut:
            raise ValueError(
                f"'{self.koleksiyon}' vektör boyutu {mevcut['boyut']}, beklenen {boyut}. "
                f"Koleksiyonu sifirla=True ile yeniden kurun."
            )

    def _kalintiyi_temizle(self) -> int:
        """`delete_collection` sonrası HAYATTA KALAN noktaları nokta nokta siler.

        ================== WINDOWS'A ÖZGÜ SESSİZ BOZULMA (30.07.2026) ==================
        qdrant_client'ın gömülü modunda `delete_collection`, koleksiyon klasörünü
        `shutil.rmtree(path, ignore_errors=True)` ile siler. Bağlantıyı kapatmadan.

        Linux'ta açık bir dosya silinebildiği için bu sorunsuz çalışır.
        WINDOWS'ta `storage.sqlite` hâlâ açık olduğundan silme BAŞARISIZ olur ve
        `ignore_errors=True` yüzünden hata YUTULUR. Hemen ardından gelen
        `create_collection` aynı dosyayı yeniden açar ve ESKİ NOKTALAR GERİ GELİR.

        Yaşanan vaka: `index_profiles.py --sifirla --ornek-seti ...` ekrana
        "isbak_ornek_uygun +5 kayıt" bastı; koleksiyonda 11 kayıt kaldı — 5 doğru
        `uygun` + üç-grup ayrımından önce yazılmış 6 `belirsiz`. Aynı 6 ihale kendi
        koleksiyonunda da durduğu için model onları HEM "ETİKET: UYGUN" HEM
        "ETİKET: BELİRSİZ" başlığı altında gördü.

        Daha kötüsü: `--ornekleri-temizle` ve `--belirsiz-yok` bayrakları da tamamen
        etkisizdi. "Kontrolsüz taban ölçümü" diye alınan koşular aslında dolu bir
        RAG ile koşmuştu ve bunu hiçbir çıktı belli etmiyordu.

        Bu yüzden sıfırlama artık DOĞRULANIYOR: yaratma sonrası koleksiyon boş
        değilse kalan her nokta id'siyle silinir. Kaç nokta kurtarıldığı
        `son_kalinti` alanında durur; çağıran taraf bunu rapor edebilir.
        ================================================================================
        """
        if self.sayi() == 0:
            return 0
        from qdrant_client.http import models

        idler: list = []
        offset = None
        while True:
            noktalar, offset = self._istemci.scroll(
                collection_name=self.koleksiyon,
                limit=256,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            idler.extend(n.id for n in noktalar)
            if offset is None:
                break
        if idler:
            self._istemci.delete(
                collection_name=self.koleksiyon,
                points_selector=models.PointIdsList(points=idler),
                wait=True,
            )
        return len(idler)

    _IMZA_DOSYASI = "_imzalar.json"

    def _imza_yolu(self) -> Path:
        return self.yol / self._IMZA_DOSYASI

    def _imza_yaz(self, imza: str, boyut: int) -> None:
        if self.bellek_ici:
            self._imzalar[self.koleksiyon] = {"imza": imza, "boyut": boyut}
            return
        p = self._imza_yolu()
        d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        d[self.koleksiyon] = {"imza": imza, "boyut": boyut}
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    def _imza_oku(self) -> dict | None:
        if self.bellek_ici:
            return self._imzalar.get(self.koleksiyon)
        p = self._imza_yolu()
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8")).get(self.koleksiyon)

    # ---- Yazma ----

    def ekle(
        self,
        *,
        idler: Sequence[str],
        vektorler: Sequence[Sequence[float]],
        payloadlar: Sequence[dict[str, Any]],
        parti: int = 64,
    ) -> int:
        from qdrant_client.http import models

        toplam = 0
        yigin: list = []
        for i, v, p in zip(idler, vektorler, payloadlar):
            yigin.append(models.PointStruct(id=i, vector=list(v), payload=p))
            if len(yigin) >= parti:
                self._istemci.upsert(collection_name=self.koleksiyon, points=yigin, wait=True)
                toplam += len(yigin)
                yigin = []
        if yigin:
            self._istemci.upsert(collection_name=self.koleksiyon, points=yigin, wait=True)
            toplam += len(yigin)
        return toplam

    def tender_sil(self, tender_id: str) -> None:
        from qdrant_client.http import models

        if not self.var_mi():
            return
        self._istemci.delete(
            collection_name=self.koleksiyon,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="tender_id", match=models.MatchValue(value=str(tender_id)))]
                )
            ),
            wait=True,
        )

    # ---- Okuma ----

    def ara(
        self,
        *,
        vektor: Sequence[float],
        limit: int = 5,
        min_skor: float | None = None,
        haric_tender_id: str | None = None,
    ) -> list[Vurus]:
        """Kosinüs benzerliğine göre en yakın `limit` kaydı döndürür.

        `haric_tender_id`: Bir ihalenin kendisini kendi kanıtı olarak göstermeyi
        engeller (aynı İKN eleme kuralı).
        """
        if not self.var_mi() or self.sayi() == 0:
            return []
        from qdrant_client.http import models

        filtre = None
        if haric_tender_id:
            filtre = models.Filter(
                must_not=[
                    models.FieldCondition(
                        key="tender_id", match=models.MatchValue(value=str(haric_tender_id))
                    )
                ]
            )

        sonuc = self._istemci.query_points(
            collection_name=self.koleksiyon,
            query=list(vektor),
            limit=limit,
            query_filter=filtre,
            score_threshold=min_skor,
            with_payload=True,
        ).points

        return [Vurus(id=str(n.id), skor=float(n.score), payload=dict(n.payload or {})) for n in sonuc]
