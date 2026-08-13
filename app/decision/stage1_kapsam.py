"""
AŞAMA 1 — KAPSAM KARARI:  "Bu iş şirketin faaliyet alanına giriyor mu?"

Burada kurulan prompt ve kuralların HER BİRİ ölçülmüş bir hata modundan doğdu;
hiçbiri tahmin değildir:

- KELİME BENZERLİĞİ TUZAKLARI: Testlerde gerçekten yanlış eşleşme üreten vakalardan
  çıkarıldı (kurumsal kaynak planlama yazılımı, tıbbi görüntüleme yapay zekâsı,
  hastane genel tesis bakımı, sinyalizasyonu yalnızca alt kalem olarak içeren genel
  yapım işi gibi).
- PROFİL BAZLI NEGATİF TERİMLER: Tek bir genel "şunlar kapsam dışı" paragrafı yerine,
  her iş paketinin KENDİ hariç tutma listesi ayrıca gösterilir. Kategoriye özel ve
  veriden türetilmiş hâlidir.
- KARŞILAŞTIRMALI ÖRNEK KURALI: Modele sadece "uygun" örnek göstermek kararı kabul
  yönüne kaydırıyordu (yanlış alarmlar arttı). Artık en yakın kabul edilmiş VE en
  yakın reddedilmiş gerçek örnek yan yana gösterilir.
- Şirketin kendi açtığı ihaleyi eleme kuralı burada DEĞİL, app/decision/on_filtre.py
  içinde kodla garanti altındadır — modele bırakıldığında çiğnendiği ölçüldü.

Aşama 1 few-shot KULLANMAZ. Temiz veri + iyi RAG'ın tek başına ne kadarını çözdüğünü
ölçebilmek için taban çizgisi bilerek "ham" tutuluyor. Few-shot eklenecekse SADECE
ayar setinden seçilmeli, final'e asla dokunulmamalı.
"""

from __future__ import annotations

import re

from app.decision.llm_client import OllamaIstemcisi
from app.decision.negatif_dogrulama import negatifleri_dogrula
from app.decision.negatif_dogrulama import ozet as negatif_ozet
from app.decision.negatif_dogrulama import prompt_blogu as negatif_blogu
from app.decision.okas_kapsam import tamami_kapsam_disi
from app.decision.schemas import KapsamSonucu, KapsamSonucuGenis
from app.domain.models import Ihale, kapsam_metni
from app.retrieval.profil_retriever import OrnekVurusu, PaketVurusu

# ÖLÇÜLDÜ (bkz. app/retrieval/profil_retriever.py'deki dağılım notu): aktif ihalelerin
# %5,3'ü 4.000 karakteri, %2,5'i 6.000'i aşıyor. 8.000'de aktif ihalelerin ~%98'i tam
# olarak modele gidiyor. num_ctx=8192 için güvenli: sistem promptu (~3.5k) + paketler
# (~1.5k) + 8k ilan metni ≈ 13k karakter ≈ 5-6.5k Türkçe token.
ILAN_METNI_MAX_KARAKTER = 8000

