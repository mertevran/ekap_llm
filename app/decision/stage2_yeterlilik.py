"""
AŞAMA 2 — YETERLİLİK KARARI:  "Bu ihalenin şartlarını karşılıyor muyuz?"

YAKLAŞIM: Model, her yeterlilik kriteri için ayrı ayrı "karşılanıyor mu" der ve
iddiasını ilan metninden KANIT göstererek desteklemek zorundadır.

TASARIM KARARLARI:

- ALAN SIRASI: Şema `ozet -> kriterler -> riskler -> eksik_kanitlar -> guven -> karar`
  sırasındadır, yani karar EN SONDA üretilir. Ollama alanları şemadaki sırayla ürettiği
  için, kararı başa almak modelin gerekçe üretmeden karara kilitlenmesine yol açar —
  bu, Aşama 1'de bizzat ölçülmüş bir hata modudur (bkz. app/decision/schemas.py).

- HATAYA DAYANIKLILIK: Bir kriter "karsilandi" deyip kanıt göstermezse, ÖNCEKİ sürümde
  TÜM analiz bir istisnayla çöküyordu; 6 kriterin 5'i doğru olsa bile ihale hiç
  değerlendirilemiyordu. Artık o kriter "belirsiz"e DÜŞÜRÜLÜR, bir uyarı kaydedilir ve
  analiz devam eder. Doğrulayıcı katmanı durumu zaten yakalar.

- KAÇIRMA İLKESİ: Aşama 2 prompt'u, Aşama 1'in "bu iş bizim alanımız" kararını
  ters çeviremeyeceğini AÇIKÇA söyler. Aşama 2'nin işi yeterliliktir; alan
  uyuşmazlığı Aşama 1'in konusudur.

ÖNEMLİ SINIRLAMA: şirket profillerindeki kapasite alanları (belgeler, personel,
tamamlanan projeler, iş deneyim belgeleri) HÂLÂ BOŞ — `veri_durumu:
kurum_ici_dogrulama_gerekli`. Yani Aşama 2 şu an çoğu kriteri dürüstçe "belirsiz"
raporlamalı. Bu bir hata değil, veri gerçeği; prompt bunu açıkça söylüyor ki model
boşluğu uydurmayla doldurmasın.
"""

from __future__ import annotations

from app.decision.llm_client import OllamaIstemcisi
from app.decision.schemas import KapsamSonucu, Kriter, YeterlilikSonucu
from app.domain.models import Ihale, kapsam_metni
from app.profiles.loader import Profil

ILAN_METNI_MAX_KARAKTER = 6000

SISTEM_PROMPTU = """Sen İSBAK A.Ş. için çalışan bir ihale YETERLİLİK değerlendirme asistanısın.

Bu ihalenin İSBAK'ın FAALİYET ALANINA girdiği ÖNCEKİ bir aşamada zaten belirlendi. Senin görevin \
o kararı tekrar tartışmak DEĞİL. Senin görevin şu: ihalenin şartlarını (istenen belgeler, \
sertifikalar, iş deneyimi, mali yeterlilik, teknik kapasite) tek tek ele alıp, İSBAK'ın \
DOĞRULANMIŞ kayıtlarına göre karşılanıp karşılanmadığını söylemek.

KURALLAR:
1. Bilgi yoksa kriteri "belirsiz" işaretle. BİLİNMEYEN BİLGİ ASLA "karsilandi" SAYILAMAZ.
2. Sana verilen ŞİRKET BAĞLAMI'nda birçok alan BOŞ olabilir — İSBAK'ın belge/proje kayıtları henüz \
kurum içi doğrulamadan geçmedi. Boş bir alan "yeterlilik yok" demek DEĞİLDİR; "bilinmiyor" demektir \
ve karşılığı "belirsiz"dir. Bu boşluğu KENDİ genel bilginle DOLDURMA, İSBAK hakkında bilmediğin \
şeyi uydurma.
3. Geçmiş benzer ihale tecrübesi TEK BAŞINA yeterlilik kanıtı sayılmaz.
4. Bir ekipmanın veya yetkinliğin profil sözlüğünde YER ALMASI, İSBAK'ın onu doğrulanmış biçimde \
sağladığını KANITLAMAZ.
5. Karar SADECE şu üçünden biri olabilir:
   - "dogrudan_uygun": tüm kritik kriterler karşılandı VE kanıtla desteklendi. Hiç "belirsiz" yok.
   - "inceleme_gerekli": bilgi eksikliği, doğrulanamayan belge ya da riskli durum var. İNSAN BAKMALI.
   - "ilgisiz": ihalenin şartları İSBAK'ın yapabileceği işle AÇIKÇA bağdaşmıyor.
6. Emin değilsen "inceleme_gerekli" de. Bir ihaleyi yanlışlıkla elemek, gereksiz yere insana \
göndermekten çok daha pahalıdır.
7. Her kriter için kanıtını "kanit_idleri" listesine yaz — KANITLAR bölümündeki referansları (K1, K2 …) \
kullan. Orada olmayan bir referans UYDURMA.
8. ÇIKTI SIRASI: önce "ozet", sonra "kriterler", "riskler", "eksik_kanitlar", sonra "guven", \
"karar" EN SON — yukarıdakilerle tutarlı olacak şekilde.

Sadece verilen JSON şemasına uyan çıktı üret. Türkçe yaz."""


