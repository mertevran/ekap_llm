"""
Şirket tercihleri önizleme — portaldaki seçim kaç ihale getiriyor? LLM ÇAĞIRMAZ.

    python evaluation/tercih_onizleme.py                    # portaldaki tercihler
    python evaluation/tercih_onizleme.py --kaynak birlesik  # profil + tercih
    python evaluation/tercih_onizleme.py --tum-havuz --ornek 20
    python evaluation/tercih_onizleme.py --kelime kamera sinyalizasyon --okas 34996

=============================================================================
NE İŞE YARAR
=============================================================================
Satış ekibi portalda ("Sistem Ayarları > Şirket Tercihleri") anahtar kelime ve
OKAS kodu seçiyor. Bu script, o seçimin kaç ihale getirdiğini ve hangilerini
getirdiğini SANİYELER İÇİNDE gösterir — LLM hiç çağrılmadan.

Demo akışı: portalda seçimi değiştir -> bu script'i koştur -> havuzun nasıl
değiştiğini gör -> beğendiğin seçimle taramayı başlat.

`--kelime` / `--okas` ile portala dokunmadan DENEME yapılabilir; tabloya
yazmaz, yalnızca "şunu seçseydim ne olurdu" sorusunu cevaplar.

=============================================================================
UYARI
=============================================================================
Filtreyi geçmek "İSBAK'a uygun" DEMEK DEĞİLDİR. Bu bir ADAY HAVUZUDUR; kararı
LLM verir. Ve `tercih` modunda profil dosyalarının ölçülmüş 41/41 geri çağırması
GEÇERSİZDİR — dar bir seçim gerçek pozitifleri kaçırabilir.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import ayarlari_al  # noqa: E402
from app.database.base import depo_olustur  # noqa: E402
from app.decision.sirket_tercihleri import (  # noqa: E402
    SirketTercihi,
    filtre_kur,
    tercihten_kur,
)
from app.decision.sql_filtre import FiltreTanimi  # noqa: E402


def _bas(baslik: str) -> None:
    print(f"\n{'=' * 74}\n{baslik}\n{'=' * 74}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kaynak", choices=("profil", "tercih", "birlesik"), default="tercih")
    ap.add_argument("--genislik", choices=("dar", "genis", "odakli"), default="genis")
    ap.add_argument("--tum-havuz", action="store_true",
                    help="Aktif havuz yerine TÜM tablo (geçmiş dahil)")
    ap.add_argument("--ornek", type=int, default=15, help="Kaç örnek başlık basılsın")
    ap.add_argument("--kelime", nargs="*", default=None,
                    help="Portal yerine BURADAN dene (tabloya yazmaz)")
    ap.add_argument("--okas", nargs="*", default=None,
                    help="Portal yerine BURADAN dene (tabloya yazmaz)")
    a = ap.parse_args()

    ayarlar = ayarlari_al()
    depo = depo_olustur(ayarlar)

    if a.kelime or a.okas:
        from app.decision.sirket_tercihleri import kod_ayikla

        tercih = SirketTercihi(
            okas_kodlari=tuple(k for k in (kod_ayikla(x) for x in (a.okas or [])) if k),
            anahtar_kelimeler=tuple(str(x).strip().lower() for x in (a.kelime or [])),
            guncelleme="(komut satırı denemesi)",
        )
        tanim: FiltreTanimi = tercihten_kur(tercih)
        print("\n  ! Portal okunmadı — komut satırından verilen seçim kullanılıyor.")
    else:
        tanim, tercih = filtre_kur(a.kaynak, genislik=a.genislik, ayarlar=ayarlar)

    _bas("SEÇİM")
    print(f"  kaynak            : {tanim.kaynak}")
    print(f"  portal güncelleme : {tercih.guncelleme or '-'}")
    print(f"  anahtar kelimeler : {', '.join(tercih.anahtar_kelimeler) or '(boş)'}")
    print(f"  OKAS kodları      : {', '.join(tercih.okas_kodlari) or '(boş)'}")
    if tercih.ozet:
        print(f"  şirket özeti      : {tercih.ozet[:70]}")
    print(f"\n  filtreye giren    : {len(tanim.terimler)} terim, "
          f"{len(tanim.okas_on_ekleri)} OKAS ön eki")

    if tanim.bos_mu():
        print("\n  ! Filtre BOŞ — hiçbir ihale seçilemez.")
        print("    Portalda anahtar kelime / OKAS girin ya da --kaynak birlesik kullanın.")
        return 1

    _bas("SONUÇ")
    iknler = depo.filtreli_iknler(tanim, sadece_aktif=not a.tum_havuz)
    kapsam = "TÜM TABLO" if a.tum_havuz else "aktif havuz"
    toplam = (len(depo.aktif_ihale_iknleri()) if not a.tum_havuz else None)
    print(f"  kapsam            : {kapsam}")
    print(f"  SEÇİLEN İHALE     : {len(iknler):,}")
    if toplam:
        print(f"  aktif havuz       : {toplam:,}   (%{100*len(iknler)/max(toplam,1):.1f} seçildi)")

    if a.ornek and iknler:
        _bas(f"ÖRNEK BAŞLIKLAR (ilk {min(a.ornek, len(iknler))})")
        for ih in depo.coklu_getir(iknler[: a.ornek], alan="ikn"):
            kod = ", ".join(o.kod for o in ih.okas_kodlari[:2]) or "-"
            print(f"  {ih.ikn}  {(ih.adi or '').replace(chr(10), ' ')[:58]}")
            print(f"      {(ih.idare_adi or '')[:54]}   OKAS: {kod}")

    _bas("SIRADAKİ")
    print("  Bu havuzu LLM'e sokmak için:")
    kaynak_bayragi = f"--filtre-kaynak {a.kaynak}"
    havuz_bayragi = " --tum-havuz" if a.tum_havuz else ""
    print(f"    python scripts/tara_ve_kaydet.py --n 10 --rastgele --filtreli "
          f"{kaynak_bayragi}{havuz_bayragi}")
    print("\n  Filtreyi geçmek 'uygun' demek DEĞİLDİR — kararı LLM verir.")
    if a.kaynak == "tercih":
        print("  'tercih' modunda profillerin 41/41 geri çağırma ölçümü GEÇERSİZDİR.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
