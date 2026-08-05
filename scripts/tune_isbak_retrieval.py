"""
Grid Search optimizasyonunu çalıştıran betik.
"""

import argparse
import json
from pathlib import Path

from app.config.isbak_rag_settings import IsbakRagSettings
from app.evaluation.relevance_dataset import RelevanceDataset
from app.evaluation.retrieval_tuning import RetrievalGridSearchTuner
from app.indexing.embedder import BgeM3Embedder
from app.vector_store.qdrant_store import QdrantVectorStore


def main():
    parser = argparse.ArgumentParser(description="Grid Search Optimizasyonu")
    parser.add_argument("--queries", required=True, help="Sorgu dosyası")
    parser.add_argument("--labels", required=True, help="Etiket dosyası")
    parser.add_argument("--output", required=True, help="Çıktı JSON raporu")
    parser.add_argument("--device", default="cpu", help="Gömme cihazı")
    args = parser.parse_args()

    print("Sorgular ve etiketler yükleniyor...")
    with open(args.queries, encoding="utf-8") as f:
        data = json.load(f)
        queries = data["queries"]

    ds = RelevanceDataset(args.labels)
    splits = RelevanceDataset.compute_dataset_splits(queries)

    # Sadece development set üzerinde grid search yapıyoruz
    dev_queries = []
    for q in queries:
        qid = q["query_id"]
        if splits.get(qid) == "development":
            labels_for_query = ds.get_labels_by_query(qid)
            reviewed = [
                L
                for L in labels_for_query
                if L.label_status == "reviewed" and L.relevance_grade is not None
            ]

            relevant_ikns = []
            graded = {}
            for L in reviewed:
                grade = L.relevance_grade
                graded[L.ikn] = grade
                if grade >= 2:
                    relevant_ikns.append(L.ikn)

            q["relevant_ikns"] = relevant_ikns
            q["graded_relevance"] = graded
            dev_queries.append(q)

    print(f"Development set üzerinde tuning yapılacak: {len(dev_queries)} sorgu.")
    if not dev_queries:
        print("Tuning için development set boş! Çıkılıyor.")
        return

    base_settings = IsbakRagSettings()

    print("Model ve Vektör veritabanı başlatılıyor...")
    embedder = BgeM3Embedder(
        model_name=base_settings.embedding_model, device=args.device, show_progress_bar=False
    )
    store = QdrantVectorStore(
        path=str(base_settings.resolved_qdrant_path), collection_name=base_settings.collection_name
    )

    tuner = RetrievalGridSearchTuner(embedder, store, dev_queries, ds.labels)

    # Örnek parametre uzayı (Grid Search)
    param_grid = {
        "semantic_weight": [0.60, 0.70, 0.80],
        "lexical_weight": [0.10, 0.20, 0.30],
        "profile_weight": [0.0, 0.10, 0.15],
        "metadata_weight": [0.05, 0.10],
        "minimum_final_score": [0.35, 0.40],
    }
    # Filtreleme: Ağırlıkların toplamı 1.0 (veya 1.05 filan) civarında olanları almak isteyebiliriz ama
    # Retriever formülü: final_score = sum(weights) - penalty. (Sınır şartı aranmaz ama aşırı büyük olmamalı)

    print("Grid search başlıyor...")
    results = tuner.run(param_grid)
    store.close()

    top_10 = results[:10]

    report = {
        "metadata": {
            "query_count": len(dev_queries),
            "split": "development",
            "total_combinations": len(results),
        },
        "best_configuration": top_10[0]["params"] if top_10 else {},
        "top_10_results": top_10,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"Tuning tamamlandı. Sonuçlar {out_path} dosyasına yazıldı.")
    if top_10:
        print("\nEN İYİ KONFİGÜRASYON:")
        print(json.dumps(top_10[0]["params"], indent=2))
        print("METRİKLER:")
        print(json.dumps(top_10[0]["metrics"], indent=2))


if __name__ == "__main__":
    main()