SISTEM_PROMPTU = """Sen İSBAK (İstanbul Bilişim ve Akıllı Kent Teknolojileri A.Ş.) için çalışan bir ihale \
KAPSAM değerlendirme asistanısın. Görevin TEK BİR SORUYU cevaplamak: bu ihalenin konusu İSBAK'ın \
iş paketleriyle (aşağıda "İLGİLİ İŞ PAKETLERİ" olarak verilecek) örtüşüyor mu? Üç seçenekten \
birini seç: "uygun", "belirsiz", "uygun_degil".

SANA SORULMAYAN ŞEY: İSBAK'ın bu ihalenin yeterlilik şartlarını (sertifika, iş deneyimi, mali \
yeterlilik) karşılayıp karşılamadığı. O AYRI bir aşamada değerlendirilir. Sen sadece "bu iş bizim \
alanımız mı" sorusuna bak.

TEMEL İLKE: Kaçırma (aslında uygun olan bir ihaleyi "uygun_degil" demek ya da gereksiz yere düşük \
skorla elemek) en pahalı hatadır. Emin değilsen "belirsiz" de — "uygun_degil" sadece açıkça \
alakasız olduğunda kullanılır.

- ÇIKTI SIRASI ÖNEMLİ: önce "gerekce" yaz (kanıtları tart), sonra "eslesen_paket"/"eslesen_okas", \
sonra "ilgi_skoru" (o gerekçeye dayanarak), "karar" EN SON — yukarıdakilerle TUTARLI olacak şekilde. \
Kararı önce verip gerekçeyi sonradan uydurma.
- "eslesen_paket" alanına, İLGİLİ İŞ PAKETLERİ listesinden en çok örtüşen paketin TAM başlığını yaz. \
karar "uygun" ya da "belirsiz" ise bu alanı ASLA boş bırakma. Sadece "uygun_degil" ise ve hiçbir \
paketle gerçek örtüşme yoksa null bırakabilirsin.
- "ilgi_skoru" SENİN KENDİ YARGINDIR, sana verilen bir sayının kopyası DEĞİLDİR. \
İki farklı sayı var, karıştırma:
  (a) İş paketi listesinde parantez içinde gördüğün "(benzerlik: 0.53)" değerleri — bunlar \
  METİN YAKINLIĞI ölçen makine skorlarıdır. Girdi bilgisidir, senin cevabın değildir. \
  Bu sayıyı "ilgi_skoru" alanına KOPYALAMA ve gerekçende ona dayanma.
  (b) "ilgi_skoru" — İSBAK'ın iş alanıyla GERÇEK örtüşmeye dair SENİN değerlendirmen. \
  Ölçek: 0.8-1.0 çok güçlü/doğrudan örtüşme, 0.5-0.8 makul örtüşme, 0.2-0.5 zayıf, \
  0.0-0.2 örtüşme yok. Bir ihaleyi "uygun_degil" buluyorsan ilgi_skoru DÜŞÜK olmalıdır \
  (0.2-0.4 bandı); yüksek bir skorla "uygun_degil" demek kendi kendiyle çelişir.
- ilgi_skoru 0.5'in altındaysa karar ASLA "uygun" olamaz. BU KURAL TEK YÖNLÜDÜR VE TERSİ \
GEÇERLİ DEĞİLDİR: skorun 0.5'in ÜSTÜNDE olması kararı "uygun" YAPMAZ. "Skor 0.5'in üzerinde \
olduğu için uygundur" CÜMLESİNİ KURMA — bu bir gerekçe değildir. Gerekçen işin KONUSUNA \
dayanmalı, sayıya değil. Aşağıdaki tuzaklardan biri geçerliyse yüksek skorlu bir ihale de \
"uygun_degil" olur.

İHALE TÜRÜ BİR KAPSAM ÖLÇÜTÜ DEĞİLDİR. "Mal", "Hizmet", "Yapım" ve "Danışmanlık" ihale \
usulleridir, iş alanı tanımı değil. İSBAK hem ürün/malzeme tedarik eder, hem hizmet verir, hem \
yapım işi üstlenir. Bir ihalenin "mal alımı" olması onu KAPSAM DIŞI YAPMAZ; trafik ışığı, \
sinyalizasyon malzemesi, kamera, sensör, denetleyici gibi kalemlerin alımı İSBAK'ın çekirdek \
işidir. Sana verilen iş paketi tanımları yetkinlik listesidir — orada "tedarik" kelimesinin \
geçmemesi, İSBAK'ın tedarik yapmadığı anlamına GELMEZ. Kararını işin KONUSUNA göre ver, alım \
biçimine göre değil.

NE ZAMAN "BELİRSİZ" DENİR — iki kural, ikisi de bağlayıcı:

(B1) KANIT YETERSİZLİĞİ. İlan kapsamı işin ne olduğunu SOMUT KALEMLERLE tarif etmiyorsa — \
örneğin sadece "N kalemden oluşan ... işi" ya da "N modülden oluşan ... sistemi" deyip kalemleri \
saymıyorsa, ya da "ayrıntılı bilgiye idari şartnameden ulaşılabilir" deyip teknik içerik \
vermiyorsa — elinde başlıktan başka kanıt yok demektir. Bu durumda karar "belirsiz" olmak ZORUNDA, \
ilgi_skoru EN FAZLA 0.5 olabilir ve **"belirsiz_tipi" alanına "kanit_yetersiz" YAZMALISIN**. \
Başlık ne kadar ilgili görünürse görünsün, başlıktan iş kapsamı çıkarımı YAPMA. Bir insanın \
şartnameye bakması gerekiyor demektir; senin görevin bunu dürüstçe bildirmek, tahmin etmek değil.

"belirsiz_tipi" AYRIMI — karar "belirsiz" ise bu alanı MUTLAKA doldur, aksi hâlde null bırak:
- "kanit_yetersiz": İlan işin ne olduğunu SÖYLEMİYOR. Kararsız değilsin, ELİNDE VERİ YOK. \
Kalemler/modüller sayılmamış, içerik şartnameye havale edilmiş. Bu ihale bir insana gitmeli.
- "zayif_ortusme": İlan işin ne olduğunu yeterince açık söylüyor AMA İSBAK'ın alanıyla örtüşme \
zayıf ya da kısmi. Burada gerçekten kararsızsın.
İkisi FARKLI SONUÇ doğurur: birinde eksik olan BİLGİ, diğerinde eksik olan ÖRTÜŞME. Karıştırma.

(B2) KALEM AĞIRLIĞI. İhale çok kalemliyse, İSBAK'ın alanına giren kalemlerin işin AĞIRLIKLI \
kısmını oluşturup oluşturmadığına bak. Kalemlerden yalnızca biri/azı örtüşüyor, geri kalanı \
(araç, genel donanım, bilgisayar/tablet, tanıtım-görünürlük faaliyetleri, inşaat, personel vb.) \
başka alanlara aitse karar "belirsiz" olur — "uygun" DEĞİL. Gerekçende SADECE örtüşen kalemi \
sayıp diğerlerini görmezden GELME; kalemlerin tamamını tart ve gerekçende bunu göster.

KELİME BENZERLİĞİ TUZAKLARI — kelime düzeyinde örtüşme TEK BAŞINA yeterli değildir. Aşağıdaki \
durumlarda İŞİN GERÇEK NİTELİĞİNE bak ve "uygun_degil" de:
- Genel amaçlı ERP/CRM/muhasebe/İK/bordro yazılımı (SAP, Oracle vb.) — bunlar İSBAK'ın "kurumsal \
yazılım" paketine GİRMEZ, ERP entegratörlerinin işidir.
- Başka bir kurumun (bakanlık, KOSGEB, başka bir kamu kurumu) KENDİNE ÖZEL, iç işleyişine yönelik \
yazılım/sistem projesi — CBS/YZ/veri analitiğiyle kelime düzeyinde örtüşse bile İSBAK'ın alanı değil.
- Tıbbi/sağlık amaçlı yapay zeka, görüntüleme (MR, tomografi) veya yazılımları — "görüntüleme" ya da \
"yapay zeka" geçse de İSBAK'ın trafik/kent odaklı görüntü işleme alanına girmez.
- Bir tesisin (hastane, fabrika) GENEL bakım/onarım sözleşmesi — CCTV listenin sadece bir kalemiyse \
ve asıl iş genel tesis bakımıysa (santral, TV yayını, çağrı sistemi) bu bir kamera projesi değildir.
- Genel yapım/inşaat işleri — "sinyalizasyon" gibi bir kelime genel bir yapım sözleşmesinin küçük bir \
alt kalemi olarak geçiyorsa, bu İSBAK'ın teknoloji sistemi kurma işiyle aynı değildir.
- FİZİKSEL ALTYAPI YAPIM İŞLERİ: işin konusu zeminin/yapının KENDİSİNİ imal etmekse ve ortada \
kurulacak/tedarik edilecek HİÇBİR elektronik sistem, cihaz ya da yazılım yoksa karar "uygun_degil"dir. \
Örnekler: beton parke/asfalt ile yol ve kaldırım yapımı, bordür döşeme, cephe sağlaştırma, çatı \
yenileme, bina tadilat/onarımı, park mobilyası (kamelya, bank, oyun grubu) yapımı, dere ıslahı. \
İSBAK bu işlerin ÜZERİNE teknoloji kurar; işin kendisini yapmaz. \
DİKKAT: bir iş paketinin başlığında "Ulaşım", "Trafik" ya da "Kent" geçmesi, o paketin yol/kaldırım \
İMALATINI kapsadığı anlamına GELMEZ — "Ulaşım Planlama" planlama ve modelleme işidir, zemin imalatı \
değil. Bu tür ihalelerde iş paketi başlığındaki kelime benzerliğine ALDANMA.

BENZER GEÇMİŞ İHALELERİ KIYASLAMA KURALI: Sana ÜÇ ayrı grup gösterilebilir; her grubun etiketi \
başlığında AÇIKÇA yazılıdır ve bu etiketler birbirinin yerine geçmez:
  1) daha önce "UYGUN" bulunmuş örnekler,
  2) daha önce "BELİRSİZ" bulunmuş örnekler (karar verilememiş, insana gitmiş işler),
  3) daha önce "UYGUN_DEGIL" bulunmuş örnekler (yüzeysel benzese de reddedilmiş işler).

Bir örneği alıntılarken ONA VERİLEN ETİKETİ değiştirme. "BELİRSİZ" grubundaki bir örneğe \
benzemek, adayın "uygun" olduğunun kanıtı DEĞİLDİR — tam tersine, aday da büyük olasılıkla \
"belirsiz"dir. Aynı şekilde gerekçende "geçmişte benzer işler uygun bulunmuştu" diyeceksen, \
gerçekten 1. gruptaki bir örneği kastettiğinden emin ol. Aday hem "uygun" hem "uygun_degil" \
grubuna orta düzeyde benziyorsa bu "belirsiz" işaretidir, körü körüne "uygun" değil.

Sadece verilen JSON şemasına uyan bir çıktı üret. Gerekçeyi Türkçe ve kısa (1-3 cümle) yaz."""


