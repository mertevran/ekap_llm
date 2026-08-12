from unittest.mock import Mock

from app.decision.isbak_decision_pipeline import IsbakDecisionPipeline
from app.decision.models import ModelDecision
from app.domain.tender import TenderRecord
from app.pipeline.isbak_tender_analysis_service import IsbakTenderAnalysisService
from app.reporting.isbak_decision_reporter import IsbakDecisionReporter
from app.retrieval.isbak_query_profile_router import ProfileRoutingSignal
from app.validation.isbak_rule_validator import IsbakRuleValidator


class FakeModel:
    def __init__(self, name: str, default_dec: str, conf: float, missing: list[str] = None):
        self.name = name
        self.default_dec = default_dec
        self.conf = conf
        self.missing = missing or []

    def analyze(
        self,
        tender_id: str,
        ikn: str,
        category_code: str,
        tender_context: str,
        company_context: str,
        **kwargs,
    ) -> ModelDecision:
        return ModelDecision(
            model_name=self.name,
            decision=self.default_dec,
            confidence=self.conf,
            birincil_profil_kodu="YAZILIM",
            ikincil_profil_kodlari=[],
            uygunluk_gerekceleri=[],
            uygunsuzluk_gerekceleri=[],
            zorunlu_kriter_sonuclari=[],
            eksik_kanitlar=self.missing,
        )


def test_isbak_decision_end_to_end(tmp_path):
    # 1. Sahte Bileşenlerin (Mocks) Hazırlanması

    # İhale Okuma (Repository)
    mock_repo = Mock()
    fake_tender = TenderRecord(
        id="1",
        ikn="2026/123",
        adi="Test İhale",
        idare_adi="İSBAK",
        ihale_turu="Hizmet",
        onay_tarihi="2026-01-01",
        sektor="Yazılım",
    )
    mock_repo.get_by_ikn.return_value = fake_tender

    # Belge Oluşturma (Document Builder)
    mock_doc_builder = Mock()
    mock_doc_builder.build.return_value = {"sections": [{"text": "Test Şartname"}]}

    # Bilgi Getirme (Retriever)
    mock_retriever = Mock()
    fake_chunk = Mock()
    fake_chunk.ikn = "2025/999"
    fake_chunk.evidence_chunks = [
        Mock(chunk_id="c1", text="Benzer ihale içeriği.", semantic_score=0.92)
    ]
    fake_chunk.scores = Mock(final=0.92)
    fake_chunk.primary_profile_code = "YAZILIM"

    fake_chunk_self = Mock()
    fake_chunk_self.ikn = "2026/123"
    fake_chunk_self.evidence_chunks = [Mock(chunk_id="c2", text="Kendisi.", semantic_score=0.99)]
    fake_chunk_self.scores = Mock(final=0.99)
    fake_chunk_self.primary_profile_code = "YAZILIM"

    mock_retriever.retrieve.return_value = [fake_chunk_self, fake_chunk]

    # Profil Bağlamı (Router & Loader)
    mock_router = Mock()
    mock_router.analyze.return_value = ProfileRoutingSignal(
        primary_groups=["YAZILIM"],
        secondary_groups=[],
        negative_groups=[],
        confidence=0.9,
        matched_phrases=[],
        ambiguous_terms=[],
    )

    mock_loader = Mock()
    mock_loader.build_evaluation_context.return_value = {
        "birincil_profil_kodu": "YAZILIM",
        "evaluation_rules": {"ikinci_gorus_tetikleyicileri": ["dusuk_guven_duzeyi"]},
    }

    # Karar ve Doğrulama
    qwen = FakeModel("qwen", "uygun", 0.70)
    validator = IsbakRuleValidator()

    decision_pipeline = IsbakDecisionPipeline(
        primary_model=qwen, validator=validator
    )

    # 2. Servis Montajı
    service = IsbakTenderAnalysisService(
        tender_repository=mock_repo,
        document_builder=mock_doc_builder,
        retriever=mock_retriever,
        decision_pipeline=decision_pipeline,
        profile_loader=mock_loader,
        query_router=mock_router,
        max_evidence_chunks=3,
        max_chunks_per_tender=1,
    )

    # 3. Çalıştırma
    report = service.analyze_tender("2026/123")

    # 4. Doğrulamalar

    # İhale Okuma ve Belge Oluşturma Kontrolü
    mock_repo.get_by_ikn.assert_called_with("2026/123")
    mock_doc_builder.build.assert_called_once()

    # Kendi İKN'sini Çıkarma
    assert len(report.evidence) == 1
    assert report.evidence[0].ikn == "2025/999"  # 2026/123 hariç tutuldu

    # Kararlar
    assert report.decision is not None
    assert report.decision.primary_model.decision == "uygun"

    # Nihai Karar
    assert report.decision.final_decision == "uygun"
    assert report.decision.human_review_required is True

    # 5. Rapor Oluşturma
    reporter = IsbakDecisionReporter(output_dir=tmp_path)
    json_path, md_path = reporter.generate_reports(report, fake_tender)

    assert json_path.exists()
    assert md_path.exists()

    with open(md_path, encoding="utf-8") as f:
        content = f.read()
        assert "## İhale bilgisi" in content
        assert "## İhale bilgisi" in content
