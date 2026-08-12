import argparse
import sys
from pathlib import Path

# Root path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.company_profiles.isbak_profile_loader import IsbakProfileLoader
from app.config.settings import get_settings
from app.database.tender_repository import TenderNotFoundError, TenderRepository
from app.decision.isbak_decision_pipeline import IsbakDecisionPipeline
from app.decision.ollama_decision_model import OllamaDecisionModel
from app.indexing.document_builder import TenderDocumentBuilder
from app.pipeline.exceptions import (
    DatabaseAccessError,
    DecisionServiceError,
    ReportExistsError,
    TenderAnalysisError,
)
from app.pipeline.isbak_tender_analysis_service import IsbakTenderAnalysisService
from app.reporting.isbak_decision_reporter import IsbakDecisionReporter
from app.retrieval.isbak_query_profile_router import IsbakQueryProfileRouter
from app.retrieval.isbak_tender_retriever import IsbakTenderRetriever
from app.validation.isbak_rule_validator import IsbakRuleValidator


def build_analysis_service(args, settings):
    """CLI veya testler tarafından kullanılabilen bağımlılık yapılandırma işlevi."""
    tender_repo = TenderRepository()
    doc_builder = TenderDocumentBuilder()

    try:
        import logging

        from app.config.isbak_rag_settings import get_isbak_rag_settings
        from app.indexing.embedder import BgeM3Embedder
        from app.vector_store.qdrant_store import QdrantVectorStore

        rag_settings = get_isbak_rag_settings()

        if not rag_settings.resolved_qdrant_path.exists():
            raise TenderAnalysisError(
                f"Qdrant yolu bulunamadı: {rag_settings.resolved_qdrant_path}"
            )

        if not rag_settings.collection_name or not rag_settings.collection_name.strip():
            raise TenderAnalysisError("Qdrant koleksiyon adı boş olamaz.")

        logger = logging.getLogger(__name__)
        logger.info(
            f"Qdrant Yolu: {rag_settings.resolved_qdrant_path}, Koleksiyon: {rag_settings.collection_name}"
        )

        embedder = BgeM3Embedder(
            model_name=rag_settings.embedding_model,
            device=rag_settings.embedding_device,
            cache_folder=settings.model_cache_path,
            batch_size=rag_settings.embedding_batch_size,
        )

        vector_store = QdrantVectorStore(
            path=str(rag_settings.resolved_qdrant_path),
            collection_name=rag_settings.collection_name,
        )
        retriever = IsbakTenderRetriever(
            embedder=embedder, vector_store=vector_store, settings=rag_settings
        )
    except ModuleNotFoundError as e:
        raise TenderAnalysisError(
            f"Ortam bağımlılığı nedeniyle doğrulanamadı. Eksik modül: {e.name}"
        )

    primary_model = OllamaDecisionModel(
        name=settings.qwen_model,
        host=settings.ollama_base_url,
        prompt_version="isbak_qwen_decision_v1",
    )

    validator = IsbakRuleValidator()
    decision_pipeline = IsbakDecisionPipeline(
        primary_model=primary_model, validator=validator
    )

    profile_loader = IsbakProfileLoader()
    query_router = IsbakQueryProfileRouter()

    top_k = getattr(args, "top_k", 5)

    service = IsbakTenderAnalysisService(
        tender_repository=tender_repo,
        document_builder=doc_builder,
        retriever=retriever,
        decision_pipeline=decision_pipeline,
        profile_loader=profile_loader,
        query_router=query_router,
        max_evidence_chunks=top_k,
        max_chunks_per_tender=1,
    )

    return service


def create_out_print(json_only, verbose):
    def _print(msg, force_stderr=False, end="\n"):
        if json_only:
            if force_stderr or verbose:
                print(msg, file=sys.stderr, end=end)
        else:
            print(msg, file=sys.stdout, end=end)

    return _print


