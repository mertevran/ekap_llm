# EKAP–İSBAK İhale Karar Destek Sistemi

Bu proje, EKAP (Elektronik Kamu Alımları Platformu) üzerinde yayımlanan binlerce kamu ihalesini otonom olarak analiz edip, İSBAK A.Ş. şirket faaliyet profilleriyle (AUS-01, TEK-01 vb.) eşleştirerek anlamsal (semantic) uygunluk değerlendirmesi yapan bir Karar Destek Sistemi'dir.

Sistem:
* Canlı PostgreSQL veritabanından güncel ihale dokümanlarını alır.
* İSBAK şirket profilleriyle (20 adet aktif profil) ihaleleri anlamsal olarak karşılaştırır.
* Milyonlarca kelimelik dokümanlar içinden en uygun profili ve ihaleyi tespit eder.
* İhale kapsamı ile şirket faaliyet profili arasındaki uyumu katı iş kuralları ve LLM desteği ile değerlendirir.
* Kaynak kanıtlara dayalı gerekçeli kararlar üretir.

**Kısaca:** Manuel olarak incelenmesi günler sürecek ihale yığınlarını saniyeler içinde tarayan, hangi ihalenin hangi departman (profil) ile ilgili olduğunu bulan ve insan onayına (Human-in-the-Loop) gerekçeli raporlar sunan yapay zeka destekli, güvenli bir filtredir.

---

## 2. Projenin Temel Amacı

* **Hedef İhaleleri Belirlemek:** Dev ihale havuzunda, şirketin ana faaliyet alanlarına ve yetkinliklerine (örneğin akıllı ulaşım, yazılım, siber güvenlik) uygun ihaleleri radar ekranına düşürmek.
* **Manuel İnceleme Yükünü Azaltmak:** İhale inceleme ekiplerinin alakasız ihalelere vakit harcamasını engellemek.
* **Kontrollü Yapay Zeka Kararı:** Büyük Dil Modellerini (LLM) tek başına karar verici ("Blackbox") yapmadan; LLM'in muhakeme gücünü Python tabanlı katı doğrulama (Guardrails) kurallarıyla dengelemek.
* **Yanlış Riskleri (False Positive / Negative) Azaltmak:** Jenerik bir kelime ("araç", "bakım") geçtiği için ilgisiz bir ihalenin alınmasını veya tam tersi ilgili bir ihalenin reddedilmesini engellemek.
* **Gerekçeli ve Kaynaklı Sonuç:** İnsan karar vericinin önüne sadece "uygun" veya "değil" etiketi koymak yerine, ihalenin hangi maddesinden dolayı bu kararın verildiğini şeffaf kanıtlarla sunmak.
* **Minimum Donanım, Maksimum Verim:** Ağır GPU sunucularına veya dış bulut servislerine (OpenAI vb.) ihtiyaç duymadan, yalnızca CPU üzerinde, kurumsal veri güvenliği sınırları içinde çalışabilmek.

---

## 3. Güncel Sistem Mimarisi

Sistem, Retriever (Getirim) ve Generator (Üretim) aşamalarından oluşan klasik bir RAG mimarisini çok daha güvenli ve kurallı bir yaklaşımla (Source-Grounded) birleştirir.

```text
PostgreSQL (Source of Truth)
   │
   ▼
İhale verisinin alınması
   │
   ▼
Doküman oluşturma / mantıksal bölümleme
   │
   ▼
BGE-M3 embedding (gömme) - CPU
   │
   ▼
FAISS Vektör İndeksi
   │
   ▼
İSBAK profil eşleştirme (Aday Bulma)
   │
   ▼
Aday profil puanlama (Scoring)
   │
   ▼
Gerçek ihale verisinin PostgreSQL'den YENİDEN okunması
   │
   ▼
Kanıt seçimi (Dinamik 1/3/4 parça)
   │
   ▼
Compact Context (Kompakt Şirket Bağlamı)
   │
   ▼
Qwen 3.5 4B (Ollama - JSON Schema)
   │
   ▼
Python Doğrulama (SourceGroundedPythonValidator)
   │
   ▼
Güven kalibrasyonu (Confidence Scoring)
   │
   ▼
uygun / uygun_degil / inceleme_gerekli
   │
   ▼
İnsan Onayı / Uzman İncelemesi
```

