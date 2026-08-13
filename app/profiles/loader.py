"""
Şirket profili katmanı — 20 iş paketinin TEK doğruluk kaynağı.

Şirketin ne iş yaptığı, `app/profiles/data/profiles/` altındaki 20 JSON dosyasında
tanımlıdır. Her dosya bir "iş paketi"dir ve şunları içerir: yetkinlikler, anahtar
terimler, ihale kategorisi sinyalleri ve o paketin KAPSAM DIŞI bıraktığı konular
(`negatif_terimler`).

Bu modül o dosyaları okur, doğrular ve programın geri kalanına tek bir arayüzden
sunar. Profilleri değiştirmek için kodu değil, JSON dosyalarını düzenleyin.

TASARIM KARARI — tek kaynak: Paket yönlendirmesi için ayrı, elle yazılmış anahtar
kelime listeleri TUTULMAZ; aynı bilgi zaten JSON'ların `ihale_kategori_sinyalleri`
alanındadır. İki ayrı kaynak zamanla kaçınılmaz olarak birbirinden ayrışır (nitekim
ayrışmıştı). Yönlendirme doğrudan bu dosyalardan beslenir — bkz. `aile_sinyalleri()`.

ÖNCELİK ALANI BİR VARSAYIMDIR: Profil verisinde kritik/orta/düşük diye resmî bir alan
yoktur. Kod, aile bazlı sezgisel bir eşleme kullanır (doğrudan hizmet üreten aileler
-> kritik). Bu, kurum tarafından onaylanmış bir sınıflandırma DEĞİLDİR.

KAPASİTE ALANLARI ŞU AN BOŞTUR: `personel_kapasitesi`, `belgeler`,
`tamamlanan_projeler`, `is_deneyim_belgeleri` gibi alanlar doldurulmamıştır
(`veri_durumu: kurum_ici_dogrulama_gerekli`). Aşama 1 (kapsam kararı) bunlara ihtiyaç
duymaz ve sorunsuz çalışır. Aşama 2 (yeterlilik) duyar; bu yüzden Aşama 2 şu an
"kanıt yok" durumunu dürüstçe raporlamak zorundadır, tahmin yürütmemelidir.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

VERI_DIZINI = Path(__file__).parent / "data"

Oncelik = Literal["kritik", "orta", "dusuk"]

# Doğrudan hizmet üreten aileler -> "kritik". Ortak/destekleyici aileler -> "orta".
_KRITIK_AILELER = {
    "Akıllı Ulaşım Sistemleri",
    "Entegre Akıllı Şehir Çözümleri",
    "Planlama ve Projelendirme",
}


@dataclass(frozen=True)
class Profil:
    kod: str
    ad: str
    aile: str
    aciklama: str
    anahtar_kelimeler: list[str]
    guclu_terimler: list[str]
    negatif_terimler: list[str]
    okas_on_ekleri: list[str]
    oncelik: Oncelik
    yetkinlikler: list[str] = field(default_factory=list)
    # Aşama 2 için — şu an boş, kurum içi doğrulama bekliyor
    belgeler: list = field(default_factory=list)
    tamamlanan_projeler: list = field(default_factory=list)
    veri_durumu: str = "kurum_ici_dogrulama_gerekli"

    def embed_metni(self) -> str:
        """Retrieval koleksiyonuna gömülecek metin.

        negatif_terimler BİLEREK DAHİL EDİLMEZ: negatif terimi embedding'e katmak,
        tam olarak o terimin geçtiği (ve İSBAK'a uygun OLMAYAN) ihalelerle yüksek
        benzerlik kurulmasına yol açar. Negatif terimler LLM'e ayrı bir "dikkat"
        listesi olarak gösterilir (bkz. app/decision/stage1_kapsam.py).
        """
        return f"{self.ad}. {self.aciklama} Anahtar kelimeler: {', '.join(self.anahtar_kelimeler)}."


def _oku(yol: Path) -> dict:
    with open(yol, encoding="utf-8-sig") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def profilleri_yukle(dizin: Path = VERI_DIZINI) -> tuple[Profil, ...]:
    kayit = _oku(dizin / "profile_registry.json")
    profiller: list[Profil] = []

    for girdi in kayit["profiller"]:
        if not girdi.get("aktif", True):
            continue
        ham = _oku(dizin / girdi["profil_dosyasi"])
        sinyaller = ham.get("ihale_kategori_sinyalleri", {})
        yetkinlikler = ham.get("birincil_yetkinlikler", [])
        aile = ham.get("profil_ailesi", "")

        # v1.1.0'da eklenen eş anlamlılar/kısaltmalar (ör. "Dinamik Kavşak" ->
        # "Akıllı Kavşak") — retrieval'ın terim boşluğunu kapatması için.
        es_anlamlilar: list[str] = []
        for madde in ham.get("abbreviations_and_jargon", []):
            es_anlamlilar.append(madde.get("term", ""))
            es_anlamlilar.extend(madde.get("aliases", []))
        for ekipman in ham.get("technical_equipment", []):
            es_anlamlilar.append(ekipman.get("name", ""))
            es_anlamlilar.extend(ekipman.get("aliases", []))

        anahtar = list(
            dict.fromkeys(
                sinyaller.get("guclu_terimler", [])
                + sinyaller.get("destekleyici_terimler", [])
                + sinyaller.get("genel_terimler", [])
                + [t for t in es_anlamlilar if t]
            )
        )

        profiller.append(
            Profil(
                kod=ham["profil_kodu"],
                ad=ham["profil_adi"],
                aile=aile,
                aciklama=(
                    f"{aile} kapsamında ana yetkinlikler: {', '.join(yetkinlikler)}."
                ),
                anahtar_kelimeler=anahtar,
                guclu_terimler=list(sinyaller.get("guclu_terimler", [])),
                negatif_terimler=list(sinyaller.get("negatif_terimler", [])),
                okas_on_ekleri=list(sinyaller.get("okas_kod_on_ekleri", [])),
                oncelik="kritik" if aile in _KRITIK_AILELER else "orta",
                yetkinlikler=list(yetkinlikler),
                belgeler=list(ham.get("belgeler", [])),
                tamamlanan_projeler=list(ham.get("tamamlanan_projeler", [])),
                veri_durumu=ham.get("veri_durumu", "kurum_ici_dogrulama_gerekli"),
            )
        )

    return tuple(profiller)


def profil_getir(kod: str) -> Profil | None:
    return next((p for p in profilleri_yukle() if p.kod == kod), None)


def aile_sinyalleri() -> dict[str, dict[str, list[str]]]:
    """Profil ailesi (AUS/ENT/TEK/PLN/OPS) bazında birleştirilmiş sinyal sözlüğü.

    Yönlendirme, elle yazılmış ayrı bir kelime listesinden değil, profil
    JSON'larının kendisinden beslenir. Böylece tek bir doğruluk kaynağı olur.
    """
    gruplar: dict[str, dict[str, list[str]]] = {}
    for p in profilleri_yukle():
        aile_kodu = p.kod.split("-")[0]
        g = gruplar.setdefault(aile_kodu, {"guclu": [], "genel": [], "negatif": [], "okas": []})
        g["guclu"].extend(p.guclu_terimler)
        g["genel"].extend(p.anahtar_kelimeler)
        g["negatif"].extend(p.negatif_terimler)
        g["okas"].extend(p.okas_on_ekleri)
    for g in gruplar.values():
        for a in g:
            g[a] = list(dict.fromkeys(g[a]))
    return gruplar


if __name__ == "__main__":
    ps = profilleri_yukle()
    print(f"{len(ps)} profil yüklendi — kaynak: {VERI_DIZINI}\n")
    for p in ps:
        print(
            f"  [{p.oncelik:6s}] {p.kod}  {p.ad:42s} "
            f"kelime={len(p.anahtar_kelimeler):3d} negatif={len(p.negatif_terimler):2d} "
            f"okas_ön_ek={len(p.okas_on_ekleri)}"
        )
    print("\nAile sinyalleri:")
    for aile, s in aile_sinyalleri().items():
        print(f"  {aile}: güçlü={len(s['guclu'])} genel={len(s['genel'])} negatif={len(s['negatif'])}")