def main():
    parser = argparse.ArgumentParser(description="EKAP İhale Analiz Aracı")
    parser.add_argument("ikn", type=str, help="Analiz edilecek ihalenin İKN numarası")
    parser.add_argument(
        "--output-dir", type=str, default="reports", help="Raporların kaydedileceği dizin"
    )
    parser.add_argument(
        "--top-k", type=int, default=5, help="Getirilecek benzersiz RAG kanıtı sayısı"
    )

    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Yalnızca JSON formatında rapor üret (şu anda aktif değil)",
    )
    parser.add_argument("--verbose", action="store_true", help="Detaylı loglama")
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Rapor dosyası oluşturma, sadece sonucu ekranda göster",
    )
    parser.add_argument(
        "--force", action="store_true", help="Rapor mevcut olsa dahi zorla yeniden analiz et"
    )

    args = parser.parse_args()
    out_print = create_out_print(args.json_only, args.verbose)

    if not args.ikn or not args.ikn.strip():
        out_print("HATA: İKN boş olamaz.", force_stderr=True)
        sys.exit(1)

    out_print(f"İKN: {args.ikn}")
    out_print("Analiz başlatılıyor...")

    # Erken Rapor Kontrolü (Gereksiz model çağrısını engellemek için)
    if not args.no_write:
        safe_ikn = args.ikn.strip().replace("/", "_").replace("\\", "_")
        json_path = Path(args.output_dir) / f"analysis_{safe_ikn}.json"
        if json_path.exists() and not args.force:
            out_print(
                f"\nHATA: '{args.ikn}' için rapor zaten '{args.output_dir}' dizininde mevcut.",
                force_stderr=True,
            )
            out_print("Yeniden çalıştırmak için --force parametresini ekleyin.", force_stderr=True)
            sys.exit(1)

    try:
        settings = get_settings()

        try:
            service = build_analysis_service(args, settings)
        except TenderAnalysisError as e:
            out_print(f"\n{str(e)}", force_stderr=True)
            out_print(
                "Gerçek servis testi için 'sentence_transformers' ve 'qdrant_client' gereklidir.",
                force_stderr=True,
            )
            sys.exit(2)

        # Analizi Çalıştır
        tender_repo = TenderRepository()
        tender_record = tender_repo.get_by_ikn(args.ikn)
        report = service.analyze_tender(args.ikn)

        out_print(f"Analiz Kimliği (ID): {report.analysis_id}")
        out_print("İhale verisi: Okundu")
        out_print("Bilgi getirme (RAG): Tamamlandı")

        # Sonuçları ekrana yazdır
        if report.decision:
            out_print(f"Qwen Kararı: {report.decision.primary_model.decision}")
            val_status = (
                "Kritik eksiklikler bulundu"
                if not report.decision.validation.passed
                else "Başarılı"
            )
            out_print(f"Kural Doğrulaması: {val_status}")

            out_print(f"Nihai Karar: {report.decision.final_decision}")
            out_print(f"Güven Düzeyi: {report.decision.final_confidence}")

            review_req = "Evet" if report.decision.human_review_required else "Hayır"
            out_print(f"İnsan İncelemesi Gerekli mi: {review_req}")

            # Veritabanını LLM sonucuyla güncelle
            if not args.no_write:
                try:
                    from app.indexing.index_state_repository import IndexStateRepository
                    idx_repo = IndexStateRepository()
                    idx_repo.update_classification(
                        tender_id=str(tender_record.id),
                        classification=report.decision.final_decision
                    )
                    out_print(f"Veritabanı (tender_index_state) güncellendi: {report.decision.final_decision}")
                except Exception as e:
                    out_print(f"\nHATA: Veritabanı (tender_index_state) güncellenemedi: {str(e)}", force_stderr=True)
        else:
            out_print("Karar modeli çalıştırılamadı.", force_stderr=True)

        # Raporlama
        if args.json_only:
            reporter = IsbakDecisionReporter(output_dir=args.output_dir)
            json_output = reporter.render_json(report, tender_record)
            print(json_output, file=sys.stdout)
            if not args.no_write:
                try:
                    reporter.write_json(report, tender_record, overwrite=args.force)
                except ReportExistsError as e:
                    out_print(
                        f"\nHATA: Rapor yazılırken çakışma oluştu. {str(e)}", force_stderr=True
                    )
                    sys.exit(1)
        else:
            if not args.no_write:
                reporter = IsbakDecisionReporter(output_dir=args.output_dir)
                try:
                    reporter.generate_reports(report, tender_record, overwrite=args.force)
                    out_print(f"Raporlar '{args.output_dir}' dizinine kaydedildi.")
                except ReportExistsError as e:
                    out_print(
                        f"\nHATA: Rapor yazılırken çakışma oluştu. {str(e)}", force_stderr=True
                    )
                    sys.exit(1)
            else:
                out_print("Rapor dosyası oluşturulmadı (--no-write aktif).")

        sys.exit(0)

    except TenderNotFoundError:
        out_print(
            f"\nHATA: İhale bulunamadı ({args.ikn}). Lütfen veritabanında aktif olduğunu doğrulayın.",
            force_stderr=True,
        )
        sys.exit(1)
    except DatabaseAccessError as e:
        out_print(
            "\nSİSTEM HATASI: Veritabanı veya Vektör İndeksine ulaşılamıyor.", force_stderr=True
        )
        if args.verbose:
            out_print(f"Detay: {str(e)}", force_stderr=True)
        sys.exit(2)
    except DecisionServiceError as e:
        out_print(
            "\nSİSTEM HATASI: Karar Modeli (Ollama) ile iletişim veya doğrulama hatası.",
            force_stderr=True,
        )
        if args.verbose:
            out_print(f"Detay: {str(e)}", force_stderr=True)
        sys.exit(2)
    except TenderAnalysisError as e:
        out_print("\nANALİZ HATASI: İhale işlenirken sorun oluştu.", force_stderr=True)
        if args.verbose:
            out_print(f"Detay: {str(e)}", force_stderr=True)
        sys.exit(2)
    except Exception as e:
        out_print("\nHATA: Beklenmeyen bir sistem hatası oluştu.", force_stderr=True)
        if args.verbose:
            out_print(f"Detay: {type(e).__name__} - {str(e)}", force_stderr=True)
        sys.exit(3)


if __name__ == "__main__":
    main()