**Katmanların Görevleri:**
* **Gömme ve Arama:** İhaleleri ve profilleri uzaysal vektörlere çevirip birbirine yakın olanları bulur.
* **Grounding (Gerçekleme):** Vektör indeksindeki olası eskimiş veri riskine karşı, karara girecek veriyi canlı veritabanından çekerek doğrular.
* **Generation (Karar Üretimi):** Qwen LLM, bağlamı okur ve ihale katılım/uygunluk durumunu bir taslak JSON olarak üretir.
* **Validation (Doğrulama):** Python katmanı, Qwen'in verdiği kararın kurallara, gerçek belgelere ve şirket profiline tam uygun olup olmadığını denetler, uydurmaları yakalar.

---

## 4. Neden PostgreSQL + FAISS Birlikte Kullanılıyor?

Sistemin kalbinde yatan temel ayrım şudur:

* **PostgreSQL:** Gerçek veri kaynağıdır (Source of Truth).
* **FAISS:** Anlamsal getirim (Semantic Retrieval) ve aday bulma motorudur.

FAISS nihai bir karar kaynağı değildir. Yalnızca benzer ihale ve şirket profil adaylarını hızlıca bulmak, milyonlarca kayıt arasından potansiyel eşleşmeleri saliseler içinde listelemek için kullanılır. 

Ancak FAISS indeksleri belirli zaman aralıklarında (batch) güncellenir. Eğer indeks ile gerçek veri arasında bir saniyelik bile fark oluşursa, sistemin yanlış karar verme riski doğar. Bu nedenle FAISS üzerinden "Bu ihale AUS-01 için %80 uygun adaydır" tespiti yapıldıktan sonra; nihai LLM karar sürecine **asla FAISS'teki eski metin sokulmaz**. 

Sistem, bulunan adayın güncel ve gerçek ihale verisini tekrar **PostgreSQL'den çeker**. Böylece, indeks güncelliği ile canlı veri arasındaki operasyonel farkın karar güvenliğini bozması %100 engellenmiş olur. FAISS'in güncelliği sadece "yeni ihalelerin ne kadar çabuk radara gireceğini" belirler, kararın doğruluğunu etkilemez.

---

## 5. Şirket Profilleri

Sistem, İSBAK A.Ş.'nin departmanlarını ve iş alanlarını temsil eden 20 ayrı Şirket Faaliyet Profili (JSON) üzerinden çalışır.

**Profil Örnekleri:**
* `AUS-01` Trafik Yönetimi ve Sinyalizasyon
* `AUS-04` Toplu Taşıma ve Raylı Sistem Teknolojileri
* `ENT-02` Kamera, Video Analitik ve Güvenlik Sistemleri
* `TEK-01` Yazılım, Veri ve Sistem Bütünleştirme
* `TEK-04` Bilgi ve Siber Güvenlik
* `OPS-02` Bakım, Onarım ve Teknik Destek

