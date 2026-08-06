#!/usr/bin/env python
"""Hedef sunucuda iş parçacığı ve 100 kararlık dayanıklılık testi çalıştırır."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "PostgreSQL kaynaklı karar hattını sıralı olarak 8/12/16/24 iş "
            "parçacığında ve ardından 100 kararda ölçer."
        )
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--threads",
        type=int,
        nargs="+",
        default=[8, 12, 16, 24],
    )
    parser.add_argument("--comparison-decisions", type=int, default=10)
    parser.add_argument("--endurance-decisions", type=int, default=100)
    parser.add_argument("--endurance-thread", type=int, default=12)
    parser.add_argument("--limit-per-profile", type=int, default=10)
    parser.add_argument("--profile-code", action="append", default=[])
    parser.add_argument("--resource-interval", type=float, default=1.0)
    parser.add_argument("--max-swap-growth-mb", type=float, default=512.0)
    parser.add_argument("--skip-comparison", action="store_true")
    parser.add_argument("--skip-endurance", action="store_true")
    parser.add_argument("--allow-inactive-database-records", action="store_true")
    parser.add_argument("--require-target-hardware", action="store_true")
    return parser.parse_args()


def _validate_args(args: argparse.Namespace) -> None:
    positive_integers = [
        *args.threads,
        args.comparison_decisions,
        args.endurance_decisions,
        args.endurance_thread,
        args.limit_per_profile,
    ]
    if any(value <= 0 for value in positive_integers):
        raise ValueError("İş parçacığı ve karar sayıları pozitif olmalıdır.")
    if args.resource_interval <= 0 or args.max_swap_growth_mb < 0:
        raise ValueError("Kaynak aralığı pozitif, takas sınırı negatif olmamalıdır.")


def _resource_swap_growth(resource_csv: Path) -> tuple[float, float]:
    if not resource_csv.exists():
        return 0.0, 0.0
    with resource_csv.open(encoding="utf-8", newline="") as file:
        values = [
            float(row["swap_used_mb"])
            for row in csv.DictReader(file)
            if row.get("swap_used_mb") not in (None, "")
        ]
    if not values:
        return 0.0, 0.0
    return round(values[-1] - values[0], 2), round(max(values) - values[0], 2)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _run_case(
    *,
    case_name: str,
    thread_count: int,
    decision_count: int,
    args: argparse.Namespace,
    root_dir: Path,
) -> dict[str, Any]:
    case_dir = root_dir / case_name
    decision_dir = case_dir / "decisions"
    resource_dir = case_dir / "resources"
    case_dir.mkdir(parents=True, exist_ok=True)
    resource_dir.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "scripts/run_database_tender_decision_chain.py",
        "--limit-per-profile",
        str(args.limit_per_profile),
        "--max-decisions",
        str(decision_count),
        "--report-dir",
        str(decision_dir),
        "--log-level",
        "INFO",
    ]
    for profile_code in args.profile_code:
        command.extend(["--profile-code", profile_code])
    if args.allow_inactive_database_records:
        command.append("--allow-inactive-database-records")

    environment = os.environ.copy()
    project_root = str(Path.cwd())
    existing_python_path = environment.get("PYTHONPATH", "")
    environment.update(
        {
            "OLLAMA_NUM_THREAD": str(thread_count),
            "OLLAMA_NUM_PARALLEL": "1",
            "OLLAMA_MAX_LOADED_MODELS": "1",
            "PYTHONPATH": (
                project_root
                if not existing_python_path
                else project_root + os.pathsep + existing_python_path
            ),
        }
    )
    started_at = datetime.now(UTC).isoformat()
    with (case_dir / "decision_stdout.log").open("w", encoding="utf-8") as stdout:
        process = subprocess.Popen(
            command,
            cwd=Path.cwd(),
            env=environment,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            text=True,
        )
        monitor_command = [
            sys.executable,
            "scripts/monitor_system_resources.py",
            "--pid",
            str(process.pid),
            "--output-dir",
            str(resource_dir),
            "--interval",
            str(args.resource_interval),
        ]
        with (case_dir / "resource_monitor.log").open(
            "w", encoding="utf-8"
        ) as monitor_log:
            monitor = subprocess.Popen(
                monitor_command,
                cwd=Path.cwd(),
                env=environment,
                stdout=monitor_log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            exit_code = process.wait()
            try:
                monitor_exit_code = monitor.wait(timeout=30)
            except subprocess.TimeoutExpired:
                monitor.terminate()
                try:
                    monitor_exit_code = monitor.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    monitor.kill()
                    monitor_exit_code = monitor.wait()

    decision_summary = _read_json(
        decision_dir / "tender_decision_run_summary.json"
    )
    resource_summary = _read_json(
        resource_dir / "system_resources_summary.json"
    )
    swap_end_growth, swap_peak_growth = _resource_swap_growth(
        resource_dir / "system_resources.csv"
    )
    decisions = int(decision_summary.get("decisions") or 0)
    elapsed = float(decision_summary.get("elapsed_seconds") or 0.0)
    expected_count_met = decisions == decision_count
    swap_gate_passed = swap_peak_growth <= args.max_swap_growth_mb
    return {
        "case_name": case_name,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "thread_count": thread_count,
        "requested_decisions": decision_count,
        "completed_decisions": decisions,
        "decision_exit_code": exit_code,
        "monitor_exit_code": monitor_exit_code,
        "elapsed_seconds": elapsed,
        "seconds_per_decision": round(elapsed / decisions, 3) if decisions else None,
        "decisions_per_hour": round(decisions * 3600 / elapsed, 3) if elapsed else 0.0,
        "expected_count_met": expected_count_met,
        "swap_end_growth_mb": swap_end_growth,
        "swap_peak_growth_mb": swap_peak_growth,
        "swap_gate_passed": swap_gate_passed,
        "passed": bool(
            exit_code == 0
            and monitor_exit_code == 0
            and expected_count_met
            and swap_gate_passed
        ),
        "decision_summary": decision_summary,
        "resource_summary": resource_summary,
    }


def main() -> int:
    args = parse_args()
    _validate_args(args)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    hardware = {
        "logical_cpu_count": psutil.cpu_count(logical=True),
        "physical_cpu_count": psutil.cpu_count(logical=False),
        "total_ram_mb": round(psutil.virtual_memory().total / 1024**2, 2),
        "total_swap_mb": round(psutil.swap_memory().total / 1024**2, 2),
    }
    target_hardware_met = bool(
        (hardware["logical_cpu_count"] or 0) >= 24
        and hardware["total_ram_mb"] >= 15 * 1024
    )
    if args.require_target_hardware and not target_hardware_met:
        raise RuntimeError(
            "Bu makine en az 24 mantıksal işlemci ve yaklaşık 16 GB RAM "
            "hedefini karşılamıyor."
        )

    cases: list[dict[str, Any]] = []
    if not args.skip_comparison:
        for thread_count in dict.fromkeys(args.threads):
            cases.append(
                _run_case(
                    case_name=f"threads_{thread_count}",
                    thread_count=thread_count,
                    decision_count=args.comparison_decisions,
                    args=args,
                    root_dir=output_dir,
                )
            )
    if not args.skip_endurance:
        cases.append(
            _run_case(
                case_name=(
                    f"endurance_{args.endurance_decisions}_threads_"
                    f"{args.endurance_thread}"
                ),
                thread_count=args.endurance_thread,
                decision_count=args.endurance_decisions,
                args=args,
                root_dir=output_dir,
            )
        )

    comparison_passes = [
        case
        for case in cases
        if case["case_name"].startswith("threads_") and case["passed"]
    ]
    best_case = (
        max(comparison_passes, key=lambda item: item["decisions_per_hour"])
        if comparison_passes
        else None
    )
    report = {
        "timestamp": datetime.now(UTC).isoformat(),
        "hardware": hardware,
        "target_hardware_met": target_hardware_met,
        "single_request_mode": True,
        "ollama_num_parallel": 1,
        "ollama_max_loaded_models": 1,
        "max_swap_growth_mb": args.max_swap_growth_mb,
        "swap_policy": "emergency_only",
        "best_thread_count": best_case["thread_count"] if best_case else None,
        "all_cases_passed": bool(cases) and all(case["passed"] for case in cases),
        "cases": cases,
    }
    report_path = output_dir / "server_capacity_benchmark_summary.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_cases_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
