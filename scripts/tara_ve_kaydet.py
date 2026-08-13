"""
Etiketsiz aktif ihaleleri 5'er 5'er tara, sonucu veritabanına yaz, insan onayı bekle.

    python scripts/tara_ve_kaydet.py                      # 5 ihale tara ve yaz
    python scripts/tara_ve_kaydet.py --n 10
    python scripts/tara_ve_kaydet.py --durum              # ne kadar ilerledik
    python scripts/tara_ve_kaydet.py --incelenmemis 20    # insan kararı bekleyenler
    python scripts/tara_ve_kaydet.py --etiketle 2026/123 oneriliyor --notu "EDS bakımı"
    python scripts/tara_ve_kaydet.py --yerel              # Postgres'i dene bile, doğrudan SQLite

=============================================================================
NEDEN BU AKIŞ
=============================================================================
Şu ana kadarki bütün ölçümler ETİKETLİ setlerde yapıldı ve o setlerin 30'u eski
bir modelin "uygun" dediği havuzdan geliyor. Yani "daha önce bulunmuş uygunları
kaybetmiyoruz" ölçüldü; "hiç bulunmamış uygunları buluyoruz" ölçülmedi.

Canlıda sistem etiket görmeyecek. Gerçek sınav bu: rastgele, etiketsiz, aktif
ihaleler. Bu script onları tarar ve HER SONUCU KALICI OLARAK YAZAR.

Kritik fark: `triyaj_kosusu.py` sonucu JSON/CSV'ye yazıyordu ve insan kararı
hiçbir yere kaydedilmiyordu — seed 7/13/21'de 45 ihaleye gözle bakıldı, o
bilginin tamamı kayboldu. Burada `insan_karar` sütunu var; her inceleme
KALICI ETİKET üretir. 20 parti sonra 100 satırlık gerçek bir ölçüm seti olur.

`insan_karar` sözlüğü `public.tenders.takip_durumu` ile uyumlu:
'alınması öneriliyor' / 'alınması önerilmiyor' / 'inceleniyor'.
Kısayollar: oneriliyor | onerilmiyor | inceleniyor

=============================================================================
NE YAZILMAZ
=============================================================================
İhale verisine hiçbir şey yazılmaz. `public.tenders`, `tender_announcements` ve
`llm_rag.tender_index_state` tablolarına DOKUNULMAZ. Yalnızca bu projenin kendi
tablosuna (`llm_rag.tender_scope_decisions`) satır eklenir.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import ayarlari_al  # noqa: E402
from app.database.karar_deposu import INSAN_KARARLARI, KararSatiri  # noqa: E402
from app.database.karar_deposu import olustur as depo_olustur_karar  # noqa: E402
from app.domain.models import en_iyi_ilan, kapsam_metni  # noqa: E402
from app.ortam import ortam_ozeti, ozet_satiri  # noqa: E402
from app.pipeline.servis import servis_olustur  # noqa: E402

KISAYOL = {
    "oneriliyor": INSAN_KARARLARI[0],
    "onerilmiyor": INSAN_KARARLARI[1],
    "inceleniyor": INSAN_KARARLARI[2],
}
ISARET = {"uygun": "!!", "belirsiz": " ?", "uygun_degil": "  ", "HATA": "XX"}


def _konfig(ayarlar) -> dict:
    return {
        "veri_kaynagi": ayarlar.data_backend,
        "model": ayarlar.birincil_model,
        "embedding": f"{ayarlar.embedding_backend}/{ayarlar.embedding_model}",
        "dusunme": ayarlar.llm_dusunme,
        "num_gpu": ayarlar.llm_num_gpu,
        "seed": ayarlar.llm_seed,
        "num_ctx": ayarlar.num_ctx,
        "paket_k": ayarlar.retrieval_paket_k,
        "ornek_k": ayarlar.retrieval_ornek_k,
        "min_skor": ayarlar.retrieval_min_skor,
        "belirsiz_netlestirme_esigi": ayarlar.belirsiz_netlestirme_esigi,
        "paket_dogrula": ayarlar.paket_dogrula,
        "destekleyici_kurali": ayarlar.destekleyici_paket_kurali,
        "sert_on_filtre": ayarlar.sert_on_filtre_skoru,
        "okas_vetosu": ayarlar.okas_vetosu,
        "nitel_yakinlik": ayarlar.nitel_yakinlik,
        "negatif_dogrula": ayarlar.negatif_dogrula,
        "faaliyet_ortusmesi": ayarlar.faaliyet_ortusmesi,
        "b1_alan_terimi_istisnasi": ayarlar.b1_alan_terimi_istisnasi,
    }


def durum_bas(depo) -> None:
    s = depo.sayimlar()
    tekil = s.pop("_tekil_ihale", 0)
    insan = s.pop("_insan_etiketli", 0)
    print(f"\n  depo            : {depo.nerede}")
    print(f"  taranmış ihale  : {tekil:,}")
    print(f"  yazılmış satır  : {sum(s.values()):,}  (yeniden taramalar dahil)")
    for k, v in sorted(s.items(), key=lambda x: -x[1]):
        print(f"     {k:12s} {v:,}")
    print(f"  İNSAN ETİKETİ   : {insan:,}   <-- ölçüm setimizin gerçek boyutu")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5, help="Bu partide kaç ihale (varsayılan 5)")
    ap.add_argument("--durum", action="store_true", help="Sadece ilerleme raporu")
    ap.add_argument("--incelenmemis", type=int, metavar="N", help="İnsan kararı bekleyen N satırı listele")
    ap.add_argument("--etiketle", nargs=2, metavar=("IKN", "KARAR"),
                    help=f"İnsan kararı yaz. KARAR: {' | '.join(KISAYOL)}")
    ap.add_argument("--notu", default="", help="--etiketle ile birlikte serbest not")
    ap.add_argument("--inceleyen", default="", help="--etiketle ile birlikte kişi adı")
    ap.add_argument("--yerel", action="store_true", help="Postgres'i denemeden yerel SQLite'a yaz")
    ap.add_argument("--rastgele", action="store_true",
                    help="Rastgele örneklem (etiketli set için ÖNERİLİR). "
                         "Varsayılan sıralı seçim ihale tarihine göredir — taraflı.")
    ap.add_argument("--seed", type=int, default=42, help="--rastgele ile birlikte")
    ap.add_argument("--filtreli", action="store_true",
                    help="SQL ön filtresini GEÇEN ihalelerden örnekle. Pozitif yoğunluğu "
                         "yüksek, etiket biriktirmek için verimli. --rastgele ile birlikte "
                         "kullanın. DİKKAT: sadece bundan etiketlemeyin — etiketli set "
                         "filtrenin yanlılığını miras alır ve geri çağırma bir daha "
                         "ölçülemez. Gerekçe kodda, havuz seçimi bölümünde.")
    ap.add_argument("--filtre-genislik", choices=("dar", "genis", "odakli"), default="genis")
    ap.add_argument("--filtre-kaynak", choices=("profil", "tercih", "birlesik"),
                    default="profil",
                    help="Filtre neyden kurulsun? profil = iş paketi dosyaları (ölçülmüş, "
                         "41/41 geri çağırma). tercih = portaldaki Şirket Tercihleri "
                         "(anahtar kelimeler + OKAS kodları, İŞ PAKETLERİNDEN BAĞIMSIZ). "
                         "birlesik = ikisi. Gerekçe: app/decision/sirket_tercihleri.py")
    ap.add_argument("--tum-havuz", action="store_true",
                    help="--filtreli ile: aktif havuz yerine TÜM tabloyu (~50k, geçmiş "
                         "ve sonuçlanmış dahil) tara. Etiket biriktirmek için 13 kat "
                         "geniş kaynak — kapsam sorusu ihale tarihinden bağımsızdır. "
                         "Bu ihalelere teklif VERİLEMEZ, sadece ölçüm içindir.")
    ap.add_argument("--yeniden", type=int, metavar="N",
                    help="Son taranan N ihaleyi YENİDEN tara. Konfigürasyon değiştikten "
                         "sonra 'aynı ihalelerde ne değişti' sorusunu cevaplar. Eski satır "
                         "silinmez, yeni satır eklenir — öncesi/sonrası karşılaştırılır.")
    ap.add_argument("--ikn", nargs="+", metavar="IKN",
                    help="Belirli İKN'leri tara (daha önce taranmış olsalar bile)")
    ap.add_argument("--son-sil", type=int, metavar="N",
                    help="En son eklenen N satırı sil (örn. yeniden taramanın ürettiği "
                         "tekrarlar). Önce ÖNİZLEME basar; silmek için --onayla gerekir.")
    ap.add_argument("--onayla", action="store_true", help="--son-sil için onay")
    ap.add_argument("--tekrarlari-temizle", action="store_true",
                    help="Aynı ihalenin eski satırlarını sil, EN YENİSİNİ bırak. "
                         "İnsan etiketi eski satırdaysa yeniye taşınır. Sonrasında "
                         "tender_id UNIQUE olur ve yeniden tarama ÜZERİNE YAZAR.")
    a = ap.parse_args()

    ayarlar = ayarlari_al()
    depo = depo_olustur_karar(ayarlar, zorla="sqlite" if a.yerel else None)

    # ---- salt bilgi modları ----
    if a.durum:
        durum_bas(depo)
        return 0

    if a.etiketle:
        ikn, karar = a.etiketle
        tam = KISAYOL.get(karar, karar)
        if tam not in INSAN_KARARLARI:
            print(f"Geçersiz karar: {karar!r}\n  Kullan: {' | '.join(KISAYOL)}")
            return 1
        n = depo.insan_karari_yaz(ikn, tam, a.notu, a.inceleyen)
        print(f"{ikn} -> {tam!r}   ({n} satır güncellendi)" if n
              else f"! {ikn} depoda bulunamadı — önce taranmış olmalı")
        return 0 if n else 1

    if a.tekrarlari_temizle:
        onceki = depo.sayimlar()
        toplam = sum(v for k, v in onceki.items() if not k.startswith("_"))
        tekil = onceki.get("_tekil_ihale", 0)
        print(f"\n  depo   : {depo.nerede}")
        print(f"  önce   : {toplam} satır / {tekil} tekil ihale  "
              f"({toplam - tekil} tekrar)")
        silinen, tasinan = depo.tekrarlari_temizle()
        print(f"  silinen: {silinen} eski satır")
        if tasinan:
            print(f"  taşınan: {tasinan} insan etiketi eski satırdan yeniye aktarıldı")
        print(f"\n  Artık tender_id UNIQUE — yeniden tarama ÜZERİNE YAZAR, "
              f"insan etiketi korunur.")
        durum_bas(depo)
        return 0

    if a.son_sil:
        satirlar = depo.son_satirlar(a.son_sil)
        if not satirlar:
            print("Silinecek satır yok.")
            return 0
        print(f"\n{'=' * 78}")
        print(f"SİLİNECEK {len(satirlar)} SATIR — {depo.nerede}")
        print(f"{'=' * 78}")
        etiketli = 0
        for r in satirlar:
            iz = ""
            if r.get("insan_karar"):
                iz = f"   !! İNSAN ETİKETİ VAR: {r['insan_karar']}"
                etiketli += 1
            print(f"  id={r['id']:<6} {r['karar']:12s} {str(r['ilgi_skoru']):7s} "
                  f"{r['ikn']:14s} {(r['adi'] or '')[:38]}{iz}")
        if etiketli:
            print(f"\n  !! DİKKAT: {etiketli} satırda insan etiketi var. Silinirse o emek kaybolur.")
        if not a.onayla:
            print(f"\n  Silmek için: --son-sil {a.son_sil} --onayla")
            return 0
        n = depo.son_satirlari_sil(a.son_sil)
        print(f"\n  {n} satır silindi.")
        durum_bas(depo)
        return 0

    if a.incelenmemis:
        satirlar = depo.incelenmemisler(a.incelenmemis)
        print(f"\nİNSAN KARARI BEKLEYEN {len(satirlar)} SATIR\n{'=' * 78}")
        for r in satirlar:
            print(f"\n{ISARET.get(r['karar'], '  ')} {r['karar']:12s} skor={r['ilgi_skoru']} "
                  f"(retrieval {r['retrieval_en_ust_skor']})  [{r['ilan_tipi']}]")
            print(f"   {r['ikn']}  {(r['adi'] or '')[:64]}")
            print(f"   paket: {r['eslesen_paket']}")
            print(f"   {(r['gerekce'] or '')[:200]}")
        print(f"\n{'=' * 78}")
        print("Etiketlemek için:")
        print("  python scripts/tara_ve_kaydet.py --etiketle <İKN> oneriliyor|onerilmiyor|inceleniyor")
        return 0

    # ---- tarama ----
    servis = servis_olustur(ayarlar, sadece_asama1=True)
    konfig = _konfig(ayarlar)
    ortam = ortam_ozeti()

    # --- HAVUZ SEÇİMİ: rastgele mi, SQL filtresinden geçenler mi ---
    #
    # İKİ TABAKA BİLEREK AYRI TUTULUYOR ve hangisinden geldiği koşu kaydına
    # yazılıyor (`konfig["ornekleme"]`). Karıştırılırsa ölçüm bozulur:
    #
    #   rastgele : Tüm aktif havuzdan. %88'i bariz negatif, etiketlemesi hızlı.
    #              DEĞERİ: filtrenin KAÇIRDIĞI bir pozitif buraya düşerse GÖRÜLÜR.
    #              Geri çağırma ölçümünün bağımsız kalmasını sağlayan tek kaynak.
    #
    #   filtreli : Yalnızca SQL ön filtresini geçenler. Pozitif yoğunluğu çok
    #              daha yüksek, etiket biriktirmek için verimli.
    #              RİSKİ: SADECE bundan etiketlenirse etiketli set filtrenin
    #              yanlılığını miras alır ve "filtre pozitif kaçırıyor mu?"
    #              sorusu BİR DAHA ölçülemez hale gelir — ölçüm kendi kendini
    #              doğrular. Raporun 9.3'teki hatanın (kaçırma setinin tamamı
    #              eski bir modelin bulduğu havuzdan) ikinci kez tekrarı olurdu.
    #
    # Doğru kullanım: ikisinden de etiketle, --notu alanına hangisi olduğunu yaz.
    filtre_tanimi = None
    if a.filtreli:
        from app.decision.sirket_tercihleri import filtre_kur

        filtre_tanimi, tercih = filtre_kur(a.filtre_kaynak, genislik=a.filtre_genislik)
        tum = servis.depo.filtreli_iknler(filtre_tanimi, sadece_aktif=not a.tum_havuz)
        kapsam_adi = "TÜM TABLO" if a.tum_havuz else "aktif havuz"
        konfig["ornekleme"] = (
            f"filtreli/{filtre_tanimi.kaynak}/"
            f"{'tum-havuz' if a.tum_havuz else 'aktif'}")
        konfig["filtre_kaynak"] = a.filtre_kaynak

        # Portalda ne yazdığı KOŞU KAYDINA girmeli: tercihler değiştiğinde iki
        # koşu kıyaslanamaz hale gelir ve sebebi sonradan bulunamaz.
        if a.filtre_kaynak in ("tercih", "birlesik"):
            print(f"\n  ŞİRKET TERCİHLERİ (portal, güncelleme: {tercih.guncelleme or '-'})")
            print(f"    anahtar kelimeler : {', '.join(tercih.anahtar_kelimeler) or '(boş)'}")
            print(f"    OKAS kodları      : {', '.join(tercih.okas_kodlari) or '(boş)'}")
            konfig["sirket_tercihleri"] = {
                "kelimeler": list(tercih.anahtar_kelimeler),
                "okas": list(tercih.okas_kodlari),
                "guncelleme": tercih.guncelleme,
            }
            if tercih.bos_mu() and a.filtre_kaynak == "birlesik":
                print("    ! Tercih boş — filtre profil dosyalarına düştü.")
            elif tercih.bos_mu():
                print("    ! Tercih BOŞ ve kaynak 'tercih' — hiçbir ihale seçilmeyecek.")

        print(f"\n  SQL FİLTRESİ ({kapsam_adi}, kaynak={filtre_tanimi.kaynak}): "
              f"{len(tum):,} ihale geçti "
              f"({len(filtre_tanimi.terimler)} terim, {len(filtre_tanimi.okas_on_ekleri)} OKAS ön eki)")
        if a.filtre_kaynak == "tercih":
            print("    ! 'tercih' modu: 41/41 geri çağırma garantisi GEÇERSİZ —")
            print("      o ölçüm profil dosyalarıyla alınmıştı.")
        if a.tum_havuz:
            print("  ! TÜM TABLO modu: geçmiş/iptal/sonuçlanmış ihaleler DAHİL.")
            print("    Bunlara teklif VERİLEMEZ — yalnızca etiket üretmek için.")
        if not a.rastgele:
            print("  ! --filtreli ile --rastgele birlikte kullanılmalı, yoksa hep")
            print("    aynı ilk N ihale gelir ve parti parti ilerleyemezsin.")
    else:
        tum = servis.depo.aktif_ihale_iknleri()
        konfig["ornekleme"] = "rastgele" if a.rastgele else "sirali"

    islenmis_ikn = depo.islenmis_iknler()
    islenmis = depo.islenmis_tender_idler()

    print(f"\n{'=' * 78}")
    print(f"TARAMA — {a.n} ihale")
    print(f"{'=' * 78}")
    print(f"  kaynak          : {ayarlar.data_backend}, {len(tum):,} ihale "
          f"[{konfig.get('ornekleme', '?')}]")
    print(f"  hedef           : {depo.nerede}")
    print(f"  model           : {ayarlar.birincil_model} | think={ayarlar.llm_dusunme}")
    print(f"  min_skor={ayarlar.retrieval_min_skor} netleştirme={ayarlar.belirsiz_netlestirme_esigi} "
          f"paket_dogrula={ayarlar.paket_dogrula}")
    print(f"  daha önce taranmış: {len(islenmis):,}")
    print(f"  ortam           : {ozet_satiri()}\n")

    # --- yeniden tarama / belirli İKN modu ---
    onceki: dict[str, dict] = {}
    if a.yeniden or a.ikn:
        if a.yeniden:
            eski = depo.son_taranmislar(a.yeniden)
            onceki = {r["ikn"]: r for r in eski}
            secilen = list(onceki)
            print(f"  YENİDEN TARAMA    : son {len(secilen)} ihale\n")
        else:
            secilen = list(a.ikn)
            onceki = {r["ikn"]: r for r in depo.son_taranmislar(500) if r["ikn"] in set(secilen)}
            print(f"  BELİRTİLEN İKN'ler: {len(secilen)}\n")
        islenmis = set()          # atlama kapalı — bilerek yeniden taranıyor
        kalan = secilen

    # SEÇİM İKN ÜZERİNDEN YAPILIR — tender_id üzerinden yapılamaz, çünkü
    # tender_id'yi öğrenmek için ihaleyi çekmek gerekiyor. Aksi halde her partide
    # aynı ilk N İKN seçilir, hepsi "zaten taranmış" diye atlanır, 0 satır yazılır.
    else:
        kalan = [ikn for ikn in tum if ikn not in islenmis_ikn]
    if not kalan:
        print("  Taranmadık aktif ihale kalmadı.")
        durum_bas(depo)
        return 0

    if a.yeniden or a.ikn:
        pass                       # seçim yukarıda yapıldı
    elif a.rastgele:
        # Etiketli set kurmak için rastgele örneklem DAHA İYİ: `aktif_ihale_iknleri()`
        # ihale tarihine göre sıralı geliyor, yani sıralı seçim "en yakın kapanacak
        # ihaleler" demek — temsili değil, taraflı bir örneklem olur.
        import random
        rastgele = random.Random(a.seed + len(islenmis_ikn))
        secilen = rastgele.sample(kalan, min(a.n, len(kalan)))
    else:
        secilen = kalan[: a.n]

    if not (a.yeniden or a.ikn):
        print(f"  taranmadık kalan  : {len(kalan):,}"
              f"{'  (rastgele örneklem)' if a.rastgele else '  (tarihe göre sıralı)'}")

    yazilacak: list[KararSatiri] = []
    t_basla = time.time()
    atlanan = 0

    for i, ikn in enumerate(secilen, 1):
        t0 = time.time()
        try:
            ihale = servis.depo.ikn_ile_getir(ikn)
        except Exception as e:  # noqa: BLE001
            print(f"[{i:3d}/{len(secilen)}] XX okunamadı {ikn}: {type(e).__name__}: {e}")
            continue

        if ihale.id in islenmis:
            atlanan += 1
            continue

        adi = (ihale.adi or "").replace("\n", " ").strip()
        try:
            sonuc = servis.ihaleyi_analiz_et(ihale)
            k = sonuc.kapsam
            karar = k.karar if k else "HATA"
            skor = k.ilgi_skoru if k else None
            gerekce = k.gerekce if k else ""
            paket = (k.eslesen_paket if k else None) or None
            okas = list(k.eslesen_okas) if k else []
            btip = getattr(k, 'belirsiz_tipi', None) if k else None
        except Exception as e:  # noqa: BLE001
            karar, skor, gerekce, paket, okas, btip = "HATA", None, f"{type(e).__name__}: {e}", None, [], None
            sonuc = None

        sure = round(time.time() - t0, 1)
        ilan = en_iyi_ilan(ihale)
        paketler = (sonuc.kullanilan_paketler if sonuc else []) or []

        yazilacak.append(KararSatiri(
            tender_id=ihale.id, ikn=ihale.ikn, adi=adi,
            idare_adi=(ihale.idare_adi or "").replace("\n", " ").strip(),
            il=ihale.il, ihale_turu=ihale.ihale_turu, ihale_tarihi=ihale.ihale_tarihi,
            karar=karar, ilgi_skoru=skor, belirsiz_tipi=btip, gerekce=gerekce,
            eslesen_paket=paket, eslesen_okas=okas,
            retrieval_en_ust_skor=(paketler[0].get("benzerlik") if paketler else None),
            kullanilan_paketler=paketler,
            ilan_id=(ilan.id if ilan else None),
            ilan_tipi=(ilan.ilan_tipi if ilan else None),
            kapsam_metni_uzunlugu=len(kapsam_metni(ihale) or ""),
            on_filtre_kurali=(sonuc.on_filtre_kurali if sonuc else None),
            kod_uyarilari=(list(sonuc.notlar) if sonuc else []),
            model=ayarlar.birincil_model, konfigurasyon=konfig,
            sure_sn=sure, makine=ortam.get("makine"),
        ))

        onc = onceki.get(ikn)
        degisim = ""
        if onc:
            eski_k = onc["karar"]
            degisim = (f"   [{eski_k} -> {karar}]  <<< DEĞİŞTİ" if eski_k != karar
                       else f"   [{eski_k} = aynı]")
        tip = f" ({btip})" if btip else ""
        print(f"[{i:3d}/{len(secilen)}] {ISARET.get(karar, '  ')} {karar:12s}{tip} "
              f"skor={str(skor):7s} {sure:5.1f}sn  {adi[:44]}{degisim}", flush=True)
        # GEREKÇE HER KARARDA BASILIR — `uygun_degil` dahil.
        #
        # Eskiden yalnızca uygun/belirsiz için basılıyordu. Ama bu script'in işi
        # ETİKET ÜRETMEK: insan modelin kararına katılmayacaksa gerekçeyi görmek
        # ZORUNDA. `uygun_degil` gerekçesini gizlemek, tam da tartışılması gereken
        # kararı görünmez yapıyordu — kullanıcı "buna niye uygun değil dedi"
        # sorusunu sorabilmek için veritabanına bakmak zorunda kalıyordu.
        if gerekce:
            print(f"           └─ {gerekce[:300]}", flush=True)
        # TEŞHİS NOTLARI — kod düzeyindeki her müdahale burada görünür:
        # ham_ilgi_skoru (düzeltmeden önceki model skoru), faaliyet_ortusmesi,
        # negatif_dogrulandi, okas_vetosu, paket uydurması, netleştirme...
        # Veritabanına `kod_uyarilari` olarak zaten yazılıyordu ama ekranda
        # görünmüyordu; etiketleme sırasında kararın NEDEN öyle çıktığını
        # görmeden insan kararı vermek zor.
        for notu in (sonuc.notlar if sonuc else []):
            print(f"              · {notu}", flush=True)

    n = depo.yaz(yazilacak)
    toplam = time.time() - t_basla

    print(f"\n{'=' * 78}")
    print(f"  {n} satır yazıldı -> {depo.nerede}")
    if atlanan:
        print(f"  {atlanan} ihale zaten taranmıştı, atlandı")
    if yazilacak:
        print(f"  süre: {toplam/60:.1f} dk  ({toplam/len(yazilacak):.0f} sn/ihale)")
    durum_bas(depo)
    print(f"\n  Sıradaki: --incelenmemis {a.n}  ile bak, --etiketle ile karar ver.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
