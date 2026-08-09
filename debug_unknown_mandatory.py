from app.decision.isbak_decision_pipeline import IsbakDecisionPipeline
from app.decision.models import CriterionResult, ModelDecision
from app.decision.validator import IsbakDeterministicValidator

def decision(**overrides):
    data = dict(
        model_name="test",
        decision="uygun",
        confidence=0.8,
        birincil_profil_kodu="ENT-05",
        ikincil_profil_kodlari=[],
        uygunluk_gerekceleri=["Akıllı aydınlatma kapsamı örtüşüyor."],
        uygunsuzluk_gerekceleri=[],
        zorunlu_kriter_sonuclari=[],
        faaliyet_eslesmesi="guclu",
        negatif_kapsam_cakismasi=False,
        katilim_yeterliligi_durumu="dogrulanmadi",
        kritik_faaliyet_belirsizlikleri=[],
        dogrulanamayan_katilim_sartlari=["İş deneyim belgesi doğrulanamadı."],
        kullanilan_chunk_idleri=["chk_1"],
    )
    data.update(overrides)
    return ModelDecision(**data)

class _CriterionDecisionModel:
    name = "test-model"
    def __init__(self, criterion_status: str) -> None:
        self.criterion_status = criterion_status
    def analyze(self, **kwargs):
        criterion = CriterionResult(
            criterion_id="iso_9001",
            description="ISO 9001 belgesi",
            status=self.criterion_status,
            evidence_chunk_ids=["chk_1"],
        )
        return decision(zorunlu_kriter_sonuclari=[criterion])

pipeline = IsbakDecisionPipeline(
    primary_model=_CriterionDecisionModel("bilinmiyor"),
    validator=IsbakDeterministicValidator(),
)

result = pipeline.run(
    tender_id="1", ikn="2026/1", tender_name="Test ihalesi", authority_name="Test idaresi",
    category_code="ENT-05", primary_profile_code="ENT-05", secondary_profile_codes=[],
    tender_context="İhale bağlamı", company_context="Şirket bağlamı", evaluation_rules={},
    evidence_count=1, valid_chunk_ids=["chk_1"]
)

print(f"activity_decision: {result.activity_decision}")
print(f"final_decision: {result.final_decision}")
print(f"merge_rule: {result.merge_rule}")
print(f"participation_status: {result.katilim_yeterliligi_durumu}")

# Debug variables:
print("--- DEBUG ---")
print(f"primary_decision: {result.primary_model.decision}")
val = result.validation
print(f"forced_decision: {val.forced_decision}")
print(f"has_blocking_issue: {val.has_blocking_issue}")
print(f"issues: {[i.code for i in val.issues]}")
