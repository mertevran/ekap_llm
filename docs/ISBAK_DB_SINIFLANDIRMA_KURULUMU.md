# İSBAK Veritabanı Sınıflandırma Paketi

Bu paket, mevcut EKAP projesine GitHub tarafına dokunmadan yerel olarak
İSBAK çoklu alt profil sınıflandırmasını ekler.

## Eklenen dosyalar

- `app/classification/isbak_tender_profile_classifier.py`
- `app/indexing/isbak_tender_payload.py`
- `scripts/classify_isbak_tenders.py`
- `tools/update_isbak_profile_signals.py`
- iki birim sınaması
- profil sinyal sözlüğü

## Güvenli çalışma biçimi

`classify_isbak_tenders.py` yalnızca veritabanından okur. Qdrant'a yazmaz,
veritabanında güncelleme veya silme yapmaz.

## Kurulum

```bash
unzip isbak_db_siniflandirma_paketi_v1.zip
bash isbak_db_siniflandirma_paketi/install_local.sh   /mnt/c/Users/merte/OneDrive/Masaüstü/ekap_rag_3model
```

## Sınamalar

```bash
cd /mnt/c/Users/merte/OneDrive/Masaüstü/ekap_rag_3model

PYTHONPATH=. pytest -q   tests/test_isbak_profile_loader.py   tests/unit/test_isbak_tender_profile_classifier.py   tests/unit/test_isbak_tender_payload.py
```

## İlk veritabanı denemesi

```bash
PYTHONPATH=. python scripts/classify_isbak_tenders.py   --limit 25   --batch-size 25   --show 25
```

Çıktılar:

- `reports/isbak_profile_classification/isbak_tender_profiles.json`
- `reports/isbak_profile_classification/isbak_tender_profiles.csv`

Bu sürüm Qdrant indekslemesi yapmaz. İlk 25 gerçek ihale sonucu kontrol
edildikten sonra Qdrant eşitleme paketi hazırlanmalıdır.
