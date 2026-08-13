"""
Deterministik kural doğrulayıcı — Aşama 2 model çıktısını kodla denetler.

NE YAPAR: Modelin Aşama 2 çıktısını 13 sabit kuralla denetler. Model uydurduğunda,
kendi kuralını çiğnediğinde veya kanıtsız iddia ettiğinde kararı kod seviyesinde
düzeltir. Olasılıksal bir modelin üstünde duran deterministik güvenlik katmanıdır.

TASARIM KARARLARI:

- TEK doğrulayıcı vardır. Daha önce iki ayrı doğrulayıcı sınıfı bulunuyordu ve biri
  boru hattının gönderdiği parametreyi kabul bile etmiyordu (bağlansaydı hata
  verecekti). İkisi tek bir uygulamada birleştirildi.

- `kritik_baglam_atlandi` bayrağı uçtan uca geçirilir. Daha önce bu bayrak
  hesaplanıp yolda kayboluyor, dolayısıyla KURAL 13 üretimde hiç tetiklenmiyordu
  (bkz. app/pipeline/servis.py).

- Zorunlu karar önceliği AÇIKÇA tanımlıdır: kurallar bir "şiddet" sırasına göre
  uygulanır ve en şiddetli olan kazanır. Daha önce kurallar sırayla birbirinin
  sonucunu eziyordu; yani dosyadaki fiziksel satır sırası anlamsal önceliği
  belirliyordu — kırılgan bir davranıştı.

KAÇIRMA İLKESİ: Bu doğrulayıcı bir kararı ASLA "ilgisiz"e sertleştirerek Aşama 1'in
"uygun" kararını gölgede bırakamaz — en fazla "inceleme_gerekli"ye düşürür. Tek
istisna, modelin faaliyet alanı uyumsuzluğunu AÇIKÇA işaretlemesidir (KURAL 6).
"""

from __future__ import annotations

import re

from app.decision.schemas import DogrulamaSonucu, YeterlilikKarari, YeterlilikSonucu

# Zorunlu karar şiddet sırası — büyük olan kazanır.
_SIDDET: dict[str, int] = {"inceleme_gerekli": 1, "ilgisiz": 2}

# Aşama 2'de "belirsiz/karşılanmadı" olduğunda ihaleyi elemek yerine insana götürmek
# gereken kritik kriterler.
_KRITIK_KRITERLER = {"mali_yeterlilik", "sertifika", "is_deneyimi"}


def _kucuk(metin: str) -> str:
    return metin.replace("İ", "i").replace("I", "ı").lower()