# ============================================================================
# SKOR YANKISI — SAYIYI GÖSTERMEZSEN KOPYALAYAMAZ (04.08.2026)
# ============================================================================
# Sistem promptu satır ~57'de açıkça diyor: "ilgi_skoru SENİN KENDİ YARGINDIR,
# sana verilen bir sayının kopyası DEĞİLDİR." Model bunu düzenli olarak çiğniyor.
#
# ÖLÇÜLDÜ (`evaluation/evaluate.py` zaten `skor_yankisi_sayisi` alanını yazıyor):
#     kaçırma seti v7 (think açık) : 30 ihalenin 18'inde yankı
#     kaçırma seti v5              : 30 ihalenin 26'sında
#     kaçırma seti v1 (think kapalı):            9'unda
#
# Zararı kozmetik değil. `UYGUN_ICIN_MIN_ILGI_SKORU = 0.5` ve
# `BELIRSIZ_NETLESTIRME_ESIGI = 0.50` iki KARAR KAPISI ve ikisi de `ilgi_skoru`ya
# bakıyor. Retrieval skorları 0.48-0.55 bandında yoğunlaşıyor — yani her iki kapı
# da, modelin yargısı yerine kopyalanmış bir sayının 0.50'nin hangi tarafına
# düştüğüne bakarak karar veriyor. 2026/1280806'da fark 0.0049'du.
#
# Prompt'u sertleştirmek denendi, tutmadı. Sayıyı hiç göstermemek kökten çözüm
# ama modelin paketleri ağırlıklandırmasını da engelliyor. Orta yol: SIRALAMA
# BİLGİSİ KALSIN, KOPYALANACAK SAYI KALMASIN.
#
# Bantlar `Sonuclar/esik_analizi.json` ölçümünden geliyor: insan onaylı
# pozitiflerin minimumu 0.5149, etiketli 'belirsiz'lerin minimumu 0.481.
_YAKINLIK_BANTLARI = ((0.60, "çok yakın"), (0.55, "yakın"), (0.50, "orta"))


def yakinlik_etiketi(benzerlik: float) -> str:
    for esik, etiket in _YAKINLIK_BANTLARI:
        if benzerlik >= esik:
            return etiket
    return "uzak"


def _paket_satiri(p: PaketVurusu, nitel_yakinlik: bool = False) -> str:
    olcu = (
        f"yakınlık: {yakinlik_etiketi(p.benzerlik)}"
        if nitel_yakinlik
        else f"benzerlik: {p.benzerlik}"
    )
    satir = f"- {p.baslik} [{p.oncelik}] ({olcu})"
    if p.negatif_terimler:
        satir += (
            f"\n  (DİKKAT — bu terimler bu paketin KAPSAMINA GİRMEZ, tam tersine bu paketten "
            f"HARİÇ TUTULMUŞTUR: {', '.join(p.negatif_terimler)}. İhale metninde bunlardan biri "
            f"geçiyorsa bu paketi 'eslesen_paket' olarak SEÇME — başka paket ara, hiçbiri "
            f"uymuyorsa karar 'uygun_degil' olsun.)"
        )
    return satir


# Yatay/destekleyici profil aileleri. Bunlar "ne yapıldığını" değil "nasıl yapıldığını"
# tanımlar: kurulum, bakım, danışmanlık, yazılım, donanım, ağ, güvenlik. Bir ihale
# SADECE bunlarla eşleşiyorsa, hangi ALANIN kurulumu/bakımı olduğu belirsizdir.
# `profile_registry.json` bunu zaten biliyor: her alan profilinin `destekleyici_profiller`
# listesinde OPS-* ve TEK-* var.
DESTEKLEYICI_ON_EKLER = ("OPS-", "TEK-")


def _destekleyici_mi(kod: str) -> bool:
    return kod.upper().startswith(DESTEKLEYICI_ON_EKLER)


