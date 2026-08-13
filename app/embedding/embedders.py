"""
Embedding katmanı — üç arka uç, tek arayüz.

DİKKAT — arka uçlar birbirinin yerine geçmez: Ollama'nın `/api/embeddings` ucu
(kuantize bge-m3) ile `sentence-transformers` (BAAI/bge-m3, fp32) AYNI modeli kullansa
da FARKLI vektörler üretir. Bu vektörler aynı koleksiyonda karıştırılamaz, yoksa
benzerlik hesabı anlamsızlaşır.

Çözüm: her arka ucun bir `imza`sı vardır ve her vektör koleksiyonu hangi imzayla
kurulduğunu kaydeder. Farklı bir imzayla sorgu gelirse hata verilir — bkz.
app/retrieval/qdrant_deposu.py. Arka ucu değiştirirseniz indeksi yeniden kurun.

Neden `ollama` python paketi yok: import anında global bir istemci kuruyor ve proxy'li
ortamlarda import'u patlatıyor. Ollama'nın REST API'sine doğrudan httpx ile gidiyoruz;
hiçbir özellik kaybı yok.
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol, Sequence


class Embedder(Protocol):
    imza: str
    boyut: int

    def embed(self, metinler: Sequence[str]) -> list[list[float]]: ...


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


class OllamaEmbedder:
    """Ollama'daki bge-m3. VARSAYILAN — elindeki kurulum, ek indirme yok.

    Ollama vektörleri normalize DÖNDÜRMEZ; burada elle normalize ediyoruz ki
    kosinüs benzerliği = iç çarpım olsun (Qdrant COSINE ile tutarlı).
    """

    def __init__(self, model: str = "bge-m3", host: str = "http://localhost:11434", timeout: float = 120.0):
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.imza = f"ollama:{model}"
        self._boyut: int | None = None

    @property
    def boyut(self) -> int:
        if self._boyut is None:
            self._boyut = len(self.embed(["boyut denetimi"])[0])
        return self._boyut

    def embed(self, metinler: Sequence[str]) -> list[list[float]]:
        import httpx  # tembel

        cikti: list[list[float]] = []
        with httpx.Client(timeout=self.timeout) as c:
            for metin in metinler:
                y = c.post(
                    f"{self.host}/api/embeddings",
                    json={"model": self.model, "prompt": str(metin)},
                )
                y.raise_for_status()
                cikti.append(_normalize(y.json()["embedding"]))
        return cikti


class SentenceTransformerEmbedder:
    """BAAI/bge-m3, sentence-transformers ile. Toplu indekslemede (binlerce ihale)
    GPU'da Ollama'dan belirgin hızlı. İlk kullanımda ~2.2GB model indirir."""

    def __init__(
        self,
        model: str = "BAAI/bge-m3",
        device: str = "cpu",
        cache_folder: str | None = None,
        batch_size: int = 8,
    ):
        from sentence_transformers import SentenceTransformer  # tembel

        self.model_adi = model
        self.batch_size = batch_size
        self.imza = f"st:{model}"
        self._model = SentenceTransformer(model, device=device, cache_folder=cache_folder)
        self._boyut: int | None = None

    @property
    def boyut(self) -> int:
        if self._boyut is None:
            self._boyut = len(self.embed(["boyut denetimi"])[0])
        return self._boyut

    def embed(self, metinler: Sequence[str]) -> list[list[float]]:
        if not metinler:
            return []
        v = self._model.encode(
            [str(m).strip() for m in metinler],
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return v.tolist()


class FakeEmbedder:
    """Model gerektirmeyen, deterministik sahte gömme — SADECE TEST İÇİN.

    Kelime seviyesinde hash'leyip torba-vektör üretir; anlamsal değildir ama
    aynı metin her zaman aynı vektörü verir ve kelime örtüşmesi olan metinler
    birbirine yakın çıkar. Bu sayede TÜM boru hattı (indeksleme, sorgulama,
    skorlama, karar akışı) Ollama/GPU olmadan uçtan uca test edilebilir.

    ASLA gerçek karar üretmek için kullanılmaz — imzası koleksiyona yazılır,
    yanlışlıkla gerçek bir koleksiyona karışırsa hata verir.
    """

    imza = "fake:hash-bow"

    def __init__(self, boyut: int = 256):
        self.boyut = boyut

    def embed(self, metinler: Sequence[str]) -> list[list[float]]:
        cikti = []
        for metin in metinler:
            v = [0.0] * self.boyut
            for kelime in str(metin).lower().split():
                h = int(hashlib.md5(kelime.encode("utf-8")).hexdigest(), 16)
                v[h % self.boyut] += 1.0
            cikti.append(_normalize(v) if any(v) else [1.0] + [0.0] * (self.boyut - 1))
        return cikti


def embedder_olustur(ayarlar=None) -> Embedder:
    """Fabrika — .env'deki EMBEDDING_BACKEND'e göre doğru arka ucu döndürür."""
    if ayarlar is None:
        from app.config.settings import ayarlari_al

        ayarlar = ayarlari_al()

    arka_uc = ayarlar.embedding_backend
    if arka_uc == "ollama":
        return OllamaEmbedder(model=ayarlar.embedding_model, host=ayarlar.ollama_host)
    if arka_uc == "sentence-transformers":
        return SentenceTransformerEmbedder(
            model="BAAI/bge-m3", cache_folder=str(ayarlar.model_cache_yolu)
        )
    if arka_uc == "fake":
        return FakeEmbedder()
    raise ValueError(f"Bilinmeyen EMBEDDING_BACKEND: {arka_uc}")
