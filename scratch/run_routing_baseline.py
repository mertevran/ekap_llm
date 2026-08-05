import json
from pathlib import Path

from app.retrieval.isbak_query_profile_router import IsbakQueryProfileRouter


def main():
    router = IsbakQueryProfileRouter()

    queries_path = Path("evaluation/isbak_retrieval_queries.json")
    data = json.loads(queries_path.read_text(encoding="utf-8"))

    results = []

    for item in data.get("queries", []):
        query_id = item.get("query_id")
        text = item.get("query")
        expected_groups = set(item.get("expected_profile_groups", []))

        signal = router.analyze(text)

        predicted_primary = []
        if signal.primary_groups:
            predicted_primary = [g.split("-")[0] for g in signal.primary_groups]

        results.append({
            "query_id": query_id,
            "query": text,
            "expected_groups": list(expected_groups),
            "predicted_primary": predicted_primary,
            "predicted_secondary": signal.secondary_groups,
            "predicted_raw_primary_groups": signal.primary_groups,
        })

    out_path = Path("scratch/routing_after_13_2.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Evaluated {len(results)} queries. Saved to {out_path}")

if __name__ == "__main__":
    main()