# ============================================================================
# B1 — KANIT YETERSİZLİĞİNİ KOD TESPİT EDER (30.07.2026)
# ============================================================================
# B1 kuralı prompt'ta yazılı ama modelin deseni METİNDE KENDİ FARK ETMESİNE
# bağlıydı ve fark etmiyor. Gerçek vaka: "Dijital İnsan Kaynakları Yönetim
# Sistemi" (2026/1121504) — ilan "1 Adet 13 Modülden oluşan ... Ayrıntılı
# bilgiye idari şartnameden ulaşılabilir" diyor, modüller sayılmamış. Model B1'i
# hiç uygulamadı, başlıktan çıkarım yapıp `uygun_degil` dedi. Oysa en yakın paket
# ENT-04 "Geçiş Kontrolü ve Personel Devam Sistemleri" (PDKS) — 13 modülün içinde
# personel devam takibi olması çok muhtemel. Kısmi örtüşme + bilinmeyen içerik.
#
# ÖLÇÜLDÜ (300 rastgele aktif ihale):
#     kalem listesi VAR            162  (%54)
#     kanıt YETERSİZ               137  (%46)
#     kanıt yetersiz + paket >=0.50  24  (%8)   <-- sinyalin verildiği küme
#
# NEDEN ZORLAMA DEĞİL, SİNYAL: o 24 ihalenin yalnızca 3-4'ü gerçekten `belirsiz`
# olmalı. Geri kalanı (araç kiralama, personel taşıma, ERP lisansı, veri merkezi
# yer temini) sistemin ŞU AN DOĞRU reddettiği ihaleler. Kod zorlaması 20 doğru
# kararı bozup 4 doğru karar kazandırırdı — kötü takas. Bu yüzden model bilgiyi
# alıyor, kararı kendi veriyor.
_SARTNAMEYE_HAVALE = re.compile(
    r"ayrıntılı bilgiye.{0,80}şartname\w*\s*(?:dan|den)?\s*ulaşılabilir\.?", re.I | re.S
)
_NITELIK_ALANI = re.compile(r"Niteliği,?\s*türü ve miktarı\s*\|\s*:\s*\|(.{0,4000}?)\|", re.S)
# SADECE BAŞTAKİ sayaç elenir: "4 Kalem - <başlık>", "1 Adet 13 Modülden oluşan …".
# Metin İÇİNDEKİ miktarlar ("… 50 ADET, … 10 ADET") KORUNUR — onlar sayımın
# kendisidir, yani kalem listesi VAR demektir. İlk sürüm hepsini siliyordu ve
# ESP32/FPGA gibi gerçekten sayılmış ilanları "kanıt yok" sanıyordu.
_SAYAC_KALIBI = re.compile(
    r"^[\s\-–—]*(?:\d+\s*(?:kalem|adet|modül|modülden|grup|kısım)\w*(?:\s+oluşan)?[\s\-–—]*)+",
    re.I,
)
_KALEM_LISTESI_ESIGI = 60  # havale + başlık + sayaç atılınca geriye kalan bilgi


def kalem_listesi_yok_mu(
    kapsam: str,
    adi: str | None = None,
    alan_terimleri: frozenset[str] | None = None,
) -> bool:
    """İlan işin ne olduğunu SOMUT KALEMLERLE söylüyor mu?

    "N kalem/modül ... ayrıntı şartnamede" deyip içeriği saymıyorsa True.

    ÜÇ ŞEY ELENİR, geriye anlamlı metin kalıyor mu diye bakılır:
      1. "Ayrıntılı bilgiye ... şartnameden ulaşılabilir" havalesi (boilerplate)
      2. İHALE BAŞLIĞI — `Niteliği` alanı çoğu ilanda başlığı AYNEN tekrarlıyor.
         Elenmezse başlık "içerik" sanılıyor. Gerçek vaka: İBB Mikroservis
         ihalesi ("4 Kalem - <başlığın tamamı>") yanlışlıkla "listesi var"
         sayılmıştı.
      3. Sayaç kalıpları ("4 Kalem", "1 Adet", "13 Modülden oluşan")

    Desen bulunamazsa False döner — belirsizlikte sinyal ÜRETMEYİZ, çünkü yanlış
    tetiklenen bir "kanıt yok" uyarısı modeli gereksiz yere `belirsiz`e iter.
    """
    if not kapsam:
        return False
    m = _NITELIK_ALANI.search(kapsam)
    if not m:
        return False

    ham_nitelik = m.group(1)
    kalan = _SARTNAMEYE_HAVALE.sub(" ", ham_nitelik).strip()
    kalan = _SAYAC_KALIBI.sub("", kalan)

    # ALAN TERİMİ İSTİSNASI (07.08.2026) — düşünme kapatılınca ortaya çıktı.
    #
    # ÖLÇÜLDÜ: `LLM_DUSUNME=false` ile kaçırma seti 30/30'dan 20/30'a düştü.
    # On hatanın HEPSİ `uygun -> belirsiz` ve hepsinin gerekçesi aynı:
    #     "40 Kalem CCTV Haberleşme, Kontrol ve Kent İzleme Merkezi Sistemi"
    #     "53 Kalemde Elektronik Denetleme Sistemi Malzeme Alımı"
    #     "36 kalem Güvenlik Kamera Sistemi ve Fiber Optik Altyapı"
    # Bunlar BELİRSİZ İFADELER DEĞİL — işi açıkça tarif ediyorlar. Sorun şu:
    # `Niteliği` alanı başlığı tekrarlıyor, kod başlığı eliyor, geriye bir şey
    # kalmıyor ve "kanıt yok" deniyor. Yani ELENEN ŞEYİN KENDİSİ kanıttı.
    #
    # Düşünme açıkken model bu kuralı bazen aşıyordu; kapalıyken prompt'un
    # lafzına birebir uyuyor. Hız kazancının bedeli buydu.
    #
    # ÇÖZÜM: başlığı elemeden ÖNCE, alanın profil sözlüğünden bir terim içerip
    # içermediğine bak. İçeriyorsa işin ne olduğu bellidir — uyarı verilmez.
    #
    # NEDEN "Dijital İK Yönetim Sistemi" HÂLÂ BELİRSİZ KALIR: o ilanda
    # "13 Modülden oluşan" var ama hiçbir profil terimi yok; modüllerin içeriği
    # gerçekten bilinmiyor. İstisna yalnızca ALAN SÖZLÜĞÜ eşleştiğinde açılıyor.
    #
    # ÖLÇÜM: yerel veride B1 tetiklenen 5 pozitifin 4'ünü kurtarıyor,
    # 16 etiketli negatifte SIFIR yanlış tetiklenme.
    if alan_terimleri and _alan_terimi_geciyor(ham_nitelik, alan_terimleri):
        return False

    # Başlıktaki kelimeleri çıkar — geriye BAŞLIKTA OLMAYAN bilgi kalıyor mu?
    if adi:
        baslik_kelimeleri = {k for k in _kelimele(adi) if len(k) > 2}
        kalan = " ".join(k for k in kalan.split() if _sadelestir(k) not in baslik_kelimeleri)

    return len(" ".join(kalan.split())) < _KALEM_LISTESI_ESIGI


# Alan terimi sayılmak için en az uzunluk. "yol", "kart" gibi kısa parçalar
# her ilana vurur ve istisnayı her yerde açar.
#
# 4 SEÇİLDİ ÇÜNKÜ "CCTV" 4 karakter ve gerçek bir alan terimi. 5'te eleniyordu.
# KELİME DÜZEYİNE İNİLMEDİ (çok kelimeli terimleri parçalamak): denendi ve
# ölçüldü — profiller arası belge sıklığı 20 profillik bir kümede zayıf bir
# ayırt edici ve "hizmet" gibi genel kelimeler sözlüğe sızıyor; istisna o zaman
# neredeyse her ilanda açılıp B1'i tamamen etkisiz kılıyor.
MIN_ALAN_TERIMI_UZUNLUGU = 4


