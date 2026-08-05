"""
Değerlendirme ve etiket sonuçlarına dayanarak hata analizi çalıştıran betik.
"""

import argparse
import json
from pathlib import Path

from app.evaluation.retrieval_error_analysis import RetrievalErrorAnalyzer


def main():
    parser = argparse.ArgumentParser(description="İSBAK RAG Hata Analizi")
    parser.add_argument(
        "--evaluation", required=True, help="evaluate_isbak_retrieval çıktısı (JSON)"
    )
    parser.add_argument("--labels", required=True, help="Etiket dosyası (JSON)")
    parser.add_argument("--output", required=True, help="Analiz çıktısı (JSON)")

    args = parser.parse_args()

    analyzer = RetrievalErrorAnalyzer(args.evaluation, args.labels)
    result = analyzer.analyze()

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Hata analizi tamamlandı. Çıktı: {out_path}")
    summary = result.get("summary", {})
    print(f"Toplam Hata Sayısı: {summary.get('total_errors_found', 0)}")
    for k, v in summary.get("error_counts", {}).items():
        print(f"  - {k}: {v}")


if __name__ == "__main__":
    main()
