"""
Ollama istemcisi — yapılandırılmış (şemaya bağlı) JSON çıktısı.

`ollama` python paketi BİLEREK kullanılmıyor: import anında global bir istemci kuruyor
ve proxy'li ortamlarda import'u patlatıyor (sandbox'ta bizzat yaşandı). REST API'ye
httpx ile gidiyoruz — `format` alanına Pydantic'in ürettiği JSON şemasını verince
Ollama alanları ŞEMA SIRASIYLA üretiyor, yani schemas.py'deki alan sırası kararı
doğrudan etkiliyor (bkz. o dosyanın başlığı).

Sağlamlık: ağ hataları için yeniden deneme + geri çekilme; JSON/şema hatası için bir
düzeltme turu. İkisi de tükenirse `LlmHatasi` fırlatır — çağıran taraf bunu yakalayıp
"belirsiz"e düşürür, sessizce yanlış karar üretmez.

=============================================================================
DÜŞÜNME MODU (`think`) — 29.07.2026'da EKLENDİ, HENÜZ ÖLÇÜLMEDİ
=============================================================================
qwen3 hibrit bir akıl yürütme modeli: Ollama'ya `think` alanı gönderilmezse model
KENDİ varsayılanına göre davranır ve akıl yürütmesini `message.thinking` alanına
yazar. Bu alan bugüne kadar OKUNMADAN ATILIYORDU — yani model muhtemelen zaten
zincirleme düşünüyordu, biz ne düşündüğünü hiç görmedik ve açık/kapalı olmasının
etkisini hiç ölçmedik. v1-v5 dahil TÜM ölçüm geçmişi bu bilinmeyenin üstüne kurulu.

Şimdi:
  - `dusunme=None`  -> `think` gönderilmez (ESKİ DAVRANIŞ, varsayılan; geriye dönük uyumlu)
  - `dusunme=False` -> düşünme kapalı; üretim kısalır
  - `dusunme=True`  -> düşünme açık ve İZLENEBİLİR

Her durumda `message.thinking` yakalanıp `son_dusunce`ye konur. Model düşünmeyi
desteklemiyorsa Ollama 400 döner; bu durumda `think` düşürülüp bir kez daha denenir
(program çökmez — projenin ilkesi: ölçülmemiş yapılandırma bir uyarıdır, hata değildir).
=============================================================================
"""

from __future__ import annotations

import json
import logging
import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LlmHatasi(RuntimeError):
    pass


_NS = 1_000_000_000  # Ollama süreleri nanosaniye döndürür


def _kullanim(yanit: dict) -> dict[str, float]:
    """Ollama yanıtındaki token/süre sayaçlarını okunur hale getirir.

    Alanların hepsi OPSİYONEL — Ollama sürümü ya da model değişirse eksik gelebilir.
    Eksik alan ölçümü durdurmaz, sadece o satır yazılmaz: bu bir teşhis verisi,
    karar yolunda değil.
    """
    def sayi(anahtar: str) -> float | None:
        d = yanit.get(anahtar)
        return float(d) if isinstance(d, (int, float)) else None

    girdi_tok, cikti_tok = sayi("prompt_eval_count"), sayi("eval_count")
    girdi_sn = (sayi("prompt_eval_duration") or 0) / _NS
    cikti_sn = (sayi("eval_duration") or 0) / _NS
    toplam_sn = (sayi("total_duration") or 0) / _NS

    k: dict[str, float] = {}
    if girdi_tok is not None:
        k["girdi_token"] = girdi_tok
    if cikti_tok is not None:
        k["cikti_token"] = cikti_tok
    if girdi_sn:
        k["girdi_sn"] = round(girdi_sn, 2)
    if cikti_sn:
        k["cikti_sn"] = round(cikti_sn, 2)
    if toplam_sn:
        k["toplam_sn"] = round(toplam_sn, 2)
    # Asıl bakılacak sayı: üretim hızı. CPU'da bu, saniyede birkaç token'a
    # düşüyor ve toplam süreyi o belirliyor.
    if cikti_tok and cikti_sn:
        k["cikti_token_sn"] = round(cikti_tok / cikti_sn, 1)
    if girdi_tok and girdi_sn:
        k["girdi_token_sn"] = round(girdi_tok / girdi_sn, 1)
    # Sürenin yüzde kaçı üretimde geçti — prompt kısaltmanın tavanını verir.
    if cikti_sn and toplam_sn:
        k["uretim_payi"] = round(100 * cikti_sn / toplam_sn, 1)
    return k