def alan_sozlugu(profiller) -> frozenset[str]:
    """Profillerin `anahtar_kelimeler` listesinden katlanmış alan sözlüğü.

    `guclu_terimler` YETMİYOR: ölçüldü, B1 hatalarının yalnızca 3/9'unu
    yakalıyordu. `anahtar_kelimeler` yükleyicide guclu + destekleyici + genel +
    ekipman eş anlamlıları olarak birleşiyor ve 5/5'ini yakalıyor.
    """
    from app.decision.sql_filtre import katla

    return frozenset(
        katla(t)
        for p in profiller or []
        for t in (p.anahtar_kelimeler or [])
        if len(str(t)) >= MIN_ALAN_TERIMI_UZUNLUGU
    )


def _alan_terimi_geciyor(metin: str, sozluk: frozenset[str]) -> bool:
    from app.decision.sql_filtre import katla

    h = katla(metin)
    return any(t in h for t in sozluk)


def _sadelestir(k: str) -> str:
    return k.strip(" .,;:()[]/-").casefold()


def _kelimele(metin: str) -> set[str]:
    return {_sadelestir(k) for k in (metin or "").split()}


def kullanici_mesaji(
    ihale: Ihale,
    paketler: list[PaketVurusu],
    uygun_ornekler: list[OrnekVurusu] | None = None,
    red_ornekler: list[OrnekVurusu] | None = None,
    belirsiz_ornekler: list[OrnekVurusu] | None = None,
    *,
    min_skor: float = 0.0,
    destekleyici_kurali: bool = False,
    nitel_yakinlik: bool = False,
    kanit_uyarisini_bastir: bool = False,
    negatif_dogrula: bool = False,
    alan_terimleri: frozenset[str] | None = None,
) -> str:
    metin = kapsam_metni(ihale)
    if metin and len(metin) > ILAN_METNI_MAX_KARAKTER:
        metin = metin[:ILAN_METNI_MAX_KARAKTER] + " [...kırpıldı...]"

    okas = ihale.okas_metni()
    okas_kismi = f"\nResmi sınıflandırma kodları (OKAS): {', '.join(okas)}" if okas else ""
    metin_kismi = f"\nİlan kapsamı (temizlenmiş): {metin}" if metin else ""

    # Üç grup, üç ayrı ve AÇIK etiket. Karıştırılmaları v1'deki üç hatanın da sebebiydi.
    def _grup(baslik: str, ornekler: list[OrnekVurusu] | None) -> str:
        if not ornekler:
            return ""
        # Örnek skorları da nitelleştiriliyor: yankı kanalı paket listesiyle sınırlı
        # değil, model buradaki sayıyı da kopyalayabiliyor.
        liste = "\n".join(
            f"- {o.baslik} "
            + (
                f"(yakınlık: {yakinlik_etiketi(o.benzerlik)})"
                if nitel_yakinlik
                else f"(benzerlik: {o.benzerlik})"
            )
            for o in ornekler
        )
        return f"\n\n{baslik}\n{liste}"

    ornek_kismi = (
        _grup(
            'BENZER GEÇMİŞ İHALELER — ETİKET: "UYGUN" (onaylanmış, İSBAK\'ın alanına girdiği '
            "teyit edilmiş somut işler):",
            uygun_ornekler,
        )
        + _grup(
            'BENZER GEÇMİŞ İHALELER — ETİKET: "BELİRSİZ" (karar VERİLEMEMİŞ, insan incelemesine '
            "gitmiş işler. Bunlara benzemek 'uygun' kanıtı DEĞİLDİR — aksine adayın da "
            "'belirsiz' olma ihtimalini güçlendirir):",
            belirsiz_ornekler,
        )
        + _grup(
            'BENZER GEÇMİŞ İHALELER — ETİKET: "UYGUN_DEGIL" (yüzeysel/kelime düzeyinde benzese '
            "de REDDEDİLMİŞ gerçek işler):",
            red_ornekler,
        )
    )

    # --- İŞ PAKETİ BÖLÜMÜ ---
    # Eşiğin ALTINDA kalan paketler prompt'tan ATILMAZ; başlık dürüstleşir. Sebep:
    # "İLGİLİ İŞ PAKETLERİ" başlığı modele bu ihalenin bir paketle ilgili OLDUĞUNU
    # söylüyordu. 29.07 triyajında cami inşaatı, okul onarımı ve öğrenci taşıma
    # ihalelerine bu başlıkla beş paket sunuldu ve üçüne de "uygun" dendi.
    gecenler = [p for p in paketler if p.benzerlik >= min_skor] if min_skor > 0 else list(paketler)

    if not gecenler and paketler:
        en_yuksek = max(p.benzerlik for p in paketler)
        # Nitel modda buradaki ham sayı da gizlenir; aksi halde yankı için tek
        # başına yeterli bir kaynak olarak kalırdı.
        olcu_kismi = (
            "hiçbiri anlamlılık eşiğini geçmedi"
            if nitel_yakinlik
            else f"en yüksek benzerlik {en_yuksek:.4f}, anlamlılık eşiği {min_skor}"
        )
        paket_bolumu = (
            f"İŞ PAKETİ EŞLEŞMESİ YOK\n"
            f"Bu ihale İSBAK'ın 20 iş paketinin HİÇBİRİYLE anlamlı düzeyde örtüşmüyor "
            f"({olcu_kismi}). "
            f"Aşağıdakiler sadece 'en yakın' olanlar — İLGİLİ OLDUKLARI ANLAMINA GELMEZ, "
            f"bir örtüşme kanıtı olarak KULLANMA:\n"
            + "\n".join(_paket_satiri(p, nitel_yakinlik) for p in paketler)
        )
    else:
        paket_bolumu = (
            f"İLGİLİ İŞ PAKETLERİ (retrieval'dan gelen en yakın {len(gecenler)} paket):\n"
            + "\n".join(_paket_satiri(p, nitel_yakinlik) for p in gecenler)
        )

    # --- DESTEKLEYİCİ PAKET UYARISI ---
    destek_uyarisi = ""
    if destekleyici_kurali and gecenler and all(_destekleyici_mi(p.kod) for p in gecenler):
        destek_uyarisi = (
            "\n\nDİKKAT — YALNIZCA YATAY PAKET EŞLEŞMESİ:\n"
            "Eşleşen paketlerin hepsi YATAY/DESTEKLEYİCİ paketler (kurulum, bakım, "
            "danışmanlık, yazılım, donanım, ağ, güvenlik). Bunlar İSBAK'ın işi 'NASIL' "
            "yaptığını tanımlar, 'NE' yaptığını değil. İSBAK her türlü bakım-onarım ya da "
            "yazılım işini yapmaz; KENDİ ALANINDAKİ sistemlerin (trafik, ulaşım, kamera, "
            "aydınlatma, kent donatısı) kurulumunu ve bakımını yapar.\n"
            "Hiçbir ALAN paketi (AUS-*, ENT-*, PLN-*) eşleşmediyse, işin hangi alana ait "
            "olduğu belirsizdir. Ölçülmüş örnekler: ambulans mekanik bakımı, belediye "
            "hizmet binası bakımı, römorkör sörveyi ve laboratuvar işletmesi — dördü de "
            "'Bakım, Onarım ve Teknik Destek' paketiyle eşleşti, dördü de İSBAK'ın işi DEĞİL."
        )

    # --- B1: KANIT YETERSİZLİĞİ (kod tespit eder, karar modelin) ---
    # Uyarı SADECE eşiği geçen bir paket varken veriliyor. Eşiği geçen paket yoksa
    # eksik kalem listesi kararı değiştirmez — akaryakıt alımında kalemleri bilmemek
    # sonucu etkilemez. Örtüşme ihtimali varken ise değiştirir: İK sisteminin 13
    # modülünün içinde PDKS olabilir ve ENT-04 zaten eşiği geçmiştir.
    # `kanit_uyarisini_bastir`: OKAS vetosu (bkz. app/decision/okas_kapsam.py).
    # İhalenin TÜM OKAS kodları açıkça kapsam dışı bir bölümdeyse bu uyarı hiç
    # verilmez — prompt'taki (a) istisnasının deterministik hali. Gerçek vaka:
    # 2026/1280806 öğrenci taşıma (OKAS 60*), uyarı yüzünden `belirsiz` olmuştu.
    kanit_uyarisi = ""
    if (gecenler and not kanit_uyarisini_bastir
            and kalem_listesi_yok_mu(metin, ihale.adi, alan_terimleri)):
        kanit_uyarisi = (
            "\n\nDİKKAT — İLANDA KALEM/MODÜL LİSTESİ YOK:\n"
            "Bu ilanın 'Niteliği, türü ve miktarı' alanı işin İÇERİĞİNİ saymıyor; yalnızca bir "
            "sayı veriyor ve ayrıntıyı şartnameye havale ediyor. Yani elinde BAŞLIKTAN BAŞKA "
            "kanıt YOK ve yukarıda eşiği geçen bir iş paketi VAR.\n"
            "Sayılmayan kalemlerin içinde o paketin kapsamına giren bir bileşen BULUNABİLİR. "
            "Başlığa bakıp 'bu iş bizim alanımız değil' diye kesip atma — bilmediğin şeyi "
            "yokmuş gibi sayma.\n"
            "Bu durumda karar 'belirsiz' ve belirsiz_tipi 'kanit_yetersiz' olmalıdır.\n"
            "İSTİSNA — şu iki durumda 'uygun_degil' demeye devam et, eksik kalem listesi o "
            "kararı DEĞİŞTİRMEZ:\n"
            "  (a) İşin türü başlıktan zaten kesin anlaşılıyor ve İSBAK'ın hiçbir alanıyla "
            "ilgisi yok (akaryakıt, gıda, temizlik, giyim, ilaç, kırtasiye gibi).\n"
            "  (b) Yukarıdaki KELİME BENZERLİĞİ TUZAKLARI maddelerinden biri geçerli — "
            "özellikle FİZİKSEL ALTYAPI YAPIM İŞLERİ (yol/kaldırım/parke/bordür imalatı, bina "
            "tadilatı, park mobilyası). Orada işin ne olduğu bellidir; eksik olan kalem dökümü "
            "kararı etkilemez."
        )

    # --- NEGATİF TERİM DOĞRULAMASI ---
    # Sunulan paketlerin dışlama terimleri ilan metninde GERÇEKTEN geçiyor mu?
    # Geçenler kanıtıyla (metinden alıntıyla) gösterilir. Gerekçe:
    # app/decision/negatif_dogrulama.py
    negatif_uyarisi = ""
    if negatif_dogrula and gecenler:
        negatif_uyarisi = negatif_blogu(negatifleri_dogrula(metin, gecenler))

    return f"""İHALE:
Adı: {ihale.adi}
İdare: {ihale.idare_adi}
İl: {ihale.il or "belirtilmemiş"}
Tür: {ihale.ihale_turu or "belirtilmemiş"}{okas_kismi}{metin_kismi}

{paket_bolumu}{destek_uyarisi}{negatif_uyarisi}{kanit_uyarisi}{ornek_kismi}

Yukarıdaki bilgilere göre bu ihale İSBAK'ın KAPSAMINA giriyor mu? JSON şemasına uygun cevap ver."""