def _sirket_baglami(profiller: list[Profil]) -> str:
    satirlar = ["## İSBAK ŞİRKET BAĞLAMI (eşleşen profiller)"]
    for p in profiller:
        satirlar.append(f"\n### {p.kod} — {p.ad}  [{p.aile}]")
        if p.yetkinlikler:
            satirlar.append("Birincil yetkinlikler: " + ", ".join(p.yetkinlikler))
        satirlar.append(
            f"Belgeler: {', '.join(str(b) for b in p.belgeler) if p.belgeler else 'KAYIT YOK (doğrulanmadı)'}"
        )
        satirlar.append(
            "Tamamlanan projeler: "
            + (
                ", ".join(str(x) for x in p.tamamlanan_projeler)
                if p.tamamlanan_projeler
                else "KAYIT YOK (doğrulanmadı)"
            )
        )
        satirlar.append(f"Veri durumu: {p.veri_durumu}")
    satirlar.append(
        "\nUYARI: 'KAYIT YOK' yazan alanlar İSBAK'ın o yeterliliğe sahip OLMADIĞI anlamına gelmez; "
        "kurum içi doğrulama henüz yapılmadığı için BİLİNMİYOR demektir. Karşılığı 'belirsiz'dir."
    )
    return "\n".join(satirlar)


def kullanici_mesaji(
    ihale: Ihale,
    kapsam: KapsamSonucu,
    profiller: list[Profil],
    kanitlar: list[dict],
) -> str:
    metin = kapsam_metni(ihale)
    if metin and len(metin) > ILAN_METNI_MAX_KARAKTER:
        metin = metin[:ILAN_METNI_MAX_KARAKTER] + " [...kırpıldı...]"

    ozellikler = ihale.ozellik_metni()
    okas = ihale.okas_metni()

    kanit_kismi = "KANITLAR (geçmiş benzer ihaleler):\n" + (
        "\n".join(f"[K{i}] İKN {k.get('ikn','?')} — {k.get('baslik','')}" for i, k in enumerate(kanitlar, 1))
        or "(kanıt bulunamadı)"
    )

    return f"""AŞAMA 1 SONUCU (kapsam — tekrar tartışılmayacak):
karar: {kapsam.karar} (ilgi skoru {kapsam.ilgi_skoru})
eşleşen paket: {kapsam.eslesen_paket or "-"}
gerekçe: {kapsam.gerekce}

İHALE:
Adı: {ihale.adi}
İdare: {ihale.idare_adi}
İl: {ihale.il or "belirtilmemiş"}
Tür: {ihale.ihale_turu or "belirtilmemiş"} / Usul: {ihale.ihale_usulu or "belirtilmemiş"}
OKAS: {", ".join(okas) or "belirtilmemiş"}

İLAN KAPSAMI:
{metin or "(metin yok)"}

İHALE ÖZELLİKLERİ:
{ozellikler or "(özellik kaydı yok)"}

{_sirket_baglami(profiller)}

{kanit_kismi}

Bu ihalenin yeterlilik şartlarını değerlendir. JSON şemasına uygun cevap ver."""


def _kanitsiz_kriterleri_dusur(sonuc: YeterlilikSonucu) -> tuple[YeterlilikSonucu, list[str]]:
    """'karsilandi' deyip kanıt göstermeyen kriteri 'belirsiz'e düşürür.

    Önceki sürümde bu durum tüm analizi bir istisnayla çökertiyordu: tek bir kötü
    kriter, doğru üretilmiş diğer beş kriteri de çöpe atıyordu.
    """
    uyarilar: list[str] = []
    yeni: list[Kriter] = []
    for k in sonuc.kriterler:
        if k.durum == "karsilandi" and not k.kanit_idleri:
            uyarilar.append(
                f"'{k.kriter_id}' kriteri kanıtsız 'karsilandi' olarak geldi — 'belirsiz'e düşürüldü."
            )
            yeni.append(k.model_copy(update={"durum": "belirsiz"}))
        else:
            yeni.append(k)
    if not uyarilar:
        return sonuc, []
    return sonuc.model_copy(update={"kriterler": yeni}), uyarilar


def yeterlilik_karari_uret(
    ihale: Ihale,
    kapsam: KapsamSonucu,
    profiller: list[Profil],
    kanitlar: list[dict],
    istemci: OllamaIstemcisi,
) -> tuple[YeterlilikSonucu, list[str]]:
    ham = istemci.yapisal_uret(
        sistem=SISTEM_PROMPTU,
        kullanici=kullanici_mesaji(ihale, kapsam, profiller, kanitlar),
        sema=YeterlilikSonucu,
    )
    return _kanitsiz_kriterleri_dusur(ham)