class OllamaIstemcisi:
    def __init__(
        self,
        model: str,
        host: str = "http://localhost:11434",
        timeout: float = 180.0,
        max_deneme: int = 2,
        geri_cekilme_sn: float = 1.0,
        num_ctx: int = 8192,
        seed: int | None = 42,
        dusunme: bool | None = None,
        num_gpu: int | None = None,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.max_deneme = max_deneme
        self.geri_cekilme_sn = geri_cekilme_sn
        self.num_ctx = num_ctx
        self.seed = seed
        self.dusunme = dusunme
        self.num_gpu = num_gpu

        # Son çağrının akıl yürütmesi. Ölçüm/teşhis içindir; çağıran taraf okumak
        # ZORUNDA DEĞİL. Her `yapisal_uret` çağrısında sıfırlanıp yeniden doldurulur.
        self.son_dusunce: str = ""
        self.dusunme_destekleniyor: bool = True

        # SON ÇAĞRININ TOKEN MUHASEBESİ (04.08.2026'da eklendi).
        #
        # Ollama /api/chat yanıtında bu sayaçlar ZATEN geliyordu ve okunmadan
        # atılıyordu. Sonuç: "prompt'u mu kısaltalım, düşünmeyi mi kapatalım,
        # kontrastif örnek mi azaltalım" tartışmasının tamamı karakter sayısından
        # token TAHMİN ederek yürüdü. Gerek yok — sayı sunucudan geliyor.
        #
        # CPU'da ayrım belirleyici: prefill (girdi işleme) paralelleşir, decode
        # (üretim) token token ilerler ve asıl yavaş olan odur. Hangi ucun ne kadar
        # sürdüğü bilinmeden yapılan her optimizasyon körlemesine.
        #
        #   girdi_token / girdi_sn   -> prefill
        #   cikti_token / cikti_sn   -> decode  (düşünme dahil)
        self.son_kullanim: dict[str, float] = {}

    def yapisal_uret(self, *, sistem: str, kullanici: str, sema: type[T]) -> T:
        """Verilen Pydantic şemasına uyan bir yanıt üretir."""
        import httpx  # tembel

        # TEKRARLANABİLİRLİK — `temperature: 0` TEK BAŞINA YETMİYOR.
        #
        # 28.07.2026'da ölçüldü: v4 ve v5 koşuları arasında SADECE bir son-işleme
        # kuralı değişmişken (prompt, retrieval ve veri birebir aynı) 15 ihalenin
        # 10'unun çıktısı değişti. Skorlar savruldu (0.5049 -> 0.75, 0.47 -> 0.20)
        # ve bir karar tamamen döndü (belirsiz -> uygun). Sebep: Ollama'ya sabit bir
        # `seed` verilmemişti. Bu, v1-v5 arasındaki tüm ince karşılaştırmaları
        # şüpheli hale getirdi — tek ihalelik farklar (n=15'te %6,7) gürültüden
        # ayırt edilemez oldu.
        #
        # UYARI: seed tek başına da yetmeyebilir. 6GB VRAM'de qwen3:8b kısmen CPU'ya
        # taşıyor; katman bölünmesi koşular arasında değişirse kayan nokta toplama
        # sırası değişir ve seed'e rağmen küçük sapmalar kalabilir. Bu yüzden
        # VARSAYMAK YERİNE ÖLÇÜN: `python evaluation/tutarlilik_testi.py` (plan T14).
        secenekler: dict = {"temperature": 0, "num_ctx": self.num_ctx}
        if self.seed is not None:
            secenekler["seed"] = self.seed
        # num_gpu=0 -> tamamen CPU. GPU'suz sunucu senaryosunu ve kararların
        # donanıma bağlı olup olmadığını ölçmek için. `None` ise gönderilmez;
        # Ollama kendi yerleştirme kararını verir (normal çalışma).
        if self.num_gpu is not None:
            secenekler["num_gpu"] = self.num_gpu

        govde = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": sistem},
                {"role": "user", "content": kullanici},
            ],
            "format": sema.model_json_schema(),
            "stream": False,
            "options": secenekler,
        }
        # `None` ise anahtar HİÇ gönderilmez -> modelin kendi varsayılanı (eski davranış).
        if self.dusunme is not None and self.dusunme_destekleniyor:
            govde["think"] = self.dusunme

        self.son_dusunce = ""
        self.son_kullanim = {}
        son_hata: Exception | None = None

        for duzeltme in range(2):  # 0 = ilk deneme, 1 = "sadece geçerli JSON" hatırlatması
            ham, dusunce = self._istek(httpx, govde)
            if dusunce:
                self.son_dusunce = dusunce
            try:
                return sema.model_validate_json(ham)
            except (ValidationError, json.JSONDecodeError) as e:
                son_hata = e
                logger.warning("Şema/JSON hatası (düzeltme turu %d): %s", duzeltme, e)
                if duzeltme == 0:
                    govde["messages"] = list(govde["messages"]) + [
                        {"role": "assistant", "content": ham[:2000]},
                        {
                            "role": "user",
                            "content": (
                                "Bu yanıt verilen JSON şemasına uymuyor. SADECE şemaya uyan "
                                "geçerli bir JSON üret; açıklama, markdown, kod bloğu ekleme."
                            ),
                        },
                    ]

        raise LlmHatasi(f"{self.model} şemaya uyan çıktı üretemedi: {son_hata}")

    def _istek(self, httpx, govde: dict) -> tuple[str, str]:
        """(içerik, düşünce) döndürür. Düşünce yoksa boş string."""
        son_hata: Exception | None = None

        # BAĞLANTI ile OKUMA bütçesi AYRI.
        #
        # Tek bir `timeout=X` httpx'te dört fazın (connect/read/write/pool) hepsine
        # aynı değeri verir. CPU koşusunda okuma bütçesini 1 saate çıkarmak gerekiyor;
        # ama aynı değer connect'e de uygulanırsa Ollama HİÇ ayakta değilken script
        # bir saat boyunca donar. Bağlantı 10 saniyede kesilir: sunucu kapalıysa
        # anında anlaşılır, açıksa çıkarım istediği kadar sürebilir.
        zaman_asimi = httpx.Timeout(self.timeout, connect=10.0)

        for deneme in range(1, self.max_deneme + 1):
            try:
                with httpx.Client(timeout=zaman_asimi) as c:
                    y = c.post(f"{self.host}/api/chat", json=govde)
                    if y.status_code == 404:
                        raise LlmHatasi(
                            f"Model '{self.model}' bulunamadı (404). "
                            f"`ollama pull {self.model}` ile indirin."
                        )
                    # Model düşünmeyi desteklemiyorsa Ollama 400 döner. Ölçümü
                    # durdurmak yerine `think`i düşürüp devam ediyoruz.
                    if y.status_code == 400 and "think" in govde:
                        if "think" in y.text.lower():
                            logger.warning(
                                "'%s' düşünme modunu desteklemiyor; `think` düşürülüp "
                                "devam ediliyor. (Ollama: %s)",
                                self.model,
                                y.text[:200],
                            )
                            self.dusunme_destekleniyor = False
                            govde.pop("think", None)
                            continue
                    y.raise_for_status()
                    govde_yanit = y.json()
                    self.son_kullanim = _kullanim(govde_yanit)
                    mesaj = govde_yanit.get("message", {})
                    return mesaj.get("content", ""), (mesaj.get("thinking") or "")
            except LlmHatasi:
                raise
            except httpx.ReadTimeout as e:
                # ZAMAN AŞIMINDA YENİDEN DENEME YOK — bilerek.
                #
                # Ollama bir modele gelen istekleri sıraya alır. Okuma zaman aşımı
                # yaşandığında sunucu ilk isteği HÂLÂ ÜRETİYOR olur; ikinci deneme
                # kuyruğa girer, önce birincinin bitmesini bekler, sonra kendi
                # üretimini yapar. Yani yeniden deneme bir zaman aşımını çözmez,
                # bekleme süresini KATLAR ve asıl sorunu (bütçe yetersiz) gizler.
                # CPU koşusunda tam olarak bu yaşandı: 180 sn'lik iki deneme,
                # tek bir çıkarımın gerçekte ne kadar sürdüğünü hiç göstermeden
                # "Ollama'ya ulaşılamadı" diye bitiyordu — sunucu ayaktayken.
                raise LlmHatasi(
                    f"Ollama {self.timeout:.0f} saniyede yanıt vermedi (model: {self.model}, "
                    f"num_gpu={self.num_gpu}). Sunucu AYAKTA, çıkarım bütçeyi aştı.\n"
                    f"  · CPU'da (LLM_NUM_GPU=0) tek çıkarım 10-40 dakika sürebilir; "
                    f".env'de LLM_TIMEOUT_SN değerini yükseltin (ör. 3600).\n"
                    f"  · num_ctx={self.num_ctx} düşürmek üretimi hızlandırır.\n"
                    f"  · Süreyi ölçmek için önce TEK ihale koşturun: "
                    f"scripts/tara_ve_kaydet.py --ikn <IKN>"
                ) from e
            except Exception as e:
                son_hata = e
                logger.warning("Ollama ağ hatası (deneme %d/%d): %s", deneme, self.max_deneme, e)
                if deneme < self.max_deneme:
                    time.sleep(self.geri_cekilme_sn)
        raise LlmHatasi(f"Ollama'ya ulaşılamadı ({self.host}): {son_hata}")

    def hazir_mi(self) -> tuple[bool, str]:
        """check_setup için: sunucu ayakta mı ve model yüklü mü?"""
        import httpx

        try:
            with httpx.Client(timeout=10.0) as c:
                y = c.get(f"{self.host}/api/tags")
                y.raise_for_status()
                modeller = [m["name"] for m in y.json().get("models", [])]
        except Exception as e:
            return False, f"Ollama'ya ulaşılamıyor ({self.host}): {e}"
        if self.model not in modeller:
            return False, f"'{self.model}' yüklü değil. Mevcut: {', '.join(modeller) or '(yok)'}"
        return True, f"'{self.model}' hazır"
