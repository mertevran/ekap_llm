"""
Geniş aday havuzu dışa aktarma betiği.
Sorguları farklı yapılandırmalarla çalıştırarak havuz oluşturur ve JSON'a aktarır.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict

from app.config.isbak_rag_settings import IsbakRagSettings
from app.evaluation.relevance_dataset import IsbakRelevanceLabel, RelevanceDataset
from app.indexing.embedder import BgeM3Embedder
from app.retrieval.isbak_tender_retriever import IsbakTenderRetriever
from app.vector_store.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def export_candidates(
    queries_path: str,
    output_path: str,
    limit: int,
    candidate_limit: int,
    device: str,
) -> None:
    logger.info(f"Sorgu dosyası yükleniyor: {queries_path}")
    with open(queries_path, encoding="utf-8") as f:
        data = json.load(f)
        queries = data["queries"]

    splits = RelevanceDataset.compute_dataset_splits(queries)
    dataset = RelevanceDataset(output_path)

    # Temel ayarlar
    base_settings = IsbakRagSettings()

    logger.info("Vektör veritabanı ve embedder başlatılıyor...")
    embedder = BgeM3Embedder(
        model_name=base_settings.embedding_model,
        device=device,
        batch_size=base_settings.embedding_batch_size,
        show_progress_bar=False,
    )

    vector_store = QdrantVectorStore(
        path=str(base_settings.resolved_qdrant_path),
        collection_name=base_settings.collection_name,
    )

    # Farklı arama stratejileri
    strategies = [
        # 1. Varsayılan (Default)
        IsbakRagSettings(),
        # 2. Semantic-heavy
        IsbakRagSettings(
            semantic_weight=0.90,
            lexical_weight=0.05,
            profile_weight=0.0,
            metadata_weight=0.05,
            minimum_final_score=0.20,
        ),
        # 3. Lexical-heavy
        IsbakRagSettings(
            semantic_weight=0.20,
            lexical_weight=0.70,
            profile_weight=0.05,
            metadata_weight=0.05,
            minimum_final_score=0.20,
        ),
        # 4. Profile-heavy
        IsbakRagSettings(
            semantic_weight=0.30,
            lexical_weight=0.10,
            profile_weight=0.50,
            metadata_weight=0.10,
            minimum_final_score=0.20,
        ),
    ]

    total_exported = 0

    for query_obj in queries:
        q_id = query_obj["query_id"]
        q_text = query_obj["query"]
        split = splits[q_id]

        logger.info(f"Sorgu işleniyor: {q_id} - {q_text} ({split})")

        seen_ikns: set[str] = set()
        merged_results = []

        # Daha önce etiketlenmiş olanları hatırla (tekrar eklememek veya status'u korumak için)
        existing_labels = dataset.get_labels_by_query(q_id)
        existing_ikns = {lbl.ikn for lbl in existing_labels}

        for strategy_settings in strategies:
            retriever = IsbakTenderRetriever(
                embedder=embedder,
                vector_store=vector_store,
                settings=strategy_settings,
            )

            try:
                # limit'i biraz daha yüksek tutup pooling yapıyoruz
                results = retriever.retrieve(
                    q_text,
                    limit=limit,
                    candidate_limit=candidate_limit,
                    minimum_semantic_score=0.30,  # Aday havuzu için eşikleri düşür
                    minimum_final_score=0.25,
                )

                for r in results:
                    if r.ikn not in seen_ikns:
                        seen_ikns.add(r.ikn)
                        merged_results.append(r)
            except Exception as e:
                logger.error(f"Sorgu {q_id} için arama hatası (strateji atlanıyor): {e}")

        # Puanlara göre sırala (Varsayılan stratejinin ürettiği sıraya göre değil, genel final_score'a göre)
        # Ama merged_results içindeki final_score farklı stratejilerden geldiği için anlamlı olmayabilir.
        # En iyisi hepsini varsayılan ayarlar ile tekrar puanlamak, ama havuzu geniş tutmak.

        # Basitçe eklenme sırasına veya en yüksek semantic_score'a göre sıralayabiliriz.
        merged_results.sort(key=lambda x: x.scores.semantic, reverse=True)

        logger.info(f"Sorgu {q_id} için {len(merged_results)} benzersiz aday bulundu.")

        for rank, res in enumerate(merged_results, start=1):
            if res.ikn in existing_ikns:
                continue  # Zaten veri setinde var

            label = IsbakRelevanceLabel(
                query_id=q_id,
                query=q_text,
                ikn=res.ikn,
                tender_name=res.tender_name,
                idare_adi=res.idare_adi,
                rationale=res.rationale,
                primary_profile_code=res.primary_profile_code,
                profile_codes=res.profile_codes,
                rank=rank,
                scores=asdict(res.scores),
                relevance_grade=None,
                label_status="pending",
                notes="",
                reviewed_at="",
                dataset_split=split,
            )
            dataset.add_label(label)
            total_exported += 1

    dataset.save()
    logger.info(f"Toplam {total_exported} yeni aday '{output_path}' dosyasına eklendi.")


def main():
    parser = argparse.ArgumentParser(description="Etiketleme için geniş aday havuzu oluşturur.")
    parser.add_argument("--queries", required=True, help="Sorgu JSON dosyası")
    parser.add_argument("--output", required=True, help="Çıktı JSON dosyası")
    parser.add_argument("--limit", type=int, default=20, help="Strateji başına limit")
    parser.add_argument("--candidate-limit", type=int, default=100, help="Arama adayı limiti")
    parser.add_argument("--device", default="cpu", help="Gömme cihazı (cpu/cuda)")

    args = parser.parse_args()
    export_candidates(args.queries, args.output, args.limit, args.candidate_limit, args.device)


if __name__ == "__main__":
    main()
