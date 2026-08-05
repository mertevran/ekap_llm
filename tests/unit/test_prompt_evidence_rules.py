from app.decision.ollama_decision_model import GEMMA_CORRECTION_MSG, PROMPTS, QWEN_CORRECTION_MSG


def test_prompt_semantic_rules_present():
    targets = [
        PROMPTS["isbak_qwen_decision_v3"],
        PROMPTS["isbak_gemma_review_v3"],
        PROMPTS["isbak_gemma_review_compact"],
        QWEN_CORRECTION_MSG,
        GEMMA_CORRECTION_MSG
    ]

    for text in targets:
        # A & B. Kanıt eksikliği != karşılanmıyor, karsilanmiyor vs bilinmiyor
        assert "Kanıt bulunmaması tek başına karsilanmiyor değildir" in text or "kanıt bulunmaması yeterli değildir" in text or "kanıt yoksa ve negatif kanıt da yoksa status=bilinmiyor kullan" in text or "karsilanmiyor olarak işaretlediysen bunu düzelt" in text

        # C. Fiyat avantajı
        assert "fiyat avantajı" in text.lower()

        # D. Elektronik eksiltme
        assert "elektronik eksiltme" in text.lower()

        # E. Eksik kanıtları uygunluk gerekçelerinden çıkarma
        assert "eksik_kanitlar" in text or "Eksik kanıtları uygunluk_gerekceleri" in text or "belge eksikliği" in text

        # F. ZOR-* genel kuralları
        assert "ZOR" in text

def test_prompt_no_automatic_unsuitable():
    for text in [PROMPTS["isbak_qwen_decision_v3"], PROMPTS["isbak_gemma_review_v3"]]:
        assert "Bilinmiyor durumu otomatik uygun_degil üretmemelidir" in text
