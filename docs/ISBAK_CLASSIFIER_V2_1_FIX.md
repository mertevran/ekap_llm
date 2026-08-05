# İSBAK Sınıflandırıcı V2.1 Düzeltmesi

## Sorun

`inceleme_gerekli` durumundaki profiller `profile_codes` içine ekleniyordu.
Bu nedenle yalnızca geniş OKAS eşleşmesi bulunan TEK-02, kesin aday profil
gibi davranıyordu.

## Düzeltme

- `profile_codes`: yalnızca `guclu_eslesme` ve `kosullu_eslesme`
- `review_profile_codes`: yalnızca `inceleme_gerekli`
- `primary_profile_code`: yalnızca kesin aday profillerden seçilir
- `evaluation_profile_codes`: inceleme profilleri için oluşturulmaz

Böylece inceleme kayıtları Qdrant filtrelerine ve otomatik model bağlamına
kesin adaymış gibi girmez.