def dogrula(
    sonuc: YeterlilikSonucu,
    *,
    kanit_sayisi: int,
    kritik_baglam_atlandi: bool = False,
) -> DogrulamaSonucu:
    gecti = True
    celiskiler: list[str] = []
    eksik_kanit: list[str] = []
    gecersiz_referanslar: list[str] = []
    kurallar: list[str] = []
    uyarilar: list[str] = []
    zorunlu: YeterlilikKarari | None = None

    def zorla(karar: YeterlilikKarari, kural: str) -> None:
        nonlocal zorunlu
        if zorunlu is None or _SIDDET[karar] > _SIDDET[zorunlu]:
            zorunlu = karar
        if kural not in kurallar:
            kurallar.append(kural)

    # --- KURAL 2: güven aralığı (şema zaten sınırlıyor, yine de savunma) ---
    if not 0.0 <= sonuc.guven <= 1.0:
        gecti = False
        kurallar.append("K2_GECERSIZ_GUVEN")

    var_belirsiz = False
    var_karsilanmadi = False
    gorulen: dict[str, str] = {}

    for k in sonuc.kriterler:
        if k.durum == "belirsiz":
            var_belirsiz = True
        if k.durum == "karsilanmadi":
            var_karsilanmadi = True

        # --- KURAL 8: aynı kriter iki farklı durumda ---
        if k.kriter_id in gorulen and gorulen[k.kriter_id] != k.durum:
            celiskiler.append(f"Çelişki: '{k.kriter_id}' kriteri farklı durumlarla raporlandı.")
            gecti = False
            if "K8_CELISKI" not in kurallar:
                kurallar.append("K8_CELISKI")
        gorulen[k.kriter_id] = k.durum

        gerekce_kucuk = _kucuk(k.gerekce)

        # --- KURAL 9: "bilgi bulunamadı" olumlu kanıt sayılamaz ---
        if k.durum == "karsilandi" and "bilgi bulunamadı" in gerekce_kucuk:
            celiskiler.append(
                f"'{k.kriter_id}' karşılandı diyor ama gerekçesi 'bilgi bulunamadı' içeriyor."
            )
            gecti = False
            if "K9_BILGI_YOK_KANIT_DEGIL" not in kurallar:
                kurallar.append("K9_BILGI_YOK_KANIT_DEGIL")

        # --- KURAL 12: profil sözlüğünde geçmek yeterlilik kanıtı değildir ---
        if k.durum == "karsilandi" and any(
            s in gerekce_kucuk for s in ("profil sözlüğü", "eşleştirme sözlüğü", "profilde yer alan", "profilinde var")
        ):
            if not any(s in gerekce_kucuk for s in ("doğrulanmış", "kanıtlanmış", "belge", "ispat")):
                celiskiler.append(
                    f"'{k.kriter_id}' karşılandı diyor ama kanıt olarak yalnızca profil sözlüğü gösterilmiş."
                )
                gecti = False
                if "K12_PROFIL_SOZLUGU_KANIT_DEGIL" not in kurallar:
                    kurallar.append("K12_PROFIL_SOZLUGU_KANIT_DEGIL")

        # --- KURAL 7: var olmayan kanıta atıf ---
        for ref in k.kanit_idleri:
            eslesme = re.search(r"\d+", str(ref))
            gecersiz = False
            if eslesme:
                # Kanıtlar 1'den numaralanıyor (K1..Kn) — kanit_sayisi dışı geçersiz.
                gecersiz = not (1 <= int(eslesme.group()) <= kanit_sayisi)
            elif kanit_sayisi == 0:
                gecersiz = True
            if gecersiz:
                gecersiz_referanslar.append(str(ref))
                eksik_kanit.append(f"Geçersiz kanıt referansı: {ref}")
                gecti = False
                if "K7_GECERSIZ_KANIT_REF" not in kurallar:
                    kurallar.append("K7_GECERSIZ_KANIT_REF")

    # --- KURAL 3: doğrudan_uygun + belirsiz kriter ---
    if sonuc.karar == "dogrudan_uygun" and var_belirsiz:
        zorla("inceleme_gerekli", "K3_UYGUN_AMA_BELIRSIZ_KRITER")

    # --- KURAL 4: doğrudan_uygun + modelin kendi bildirdiği eksik kanıt ---
    if sonuc.karar == "dogrudan_uygun" and sonuc.eksik_kanitlar:
        eksik_kanit.extend(sonuc.eksik_kanitlar)
        zorla("inceleme_gerekli", "K4_UYGUN_AMA_EKSIK_KANIT")

    # --- KURAL 1: hiç kanıt yokken doğrudan_uygun denemez ---
    if sonuc.karar == "dogrudan_uygun" and kanit_sayisi == 0:
        celiskiler.append("Geçmiş ihale kanıtı olmadan 'dogrudan_uygun' kararı verilemez.")
        gecti = False
        zorla("inceleme_gerekli", "K1_KANITSIZ_UYGUN")

    # --- KURAL 13: kritik bağlam bütçe taşması yüzünden atıldıysa ---
    if sonuc.karar == "dogrudan_uygun" and kritik_baglam_atlandi:
        eksik_kanit.append("Kritik ihale şartları bağlam bütçesini aştığı için analiz dışı kaldı.")
        gecti = False
        zorla("inceleme_gerekli", "K13_KRITIK_BAGLAM_ATLANDI")

    # --- KURAL 11: 'ilgisiz' denmiş ama kritik kriter doğrulanamamış ---
    # Alan uyumsuzluğundan değil BİLGİ EKSİKLİĞİNDEN eleniyorsa bu insan işidir.
    if sonuc.karar == "ilgisiz":
        for k in sonuc.kriterler:
            if k.kriter_id in _KRITIK_KRITERLER and k.durum in ("karsilanmadi", "belirsiz"):
                zorla("inceleme_gerekli", "K11_KRITIK_KRITER_INSANA")
                break

    # --- KURAL 5 & 6: gerçek uyumsuzluk — sadece bunlar 'ilgisiz'e sertleştirebilir ---
    if sonuc.karar == "dogrudan_uygun" and var_karsilanmadi:
        zorla("ilgisiz", "K5_KARSILANMAYAN_ZORUNLU_KRITER")
    for k in sonuc.kriterler:
        if k.kriter_id == "faaliyet_alani" and k.durum == "karsilanmadi":
            zorla("ilgisiz", "K6_FAALIYET_ALANI_UYUMSUZ")
            break

    if zorunlu == sonuc.karar:
        zorunlu = None  # zaten aynı karar — "zorlandı" demenin anlamı yok

    return DogrulamaSonucu(
        gecti=gecti,
        zorunlu_karar=zorunlu,
        celiskiler=celiskiler,
        eksik_zorunlu_kanit=eksik_kanit,
        gecersiz_kanit_referanslari=gecersiz_referanslar,
        uygulanan_kurallar=kurallar,
        uyarilar=uyarilar,
    )
