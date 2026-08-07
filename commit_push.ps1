$ErrorActionPreference = "Stop"

git init
git remote add origin https://github.com/Isbak-Ekap/LLM.git
git fetch origin "dev-mert(tek_model)"
git symbolic-ref HEAD refs/heads/dev-mert(tek_model)
git update-ref refs/heads/dev-mert(tek_model) FETCH_HEAD
git add .

$commitMsg = @"
feat: SQL Encoder ve artımlı FAISS indeksleme entegrasyonunu tamamla

- Yeni ihaleler BGE-M3 ile embedding (gömme) edilerek mevcut FAISS indeksine eklendi
- İSBAK’a ait ihaleler embedding yapılmadan skipped_own_tender olarak takip tablosuna kaydedildi
- tender_index_state durum yönetimi upsert (varsa güncelle, yoksa ekle) mantığıyla düzeltildi
- SQL Encoder doğal dil isteğinden güvenli sorgu niyeti çıkaracak şekilde sisteme entegre edildi
- SQL sorguları Python tarafında parametreli olarak oluşturulup SQLGlot ile doğrulanacak yapı kullanıldı
- SQL Encoder ile seçilen ihalelerin FAISS retrieval (bilgi getirme) ve karar zincirine aktarımı tamamlandı
- Qwen3.5:4b modeli hem SQL niyet çıkarımı hem de ihale uygunluk kararında tek model mimarisinde kullanıldı
- Güncel ihale FAISS indeksi 119226 vektör ve profil FAISS indeksi 80 vektör olarak doğrulandı
"@

git commit -m $commitMsg
git push origin "dev-mert(tek_model)"
