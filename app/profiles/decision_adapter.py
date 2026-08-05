from typing import Literal

from pydantic import BaseModel, Field

NewDecisionType = Literal["uygun", "uygun_degil", "inceleme_gerekli"]


class FinalDecision(BaseModel):
    karar: NewDecisionType = Field(..., description="Üçlü sözleşmeye uygun nihai karar.")
    gerekce: str = Field(..., description="Kararın açıklayıcı gerekçesi.")
    uyarlama_yapildi: bool = Field(
        default=False,
        description="Eski bir karardan (veya kuraldan) yeni sözleşmeye uyarlama yapıldı mı?",
    )
    uyarlama_nedeni: str | None = Field(default=None, description="Uyarlama yapıldıysa nedeni.")


class DecisionAdapter:
    @staticmethod
    def adapt(
        raw_karar: str,
        puan: float = 0.0,
        kritik_veri_eksik: bool = False,
        model_celiskisi: bool = False,
        zorunlu_sart_saglandi: bool | None = None,
        gerekce: str = "",
    ) -> FinalDecision:
        """
        Eski karar kurallarını veya LLM çıktılarını yeni üçlü sözleşmeye
        (uygun, uygun_degil, inceleme_gerekli) dönüştürür.
        """
        yeni_karar: NewDecisionType = "inceleme_gerekli"
        uyarlama_yapildi = False
        uyarlama_nedeni = []

        # 1. Zorunlu şart kontrolü (En üst öncelik)
        if zorunlu_sart_saglandi is False:
            yeni_karar = "uygun_degil"
            uyarlama_nedeni.append("Zorunlu kriter açıkça karşılanmıyor.")
            uyarlama_yapildi = True
            return FinalDecision(
                karar=yeni_karar,
                gerekce=gerekce,
                uyarlama_yapildi=uyarlama_yapildi,
                uyarlama_nedeni="; ".join(uyarlama_nedeni),
            )

        if zorunlu_sart_saglandi is None:
            yeni_karar = "inceleme_gerekli"
            uyarlama_nedeni.append("Zorunlu kriter durumu bilinmiyor.")
            uyarlama_yapildi = True
            return FinalDecision(
                karar=yeni_karar,
                gerekce=gerekce,
                uyarlama_yapildi=uyarlama_yapildi,
                uyarlama_nedeni="; ".join(uyarlama_nedeni),
            )

        # 2. Kritik veri ve çelişki
        if kritik_veri_eksik:
            yeni_karar = "inceleme_gerekli"
            uyarlama_nedeni.append("Kritik veri eksikliği var.")
            uyarlama_yapildi = True
            return FinalDecision(
                karar=yeni_karar,
                gerekce=gerekce,
                uyarlama_yapildi=uyarlama_yapildi,
                uyarlama_nedeni="; ".join(uyarlama_nedeni),
            )

        if model_celiskisi:
            yeni_karar = "inceleme_gerekli"
            uyarlama_nedeni.append("Model çelişkisi tespit edildi.")
            uyarlama_yapildi = True
            return FinalDecision(
                karar=yeni_karar,
                gerekce=gerekce,
                uyarlama_yapildi=uyarlama_yapildi,
                uyarlama_nedeni="; ".join(uyarlama_nedeni),
            )

        # 3. Puan Sınırları ve Eski Kararlar
        raw_k = raw_karar.lower().strip()
        if raw_k in ["kosullu_uygun", "koşullu_uygun"]:
            yeni_karar = "inceleme_gerekli"
            uyarlama_nedeni.append("Eski kosullu_uygun kararı inceleme_gerekli'ye dönüştürüldü.")
            uyarlama_yapildi = True
        elif 65 <= puan < 80:
            yeni_karar = "inceleme_gerekli"
            uyarlama_nedeni.append(f"Sınırda kalan puan ({puan}) inceleme_gerekli kabul edildi.")
            uyarlama_yapildi = True
        elif raw_k in ["ilgisiz", "uygun_degil", "uygun_değil"]:
            yeni_karar = "uygun_degil"
            if raw_k == "ilgisiz":
                uyarlama_nedeni.append("Eski ilgisiz kararı uygun_degil'e dönüştürüldü.")
                uyarlama_yapildi = True
        elif raw_k in ["doğrudan_uygun", "dogrudan_uygun", "uygun"]:
            if puan >= 80:
                yeni_karar = "uygun"
                if raw_k != "uygun":
                    uyarlama_nedeni.append(f"Eski {raw_k} kararı uygun'a dönüştürüldü.")
                    uyarlama_yapildi = True
            else:
                yeni_karar = "inceleme_gerekli"
                uyarlama_nedeni.append(
                    f"Puan ({puan}) 80'in altında olduğu için uygun kararı inceleme_gerekli'ye düşürüldü."
                )
                uyarlama_yapildi = True
        else:
            yeni_karar = "inceleme_gerekli"
            uyarlama_nedeni.append(
                f"Tanımsız karar ({raw_k}) inceleme_gerekli olarak kabul edildi."
            )
            uyarlama_yapildi = True

        return FinalDecision(
            karar=yeni_karar,
            gerekce=gerekce,
            uyarlama_yapildi=uyarlama_yapildi,
            uyarlama_nedeni="; ".join(uyarlama_nedeni) if uyarlama_yapildi else None,
        )
