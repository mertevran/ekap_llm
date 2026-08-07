#!/usr/bin/env python
"""SQL Encoder ile ihale seçip mevcut tek modelli karar zincirini çalıştırır."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import scripts.run_tender_decision_chain as decision_runner
from app.config import get_settings
from app.sql_encoder.selection import select_tenders_via_sql_encoder


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description=(
            "Doğal dil isteğini güvenli SQL Encoder ile PostgreSQL'de çalıştırır "
            "ve seçilen ihaleleri BGE-M3/FAISS → Qwen → Python karar hattına aktarır."
        )
    )
    parser.add_argument("--request", required=True, help="Doğal dilde ihale seçme isteği.")
    parser.add_argument(
        "--selection-random-seed",
        type=int,
        default=20260806,
        help="SQL Encoder rastgele seçiminin yeniden üretilebilir tohumu.",
    )
    parser.add_argument(
        "--report-dir",
        default="reports/sql_encoder_decision",
        help="SQL Encoder seçimi ve karar raporlarının ortak klasörü.",
    )
    args, runner_args = parser.parse_known_args()
    return args, [item for item in runner_args if item != "--"]


def _contains_option(arguments: list[str], option: str) -> bool:
    return option in arguments or any(item.startswith(f"{option}=") for item in arguments)


def _integer_option(arguments: list[str], option: str) -> int | None:
    for index, item in enumerate(arguments):
        if item == option:
            if index + 1 >= len(arguments):
                raise ValueError(f"{option} için değer verilmedi.")
            return int(arguments[index + 1])
        if item.startswith(f"{option}="):
            return int(item.split("=", 1)[1])
    return None


def _validate_runner_arguments(arguments: list[str]) -> None:
    reserved = ("--source-mode", "--selected-ikn", "--require-selected-coverage", "--report-dir")
    conflicts = [option for option in reserved if _contains_option(arguments, option)]
    if conflicts:
        raise ValueError(
            "SQL Encoder giriş noktası şu seçenekleri kendisi yönetir: "
            + ", ".join(conflicts)
        )


def main() -> int:
    args, runner_args = parse_args()
    _validate_runner_arguments(runner_args)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    selection = select_tenders_via_sql_encoder(
        natural_language_request=args.request,
        random_seed=args.selection_random_seed,
    )
    selection_path = report_dir / "sql_encoder_selection.json"
    selection_path.write_text(
        json.dumps(selection.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    selected_count = len(selection.selected_ikns)
    limit_per_profile = _integer_option(runner_args, "--limit-per-profile")
    if limit_per_profile is not None and limit_per_profile < selected_count:
        raise ValueError(
            "--limit-per-profile, SQL Encoder ile seçilen ihale sayısından küçük olamaz: "
            f"seçilen={selected_count}, limit={limit_per_profile}"
        )
    max_decisions = _integer_option(runner_args, "--max-decisions")
    if max_decisions is not None and max_decisions < selected_count:
        raise ValueError(
            "--max-decisions, SQL Encoder ile seçilen ihale sayısından küçük olamaz: "
            f"seçilen={selected_count}, limit={max_decisions}"
        )

    settings = get_settings()
    active_statuses = {
        " ".join(str(value).split()).casefold()
        for value in settings.active_tender_status_values
    }
    selected_statuses = {
        " ".join(str(row.get("ihale_durumu") or "").split()).casefold()
        for row in selection.rows
    }
    contains_inactive = any(
        status and status not in active_statuses for status in selected_statuses
    )
    if contains_inactive and "--allow-inactive-database-records" not in runner_args:
        raise ValueError(
            "SQL Encoder seçimi aktif durum listesi dışında kayıt içeriyor. Geçmiş "
            "karşılaştırması bilinçli yapılacaksa --allow-inactive-database-records verin."
        )

    forwarded = [*runner_args]
    if limit_per_profile is None:
        forwarded.extend(["--limit-per-profile", str(selected_count)])
    if max_decisions is None:
        forwarded.extend(["--max-decisions", str(selected_count)])
    forwarded.extend(["--source-mode", "database"])
    forwarded.extend(["--report-dir", str(report_dir)])
    forwarded.append("--require-selected-coverage")
    for ikn in selection.selected_ikns:
        forwarded.extend(["--selected-ikn", ikn])

    print(
        "SQL Encoder seçimi tamamlandı | "
        f"ihale={selected_count} | model={selection.encoder_model} | "
        f"rapor={selection_path}"
    )
    sys.argv = ["run_tender_decision_chain.py", *forwarded]
    return decision_runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