**Profil Yapısı:**
* **Profil Adı ve Faaliyet Açıklaması:** Profilin genel amacını LLM'e tanıtan metinler.
* **Birincil Yetkinlikler:** Departmanın asıl varoluş sebebi olan anahtar kelimeler.
* **Güçlü Terimler:** İhalede geçtiğinde, ihalenin direkt bu profile ait olduğuna dair kesin işaret veren terimler. (Python'daki Pozitif Faaliyet doğrulaması bu terimlere bakar).
* **Destekleyici Terimler:** Yan veya tamamlayıcı yetkinlikler.
* **Negatif Terimler:** İhalede geçtiğinde, konunun bu profilin dışında olduğunu belirten kırmızı çizgiler. (Python'daki Negatif Faaliyet doğrulaması bunu kullanır).
* **Teknik Ekipman & Eylem Fiilleri:** Bağlamı zenginleştiren jargonsal yapılar.
* **OKAS Kodları:** Kamu ihale sözlüğündeki resmi kod karşılıkları.

*Önemli Not:* Profildeki bazı alanların boş bırakılması (örn: negatif terimlerin olmaması), o profilin her ihaleye "uygun" veya "uygun değil" sayılacağı anlamına gelmez. LLM ve Python, mevcut veriyi kendi dinamiğinde değerlendirir.

---

## 6. İhale Vektörleştirme ve FAISS

İhaleler bütünsel ve yapısal olarak devasa olduğu için doğrudan değerlendirilemez.

* **BGE-M3 Embedding:** İhale metinleri, CPU üzerinde çalışabilen hafif ve güçlü `BAAI/bge-m3` modeli ile 1024 boyutlu vektörlere dönüştürülür.
* **Section-Aware Chunking:** Metinler; teknik şartname, idari şartname, ihale ilanları olarak mantıksal bölümlere ayrılarak parçalanır. Anlam bütünlüğü korunur.
* **FAISS Saklama (`IndexIDMap`):** Vektörler bellek-içi (in-memory) indekslerde tutulur. İhale (`ekap_tender_chunks`) ve Şirket Profili (`isbak_company_profiles`) ayrı ayrı beslenir.
* **Artımlı (Incremental) İndeksleme:** Aynı ihalenin defalarca vektörize edilerek sistem kaynaklarını tüketmemesi için sadece veritabanında olup FAISS'te olmayan yeni/değişmiş ihaleler hedeflenir. Atomik güncelleme ile indeks bozulmaları önlenir.

---

## 7. Aday Profil Eşleştirme ve Puanlama

Sistem, vektör uzayında ihaleler ile şirket profilleri arasındaki "Aday Puanlama" (Candidate Scoring) mantığını özel bir ağırlık formülüyle hesaplar (`ScoreAggregator`).

Puanlama bileşenleri şu şekildedir:
* **%55 En Güçlü Chunk (Parça) Benzerliği:** İhalenin herhangi bir yerindeki en net eşleşme.
* **%20 Ortalama Benzerlik:** Seçilen parçaların geneline yayılan uyum.
* **%10 Bölüm Çeşitliliği:** Eşleşmelerin hem teknik hem idari bölümlere yayılma durumu (sadece bir yerde geçiyorsa puan düşer).
* **%10 OKAS Desteği:** Resmi ihale kodu ile profil OKAS kodlarının uyumu.
* **%5 İhale Başlığı Desteği:** Başlık ile profil içeriği uyuşuyorsa verilen bonus.

Ayrıca profilin "Negatif Terimleri" metinde ağırlıklı olarak geçiyorsa genel puana ceza (penalty) uygulanır.

> [!WARNING]
> **ÖNEMLİ:** Bu Retrieval Score (Getirim Puanı) bir "uygunluk olasılığı" değildir! `score = 0.70` olması ihalenin "%70 uygun" olduğu anlamına **gelmez**. Bu sadece FAISS eşleştirme sırasını belirleyen semantik (anlamsal) bir sıralama skorudur. Kararı verecek olan model ve Python'dur.

---

## 8. Kanıt Seçim Sistemi

Bütün ihaleyi (yüzlerce sayfayı) LLM'e göndermek hem imkansız hem de maliyetlidir. Sistem, ihaleden "duruma göre" dinamik sayıda kanıt çeker:

* **Clear (Net Durum - 1 Kanıt):** Hem ihale başlığı hem de OKAS kodu şirketle birebir eşleşiyorsa, yüksek ihtimalle uygundur, 1 adet en iyi teknik şartname kanıtı yeterlidir.
* **Ambiguous (Belirsiz Durum - 3 Kanıt):** Metin karmaşık, genel geçer veya düşük puanlıysa, karar vermek için modelin daha çok bağlama ihtiyacı vardır (3 kanıt).
* **Mixed / Partial (Karmaşık / Kısmi - 4 Kanıt):** İhale içinde hem profilinize uyan hem de uymayan şeyler varsa veya kısmi teklife açıksa, en derinlemesine analiz için maksimum sayıda kanıt (4 kanıt) gönderilir.

Bu sayılar LLM çağrı sayısını değil, Qwen modeline "context" (bağlam) olarak gönderilen maksimum ihale parça (chunk) sayısını ifade eder.

---

## 9. Compact Context (Kompakt Bağlam)

Şirket profil JSON dosyaları oldukça detaylıdır. Bu durum LLM token sınırlarını doldurabilir. Bu sorunu çözmek için profil bilgileri **Compact Context (Kompakt Bağlam)** işleminden geçirilir.

Kompakt bağlam bir "LLM özetleme işlemi" **değildir**. Python tabanlı bir filtrelemedir:
* İçi boş olan JSON alanları tamamen kaldırılır.
* Liste tekrarları (mükerrer terimler) temizlenir.
* Sadece karar anında Qwen'in gerçekten ihtiyaç duyduğu kritik alanlar korunur.

**Amaç:** Aynı karar açısından önemli bilgiyi, çok daha az token (belirteç) ile temsil ederek hızı ve doğruluğu artırmaktır. 

**Integrity Guard (Bütünlük Koruması):** Eğer sıkıştırma işlemi sırasında `birincil_profil` gibi hayati bir bilgi silinirse veya hasar görürse, sistem "legacy_fallback" (eski ham metin) yapısına geri döner. Kritik bilginin kaybolmasına asla izin verilmez.

---

## 10. Qwen Karar Modeli

Sistemin yegane ana modeli: **`qwen3.5:4b-q4_K_M`**

* **Platform:** Ollama
* **Donanım:** `num_gpu=0` (CPU-only çalışacak şekilde sabitlenmiştir)
* **Ayar:** `temperature=0` (Yaratıcılık kapalı, deterministik sonuç), `think=False` (Eski düşünce/chain-of-thought modu kapatılmış, sadece JSON şeması).
* **Çıktı:** Strict JSON Schema. Kararı, Türkçe gerekçeyi ve kriterleri standart bir JSON olarak döndürür.

> [!NOTE]
> **Qwen nihai kararın tek sahibi değildir.** Qwen ihaleyi yorumlar, gerekçe üretir ve birincil kararı "önerir". Ardından bu karar, sistemin asıl omurgası olan Python doğrulayıcısına girer. 

---

## 11. Python Doğrulama Katmanı (`SourceGroundedPythonValidator`)

Qwen'in ürettiği her çıktı, Python tabanlı katı kurallardan oluşan bir süzgeçten geçer. Bu katman sistemin en önemli mühendislik başarısıdır.

* **Tahrifat ve Kaynak Denetimi:** Qwen, gerekçesinde kanıt gösterdiği ID'leri gerçekten kullanmış mı? İhale metninde olmayan bir şeyi "zorunlu kriter" diye uydurmuş mu? Uydurmaları yakalar.
* **Pozitif Faaliyet Doğrulaması (YOL A):** Model ihaleye "uygun" dediyse, şirket profilinin gerçekten güçlü terimlerinden biri (Örn: "sunucu", "yazılım") ihale metninde geçiyor mu diye bakar. Geçmiyorsa "Ben uygunluk göremedim" diyerek kararı `inceleme_gerekli` seviyesine düşürür.
* **Negatif Faaliyet Doğrulaması (YOL B):** Profilin katı `negative_terms` listesine ek olarak `proven_out_of_scope` mantığı çalışır. Sadece ihalede "araç", "bakım" kelimesi geçti diye ret verilmesini engeller; "öğrenci taşıma hizmeti", "yemek alımı" gibi 2'li/3'lü tam faaliyet dışı ifadeleri tespit edip onaylar. Eğer Qwen kanıtsız biçimde ret (uygun_degil) verdiyse, kararı `inceleme_gerekli` durumuna çevirir.
* **Faaliyet Yeterliliği Ayrımı:** İhalenin İSBAK işi olması ile ihaleye girecek evraklara sahip olup olunmaması ayrılır.
* **Güven Kalibrasyonu:** Doğrulamadan geçen her adımda güven (confidence) skoru kalibre edilir. Python, skorları hiçbir zaman yükseltmez, yalnızca şüpheli durumlarda düşürür (Ceza mantığı).

---

## 12. Nihai Karar Mantığı

Python doğrulamasından sağ çıkan sonuç üç ana sınıftan birini alır:

### uygun
İhale, şirketin faaliyet alanıyla doğrulanmış biçimde örtüşüyorsa verilir. Bu karar "Şirket bütün ihale şartlarını ve belgelerini eksiksiz karşılıyor, kesin ihaleyi alır" anlamına **gelmez**. Bu esas olarak "Faaliyet kapsamı yönünden (Activity-Scope Suitability) bizim işimizdir" kararıdır.

### uygun_degil
Şirket profilinde yer alan negatif faaliyetlerle kesin olarak çatışma varsa, tamamen alan dışıysa veya doğrulanmış bir elenme (ret) nedeni kanıtlanmışsa verilir.

### inceleme_gerekli
Sistemin belirsizliği insan uzmanına bıraktığı kilit sınıftır.
* LLM uydurma (halüsinasyon) yaptıysa,
* Kanıtlar bir karara varmak için yetersizse,
* Zorunlu kriter belirsizliği varsa,
* İhale kısmi teklife açıksa ve bazı kısımlar uyup bazıları uymuyorsa,
* Qwen'in ret kararı, Python tarafından metinde kanıtlanamadıysa sistem güvenli tarafta kalarak "inceleme_gerekli" der.

---

## 13. İnsan Kontrolü ve Güvenlik (Human-in-the-Loop)

Sistemdeki en değişmez kurallardan biri şudur:
**`automatic_action_allowed = False`**

* Sistem hiçbir şart altında EKAP'a otomatik teklif vermez, otomatik sözleşme yapmaz.
* İhaleleri otomatik olarak çöpe atmaz (satın alma / elenme kesin kararı üretmez).
* `uygun` kararı insan onayına (Human Approval), `inceleme_gerekli` kararı ise insan incelemesine (Human Review) gönderilir. Uzman personeller kararları, Python'un sunduğu kesin kanıtlarla birlikte okuyup nihai süreci yönetirler.

---

## 14. Sistem Performansı

Proje **"Minimum Donanım, Maksimum Verim"** prensibiyle inşa edilmiştir. GPU'suz CPU ortamlarında çalışmak üzere optimize edilmiştir.
Aktif kaynak tüketimi `scripts/monitor_system_resources.py` üzerinden canlı ölçülmektedir. 

* **RAM Kullanımı:** Vektörler batch mantığıyla işlendiğinden System RAM ve Python RSS (tepe noktası) kısıtlı seviyelerde tutulur. Swap patlamasını engellemek için `MAX_RUNTIME_SWAP_GROWTH_MB=512` sınırı mevcuttur.
* **LLM Süreleri:** Model CPU'da `eval_duration`, `prompt_eval_duration`, ve Token/sec hızlarına dayalı çalışır. Karar başına üretilen generation token sayısı, JSON Schema kısıtı sayesinde minimumda tutularak hız kazandırılır.

---

## 15. Aktif Dosya Yapısı

```text
app/
├── config/                 # Pydantic yapılandırma ve çevresel değişkenler (.env)
├── database/               # PostgreSQL repo, model ve bağlantıları
├── decision/               # Qwen etkileşimi, Python Doğrulayıcısı ve Validator kuralları
├── domain/                 # Ortak modeller (TenderRecord vb.)
├── indexing/               # İhale indeksleyici ve FAISS bütünleşmesi
├── matching/               # Retrieval skorlama ve profil eşleştirme matematiksel algoritması
├── pipeline/               # Karar mekanizmasının uçtan uca zinciri
├── reporting/              # JSONL / CSV / Terminal rapor üreticileri
├── retrieval/              # Vektör sorgulama, kanıt sınıflandırma
└── vector_store/           # FAISS IndexIDMap sarmalayıcıları ve depolama nesneleri

scripts/
├── build_active_tenders_faiss.py   # Yeni ihaleleri FAISS'e indeksler
├── build_profiles_faiss.py         # Şirket profillerini FAISS'e indeksler
├── run_tender_decision_chain.py    # Ana Karar İşlem Hattı Betiği
├── check_database.py               # Veritabanı sağlık kontrolü
├── check_faiss_integrity.py        # İndekslerin tahrifat denetimi
└── monitor_system_resources.py     # CPU/RAM sistem metrik izleyicisi

config/isbak/                       # Şirket Profili (JSON) Klasörü
storage/
├── faiss/                          # İhale .index ve .pkl dosyaları
└── faiss_profiles/                 # Profil .index ve .pkl dosyaları
```

---

## 16. Kurulum ve Yapılandırma

Sistem Ubuntu / WSL Ubuntu temel alınarak tasarlanmıştır.

1. **Sanal Ortam ve Bağımlılıklar:**
```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

2. **Ollama Kontrolü:**
```bash
ollama --version
ollama list
```
*(Sistemde `qwen3.5:4b-q4_K_M` modelinin kurulu olduğundan emin olun).*

3. **Çevresel Değişkenler (`.env`):**
`.env.example` dosyasını `.env` olarak kopyalayıp güncelleyin:
```env
DATABASE_HOST=127.0.0.1
DATABASE_PORT=5433
DATABASE_NAME=ekap_db
DATABASE_USER=ekap_reader
DATABASE_PASSWORD=degistirin

QWEN_MODEL=qwen3.5:4b-q4_K_M
OLLAMA_BASE_URL=http://127.0.0.1:11434
```

---

## 17. Test ve Doğrulama

Aktif birim ve entegrasyon testlerini (Pytest) şu komutlarla çalıştırabilirsiniz:

```bash
# Tüm testleri çalıştırmak için
PYTHONPATH=. .venv/bin/python3 -m pytest

# Özellikle Negatif Faaliyet (Proven Out of Scope) kurallarını test etmek için (19 Test)
PYTHONPATH=. .venv/bin/python3 -m pytest tests/decision/test_source_grounded_python_validation.py -v
```

---

## 18. Canlı Karar Testi (İşlem Hattını Çalıştırma)

Ana veri kaynağı PostgreSQL olduğu için üretim ortamında `database-sequential` veya `database-random` modları kullanılır.

*Örnek: PostgreSQL'deki en yeni 10 aktif ihaleyi alıp tüm karar zincirinden (FAISS Aday > PostgreSQL Grounding > LLM > Python) geçiren komut:*

```bash
PYTHONPATH=. python3 scripts/run_tender_decision_chain.py \
  --source-mode database \
  --selection-mode database-sequential \
  --max-decisions 10 \
  --log-level INFO
```

---

## 19. Rapor Çıktıları

`scripts/run_tender_decision_chain.py` komutu tamamlandığında `reports/` dizininde şu raporlar üretilir:

* `tender_model_decisions.jsonl` (Tüm ham model çıktıları ve token verileri)
* `tender_public_decisions.jsonl` (Kullanıcıya gösterilecek sade JSON)
* `tender_public_decisions.csv` (Sadeleştirilmiş public rapor)
* `tender_model_decisions.csv` (Sistem yöneticileri için detaylı debug csv)
* `tender_review_required.csv` (Sadece "inceleme_gerekli" etiketi alan ihaleler)
* `tender_human_action_queue.csv` (Onay bekleyen veya inceleme gerektiren iş kuyruğu)
* `tender_professional_decisions.csv` (Yöneticiler için özet karar panosu)
* `database_sequential_selection.csv` (İhalelerin seçilme ve FAISS aday aşaması kayıtları)
* `tender_decision_run_summary.json` (Çalışma süresi, bellek özeti ve istatistikler)

---

## 20. Bilinen Sınırlamalar

Sistemdeki mevcut kısıtlar şunlardır:
* **PostgreSQL - FAISS Gecikmesi:** Yeni ihalelerin sisteme hemen dahil olabilmesi için FAISS indeksleme (incremental) betiğinin düzenli çalışması gerekir. O zamana kadar ihaleler radar dışı kalır.
* **Aşırı Geniş Pozitif Eşleşme:** Bazı "taşıma" veya genel yetkinlik içeren ihalelerde, profiller agresif pozitif eşleşmeler yakalayabilir.
* **Proven Out Of Scope Hassasiyeti:** Negatif faaliyet doğrulaması, özellikle karmaşık teknoloji ve bakım ihalelerinde fazla agresif davranıp, kısmi fırsatları yanlış reddedebilir. Düzenli Regex kalibrasyonuna ihtiyaç duyar.
* **LLM Varyansı:** `temperature=0` olsa da, %100 deterministik değildir. Çok nadiren modelin yapısal (schema) dışına çıkması mümkündür.
* **CPU Çıkarım (Inference) Hızı:** GPU kullanılmadığı için tek bir ihale analizi saniyeler (hatta bazen 10+ saniye) sürebilir. Ölçekleme, yataydaki CPU çekirdekleri (threading) üzerinden sağlanır.

---

## 21. Tasarım Kararları

### Neden LangChain gibi ağır bir framework kullanılmadı?
Daha düşük bellek ek yükü, bağımlılıkların tamamen kontrolümüzde olması, hatanın kolay ayıklanabilmesi ve sürecin "sihirli asistanlar" yerine deterministik Python fonksiyonları üzerinden %100 kontrol edilebilmesi için ağır orkestrasyon çatıları reddedilmiştir.

### Neden Tek Model (Qwen)?
Çoklu modellerin belleğe yüklenme maliyeti, CPU çıkarım (inference) sürelerini katlayarak uzatmaktaydı. Python Doğrulama Katmanının güçlenmesiyle LLM'in tek başına yarattığı risk azaltılmış, böylece tek model (Qwen 4B) ile hız ve RAM tasarrufu sağlanmıştır.

### Neden FAISS?
Milvus veya Qdrant gibi harici sunucu bağımlı vektör veritabanları yerine bellekte (in-memory) lokal çalışan, dış kurulum istemeyen, CPU ortamına en uygun ve en hızlı indeksleyici FAISS tercih edilmiştir.

### Neden Python Doğrulama Katmanı?
LLM'in kusursuz dilsel muhakeme yeteneği vardır ancak "katı iş kuralı" takip etme konusunda istikrarsızdır (deterministik değildir). LLM, bir hukukçu veya analiz uzmanı edasıyla metni okuyup yorumlar, Python ise formüllere dayalı matematiksel/mantıksal bir kalite kontrol şefi gibi o yorumun kanıtlarını belgelerde doğrulayarak sistemi güvenli hale getirir.