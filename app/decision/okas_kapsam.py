"""
OKAS tabanlı kapsam vetosu — B1 (kanıt yetersizliği) uyarısını susturur.

=============================================================================
NEDEN VAR
=============================================================================
Gerçek vaka (04.08.2026, canlı Postgres, 2026/1280806):

    "Ula İlçesi 2026-2027 Eğitim Öğretim Yılı İlköğretim Öğrencilerinin
     Taşınması Amacıyla 181 Günlük Hizmet Alım İşi"
    -> karar: BELİRSİZ (kanit_yetersiz),  ilgi_skoru 0.5049

Doğrusu `uygun_degil`. Yanlış karar üç mekanizmanın üst üste binmesinden çıktı:

  1. PLN-01 "Ulaşım Planlama ve Talep Yönetimi" 0.5049 aldı — `RETRIEVAL_MIN_SKOR`
     eşiğinin (0.50) 0.0049 üstünde. Gömülü metin "ulaşım/taşıma" kavramını
     yakalıyor, NEYİN taşınması olduğunu değil. (OPS-02'nin "bakım"ı yakalayıp
     neyin bakımı olduğunu yakalamamasıyla aynı hata sınıfı.)
  2. Bu eşik geçişi B1 uyarısının ÖN KOŞULU — uyarı yalnızca eşiği geçen bir
     paket varken veriliyor. 0.0049'luk marj uyarıyı açtı.
  3. `kalem_listesi_yok_mu` başlığın kelimelerini eleyip geriye anlamlı bilgi
     kalıyor mu diye bakıyor. Ama bu ihalede BAŞLIK ZATEN CEVABIN KENDİSİ:
     "öğrencilerin taşınması". Kod, işi çözen tek kanıtı silip "kanıt yok" dedi.

Üstüne `belirsizi_netlestir` de `kanit_yetersiz` için bilerek bloke (satır ~456),
yani kurtarma kapısı da kapalıydı.

B1 prompt metninde zaten bir istisna var: "(a) İşin türü başlıktan zaten kesin
anlaşılıyor ve İSBAK'ın hiçbir alanıyla ilgisi yok (akaryakıt, gıda, temizlik,
giyim, ilaç, kırtasiye gibi)". Model bu istisnayı uygulamadı. Bu modül o
istisnayı MODELİN İNSAFINA BIRAKMAK YERİNE deterministik hale getiriyor.

=============================================================================
NE YAPAR / NE YAPMAZ
=============================================================================
YAPAR : İhalenin OKAS kodlarının HEPSİ açıkça kapsam dışı bir bölümdeyse,
        B1 "kalem listesi yok" uyarısı prompt'a HİÇ EKLENMEZ.
YAPMAZ: Kararı değiştirmez, LLM'i atlamaz, `uygun_degil` dayatmaz. Model yine
        çağrılır ve kararı kendi verir.

Bu bilinçli olarak DAR tutulmuştur. Sert dışlama (OKAS kapsam dışıysa LLM'i hiç
çağırma) bir KAÇIRMA KAPISIDIR: OKAS kodu eksik ya da yanlış girilmiş bir ihale
sessizce elenir. Buradaki müdahale yalnızca yanlış tetiklenen bir uyarıyı
susturuyor — en kötü ihtimalle etkisi sıfır olur.

=============================================================================
LİSTE NASIL TÜRETİLDİ (04.08.2026)
=============================================================================
`VerilerEtiketliveri/ekap.db` içindeki tüm OKAS kodları ilk iki hanesine (bölüm)
göre gruplandı. Aşağıdaki bölümler İSBAK'ın 20 iş paketinin hiçbiriyle
ilişkilendirilemeyecek mal/hizmet sınıflarıdır.

SIZINTI TESTİ — liste, etiketli 41 POZİTİF ihalenin (30 kaçırma seti + 11 ayar/
final 'uygun') OKAS kodlarına karşı denetlendi. O 41 ihalenin kullandığı bölümler:

    30, 45, 31, 72, 32, 48, 34, 35, 44, 50, 38, 63, 71, 19, 42

Aşağıdaki listeyle KESİŞİM YOK. Test `tests/test_okas_kapsam.py` içinde koda
bağlanmıştır — liste büyütülürse test kırılır.

İki bölüm özellikle DIŞLANMADI, çünkü sızıntı testi yakaladı:
  · 19 (deri/tekstil) — 2026/957410 sinyalizasyon ihalesinde `19522110`
    "Sertleştirilmiş plastik borular" ikincil kod olarak geçiyor.
  · 63 (taşımacılık destek hizmetleri) — iki pozitifte geçiyor.
Sezgiyle bakınca ikisi de "alakasız" görünüyordu; veri aksini söyledi.

75 (askeri savunma / kamu güvenliği hizmetleri) de bilerek DIŞLANMADI:
`75241000 Kamu güvenliği hizmetleri` bir KGYS ihalesinde geçebilir. Kazancı
küçük, riski gereksiz.
"""

from __future__ import annotations

from collections.abc import Iterable

# İlk iki hane = OKAS bölümü. Hepsi "İSBAK bu işi hiçbir koşulda yapmaz" sınıfı.
KAPSAM_DISI_BOLUMLER: frozenset[str] = frozenset(
    {
        "03",  # tarım, hayvancılık, balıkçılık ürünleri
        "09",  # petrol ürünleri, akaryakıt, yakıt
        "14",  # madencilik, maden cevherleri
        "15",  # gıda ve içecek
        "16",  # tarım makineleri
        "18",  # giyim, ayakkabı, tekstil ürünleri
        "22",  # basılı malzeme, matbaa, kırtasiye
        "24",  # kimyasallar
        "33",  # tıbbi cihaz, ilaç, laboratuvar sarf
        "37",  # spor, müzik aleti, oyun
        "41",  # su temini
        "55",  # otel, yemek, catering
        "60",  # yolcu/yük TAŞIMA HİZMETLERİ (araç kiralama, öğrenci taşıma)
        "66",  # finans, sigorta
        "70",  # gayrimenkul
        "77",  # tarım, bahçe, ağaç bakım hizmetleri
        "80",  # eğitim hizmetleri
        "85",  # sağlık ve sosyal hizmetler
        "90",  # temizlik, atık, çevre hizmetleri
        "92",  # kültür, medya, eğlence
        "98",  # kişisel hizmetler (berberlik, taşınma vb.)
    }
)


def bolum(kod: str) -> str:
    """OKAS kodunun bölümü — ilk iki hane."""
    return str(kod or "").strip()[:2]


def kapsam_disi_mi(kod: str) -> bool:
    return bolum(kod) in KAPSAM_DISI_BOLUMLER


def tamami_kapsam_disi(kodlar: Iterable[str]) -> bool:
    """İhalenin TÜM OKAS kodları kapsam dışı bölümlerde mi?

    TAMAMI aranıyor, herhangi biri değil. Bir ihalede tek bir alakalı kod bile
    varsa veto uygulanmaz — kaçırmama ilkesi. Örnek: 2026/957410 sinyalizasyon
    ihalesinde altı koddan biri plastik boru; "biri kapsam dışı" kuralı o ihaleyi
    yanlış tarafa iterdi.

    Kod listesi BOŞSA False döner. Kanıt yokluğu, kapsam dışılık kanıtı değildir.
    """
    liste = [k for k in (str(x).strip() for x in kodlar) if k]
    if not liste:
        return False
    return all(kapsam_disi_mi(k) for k in liste)