# Bu eşiğin altında "uygun" denemez. Prompt'ta da yazıyor ama MODEL BU KURALI ÇİĞNİYOR.
UYGUN_ICIN_MIN_ILGI_SKORU = 0.5


def skor_karar_tutarliligini_zorla(sonuc: KapsamSonucu) -> tuple[KapsamSonucu, list[str]]:
    """ilgi_skoru ile karar arasındaki tutarlılığı KODDA garanti eder.

    NEDEN KODDA: "ilgi_skoru 0.5'in altındaysa karar ASLA 'uygun' olamaz" kuralı
    sistem promptunda açıkça yazılı, ama model onu tekrar tekrar çiğniyor. Üç ayrı
    gerçek vaka:
      - kurumsal yazılım lisansı ihalesi -> ilgi_skoru 0.4846, karar "uygun"
      - video yönetim yazılımı ihalesi   -> ilgi_skoru 0.49,   karar "uygun"
      - aynı ihale, sonraki koşuda       -> ilgi_skoru 0.49,   karar "uygun"
    İki ayrı proje, farklı promptlar, aynı hata. Deterministik bir kuralı olasılıksal
    bir modele bırakmanın bedeli bu — İSBAK-idare kuralında da aynı şey yaşanmıştı
    (bkz. app/decision/on_filtre.py).

    DÜŞÜRME YÖNÜ "belirsiz", "uygun_degil" DEĞİL. Model zaten bir örtüşme görmüş ama
    zayıf bulmuş; bunu elemeye çevirmek ihaleyi insan incelemesinden koparır ve
    kaçırma riski yaratır. Güvenli yön yukarı değil, ortadır.

    ÖLÇÜM (28.07.2026, v1-v4 dört koşunun tüm verisi): bu kural sadece Millestone
    vakasını düzeltiyor, başka hiçbir kararı değiştirmiyor — gerçek "uygun" ihalelerin
    skorları 0.65-0.78 bandında, hepsi eşiğin belirgin şekilde üstünde.
    """
    if sonuc.karar == "uygun" and sonuc.ilgi_skoru < UYGUN_ICIN_MIN_ILGI_SKORU:
        uyari = (
            f"Model ilgi_skoru={sonuc.ilgi_skoru} ile 'uygun' dedi; "
            f"{UYGUN_ICIN_MIN_ILGI_SKORU} altında 'uygun' olamaz — karar kod seviyesinde "
            f"'belirsiz'e düşürüldü (skor/karar tutarlılık kuralı)."
        )
        return (
            sonuc.model_copy(
                update={"karar": "belirsiz", "gerekce": f"{sonuc.gerekce} [{uyari}]"}
            ),
            [uyari],
        )
    return sonuc, []


