"""
Toplu tarama testleri — devam edebilirlik, sert ön filtre, çıktı bütünlüğü.

Ollama GEREKTİRMEZ; LLM sahte bir istemciyle taklit edilir. Burada ölçülen şey
model kalitesi değil, 20+ saatlik bir koşunun kesintiye dayanıklılığı.
"""

from __future__ import annotations

import json

import pytest

from app.decision.schemas import KapsamSonucu
from app.embedding.embedders import FakeEmbedder
from app.pipeline.servis import AnalizAyarlari, AnalizServisi
from app.retrieval.profil_retriever import ProfilRetriever, profilleri_indeksle
from app.retrieval.qdrant_deposu import KOLEKSIYON_PROFIL, QdrantDeposu
from scripts.toplu_tarama import bitmis_iknleri_oku, sure_metni


class SahteIstemci:
    model = "sahte:1b"

    def __init__(self, karar="uygun", skor=0.9):
        self._karar, self._skor = karar, skor
        self.cagri_sayisi = 0

    def yapisal_uret(self, *, sistem, kullanici, sema):
        self.cagri_sayisi += 1
        return KapsamSonucu(
            gerekce="test", eslesen_paket="Trafik Yönetimi ve Sinyalizasyon",
            eslesen_okas=[], ilgi_skoru=self._skor, karar=self._karar,
        )


@pytest.fixture(scope="module")
def retriever():
    r = ProfilRetriever(QdrantDeposu.BELLEK, FakeEmbedder())
    profilleri_indeksle(r._depo(KOLEKSIYON_PROFIL), r.embedder, sifirla=True)
    yield r
    r.kapat()


# ============================================================================
# Devam edebilirlik — 20 saatlik koşu kesintide çöpe gitmemeli
# ============================================================================


def test_bitmis_iknler_okunuyor(tmp_path):
    yol = tmp_path / "tarama.jsonl"
    yol.write_text(
        json.dumps({"ikn": "2026/1", "karar": "uygun"}, ensure_ascii=False) + "\n"
        + json.dumps({"ikn": "2026/2", "karar": "uygun_degil"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    assert bitmis_iknleri_oku(yol) == {"2026/1", "2026/2"}


def test_yarim_kalmis_son_satir_koşuyu_bozmuyor(tmp_path):
    """Elektrik kesintisi tam yazma anında olursa son satır yarım kalır.
    Bu satır sessizce atlanmalı, o İKN yeniden koşulmalı — çökme OLMAMALI."""
    yol = tmp_path / "tarama.jsonl"
    yol.write_text(
        json.dumps({"ikn": "2026/1", "karar": "uygun"}, ensure_ascii=False) + "\n"
        + '{"ikn": "2026/2", "kar',  # yarım
        encoding="utf-8",
    )
    assert bitmis_iknleri_oku(yol) == {"2026/1"}


def test_olmayan_dosyada_bos_kume(tmp_path):
    assert bitmis_iknleri_oku(tmp_path / "yok.jsonl") == set()


def test_sure_metni():
    assert sure_metni(90) == "1dk 30sn"
    assert sure_metni(3700) == "1sa 1dk"


# ============================================================================
# Sert ön filtre — LLM'e hiç gitmemeli
# ============================================================================


def _servis(depo, retriever, istemci, **ayar):
    return AnalizServisi(
        depo=depo, retriever=retriever, birincil_istemci=istemci,
        ayarlar=AnalizAyarlari(asama2_calissin=False, **ayar),
    )


def test_sert_esik_altinda_llm_cagrilmiyor(depo, retriever):
    """Toplu taramadaki ~%26 tasarrufun kanıtı."""
    ist = SahteIstemci()
    # 1.0 eşiği hiçbir gerçek benzerliğin üstünde -> her ihale elenmeli
    s = _servis(depo, retriever, ist, sert_on_filtre_skoru=1.0).analiz_et(ikn="2025/2196999")

    assert ist.cagri_sayisi == 0, "sert eşik altında model çağrıldı"
    assert s.kapsam.karar == "uygun_degil"
    assert s.on_filtre_kurali == "SERT_ESIK"
    assert "sert_on_filtre" in " ".join(s.notlar)


def test_sert_esik_ustunde_llm_cagriliyor(depo, retriever):
    ist = SahteIstemci()
    s = _servis(depo, retriever, ist, sert_on_filtre_skoru=0.01).analiz_et(ikn="2025/2196999")

    assert ist.cagri_sayisi == 1
    assert s.on_filtre_kurali != "SERT_ESIK"


def test_sert_esik_varsayilan_kapali(depo, retriever):
    """VARSAYILAN 0.0 — açıkça istenmedikçe hiçbir ihale sessizce elenmemeli."""
    assert AnalizAyarlari().sert_on_filtre_skoru == 0.0
    ist = SahteIstemci()
    _servis(depo, retriever, ist).analiz_et(ikn="2025/2196999")
    assert ist.cagri_sayisi == 1


def test_sert_esik_isbak_kuralini_gecersiz_kilmiyor(depo, retriever):
    """Sıra önemli: İSBAK kendi ihalesi kuralı sert eşikten ÖNCE çalışmalı,
    yoksa gerekçe 'benzerlik düşük' der ve gerçek sebep kaybolur."""
    from app.domain.models import Ihale

    ist = SahteIstemci()
    ihale = Ihale(id="x", ikn="2026/9", adi="EDS Direk Alımı", idare_adi="İSBAK A.Ş.")
    s = _servis(depo, retriever, ist, sert_on_filtre_skoru=1.0).ihaleyi_analiz_et(ihale)

    assert s.on_filtre_kurali == "IDARE_ISBAK"
    assert ist.cagri_sayisi == 0
