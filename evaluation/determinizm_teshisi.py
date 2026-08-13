"""
Determinizm teşhisi — gürültü HANGİ KATMANDAN geliyor?

    python evaluation/determinizm_teshisi.py
    python evaluation/determinizm_teshisi.py --tekrar 5 --ikn 2025/2196999

NEDEN BU SCRIPT VAR
-------------------
T14 tutarlılık testi `belirsiz` sınıfında karar tutarlılığını %50, skor
tutarlılığını %0 buldu. Skor yayılımları çok büyük (0.45 -> 0.80, 0.40 -> 0.75).
Bu büyüklükte bir sapma kayan nokta gürültüsüyle açıklanamaz.

Şimdiye kadar gürültünün LLM'den geldiği VARSAYILDI ve seed eklendi. Ama boru
hattında üç ayrı katman var ve her biri koşu başına yeniden çalışıyor:

    1) EMBEDDING   — aynı metin, aynı vektör mü?
    2) RETRIEVAL   — aynı vektör, aynı paketler ve aynı skorlar mı?
                     (bu skorlar prompt'a YAZILIYOR; değişirse LLM'in girdisi değişir)
    3) LLM         — aynı prompt, aynı çıktı mı?

Katman 1 veya 2 oynuyorsa, LLM tamamen deterministik olsa bile çıktı değişir —
çünkü ona giden metin değişmiştir. Bu durumda seed'i kurcalamak boşuna.

Bu script üçünü AYRI AYRI ölçer, böylece hangisinin düzeltileceği kesinleşir.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import ayarlari_al  # noqa: E402
from app.database.base import depo_olustur  # noqa: E402
from app.decision.llm_client import OllamaIstemcisi  # noqa: E402
from app.decision.schemas import KapsamSonucu  # noqa: E402
from app.decision.stage1_kapsam import SISTEM_PROMPTU, kullanici_mesaji  # noqa: E402
from app.domain.models import kapsam_metni  # noqa: E402
from app.embedding.embedders import embedder_olustur  # noqa: E402
from app.ortam import ozet_satiri  # noqa: E402
from app.retrieval.profil_retriever import ProfilRetriever, sorgu_metni  # noqa: E402

VARSAYILAN_IKN = "2025/2196999"


def _bas(baslik: str) -> None:
    print(f"\n{'='*76}\n{baslik}\n{'='*76}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ikn", default=VARSAYILAN_IKN)
    ap.add_argument("--tekrar", type=int, default=5)
    a = ap.parse_args()

    ayarlar = ayarlari_al()
    print(f"\nDETERMİNİZM TEŞHİSİ — {a.tekrar} tekrar")
    print(f"model={ayarlar.birincil_model} seed={ayarlar.llm_seed} "
          f"embedding={ayarlar.embedding_backend}/{ayarlar.embedding_model}")
    print(f"ortam: {ozet_satiri()}")

    depo = depo_olustur(ayarlar)
    ihale = depo.ikn_ile_getir(a.ikn)
    metin = sorgu_metni(ihale.adi or "", kapsam_metni(ihale))
    print(f"ihale: {ihale.adi[:66]}")

    embedder = embedder_olustur(ayarlar)
    sorun_katmani = []

    # ---------------------------------------------------------------- 1) EMBEDDING
    _bas("1) EMBEDDING — aynı metin, aynı vektör mü?")
    vektorler = []
    for _ in range(a.tekrar):
        vektorler.append(tuple(embedder.embed([metin])[0]))
        print(".", end="", flush=True)
    tekil = set(vektorler)
    if len(tekil) == 1:
        print(f"\n   SABİT — {a.tekrar} koşuda da aynı vektör (boyut {len(vektorler[0])})")
    else:
        enb = max(
            abs(x - y)
            for v1 in tekil for v2 in tekil
            for x, y in zip(v1, v2)
        )
        print(f"\n   !! OYNUYOR — {len(tekil)} farklı vektör, en büyük eleman farkı {enb:.3e}")
        sorun_katmani.append("EMBEDDING")

    # ---------------------------------------------------------------- 2) RETRIEVAL
    _bas("2) RETRIEVAL — aynı sorgu, aynı paketler ve skorlar mı?")
    retriever = ProfilRetriever(ayarlar.qdrant_yolu, embedder)
    try:
        sonuclar = []
        for _ in range(a.tekrar):
            paketler = retriever.paketleri_bul(metin, k=ayarlar.retrieval_paket_k)
            sonuclar.append(tuple((p.kod, p.benzerlik) for p in paketler))
            print(".", end="", flush=True)
    finally:
        retriever.kapat()

    if len(set(sonuclar)) == 1:
        print("\n   SABİT — paket sırası ve skorlar aynı")
        for kod, skor in sonuclar[0]:
            print(f"     {skor:.4f}  {kod}")
    else:
        print(f"\n   !! OYNUYOR — {len(set(sonuclar))} farklı sonuç:")
        for i, s in enumerate(sorted(set(sonuclar)), 1):
            print(f"     [{i}] " + ", ".join(f"{k}:{v:.4f}" for k, v in s))
        sadece_skor = len({tuple(k for k, _ in s) for s in sonuclar}) == 1
        print("     (paket SIRASI aynı, sadece skorlar oynuyor)" if sadece_skor
              else "     (PAKET SIRASI DA DEĞİŞİYOR — prompt içeriği değişiyor)")
        sorun_katmani.append("RETRIEVAL")

    # ---------------------------------------------------------------- 3) LLM
    _bas("3) LLM — SABİT prompt, aynı çıktı mı? (retrieval bir kez, sonra dondurulmuş)")
    retriever2 = ProfilRetriever(ayarlar.qdrant_yolu, embedder)
    try:
        paketler = retriever2.paketleri_bul(metin, k=ayarlar.retrieval_paket_k)
        uygunlar = retriever2.uygun_ornekler(metin, k=ayarlar.retrieval_ornek_k, haric=ihale.id)
        redler = retriever2.red_ornekler(metin, k=ayarlar.retrieval_ornek_k, haric=ihale.id)
        belirsizler = retriever2.belirsiz_ornekler(metin, k=ayarlar.retrieval_ornek_k, haric=ihale.id)
    finally:
        retriever2.kapat()

    # Prompt BİR KEZ üretilip donduruluyor — bundan sonra tek değişken LLM.
    sabit_mesaj = kullanici_mesaji(ihale, paketler, uygunlar, redler, belirsizler)
    istemci = OllamaIstemcisi(
        model=ayarlar.birincil_model, host=ayarlar.ollama_host,
        timeout=ayarlar.llm_timeout_sn, num_ctx=ayarlar.num_ctx, seed=ayarlar.llm_seed,
        dusunme=ayarlar.llm_dusunme,
    )

    ciktilar = []
    dusunce_uzunluklari = []
    for _ in range(a.tekrar):
        s = istemci.yapisal_uret(sistem=SISTEM_PROMPTU, kullanici=sabit_mesaj, sema=KapsamSonucu)
        ciktilar.append((s.karar, s.ilgi_skoru))
        dusunce_uzunluklari.append(len(istemci.son_dusunce))
        print(".", end="", flush=True)

    if len(set(ciktilar)) == 1:
        print(f"\n   SABİT — {ciktilar[0][0]} ({ciktilar[0][1]})")
    else:
        print(f"\n   !! OYNUYOR — {sorted(set(ciktilar))}")
        sorun_katmani.append("LLM")

    # DÜŞÜNME MODU — süre şüphelerinin cevabı burada.
    if max(dusunce_uzunluklari) == 0:
        print(f"   düşünme: YOK (message.thinking boş; think={ayarlar.llm_dusunme})")
    else:
        print(
            f"   düşünme: VAR — {min(dusunce_uzunluklari)}-{max(dusunce_uzunluklari)} karakter "
            f"(think={ayarlar.llm_dusunme}). Bu token'lar üretiliyor ve süreye giriyor.\n"
            f"   ilk 300 karakter:\n     {istemci.son_dusunce[:300]!r}"
        )
        if len(set(dusunce_uzunluklari)) > 1:
            print("   !! düşünce uzunluğu koşular arası DEĞİŞİYOR — karar sabit olsa bile "
                  "üretim yolu sabit değil.")

    # KONTROL: model kendi skorunu mu üretiyor, retrieval skorunu mu kopyalıyor?
    if paketler:
        en_ust = paketler[0].benzerlik
        eslesenler = sum(1 for _, skor in ciktilar if abs(skor - en_ust) < 1e-9)
        if eslesenler:
            print(f"\n   !! SKOR YANKISI — {eslesenler}/{len(ciktilar)} koşuda ilgi_skoru, "
                  f"retrieval en üst skoruna ({en_ust}) BİREBİR eşit.\n"
                  f"      Model kendi skorunu üretmiyor, prompt'taki benzerliği kopyalıyor.")

    # ---------------------------------------------------------------- TEŞHİS
    _bas("TEŞHİS")
    if not sorun_katmani:
        print("  Üç katman da deterministik. T14'teki oynama başka bir yerden geliyor —\n"
              "  aynı ihaleyi --ikn ile tekrar deneyin, özellikle T14'te oynayanları.")
        return 0

    print(f"  Gürültü kaynağı: {', '.join(sorun_katmani)}\n")
    if "EMBEDDING" in sorun_katmani:
        print("  EMBEDDING oynuyorsa: Ollama'nın bge-m3'ü tekrarlanabilir vektör üretmiyor.\n"
              "    -> EMBEDDING_BACKEND=sentence-transformers deneyin (fp32, deterministik).\n"
              "       Koleksiyonlar yeniden kurulmalı: index_profiles.py --sifirla\n")
    if "RETRIEVAL" in sorun_katmani and "EMBEDDING" not in sorun_katmani:
        print("  RETRIEVAL oynuyor ama embedding sabitse: Qdrant tarafında bir sorun var.\n")
    if "LLM" in sorun_katmani:
        print("  LLM sabit prompt'ta bile oynuyorsa seed uygulanmıyor ya da yetmiyor:\n"
              "    -> Modelin TAMAMEN GPU'ya sığdığını doğrulayın: `ollama ps` çıktısında\n"
              "       PROCESSOR sütunu '100% GPU' demeli. 'CPU/GPU' karışıksa katman\n"
              "       bölünmesi koşular arası değişir ve seed bunu düzeltmez.\n"
              "    -> Sığmıyorsa qwen3:4b'ye inin ya da ölçümleri N kez koşup çoğunluk alın.\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
