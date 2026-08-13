"""
Kurulum duman testi — her şey yerinde mi, tek komutta.

    python scripts/check_setup.py

Her bileşeni ayrı ayrı kontrol eder ve BİRİ PATLASA BİLE diğerlerini denemeye devam
eder — böylece tek çalıştırmada tüm eksik listesini görürsün.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OK, HATA, UYARI = "  [OK]  ", "  [HATA]", "  [UYARI]"


def kontrol(ad, fn):
    # Kontrolün ADINI önce basıyoruz: Ollama'ya giden ilk çağrı model yüklenirken
    # dakikayı bulabiliyor ve ekranda hiçbir şey yokken "script dondu mu?" belirsizliği
    # oluşuyordu. flush=True Windows konsolunda tamponlanmayı da engelliyor.
    print(f"  ... {ad} kontrol ediliyor", end="", flush=True)
    try:
        ok, mesaj = fn()
    except Exception as e:  # noqa: BLE001 — kasıtlı: hiçbir kontrol diğerlerini durdurmasın
        print(f"\r{HATA} {ad}: {type(e).__name__}: {e}" + " " * 20, flush=True)
        return False
    print(f"\r{OK if ok else HATA} {ad}: {mesaj}" + " " * 20, flush=True)
    return ok


def main() -> int:
    from app.config.settings import ayarlari_al

    ayarlar = ayarlari_al()
    print(f"\n=== EkapUnified kurulum kontrolü ===")
    print(f"  veri kaynağı : {ayarlar.data_backend}")
    print(f"  embedding    : {ayarlar.embedding_backend} / {ayarlar.embedding_model}")
    print(f"  birincil LLM : {ayarlar.birincil_model}")
    print(f"  ikincil LLM  : {ayarlar.ikincil_model or '(kapalı)'}")
    print(f"  qdrant       : {ayarlar.qdrant_yolu}\n")

    sonuclar = []

    # 1) Profiller — hiçbir dış bağımlılığı yok, ilk bu kontrol edilir.
    def _profiller():
        from app.profiles.loader import profilleri_yukle

        ps = profilleri_yukle()
        neg = sum(len(p.negatif_terimler) for p in ps)
        return len(ps) == 20, f"{len(ps)} profil, toplam {neg} negatif terim"

    sonuclar.append(kontrol("Profiller", _profiller))

    # 2) Veri kaynağı
    def _veri():
        from app.database.base import depo_olustur

        d = depo_olustur(ayarlar)
        n = d.aktif_ihale_sayisi()
        return n > 0, f"{type(d).__name__}, {n} aktif ihale"

    sonuclar.append(kontrol("Veri kaynağı", _veri))

    # 3) Temizleyici
    def _temizleyici():
        from app.database.base import depo_olustur
        from app.domain.models import en_iyi_ilan, kapsam_metni

        d = depo_olustur(ayarlar)
        ihaleler = d.aktif_ihaleler(limit=5)
        if not ihaleler:
            return False, "aktif ihale bulunamadı"
        oranlar = []
        for i in ihaleler:
            ilan = en_iyi_ilan(i)
            if ilan and ilan.icerik:
                oranlar.append(len(kapsam_metni(i)) / max(len(ilan.icerik), 1))
        ort = sum(oranlar) / len(oranlar) if oranlar else 0
        return bool(oranlar), f"{len(oranlar)} örnekte ortalama %{100*ort:.0f} metin kaldı (gerisi boilerplate)"

    sonuclar.append(kontrol("İlan temizleyici", _temizleyici))

    # 4) Embedding
    def _embed():
        from app.embedding.embedders import embedder_olustur

        e = embedder_olustur(ayarlar)
        return True, f"{e.imza}, boyut={e.boyut}"

    sonuclar.append(kontrol("Embedding", _embed))

    # 5) Qdrant profil koleksiyonu
    def _qdrant():
        from app.retrieval.qdrant_deposu import KOLEKSIYON_PROFIL, QdrantDeposu

        with QdrantDeposu(ayarlar.qdrant_yolu, KOLEKSIYON_PROFIL) as d:
            n = d.sayi()
        if n == 0:
            return False, "profil koleksiyonu boş — `python scripts/index_profiles.py` çalıştırın"
        return True, f"{n} profil indekslenmiş"

    sonuclar.append(kontrol("Qdrant", _qdrant))

    # Koleksiyon İÇERİĞİ — sayı doğru olsa bile içerik bozuk olabilir.
    def _koleksiyon_icerigi():
        from app.retrieval.profil_retriever import koleksiyonlari_dogrula

        karar_seti = Path(__file__).resolve().parents[1] / "evaluation" / "karar-seti-v1.csv"
        if not karar_seti.exists():
            return True, "karar seti yok, atlandı"
        sorunlar = koleksiyonlari_dogrula(ayarlar.qdrant_yolu, karar_seti)
        if not sorunlar:
            return True, "üç örnek koleksiyonu da karar setiyle tutarlı"
        for s in sorunlar[:6]:
            print(f"\n         !! {s}", flush=True)
        if len(sorunlar) > 6:
            print(f"         !! ... ve {len(sorunlar)-6} sorun daha", flush=True)
        print(
            "\n         DÜZELTME: python scripts/index_profiles.py --sifirla "
            "--ornek-seti evaluation/karar-seti-v1.csv",
            flush=True,
        )
        return False, f"{len(sorunlar)} tutarsızlık — ölçüm almadan önce düzeltin"

    sonuclar.append(kontrol("Koleksiyon içeriği", _koleksiyon_icerigi))

    # 6) Ollama + modeller
    def _llm():
        from app.decision.llm_client import OllamaIstemcisi

        return OllamaIstemcisi(ayarlar.birincil_model, ayarlar.ollama_host).hazir_mi()

    sonuclar.append(kontrol("Ollama (birincil model)", _llm))

    if ayarlar.ikincil_model:
        sonuclar.append(
            kontrol(
                "Ollama (ikincil model)",
                lambda: __import__(
                    "app.decision.llm_client", fromlist=["OllamaIstemcisi"]
                ).OllamaIstemcisi(ayarlar.ikincil_model, ayarlar.ollama_host).hazir_mi(),
            )
        )

    basarili = sum(sonuclar)
    print(f"\n{basarili}/{len(sonuclar)} kontrol geçti.")
    if basarili < len(sonuclar):
        print("Eksikleri giderip tekrar çalıştırın.\n")
        return 1
    print("Her şey hazır.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
