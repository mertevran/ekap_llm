# EKAP İhale Tarama ve Karar Destek Sistemi

Kamu ihale platformunda (EKAP) yayımlanan binlerce ihaleyi otomatik tarayıp, bir
şirketin faaliyet alanına **gerçekten uyanları** gerekçesiyle birlikte öne çıkaran
karar destek sistemi.

Yerel olarak çalışan bir dil modeli (LLM) kullanır — **hiçbir veri kurum dışına
çıkmaz.**

> **Bu sistem karar VERMEZ, liste KISALTIR.**
> Amaç uzmanın yerini almak değil, incelenmesi gereken listeyi elle taranabilir hale
> getirmektir. Nihai kararı her zaman insan verir.

```
      ~4.450 aktif ihale                      ~310 aday ihale
   ───────────────────────────────  ►  ──────────────────────────
   elle taranması imkânsız              bir uzman bir günde inceler
```

---

## İçindekiler

1. [Hangi problemi çözüyor](#1-hangi-problemi-çözüyor)
2. [Nasıl çalışıyor](#2-nasıl-çalışıyor)
3. [Yapay zekâ nasıl kullanılıyor](#3-yapay-zekâ-nasıl-kullanılıyor)
4. [Kurulum](#4-kurulum)
5. [Kullanım](#5-kullanım)
6. [Proje yapısı](#6-proje-yapısı)
7. [Yapılandırma](#7-yapılandırma)
8. [Ölçülmüş sonuçlar](#8-ölçülmüş-sonuçlar)
9. [Test ve ölçüm](#9-test-ve-ölçüm)
10. [Bilinen sınırlar](#10-bilinen-sınırlar)
11. [Sık karşılaşılan sorunlar](#11-sık-karşılaşılan-sorunlar)
12. [Sözlük](#12-sözlük)

---

## 1. Hangi problemi çözüyor

Bir şirketin kamu ihalelerine teklif verebilmesi için önce **hangi ihalelerin kendi
işine uyduğunu** bulması gerekir. Bu, göründüğünden zordur:

| | |
|---|---|
| Veritabanındaki toplam ihale | ~49.800 |
| Durumu "Katılıma Açık" olan | ~7.600 |
| **Teklife gerçekten açık** (tarihi geçmemiş) | **~4.450** |
| Günlük yeni ihale | ~465 |
| Bir ilan metninin uzunluğu | ortalama 6.600 karakter |

Üç ayrı zorluk aynı anda karşımıza çıkıyor:

**1. Hacim.** Günde ~465 yeni ilan geliyor. Hiçbir ekip bunu elle tarayamaz.

**2. Gürültü.** İlan metninin yarısından fazlası her ihalede birebir tekrar eden
standart maddelerdir (teminat, katılım belgeleri, teklif usulü). İşin gerçekte ne
olduğu, bu yığının içinde birkaç satırdır.

**3. Belirsizlik.** Basit anahtar kelime aramasının çözemediği kısım budur:

> *"Sondaj ve Workover Kuleleri **Kamera** Sistemi Alımı"*
> — "kamera" kelimesi tutuyor ama iş tutmuyor. Bu ihale, patlama riski olan bir petrol
> sahasında sertifikalı endüstriyel ekipman gerektiriyor; şehir içi güvenlik kamerası
> işiyle alakası yok.

Tersi de doğrudur: başlığında hiçbir teknoloji kelimesi geçmeyen bir ihale, kalemleri
incelendiğinde tam da aranan iş çıkabilir. Bu yüzden metni **anlayan** bir katman
gerekiyor.

---

## 2. Nasıl çalışıyor

Bir ihale, numarası (İKN) verildiğinde şu adımlardan geçer:

```
İKN (İhale Kayıt Numarası)
 │
 ├─ 0  ÖN FİLTRE            Yapay zekâ YOK — sadece kod.
 │                          Kesin olan şeyler modele sorulmaz:
 │                          "ihaleyi şirketin kendisi açmış", "tarihi geçmiş".
 │
 ├─ 1  VERİ OKUMA           Veritabanından ilan metni okunur (SADECE OKUMA).
 │                          Metin okuma anında temizlenir (~%51 sadeleşme).
 │
 ├─ 2  İLGİLİ BİLGİ         20 iş paketi profili + geçmişte karara bağlanmış
 │      (retrieval)         benzer ihaleler (hem kabul edilmiş hem reddedilmiş).
 │
 ├─ 3  AŞAMA 1 — KAPSAM     "Bu iş bizim faaliyet alanımıza giriyor mu?"
 │                          Model ÖNCE gerekçesini yazar, kararı EN SON verir.
 │                          Ardından dört deterministik kontrolden geçer.
 │
 ├─ 4  AŞAMA 2 — YETERLİLİK "Bu ihalenin şartlarını karşılıyor muyuz?"
 │                          13 kurallı doğrulayıcı + koşullu ikinci görüş.
 │                          (Şu an veri eksikliğinden sınırlı — bkz. Bölüm 10.)
 │
 └─ 5  SONUÇ                Karar + gerekçe + dayanaklar veritabanına yazılır.
                            Uzmanın incelemesi kalıcı bir etikete dönüşür.
```

**Neden iki ayrı aşama?**
Bir ihale tam olarak şirketin alanına girebilir ama istenen iş deneyim belgesi
şirkette olmayabilir. Bunlar iki farklı sorudur; tek bir cevaba indirgemek ayrımı
kaybettirir. Ayrıca Aşama 1 "hayır" derse Aşama 2 hiç çalışmaz — bu ciddi bir
zaman tasarrufudur.

---

## 3. Yapay zekâ nasıl kullanılıyor

Bu, "soruyu yapay zekâya sorup cevabı yazdık" tipi bir sistem **değildir**. Model üç
ayrı katmanla çerçevelenmiştir.

### 3.1 · Model, kurumsal bilgiyle besleniyor

Şirketin faaliyet alanı **20 iş paketi** halinde yazıya dökülmüştür: Trafik Yönetimi
ve Sinyalizasyon, Elektronik Denetleme, Kamera ve Video Analitik, Akıllı Aydınlatma,
Coğrafi Bilgi Sistemleri ve diğerleri. Her paket şunları içerir:

- hangi yetkinlikleri kapsadığı
- hangi anahtar terimlerle tanındığı
- **hangi konuları kapsam dışı bıraktığı** (`negatif_terimler`)

Model karar verirken bu dosyalara bakar. Yani sonuç *"yapay zekâ ne düşünüyorsa o"*
değil, *"tanımlı faaliyet alanına göre ne düşünüyor"* olur.

> Bu dosyalar `app/profiles/data/profiles/` altındadır ve düz JSON'dur. Sistemi başka
> bir şirkete uyarlamak için **kod değil, bu 20 dosya** değiştirilir.

### 3.2 · Modelin üstünde deterministik kurallar var

Kesin olan bir şey olasılıksal bir modele sorulmaz. Dört kural kodda garanti altındadır
ve **her biri gerçekten ölçülmüş bir hatadan doğmuştur:**

| Kural | Ne yapar | Hangi gerçek hatadan doğdu |
|---|---|---|
| Skor/karar tutarlılığı | Düşük skorla "uygun" denemez | Model kendi promptundaki kuralı defalarca çiğnedi (skor 0.48 iken "uygun" dedi) |
| Uydurma paket yakalama | Olmayan paket adı → karar düşürülür | Bir inşaat ihalesinde model `"Bina İşleri"` diye var olmayan bir paket uydurdu |
| Belirsiz/reddet sınırı | Zayıf örtüşmede net karar zorunlu | Model "hayır" demekten kaçınıp "bilmiyorum"a sığınıyordu |
| Arama eşiği | Örtüşme yoksa "ilgili paket" gösterilmez | Alakasız bir ihaleye "İLGİLİ İŞ PAKETLERİ" başlığıyla 5 paket sunuluyordu |

### 3.3 · Gerekçe, karardan önce üretiliyor

Çıktı şeması alanların **sırasını** zorunlu kılar: **önce gerekçe, en son karar.**

Bu ayrıntı gibi görünür ama değildir. Dil modeli alanları şemadaki sırayla üretir;
karar başa alındığında model hiçbir gerekçe üretmeden karara kilitlenir ve gerekçe,
kararın sebebi değil **sonradan uydurulmuş bir açıklaması** haline gelir. Bu da
ölçülmüş bir hata modudur.

### 3.4 · Değişmez ilke: kaçırmamak

> **Uygun bir ihaleyi elemek, uygun olmayanı listeye almaktan çok daha pahalıdır.**

Bunun koddaki karşılıkları:

- Aşama 2, Aşama 1'in "uygun" kararını asla "uygun değil"e çeviremez
- Model hata verirse sonuç `belirsiz` olur, `uygun_degil` değil
- Tarih biçimi tanınmazsa ihale elenmez, incelemeye kalır
- Koddaki tüm düşürmeler hep **ortaya** doğrudur, uca değil

Bu davranışların hepsinin otomatik testi vardır.

---

## 4. Kurulum

### 4.1 · Gereksinimler

| | |
|---|---|
| **Python** | 3.11 veya üzeri |
| **Ollama** | Yerel dil modeli sunucusu — <https://ollama.com> |
| **Donanım** | ~4 GB VRAM'li bir GPU önerilir. GPU olmadan da çalışır ama çok yavaştır. |
| **Veri** | Bir `ekap.db` (SQLite) kopyası veya canlı Postgres erişimi |

### 4.2 · Adımlar

**1) Depoyu indirin ve bağımlılıkları kurun**

```bash
git clone <depo-adresi>
cd <depo-klasoru>

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

**2) Ollama'yı kurun ve modelleri indirin**

```bash
ollama pull qwen3:4b     # karar modeli     (~2.5 GB)
ollama pull bge-m3       # embedding modeli (~1.2 GB)
```

Ollama'nın çalıştığını doğrulayın:

```bash
curl http://localhost:11434/api/tags
```

**3) Yapılandırma dosyasını oluşturun**

```bash
cp .env.example .env     # Windows: copy .env.example .env
```

`.env` dosyasını açın ve veri kaynağınızı seçin. Yerel bir SQLite dosyanız varsa:

```ini
DATA_BACKEND=sqlite
SQLITE_YOLU=data/ekap.db
```

**4) Vektör indeksini kurun**

Bu adım 20 profili vektöre çevirip aranabilir hale getirir. Bir kez yapılır:

```bash
python scripts/index_profiles.py
```

**5) Her şeyin yerinde olduğunu kontrol edin**

```bash
python scripts/check_setup.py
```

Bu komut profilleri, veritabanı bağlantısını, Ollama'yı, embedding modelini ve vektör
indeksini tek tek dener. Bir kontrol başarısız olsa bile diğerleri denenmeye devam
eder; böylece eksiklerin tamamını tek seferde görürsünüz. Hepsi `[OK]` dönüyorsa
kurulum tamamdır.

---

## 5. Kullanım

### Tek bir ihaleyi analiz etmek

```bash
python scripts/analyze_tender.py 2026/948244 --ayrintili
```

| Seçenek | Ne yapar |
|---|---|
| `--ayrintili` | Arama sonuçlarını ve kriter gerekçelerini de yazdırır |
| `--sadece-asama1` | Aşama 2'yi atlar (daha hızlı) |
| `--json` | Sadece ham JSON basar (başka bir programa aktarmak için) |
| `--kaydet dosya.json` | Sonucu dosyaya yazar |
| `--aktif 30` | İlk 30 aktif ihaleyi sırayla tarar |

### Tarama ve etiketleme (asıl iş akışı)

Sistem bir grup ihaleyi tarar, siz kararları gözden geçirip işaretlersiniz. Her
işaretleme kalıcı bir etikete dönüşür ve ölçüm seti kendiliğinden büyür.

```bash
# 5 rastgele ihale tara ve sonuçları veritabanına yaz
python scripts/tara_ve_kaydet.py --n 5 --rastgele

# Nerede kaldığını gör
python scripts/tara_ve_kaydet.py --durum

# İnsan kararı bekleyen 20 satırı listele
python scripts/tara_ve_kaydet.py --incelenmemis 20

# Bir ihaleyi etiketle
python scripts/tara_ve_kaydet.py --etiketle 2026/948244 oneriliyor --inceleyen "Ad Soyad"
```

### Toplu tarama

Uzun süren, kesildiği yerden devam edebilen tarama:

```bash
# Hızlı eleme eşiğiyle tara (LLM çağrılarından tasarruf sağlar)
python scripts/toplu_tarama.py --sert-esik 0.44

# Yarım kalan taramayı sürdür
python scripts/toplu_tarama.py --devam

# Koşmadan, mevcut sonucu raporla
python scripts/toplu_tarama.py --ozet
```

---

## 6. Proje yapısı

```
app/                        Uygulama çekirdeği
├── config/settings.py      TÜM ayarlar tek dosyada (iyi belgelenmiş — buradan başlayın)
├── domain/models.py        Veri modelleri (İhale, İlan, OKAS kodu...)
├── database/               Veri erişimi — SADECE OKUMA
│   ├── base.py             Ortak arayüz + arka uç seçici
│   ├── sqlite_depo.py      Yerel SQLite arka ucu
│   ├── postgres_depo.py    Canlı Postgres arka ucu
│   └── karar_deposu.py     Sonuçların YAZILDIĞI tek yer (kendi tablomuz)
├── text/ilan_temizleyici.py    Standart maddeleri atıp asıl kapsamı bırakır
├── profiles/               20 iş paketi profili
│   ├── loader.py           Profilleri okur ve doğrular
│   └── data/profiles/      >>> ŞİRKETE ÖZEL BİLGİ BURADA (JSON)
├── embedding/embedders.py  Metni vektöre çevirir (3 arka uç, tek arayüz)
├── retrieval/              Benzer içerik arama
│   ├── qdrant_deposu.py    Vektör veritabanı
│   └── profil_retriever.py İhaleye en yakın paketleri ve örnekleri bulur
├── decision/               KARAR KATMANI
│   ├── on_filtre.py        LLM'siz kesin kurallar
│   ├── schemas.py          Çıktı şemaları — ALAN SIRASI KRİTİK
│   ├── stage1_kapsam.py    Aşama 1: kapsam kararı
│   ├── stage2_yeterlilik.py    Aşama 2: yeterlilik kararı
│   ├── dogrulayici.py      13 deterministik kural
│   ├── llm_client.py       Ollama istemcisi
│   └── ...
├── pipeline/servis.py      Tüm adımları sırayla yöneten orkestratör
└── mcp/sorgular.py         Hazır SQL sorguları

scripts/                    Çalıştırılabilir komutlar
├── check_setup.py          Kurulum kontrolü — İLK BUNU ÇALIŞTIRIN
├── index_profiles.py       Vektör indeksini kurar
├── analyze_tender.py       Tek ihale analizi
├── tara_ve_kaydet.py       Tarama + insan etiketleme akışı
├── toplu_tarama.py         Büyük ölçekli, kesintiye dayanıklı tarama
└── ...

evaluation/                 Ölçüm ve teşhis araçları
├── evaluate.py             Etiketli set üzerinde isabet ölçer
├── compare.py              İki koşuyu karşılaştırır
├── esik_analizi.py         Eşik değerlerini veriden türetir (LLM'siz)
├── *.csv                   Etiketli test setleri
└── ...

tests/                      207 otomatik test — LLM veya sunucu GEREKTİRMEZ
Sonuclar/                   Ölçüm çıktıları, sürüm sürüm saklanır
```

### Yeni başlayan biri nereden okumaya başlamalı?

1. `app/config/settings.py` — tüm ayarlar ve *neden öyle olduğu*
2. `app/decision/schemas.py` — çıktı yapısı ve alan sırasının nedeni
3. `app/decision/stage1_kapsam.py` — asıl karar mantığı
4. `app/pipeline/servis.py` — adımların birbirine nasıl bağlandığı

---

## 7. Yapılandırma

Tüm ayarlar `.env` dosyasından okunur ve `app/config/settings.py` içinde tanımlıdır.
Her ayarın yanında ne işe yaradığı ve hangi ölçüme dayandığı yazılıdır.

Başlangıç için önerilen ayarlar:

```ini
DATA_BACKEND=postgres              # sqlite | postgres — tek satırlık geçiş
BIRINCIL_MODEL=qwen3:4b          # varsayılan karar modeli
IKINCIL_MODEL=                   # boş = ikinci görüş kapalı
LLM_DUSUNME=                     # boş = modelin kendi varsayılanı (önerilen)
LLM_SEED=42                      # tekrarlanabilirlik için sabit
RETRIEVAL_MIN_SKOR=0.0           # 0.47 önerilir (ölçüldü)
BELIRSIZ_NETLESTIRME_ESIGI=0.0   # 0.48 önerilir (ölçüldü)
ASAMA2_CALISSIN=true
```

> **Deneysel anahtarları TEK TEK açın.** `.env` içindeki 5. bölümdeki bayrakların
> hepsi varsayılan kapalıdır. Aynı anda birkaçını açarsanız sonucun hangi
> değişiklikten geldiğini ayırt edemezsiniz.

### Sistemi başka bir şirkete uyarlamak

Karar mantığı şirketten bağımsızdır. Uyarlamak için:

1. `app/profiles/data/profiles/` altındaki 20 JSON dosyasını kendi iş paketlerinizle
   değiştirin (yetkinlikler, anahtar terimler ve özellikle `negatif_terimler`)
2. `app/profiles/data/profile_registry.json` ve `company_master.json` dosyalarını
   güncelleyin
3. `python scripts/index_profiles.py --sifirla` ile indeksi yeniden kurun
4. `app/decision/on_filtre.py` içindeki "kendi ihalesi" kuralında geçen şirket adını
   değiştirin

---

## 8. Ölçülmüş sonuçlar

`qwen3:4b` modeliyle, canlı veri üzerinde alınmış sonuçlar.

### Uzman onaylı test setleri

| Set | n | Ne ölçer | Sonuç |
|---|---|---|---|
| Kaçırma seti | 30 | Hepsi uygun — kaçırıyor muyuz? | **30/30** · kaçırma **0** |
| Yanlış alarm seti | 20 | Hepsi uygun değil — gürültü üretiyor muyuz? | **18/20** |
| Karar seti | 15 | Karışık | uygun **5/5** · uygun değil **4/4** |

İki belirleyici sınıfta toplam: **35/35 pozitif, 22/24 negatif.**

### Etiketsiz gerçek veri — asıl sınav

Rastgele seçilmiş aktif ihaleler. Model hiçbir etiket görmüyor; canlıda böyle
çalışacak.

```
seed  7   →  15/15
seed 13   →  13/15
seed 21   →  15/15
```

Aday liste oranı **~%7** — yani 4.450 aktif ihalede ~310 aday.

### Neden bu sayılara güvenilebilir

Bu projede en pahalı hata yanlış karar değil, **yorumlanamayan ölçümdür.**

- **207 otomatik test** — yapay zekâ, GPU veya sunucu gerektirmeden çalışır
- **Her koşu kaydedilir:** model, tüm ayarlar, çalıştığı makine, tarih. Koşular sürüm
  sürüm `Sonuclar/` altında saklanır ve karşılaştırılabilir
- **Tek değişken kuralı** — aynı anda iki şey değiştirilip tek ölçüm alınmaz
- **Geri alınan değişikliklerin koruyucu testi vardır.** "Bu denendi, zarar verdi,
  tekrar eklenmesin" bilgisi kodda yaşar
- **Ayrılmış sınama seti** hiç kullanılmadı; nihai ölçüm için saklıdır

Bu ölçüm disipliniyle bulunan gerçek hatalar arasında şunlar var: Windows'a özgü
sessiz veri bozulması, canlı veritabanındaki tarih tipi uyumsuzluğu, ve modelin kendi
güven skorunu üretmek yerine arama skorunu kopyalaması.

---

## 9. Test ve ölçüm

### Otomatik testler

```bash
python -m pytest tests/
```

207 test çalışır ve **hiçbiri Ollama, GPU veya veritabanı sunucusu gerektirmez.** Kod
değişikliği yaptıktan sonra ilk çalıştıracağınız komut budur.

> `pytest.ini` içinde `addopts = -q` tanımlıdır. Komuta ikinci bir `-q` eklerseniz
> özet satırı susar ve yalnızca `[100%]` görürsünüz.

### İsabet ölçümü

Bu komutlar gerçek model çağrısı yapar, yani **Ollama'nın açık olmasını gerektirir** ve
uzun sürer.

```bash
python evaluation/evaluate.py --data evaluation/kacirma-seti-v1.csv --subset kacirma
python evaluation/evaluate.py --data evaluation/yanlis-alarm-seti-v1.csv --subset yanlis_alarm

# İki koşuyu karşılaştır
python evaluation/compare.py --detay
```

### Teşhis araçları

```bash
# Yerel SQLite ile canlı Postgres aynı metni mi veriyor?
python evaluation/denklik_testi.py --ornek 50

# Eşik değerlerini veriden türet (LLM çağırmaz, hızlıdır)
python evaluation/esik_analizi.py --rastgele 300

# Aynı girdi aynı çıktıyı veriyor mu?
python evaluation/determinizm_teshisi.py --ikn <İKN>

# Canlı veritabanı şemasını incele
python evaluation/canli_kesif.py
```

---

## 10. Bilinen sınırlar

Bu bölüm bilerek açık yazılmıştır. Sistemi devralan kişinin neyin çalışmadığını da
bilmesi gerekir.

**Aşama 2 (yeterlilik) veri bekliyor.**
20 profilin tamamında iş deneyim belgeleri, tamamlanmış projeler, sertifikalar ve
personel kapasitesi alanları **boştur**. Bu veri girilmeden "şartları karşılıyor muyuz"
sorusu cevaplanamaz; dürüst davranan bir model kriterlerin çoğuna "bilinmiyor" demek
zorunda kalır. Aşama 1 (kapsam) bu veriye ihtiyaç duymaz ve sorunsuz çalışır.

**`belirsiz` sınıfı çıktıda görünmüyor.**
Model bu kararı üretiyor ama netleştirme eşiği onu siliyor. Şu ana kadar 13 çevrimin
13'ü de doğruydu. Ancak aktif ihalelerin **%99'u "Ön İlan" metninden** okunuyor ve Ön
İlan kalem listesi vermiyor. *"Bilmiyorum, insana sor"* sinyalinin *"reddet"*e dönüşme
riski izlenmelidir.

**Paralellik gerekiyor.**
Günde ~465 yeni ihale × ihale başına ~76 saniye ≈ 9,8 saat/gün. Sıralı koşu günlük
akışa ancak yetişiyor; birden fazla işçi süreçle çalıştırma henüz yok.

**Etiketli veri az.**
Yanlış alarm seti 20 satırdır ve kısmen yapay zekâ etiketlidir (yani bir miktar
döngüseldir). Kaçırma setinin 30 örneği, eski bir modelin "uygun" dediği havuzdan
geliyor — yani *"bulunmuş uygunları kaybetmiyoruz"* ölçüldü, *"hiç bulunmamış uygunları
buluyoruz"* ölçülmedi.

**OKAS kataloğu (9.590 kod) tam kullanılmıyor.**
Profillerdeki kod ön ekleri seyrek ve bazıları iki hanelidir; bu yüzden naif bir
"hiçbir profile uymuyorsa kapsam dışı" kuralı güvenli çalışmaz.

**Diğer:** geçmiş ihale korpusu indekslenmedi · hibrit (anahtar kelime + vektör)
skorlama eklenmedi · rapor üretici ve HTTP API ucu yok.

---

## 11. Sık karşılaşılan sorunlar

**`ConnectionError` / Ollama'ya bağlanılamıyor**
Ollama çalışmıyor olabilir. `ollama serve` ile başlatın ve
`curl http://localhost:11434/api/tags` ile doğrulayın.

**`model not found` hatası**
Model indirilmemiş: `ollama pull qwen3:4b`

**Analiz çok yavaş / zaman aşımına uğruyor**
Muhtemelen model GPU yerine CPU'da çalışıyor. `.env` içinde `LLM_TIMEOUT_SN=3600`
yapın veya daha küçük bir model deneyin. Tek bir ihale GPU'da 64-76 saniye sürer;
CPU'da 10-40 dakika sürebilir.

**Postgres'e sürekli zaman aşımı**
Sorun büyük ihtimalle süre bütçesi değil **erişimdir**. Önce ağ erişimini doğrulayın
(`Test-NetConnection <host> -Port 5432`), sonra gerekirse
`DATABASE_CONNECT_TIMEOUT` değerini artırın.

**Arama sonuçları tuhaf / alakasız paketler geliyor**
Embedding arka ucunu değiştirdiyseniz indeks eskimiştir. Yeniden kurun:
`python scripts/index_profiles.py --sifirla`

**Testler geçiyor ama gerçek analiz hata veriyor**
Testler bilerek çevrimdışıdır. Gerçek bağlantılar için önce
`python scripts/check_setup.py` çalıştırın.

---

## 12. Sözlük

| Terim | Anlamı |
|---|---|
| **EKAP** | Elektronik Kamu Alımları Platformu — kamu ihalelerinin yayımlandığı resmî sistem |
| **İKN** | İhale Kayıt Numarası. Her ihalenin tekil kimliği (örn. `2026/948244`) |
| **OKAS** | İhale konusu mal/hizmetin sınıflandırma kodu |
| **Ön İlan** | İhalenin ayrıntılı ilandan önce yayımlanan özet duyurusu. Aktif ihalelerin ~%99'u budur |
| **İş paketi / profil** | Şirketin yaptığı işlerin tanımlandığı 20 kategoriden biri |
| **LLM** | Large Language Model — büyük dil modeli |
| **Ollama** | Dil modellerini kendi bilgisayarınızda çalıştıran sunucu yazılımı |
| **Embedding** | Metnin sayısal vektöre çevrilmiş hâli; anlam benzerliği bunun üzerinden hesaplanır |
| **RAG** | Retrieval-Augmented Generation — modele soru sormadan önce ilgili bilgiyi bulup prompt'a koyma yöntemi |
| **Retrieval** | Benzer içerik arama adımı |
| **Kaçırma** | Gerçekte uygun olan bir ihaleye "uygun değil" demek. Bu projedeki EN PAHALI hata |
| **Yanlış alarm** | Gerçekte uygun olmayan bir ihaleye "uygun" demek |
| **Deterministik** | Aynı girdiye her zaman aynı çıktıyı veren; olasılıksal olmayan |

---

## Teknoloji yığını

| Katman | Teknoloji |
|---|---|
| Dil modeli | Ollama + `qwen3:4b` (varsayılan) / `qwen3:8b` — **yerel çalışır, veri dışarı çıkmaz** |
| Yapılandırılmış çıktı | Pydantic şemaları (`format=<JSON şeması>`) |
| Embedding | `bge-m3`, 1024 boyut |
| Vektör deposu | Qdrant (gömülü mod) |
| Veri katmanı | PostgreSQL (canlı) / SQLite (yerel), psycopg 3 |
| Yapılandırma | pydantic-settings + `.env` |
| Test | pytest — 207 test |
| Dil | Python 3.11+ · ~14.000 satır |

**Kullanılan desenler:** RAG · iki aşamalı karar zinciri · LLM üstünde deterministik
kural katmanı · insan-döngüde geri besleme · sürümlenmiş ölçüm.

---

## Lisans

Bkz. [LICENSE](LICENSE).