def belirsizi_netlestir(
    sonuc: KapsamSonucu, esik: float
) -> tuple[KapsamSonucu, list[str]]:
    """`belirsiz` + düşük skor -> `uygun_degil`. Yukarıdaki kuralın AYNADAKİ EŞİ.

    SORUN: model `uygun_degil` demekten kaçınıp `belirsiz`e sığınıyor. 60 etiketli
    ihalede ölçüldü — gerçekte `uygun_degil` olan 22 ihalenin sadece 5'ine net
    `uygun_degil` dedi, 13'ünü `belirsiz`e attı. Sistem "hayır" diyemiyor,
    "bilmiyorum" diyor. Aday listesi bu yüzden şişiyor.

    NEDEN SKORLA ÇÖZÜLEBİLİR: model doğru skoru zaten üretiyor, sadece o skoru
    karara çeviremiyor. Etiketli veride temiz bir boşluk var:

        model 'belirsiz' dedi, skor 0.39-0.47  ->  13 vakanın 13'ü gerçekte uygun_degil
        model 'belirsiz' dedi, skor 0.4898     ->  gerçekten belirsiz (Millestone)
        model 'belirsiz' dedi, skor 0.5149     ->  gerçekte uygun (Siber Güvenlik SOC)

    İnsan onaylı 33 pozitifin en düşük skorları: 0.5149 / 0.5161 / 0.5207 / 0.5223.
    Yani 0.47 ile 0.5149 arasında hiçbir gerçek pozitif YOK.

    ÖLÇÜM (60 ihale, gerçek etiketi uygun|uygun_degil olanlar):

        esik    tam isabet   KACIRMA   yanlis alarm
        kapalı    42/60 %70        0             4
        0.44      49/60 %82        0             4
        0.48      55/60 %92        0             4     <-- seçilen
        0.52      55/60 %92        1             4     kaçırma başlıyor

    0.48'de 13 yanlış alarm düzeliyor, kaçırma 0'da kalıyor. 0.52'de ilk kaçırma
    geliyor (Siber Güvenlik SOC/CTI, skor 0.5149) — üst sınır orası.

    VARSAYILAN KAPALI (0.0). Açıkça istenmedikçe hiçbir ihale bu kuralla elenmez.

    =========================================================================
    30.07.2026 — `kanit_yetersiz` ASLA NETLEŞTİRİLMEZ
    =========================================================================
    B1 kuralı "kanıt yetersizse karar `belirsiz` ZORUNLU ve ilgi_skoru EN FAZLA
    0.5" diyor. Yani B1'in ürettiği her karar bu eşiğin (0.50) tam menziline
    düşüyordu ve "bilmiyorum, insana sor" sinyali sessizce "reddet"e dönüyordu.

    Gerçek vaka: "Dijital İnsan Kaynakları Yönetim Sistemi" ilanı yalnızca
    "1 Adet 13 Modülden oluşan … ayrıntı şartnamede" diyor. Modüllerin ne olduğu
    bilinmiyor; model kararsız değil, ELİNDE VERİ YOK. Bu ihale insana gitmeliydi.

    Ölçek: aktif ihalelerin %99'u "Ön İlan"dan okunuyor ve Ön İlan kalem listesi
    vermiyor — B1 kuyruğun tamamında tetiklenmeli.

    `zayif_ortusme` netleştirilmeye devam ediyor: orada model gerçekten kararsız
    ve ölçüm bu çevirmenin 13/13 doğru olduğunu gösterdi.

    `belirsiz_tipi` boşsa (eski koşular, model alanı doldurmadıysa) ESKİ DAVRANIŞ
    korunur — netleştirilir. Yalnızca AÇIK `kanit_yetersiz` sinyali kuralı durdurur.
    """
    if esik <= 0 or sonuc.karar != "belirsiz" or sonuc.ilgi_skoru >= esik:
        return sonuc, []

    if sonuc.belirsiz_tipi == "kanit_yetersiz":
        uyari = (
            f"Model 'belirsiz' + ilgi_skoru={sonuc.ilgi_skoru} ile {esik} altında kaldı ama "
            f"belirsiz_tipi='kanit_yetersiz' — ilan işin ne olduğunu söylemiyor. "
            f"Netleştirme UYGULANMADI, ihale insan incelemesine gidiyor."
        )
        return sonuc.model_copy(update={"gerekce": f"{sonuc.gerekce} [{uyari}]"}), [uyari]
    uyari = (
        f"Model ilgi_skoru={sonuc.ilgi_skoru} ile 'belirsiz' dedi; {esik} altında "
        f"gerçek bir örtüşme gözlenmedi — karar kod seviyesinde 'uygun_degil'e "
        f"netleştirildi (belirsiz/uygun_degil sınır kuralı)."
    )
    return (
        sonuc.model_copy(
            update={"karar": "uygun_degil", "gerekce": f"{sonuc.gerekce} [{uyari}]"}
        ),
        [uyari],
    )


def _normalize(s: str) -> str:
    """Karşılaştırma için başlığı sadeleştirir.

    Sondaki köşeli parantezli ekler ATILIR. Sebep: `_paket_satiri` paketi
    "- <başlık> [kritik] (benzerlik: 0.62)" biçiminde yazıyor ve model başlığı
    ÖNCELİK EKİYLE BİRLİKTE kopyalıyor. İlk sürümde bu "uydurma paket" sayılıp
    iki doğru kararı boş yere 'belirsiz'e düşürdü (ayar/v5 ve triyaj/v4).
    """
    s = " ".join((s or "").split())
    while s.endswith("]") and "[" in s:
        s = s[: s.rindex("[")].rstrip()
    return s.casefold()


