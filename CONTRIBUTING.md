# Katkı rehberi

Bu projede en pahalı hata yanlış karar değil, **yorumlanamayan ölçümdür.** Aşağıdaki
kurallar bu yüzden var.

## Değişiklik yapmadan önce

```bash
python -m pytest tests/
```

207 testin tamamı geçmelidir. Testler Ollama, GPU veya veritabanı sunucusu
gerektirmez; saniyeler içinde biter.

## Altın kurallar

**1. Tek değişken kuralı.** Aynı anda iki şey değiştirip tek ölçüm almayın. Sonucun
hangi değişiklikten geldiğini ayırt edemezsiniz. `.env` içindeki deneysel bayrakları
tek tek açın.

**2. Kaçırma yönü kutsaldır.** Uygun bir ihaleyi elemek, uygun olmayanı listeye
almaktan pahalıdır. Belirsizlikte karar **ortaya** (`belirsiz`) düşürülür, uca
(`uygun_degil`) değil. Yeni bir eleme kapısı ekliyorsanız önce ölçün.

**3. Şema alan sırasına dokunmayın.** `app/decision/schemas.py` içindeki alan sırası
bilinçlidir: önce gerekçe, en son karar. Dil modeli alanları şemadaki sırayla üretir;
kararı öne almak modelin gerekçesiz karar vermesine yol açar. Dosyanın başındaki
açıklamayı okumadan değiştirmeyin.

**4. Geri alınan bir değişikliğin koruyucu testi olsun.** "Bu denendi, zarar verdi,
tekrar eklenmesin" bilgisi insanın aklında değil, kodda yaşamalıdır.

**5. Ölçüm koşularında `LLM_SEED` değiştirilmez.** Aksi halde koşular kıyaslanamaz.

## Yeni ayar eklerken

Ayarı `app/config/settings.py` içinde tanımlayın ve `description` alanına şunları
yazın: ne yaptığı, varsayılanının neden o olduğu ve varsa hangi ölçüme dayandığı.
Aynı ayarı `.env.example` içine de örneklendirerek ekleyin.

## Kod stili

- Yorumlar **neden** sorusunu cevaplasın, **ne** sorusunu değil. Kodun ne yaptığı
  zaten kodda yazıyor.
- Bir kural ölçülmüş bir hatadan doğduysa o hatayı yorumda anlatın. Bu proje boyunca
  en değerli belge budur.
- Kısaltmalardan kaçının; değişken adları Türkçe ve açıklayıcıdır.

## Sonuç dosyaları

`Sonuclar/` altındaki ölçüm çıktıları **bilerek** versiyon kontrolündedir; regresyon
geçmişi projenin kaydıdır. Silmeyin, üzerine yazmayın — yeni bir sürüm numarasıyla
ekleyin.
