# İSBAK Alt Profil Mimarisi

Bu yapı, İSBAK'ın yalnızca yazılım kapasitesini değil; akıllı ulaşım,
entegre akıllı şehir çözümleri, planlama, elektronik-donanım, haberleşme,
bilgi güvenliği, kurulum, bakım ve danışmanlık alanlarını kategori bazlı
olarak temsil eder.

## İşleyiş

Arka uç `kategori_kodu` alanını gönderir. Profil yükleyici:

1. Kurumsal ana profili,
2. Birincil kategori profilini,
3. Seçilen ikincil profilleri,
4. Kayıtlı destekleyici profilleri,
5. Birincil profilin değerlendirme kurallarını

tek bir değerlendirme bağlamında birleştirir.

## Örnek

```python
from app.company_profiles import IsbakProfileLoader

loader = IsbakProfileLoader()
context = loader.build_evaluation_context(
    primary_code="AUS-02",
    secondary_codes=["ENT-02"],
)
```

## Doğrulama

```bash
PYTHONPATH=. python scripts/validate_isbak_profiles.py
PYTHONPATH=. pytest -q tests/test_isbak_profile_loader.py
```

## Kurum içi veri politikası

Personel sayısı, belge numarası, geçerlilik tarihi, mali kapasite, proje
tutarı, iş bitirme belgesi ve ekipman envanteri tahmin edilmemiştir.
Bu alanlar kurum içi doğrulanmış kayıtlarla doldurulmalıdır.