def paket_uydurmasini_yakala(
    sonuc: KapsamSonucu, paketler: list[PaketVurusu]
) -> tuple[KapsamSonucu, list[str]]:
    """`eslesen_paket` gerçekten sunulan listede mi? — DETERMİNİSTİK KONTROL.

    GERÇEK VAKA (29.07.2026 triyaj v1): "Mescid ve Kur'an Kursu Yapım İşleri"
    ihalesinde model `eslesen_paket="Bina İşleri"` yazıp `uygun` dedi. İSBAK'ın
    20 paketinin hiçbiri "Bina İşleri" değil — model paketi UYDURDU. Şema alanı
    "listeden TAM başlık" diye tanımlıydı ama hiçbir doğrulama yoktu.

    DÜŞÜRME YÖNÜ "belirsiz". Uydurma paket, kararın gerçek bir örtüşmeye değil
    modelin kendi kurgusuna dayandığını gösterir; ama `uygun_degil`e çevirmek
    ihaleyi insandan koparır (kaçırma en pahalı hata). Güvenli yön ortadır —
    `skor_karar_tutarliligini_zorla` ile aynı ilke.
    """
    if not sonuc.eslesen_paket or not paketler:
        return sonuc, []
    gecerli = {_normalize(p.baslik) for p in paketler}
    if _normalize(sonuc.eslesen_paket) in gecerli:
        return sonuc, []

    uyari = (
        f"Model 'eslesen_paket' olarak '{sonuc.eslesen_paket}' yazdı ama bu başlık "
        f"sunulan paket listesinde YOK (uydurma paket). Sunulanlar: "
        f"{', '.join(p.baslik for p in paketler)}."
    )
    if sonuc.karar == "uygun":
        uyari += " Karar kod seviyesinde 'belirsiz'e düşürüldü."
        return (
            sonuc.model_copy(update={"karar": "belirsiz", "gerekce": f"{sonuc.gerekce} [{uyari}]"}),
            [uyari],
        )
    return sonuc.model_copy(update={"gerekce": f"{sonuc.gerekce} [{uyari}]"}), [uyari]


def kapsam_karari_uret(
    ihale: Ihale,
    paketler: list[PaketVurusu],
    istemci: OllamaIstemcisi,
    uygun_ornekler: list[OrnekVurusu] | None = None,
    red_ornekler: list[OrnekVurusu] | None = None,
    belirsiz_ornekler: list[OrnekVurusu] | None = None,
    *,
    min_skor: float = 0.0,
    paket_dogrula: bool = False,
    destekleyici_kurali: bool = False,
    belirsiz_netlestirme_esigi: float = 0.0,
    okas_vetosu: bool = False,
    nitel_yakinlik: bool = False,
    negatif_dogrula: bool = False,
    faaliyet_ortusmesi: bool = False,
    alan_terimleri: frozenset[str] | None = None,
) -> tuple[KapsamSonucu, list[str]]:
    """Kapsam kararını üretir ve deterministik tutarlılık kapılarından geçirir.

    Dönüş: (karar, uyarılar). Uyarı listesi boş değilse kod seviyesinde bir
    düzeltme yapılmış demektir — çağıran taraf bunu izlenebilirlik için kaydeder.
    """
    uyarilar: list[str] = []

    # OKAS vetosu burada hesaplanır, mesaj kurucusunda değil: veto tetiklendiğinde
    # koşu kaydına not düşmek gerekiyor. `kullanici_mesaji` sadece sonucu uygular.
    veto = okas_vetosu and tamami_kapsam_disi(o.kod for o in ihale.okas_kodlari)
    if veto:
        uyarilar.append(
            "okas_vetosu: tüm OKAS kodları kapsam dışı bölümde, "
            "kanıt yetersizliği uyarısı verilmedi"
        )

    # Negatif terim vuruşları koşu kaydına da yazılır — prompt'a ne konduğunu
    # sonradan görebilmek için (retrieval izleriyle aynı gerekçe).
    if negatif_dogrula:
        gecenler = [p for p in paketler if p.benzerlik >= min_skor] if min_skor > 0 else paketler
        o = negatif_ozet(negatifleri_dogrula(kapsam_metni(ihale), gecenler))
        if o:
            uyarilar.append(o)

    ham = istemci.yapisal_uret(
        sistem=SISTEM_PROMPTU,
        kullanici=kullanici_mesaji(
            ihale, paketler, uygun_ornekler, red_ornekler, belirsiz_ornekler,
            min_skor=min_skor, destekleyici_kurali=destekleyici_kurali,
            nitel_yakinlik=nitel_yakinlik, kanit_uyarisini_bastir=veto,
            negatif_dogrula=negatif_dogrula, alan_terimleri=alan_terimleri,
        ),
        sema=KapsamSonucuGenis if faaliyet_ortusmesi else KapsamSonucu,
    )

    # HAM SKOR — kod düzeyindeki düzeltmelerden ÖNCE.
    # `skor_karar_tutarliligini_zorla` ve `belirsizi_netlestir` `ilgi_skoru`nun
    # üstüne yazabiliyor. `retrieval_en_ust_skor`u ayrıca saklamamızla aynı gerekçe
    # (karar_deposu.py): eşiği sonradan LLM'i TEKRAR KOŞMADAN değerlendirebilmek.
    uyarilar.append(f"ham_ilgi_skoru={ham.ilgi_skoru}")
    if faaliyet_ortusmesi:
        uyarilar.append(f"faaliyet_ortusmesi={ham.faaliyet_ortusmesi}")

    if paket_dogrula:
        ham, u = paket_uydurmasini_yakala(ham, paketler)
        uyarilar += u
    ham, u = skor_karar_tutarliligini_zorla(ham)
    uyarilar += u
    # SIRA ÖNEMLİ: önce 'uygun' -> 'belirsiz' düşürmesi, sonra 'belirsiz' -> 'uygun_degil'.
    # Böylece düşük skorlu bir 'uygun', tek adımda 'uygun_degil'e ATLAYAMAZ; iki kural
    # arka arkaya uygulanırsa aynı sonuca varır ve bu bilinçlidir — skor gerçekten
    # eşiğin altındaysa ihale zaten kapsam dışıdır.
    ham, u = belirsizi_netlestir(ham, belirsiz_netlestirme_esigi)
    return ham, uyarilar + u
