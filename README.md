<div align="center">
  <img src="https://via.placeholder.com/150x150.png?text=EKAP+ISBAK" alt="Project Logo" width="150" height="150">
  
  # EKAP İSBAK İhale Analiz Servisi
  
  **Yapay Zeka Destekli Uçtan Uca İKN Karar Motoru**

  [![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
  [![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
  [![FAISS](https://img.shields.io/badge/FAISS-Vector_Search-blue.svg)](https://github.com/facebookresearch/faiss)
  [![Ollama](https://img.shields.io/badge/Ollama-LLM-white.svg)](https://ollama.ai/)
</div>

<br />

## 📖 İçindekiler
- [Projenin Amacı](#-projenin-amacı)
- [Mimari ve Akış](#-mimari-ve-akış)
- [SQL Encoder](#-sql-encoder)
- [Temel Özellikler](#-temel-özellikler)
- [Karar Sınıfları](#-karar-sınıfları)
- [Kurulum](#-kurulum)
- [Kullanım](#-kullanım)
- [Test ve Geliştirme](#-test-ve-geliştirme)
- [Klasör Yapısı](#-klasör-yapısı)
- [Sınırlamalar ve Notlar](#-sınırlamalar-ve-notlar)

---

## 🎯 Projenin Amacı

Bu proje, Türkiye Cumhuriyeti Elektronik Kamu Alımları Platformu (EKAP) üzerinde yayınlanan ihalelerin **İstanbul Bilişim ve Akıllı Kent Teknolojileri A.Ş. (İSBAK)** çalışma alanlarına ve yeterliliklerine uygunluğunu analiz eden yapay zeka (LLM) destekli bir karar motorudur. 

Sistem; ihale şartnamelerini, ilanları ve idari gereksinimleri okuyarak **Anlamsal Arama (FAISS)** yöntemiyle güncel aktif ihaleleri tarar ve toplar. Aynı ihale farklı profillerden gelirse tek kayıt altında birleştirilir. Ardından **Qwen** modeli faaliyet uygunluğu kararı üretir; Python iş kuralları negatif kapsamı ve kanıt bütünlüğünü doğrular. Katılım yeterliliği faaliyet kararından ayrı raporlanır.

---

## 🏗️ Mimari ve Akış

Sistem, yapay zeka halüsinasyonlarını (uydurmalarını) en aza indirmek ve kurumsal iş kurallarına tam uyum sağlamak için "Çok Katmanlı Yönlendirme ve Doğrulama" mimarisini kullanır.

```mermaid
graph TD
    J[Doğal dilde ihale seçme isteği] --> K[SQL Encoder]
    K --> L[PostgreSQL - Salt okunur seçim]
    A[PostgreSQL - Ham Veri] --> B(Parçalama & FAISS İndeksleme)
    B -->|BGE-M3 & FAISS IndexFlatIP| C[İSBAK Profil Yönlendirici]
    L -->|Seçilen İKN kümesi| C
    C -->|Aday İKN| H[PostgreSQL - Gerçek Kaynak Yenilemesi]
    H -->|Tür, OKAS, Kısım, Teknik Özellik| D(Karar Motoru: qwen3.5:4b-q4_K_M)
    
    D -->|Qwen Çıktısı| E{Python İş Kuralları Doğrulayıcısı}
    E -->|Doğrulama Geçti| F[Nihai Karar Raporu]
    E -->|Çelişki / Eksik Kanıt / Belirsizlik| I[İnsan İncelemesi Gerekli]
    I --> F
    
    style A fill:#316192,color:#fff
    style B fill:#e63946,color:#fff
    style D fill:#2a9d8f,color:#fff
```

**İnsan İncelemesine Yönlendirme Durumları (Çelişki / Eksik Kanıt / Belirsizlik) Nasıl Oluşur?**
- **Çelişki:** Birincil model ihaleyi "uygun" bulsa da, Python İş Kuralları katmanının zorunlu bir kriter ihlali (örneğin donanım ihalesinde sadece yazılım profili getirilmesi) tespit etmesi.
- **Eksik Kanıt:** Birincil modelin kendi verdiği kararı destekleyecek geçerli bir metin parçası referansı (chunk_id) sunamaması veya sahte (olmayan) bir ID uydurması (halüsinasyon).
- **Belirsizlik:** İhale şartlarındaki bulanıklık nedeniyle modelin kararsız kalması veya güven skorunun düşük (düşük eminlik) olması.

### Akış Adımları:
1. **Veri Okuma Katmanı:** PostgreSQL'den ihaleye ait ham özellik, OKAS ve onay verilerini okur (Salt-Okunur).
2. **İndeksleme Katmanı:** Doğal veritabanı yapısı korunarak (Özet, Kapsam, Karakteristikler vb.) bölüm farkındalıklı parçalama yapılır ve `BAAI/bge-m3` modeliyle vektörize edilerek atomik şekilde **FAISS** indeksine yazılır.
3. **Profil Arama (Retrieval):** İSBAK profilleri (YAZILIM, DONANIM, ARGE vb.) üzerinden FAISS'te anlamsal arama yapılarak ilgili aday ihaleler bulunur.
4. **Kaynak Yenilemesi:** Aday İKN model çağrısından önce PostgreSQL'den yeniden okunur. Gerçek ihale türü, bütün OKAS kodları, kısmi teklif bilgisi, çıkarılabilen kısım listesi ve bütün teknik özellikler karar bağlamına eklenir.
5. **Karar Motoru (qwen3.5:4b-q4_K_M):** Faaliyet uygunluğunu değerlendirir; katılım yeterliliğini ayrı alanlarda raporlar.
6. **Python Kural Doğrulayıcısı (IsbakDeterministicValidator):** Model kararını geçerli parça kimlikleriyle denetler; negatif profil terimlerini ihale başlığı, OKAS kodları ve gerçek kanıt parçaları üzerinden bağımsız olarak doğrular.
7. **Güven Kalibrasyonu:** Modelin ham güven puanı; kanıt sayısı, kullanılan kanıtlar, faaliyet eşleşmesi, aday getirme puanı ve doğrulama sorunlarına göre yalnızca aşağı yönlü sınırlandırılır.
8. **Faaliyet–Katılım Ayrımı:** Nihai etiket faaliyet kapsamını gösterir. Belge, personel ve iş deneyimi gibi doğrulanamayan katılım şartları ayrı alanlarda tutulur ve tek başına faaliyet kararını değiştirmez.
9. **İnsan Kapısı:** `inceleme_gerekli` kararları insan incelemesine, `uygun` kararları zorunlu insan onayına gider. Otomatik işlem izni bütün sonuçlarda kapalıdır.
10. **Raporlama:** Profil–ihale adayları, benzersiz ihale adayları, iç kararlar, kısa dış sözleşme ve insan işlem kuyruğu ayrı dosyalarda saklanır.

---

## 🔐 SQL Encoder

SQL Encoder (SQL kodlayıcı), kullanıcının doğal dildeki ihale seçme isteğini güvenli bir PostgreSQL sorgusuna dönüştürür. Ayrı sunucu veya istemci katmanı yoktur; karar zinciri `app/sql_encoder` servisini aynı Python süreci içinde doğrudan çağır.

Qwen serbest SQL yazmaz. Yalnız durum, sonuç sayısı, rastgele sıralama, il, ihale türü, idare, anahtar kelime, OKAS ön eki ve tarih aralığından oluşan tipli arama niyetini üretir. Python bu niyetten parametreli `SELECT` sorgusu derler; SQLGlot AST (soyut sözdizim ağacı) denetimi ve salt okunur PostgreSQL işlemi sorguyu ikinci kez sınırlar.

Aktif seçimde durum filtresine ek olarak `ihale_tarihi >= CURRENT_TIMESTAMP` koşulu zorunludur. SQL ile seçilen İKN'ler FAISS içindeki alt kümeye uygulanır; seçili bir İKN indekste yoksa karar zinciri başlatılmaz.

### Eski tam FAISS indeksini kullanma

Bulduğunuz tam ihale indeksi ile ona ait payload (yük/metadata) dosyası birlikte şu konumda bulunmalıdır:

```text
storage/faiss/ekap_tender_chunks.index
storage/faiss/ekap_tender_chunks_payloads.pkl
```

Yalnız `.index` dosyası yeterli değildir. Profil araması için `storage/faiss_profiles/isbak_company_profiles.index` ve eş payload dosyası da korunmalıdır. Mevcut tam indeks kullanılıyorsa `--recreate` çalıştırılmamalıdır.

Ayrıntılı mimari ve kabul testleri: [SQL Encoder V1](docs/SQL_ENCODER_V1.md).

---

## ✨ Temel Özellikler

- **Bölüm Farkındalıklı Parçalama:** Veritabanındaki mantıksal sütun ayrımları (kapsam, teknik özellik, OKAS) tek bir metin bloğuna sıkıştırılmaz; doğal formlarında parçalanır.
- **FAISS Vektör Arama:** Milyonlarca parçayı milisaniyeler içerisinde anlamsal olarak tarayabilen lokal, bellek-içi (in-memory) L2-normalize edilmiş `IndexFlatIP` vektör mimarisi.
- **Deterministik Önbellekleme:** Gereksiz LLM (Embedding) maliyetini önlemek için `source_hash` takibi yapılarak sadece değişen ihaleler yeniden vektörleştirilir.
- **Python Guardrails:** Yapay zekanın "Blackbox" kararlarını Python katmanında deterministik iş kuralları ile ezer ve yönlendirir.
- **Benzersiz İhale Birleştirmesi:** Aynı İKN'nin farklı profillerdeki eşleşmeleri tek model çağrısında birincil ve destekleyici profiller olarak birleştirilir.
- **Kaynak Tabanlı Negatif Kapsam:** Modelin negatif kapsam beyanı gerçek ihale başlığı ve kanıt parçalarında doğrulanmadan kesin kabul edilmez; OKAS desteği ayrıca kaydedilir.
- **Şeffaf Güven Kalibrasyonu:** Ham model güveni, uygulanan üst sınır ve gerekçeleri raporda ayrı alanlar olarak sunulur.
- **İSBAK İzolasyonu:** İSBAK A.Ş. tarafından açılan kurum-içi ihaleler vektörizasyon öncesinde otomatik olarak dışlanır.

---

## 📊 Nihai Aday Puanı Hesaplaması

Aday ihalelerin uygunluk derecesi, anlamsal arama sonuçlarından elde edilen çeşitli metriklerin ağırlıklandırılmasıyla hesaplanır. Puanlama formülü şu şekildedir:

**Nihai Puan =** 
  `(0.55 × En Yüksek Parça Skoru)` + 
  `(0.20 × En İyi Parçaların Ortalama Skoru)` + 
  `(0.10 × Bölüm Çeşitlilik Skoru)` + 
  `(0.10 × OKAS Destek Skoru)` + 
  `(0.05 × Başlık Destek Skoru)`

**Kavramların Açıklaması:**
- **En Yüksek Parça Skoru:** İhale metninde, profille anlamsal olarak en yüksek benzerliği (FAISS skoru) gösteren tekil bölümün puanı.
- **En İyi Parçaların Ortalama Skoru:** İhaleye ait eşleşen en yüksek puanlı bölümlerin ortalaması (ihaleye ait bağlamın ne kadar tutarlı olduğunu ölçer).
- **Bölüm Çeşitlilik Skoru:** Eşleşen kısımların, ihale dokümanında ne kadar farklı alanlardan (ör. kapsam, teknik şartname, idari gereksinimler) geldiğini ödüllendiren metrik.
- **OKAS Destek Skoru:** İhalenin OKAS (Ortak Kamu Alımları Sözlüğü) kodlarının, profildeki anahtar kelimelerle örtüşme oranı.
- **Başlık Destek Skoru:** İhale başlığının, profildeki anahtar kelimelerle kelime bazlı örtüşme oranı.

Elde edilen nihai puandan, ihale ile kurumsal profil arasında bir uyumsuzluk tespit edilirse (negatif yönlendirme) **0.10** oranında bir **Profil Uyuşmazlığı Cezası** düşülür. 

Nihai Puan hesaplandıktan sonra, eğer puan `0.35` (minimum eşik) altında ise ihale aday havuzuna (LLM değerlendirmesine) alınmaz.

---

## ⚖️ Karar Sınıfları

Sistem, analiz sonucunda aşağıdaki 3 nihai durumdan birini döndürür:

| Karar | Açıklama |
| :--- | :--- |
| 🟢 **uygun** | İhale faaliyet konusu, seçilen İSBAK profilinin ürün/hizmet kapsamıyla kanıtlı biçimde örtüşmektedir. Katılım yeterliliği ayrı kontrol edilir. |
| 🔴 **uygun_degil** | İhale faaliyet konusu seçilen profilin kapsamı dışındadır veya doğrulanmış negatif kapsamla çelişmektedir. |
| 🟡 **inceleme_gerekli** | Faaliyet kapsamı belirsizdir ya da model kararı ile Python doğrulaması arasında kritik bir çelişki vardır. |

---

## 🚀 Kurulum

### Ön Koşullar
- Python 3.11 veya üzeri
- PostgreSQL Veritabanı (Mevcut EKAP verisinin bulunduğu sunucu)
- Ollama (Qwen modelinin yüklü olduğu LLM sunucusu)

### 1. Depoyu Klonlayın
```bash
git clone https://github.com/KeremUUnal/EkapLLM.git
cd EkapLLM
```

### 2. Sanal Ortam Oluşturun ve Bağımlılıkları Yükleyin
```bash
python -m venv .venv
source .venv/bin/activate  # Windows için: .venv\Scripts\activate
pip install -e ".[dev]"
```

### 3. Çevre Değişkenlerini Ayarlayın
`.env.example` dosyasını kopyalayarak `.env` oluşturun:
```bash
cp .env.example .env
```
`.env` dosyanızı kendi ortamınıza (PostgreSQL ve Ollama sunucu bilgileri) göre düzenleyin. 

---

## 💻 Kullanım

### 1. PostgreSQL'den FAISS'e Aktif İhale İndeksleme

Ekap ihalelerinin güncel aktif listesi PostgreSQL'den okunarak `BGE-M3` modelinden geçirilir ve **FAISS** indeksine aktarılır. `SectionAwareChunker` ile parçalanan ihale verileri `faiss-cpu` kullanılarak depolanır. İndeksleme süreci durumları (processing, indexed, vb.) PostgreSQL'deki `software_tender_index_state` tablosu üzerinde tutulmaktadır.

Kullanımı:
```bash
python scripts/build_active_tenders_faiss.py --recreate
```

İndeksi yeniden oluşturmadan güncellemek için:
```bash
python scripts/build_active_tenders_faiss.py
```

### 2. Aday İhaleleri Test Etme
Oluşturulan FAISS indeksini kullanarak profil bazlı arama testi yapar:
```bash
python scripts/test_faiss_retrieval.py
```

### 3. Model Karar Zincirini Çalıştırma
Aday ihaleleri gerçek PostgreSQL kayıtlarıyla yenileyerek karar zincirine sokar:
```bash
PYTHONPATH=. python scripts/run_database_tender_decision_chain.py \
  --limit-per-profile 10 \
  --max-decisions 10 \
  --random-seed 20260806 \
  --report-dir reports/database_decision_test
```

`--max-decisions` benzersiz ihale kararlarını sınırlar; aynı İKN farklı profillerde görünse bile bir kez modele gönderilir.

Yalnız bağlantı, şema ve kaynak yenilemesini model çağrısı olmadan denetlemek için:

```bash
PYTHONPATH=. python scripts/run_database_tender_decision_chain.py \
  --retrieval-only \
  --max-decisions 10 \
  --report-dir reports/database_source_preflight
```

Eski `--source-mode faiss` yolu yalnız geçmiş karşılaştırmalarının yeniden üretimi için korunmuştur. Yeni karar testlerinde veritabanı giriş noktası kullanılmalıdır.

### 4. SQL Encoder ile İhale Seçme

Yalnız güvenli SQL seçimini doğrulamak için:

```bash
PYTHONPATH=. python -u scripts/check_sql_encoder.py \
  --request "Aktif durumdaki 5 rastgele ihaleyi getir." \
  --random-seed 20260806
```

Seçilen ihaleleri mevcut tek-model karar hattına aktarmak için:

```bash
RUN_TIME=$(date +%Y%m%d_%H%M%S)
PYTHONPATH=. python -u scripts/run_sql_encoder_tender_decision_chain.py \
  --request "Aktif durumdaki 5 rastgele ihaleyi getir." \
  --selection-random-seed 20260806 \
  --report-dir "reports/sql_encoder_full_${RUN_TIME}" \
  -- --log-level INFO
```

Model kararını çalıştırmadan PostgreSQL → SQL Encoder → FAISS aktarımını denetlemek için son komuta `-- --retrieval-only --log-level INFO` ekleyin.

### Rapor Çıktıları
Analiz tamamlandığında `reports/` klasörü altında şu temel çıktılar oluşur:

- `profile_tender_candidates.jsonl`: profil bazlı ham aday satırları,
- `unique_tender_candidates.jsonl`: birleştirilmiş benzersiz ihale adayları,
- `tender_model_decisions.jsonl/csv`: tek ihale başına nihai karar,
- `tender_public_decisions.jsonl`: kısa ve insan onay kapılı dış çıktı,
- `tender_review_required.csv`: yalnızca gerçek insan incelemesi gereken kararlar,
- `tender_human_action_queue.csv`: insan incelemesi veya olumlu karar onayı bekleyen bütün kayıtlar,
- `database_source_preflight.json`: kullanılan kaynak şeması ve eksik/aktif olmayan İKN denetimi.
- `sql_encoder_selection.json`: parametreli SQL ve seçilen İKN'ler,
- `sql_encoder_selection_coverage.json`: seçilen İKN'lerin FAISS kapsamı.

---

## 🧪 Test ve Geliştirme

Kod kalitesini ve iş kurallarını test etmek için:

**Uçtan uca ve birim testleri:**
*(Temel testler FAISS taklidi ve veritabanı yalıtımı ile çalışır.)*
```bash
PYTHONPATH=. pytest -q tests/unit/
```

Canlı PostgreSQL kaynak bağlamı testi:

```bash
PYTHONPATH=. pytest -q tests/integration/test_database_decision_context_v4.py
```

SQL Encoder canlı PostgreSQL bütünleşme testi:

```bash
PYTHONPATH=. pytest -q -m external tests/integration/test_sql_encoder_database.py
```

İnsan etiketli karar ölçümü ve hedef sunucu kapasite koşuları için [veritabanı kaynaklı karar hattı belgesine](docs/DB_KAYNAKLI_KARAR_HATTI_V4.md) bakın.

**Kod biçimi ve tür kontrolleri:**
```bash
ruff check app scripts tests
mypy --follow-imports silent app/sql_encoder
```

---

## 📂 Klasör Yapısı

```text
ekap_rag_3model/
├── app/
│   ├── config/              # .env ve ayar yapılandırmaları
│   ├── database/            # PostgreSQL repository sınıfları
│   ├── decision/            # LLM Karar modelleri ve Python Doğrulayıcısı
│   ├── indexing/            # Parçalama ve Hash üreteçleri
│   ├── sql_encoder/         # Güvenli doğal dil → SQL seçim katmanı
│   ├── pipeline/            # Ana analiz akışını yöneten servisler
│   ├── profiles/            # İSBAK Şirket Profil yöneticileri
│   ├── reporting/           # JSON ve CSV rapor çıktı üreticileri
│   ├── retrieval/           # RAG ve Profil Router yöneticileri
│   └── vector_store/        # FAISS index ve önbellek yöneticileri
├── scripts/
│   ├── audit_chunking.py                # Parçalama denetim aracı
│   ├── audit_postgresql_tenders.py      # PostgreSQL veri şeması analiz aracı
│   ├── build_active_tenders_faiss.py    # İhaleleri vektörize eden ana betik
│   ├── check_sql_encoder.py              # SQL Encoder duman testi
│   ├── run_sql_encoder_tender_decision_chain.py  # SQL seçimi + karar zinciri
│   ├── run_tender_decision_chain.py     # LLM karar zincirini çalıştıran betik
│   └── test_faiss_retrieval.py          # Arama algoritmalarını test eden betik
├── tests/
│   └── unit/                # Bağımsız iş mantığı (birim) testleri
├── docs/                    # Mimari belgeler (POSTGRESQL_AKTIF_IHALE_FAISS_MIMARISI.md vb.)
├── reports/                 # Analiz çıktı dizini
├── .env                     # Ortam değişkenleri
└── pyproject.toml           # Proje bağımlılıkları ve meta bilgileri
```

---

## ⚠️ Sınırlamalar ve Notlar

- **Ollama Bağımlılığı:** Qwen (`qwen3.5:4b-q4_K_M`) modelinin ilgili Ollama sunucusunda kurulu ve çalışır durumda olması zorunludur.
- **Donanım İhtiyacı:** Sistem tamamen RAM (CPU) üzerinde çalışacak şekilde yapılandırılmıştır. Ekran kartı (GPU) kullanımı tamamen devredışı bırakılmıştır (`EMBEDDING_DEVICE=cpu`). BGE-M3 gömme modelinin çalışabilmesi için yeterli miktarda sistem belleğine (RAM) ihtiyaç vardır.
- **Ağ İhtiyacı:** Güncel verilerin alınması için PostgreSQL sunucusuna erişim zorunludur (Testler hariç). Qdrant bağımlılığı kaldırılmış olup yalnızca FAISS kullanılmaktadır.
- **Kısım Kaynağı:** Mevcut veritabanında ayrı bir kısım tablosu yoktur. Kısım başlıkları kapsam, ilan ve teknik özellik metinlerinden kaynak kimliği korunarak çıkarılır. Kısmi teklif açık olduğu hâlde liste güvenilir çıkarılamazsa sonuç `inceleme_gerekli` olur.
- **Olumlu Karar Güvenliği:** `uygun` sonucu otomatik teklif veya başka bir işleme bağlanamaz; insan onayı zorunludur.
