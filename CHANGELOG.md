# Değişiklik Günlüğü

## 1.1.0 — Doğrudan SQL Encoder

- Doğal dildeki ihale seçme isteğini tipli arama niyetine dönüştüren SQL Encoder eklendi.
- Python tarafında parametreli `SELECT` derleyicisi, SQLGlot AST (soyut sözdizim ağacı) denetimi ve salt okunur PostgreSQL işlemi eklendi.
- Ayrı sunucu veya istemci katmanı olmadan SQL Encoder doğrudan mevcut karar zincirine bağlandı.
- SQL ile seçilen İKN'leri mevcut FAISS indeksi içinde sınırlayan alt-küme araması ve eksiksiz kapsam kapısı eklendi.
- Mevcut tek-model karar mantığı, profil yapısı, PostgreSQL kaynak yenilemesi ve FAISS dosya biçimi korunmuştur.

## Güncel geliştirmeler

- Karar bağlamı için PostgreSQL kaynak yenilemesi eklendi; gerçek ihale türü, bütün OKAS kayıtları, kısmi teklif bilgisi, kaynak izli kısımlar ve teknik özellikler modele taşınıyor.
- Kısmi ihalelerde `uygun_kisimlar` sözleşmesi ve kaynak kısım doğrulaması eklendi.
- Olumlu kararlar zorunlu insan onayına bağlandı; bütün otomatik işlem izinleri kapatıldı.
- Düzenli ifade ön filtresi sonrasında açık vakada 1, belirsiz vakada 3, karma/kısmi vakada 4 kanıt seçimi eklendi.
- Kısa v4 model çıktı sözleşmesi, insan etiketli karar ölçümü ve hedef sunucu kapasite/dayanıklılık koşucusu eklendi.

- Ana karar zinciri Qwen + Python doğrulaması kullanan tek modelli yapıya geçirildi; Gemma artık Aşama 4 çalıştırıcısında yüklenmiyor.
- Profil negatif terimleri ihale başlığı, OKAS ve gerçek kanıt parçalarıyla Python katmanında doğrulanır hâle getirildi.
- Faaliyet uygunluğu ile belge/personel/iş deneyimi gibi katılım yeterliliği alanları karar ve raporlarda ayrıldı.
- Modelin ham güven puanına kanıt, faaliyet eşleşmesi, aday getirme puanı ve doğrulama sonucuna göre şeffaf Python üst sınırları eklendi.
- Aynı İKN'nin farklı profiller altında tekrar değerlendirilmesi kaldırıldı; profil eşleşmeleri tek benzersiz ihale kaydında birleştirildi.
- İdare adı, üst seviye FAISS yük verisindeki `authority_name` alanından da okunacak şekilde düzeltildi.
- İnsan inceleme raporuna `human_review_required`, ayrı katılım incelemesi ve güven kalibrasyonu alanları eklendi.
- Toplu karar tamamlandığında Qwen modelinin Ollama belleğinden çıkarılması eklendi.
- Aktif ihalelerin FAISS'e (benzerlik arama kütüphanesi) indekslenmesi eklendi.
- Sistem CUDA bağımlılıklarından arındırıldı, tüm işlemlerin (BGE-M3 embedding dahil) yalnızca CPU (RAM) üzerinde çalışması sağlandı.
- Qwen 3.5 ve Gemma 4 model ayarları güncellendi.
- `tender_index_state` takip tablosu oluşturuldu.
- CI/CD (sürekli entegrasyon / sürekli teslim) Ruff ve test kontrolleri güncellendi.
