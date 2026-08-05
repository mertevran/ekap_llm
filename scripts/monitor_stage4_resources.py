from __future__ import annotations

import argparse
import csv
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=1.0)
    return parser.parse_args()


def process_tree_metrics(root_pid: int) -> dict[str, float]:
    try:
        root = psutil.Process(root_pid)
        processes = [root, *root.children(recursive=True)]
    except psutil.Error:
        return {
            "test_cpu_percent": 0.0,
            "test_rss_gb": 0.0,
            "test_vms_gb": 0.0,
        }

    cpu = 0.0
    rss = 0
    vms = 0

    for process in processes:
        try:
            cpu += process.cpu_percent(interval=None)
            memory = process.memory_info()
            rss += memory.rss
            vms += memory.vms
        except psutil.Error:
            continue

    gib = 1024**3
    return {
        "test_cpu_percent": cpu,
        "test_rss_gb": rss / gib,
        "test_vms_gb": vms / gib,
    }


def ollama_metrics() -> dict[str, float]:
    cpu = 0.0
    rss = 0
    process_count = 0

    for process in psutil.process_iter(
        attrs=["name", "cmdline", "cpu_percent", "memory_info"]
    ):
        try:
            name = (process.info["name"] or "").lower()
            command = " ".join(process.info["cmdline"] or []).lower()

            if "ollama" not in name and "ollama" not in command:
                continue

            process_count += 1
            cpu += float(process.info["cpu_percent"] or 0.0)
            rss += process.info["memory_info"].rss
        except (psutil.Error, AttributeError):
            continue

    return {
        "ollama_process_count": process_count,
        "ollama_cpu_percent": cpu,
        "ollama_rss_gb": rss / (1024**3),
    }


def gpu_metrics() -> dict[str, float | None]:
    command = [
        "nvidia-smi",
        "--query-gpu=utilization.gpu,memory.used,memory.total,"
        "temperature.gpu,power.draw",
        "--format=csv,noheader,nounits",
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        first_line = result.stdout.strip().splitlines()[0]
        values = [value.strip() for value in first_line.split(",")]

        return {
            "gpu_util_percent": float(values[0]),
            "gpu_memory_used_gb": float(values[1]) / 1024,
            "gpu_memory_total_gb": float(values[2]) / 1024,
            "gpu_temperature_c": float(values[3]),
            "gpu_power_w": float(values[4]),
        }
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return {
            "gpu_util_percent": None,
            "gpu_memory_used_gb": None,
            "gpu_memory_total_gb": None,
            "gpu_temperature_c": None,
            "gpu_power_w": None,
        }


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    csv_path = args.output_dir / "system_resources.csv"
    summary_path = args.output_dir / "system_resources_summary.json"

    fields = [
        "timestamp",
        "elapsed_seconds",
        "system_cpu_percent",
        "ram_used_gb",
        "ram_available_gb",
        "ram_percent",
        "cache_gb",
        "swap_used_gb",
        "swap_percent",
        "test_cpu_percent",
        "test_rss_gb",
        "test_vms_gb",
        "ollama_process_count",
        "ollama_cpu_percent",
        "ollama_rss_gb",
        "disk_read_gb_total",
        "disk_write_gb_total",
        "network_sent_gb_total",
        "network_received_gb_total",
        "gpu_util_percent",
        "gpu_memory_used_gb",
        "gpu_memory_total_gb",
        "gpu_temperature_c",
        "gpu_power_w",
    ]

    started = time.time()
    samples: list[dict[str, float | str | None]] = []

    initial_disk = psutil.disk_io_counters()
    initial_network = psutil.net_io_counters()

    psutil.cpu_percent(interval=None)

    try:
        process = psutil.Process(args.pid)
        process.cpu_percent(interval=None)
    except psutil.Error:
        print(f"İzlenecek süreç bulunamadı: {args.pid}")
        return 1

    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()

        while psutil.pid_exists(args.pid):
            memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
            disk = psutil.disk_io_counters()
            network = psutil.net_io_counters()

            row: dict[str, float | str | None] = {
                "timestamp": datetime.now(UTC).isoformat(),
                "elapsed_seconds": round(time.time() - started, 2),
                "system_cpu_percent": psutil.cpu_percent(interval=None),
                "ram_used_gb": memory.used / (1024**3),
                "ram_available_gb": memory.available / (1024**3),
                "ram_percent": memory.percent,
                "cache_gb": getattr(memory, "cached", 0) / (1024**3),
                "swap_used_gb": swap.used / (1024**3),
                "swap_percent": swap.percent,
                **process_tree_metrics(args.pid),
                **ollama_metrics(),
                "disk_read_gb_total": (
                    (disk.read_bytes - initial_disk.read_bytes) / (1024**3)
                    if disk and initial_disk
                    else 0.0
                ),
                "disk_write_gb_total": (
                    (disk.write_bytes - initial_disk.write_bytes) / (1024**3)
                    if disk and initial_disk
                    else 0.0
                ),
                "network_sent_gb_total": (
                    network.bytes_sent - initial_network.bytes_sent
                )
                / (1024**3),
                "network_received_gb_total": (
                    network.bytes_recv - initial_network.bytes_recv
                )
                / (1024**3),
                **gpu_metrics(),
            }

            writer.writerow(row)
            file.flush()
            samples.append(row)
            time.sleep(args.interval)

    numeric_fields = [
        field
        for field in fields
        if field not in {"timestamp", "elapsed_seconds"}
    ]

    metrics: dict[str, dict[str, float]] = {}

    for field in numeric_fields:
        values = [
            float(sample[field])
            for sample in samples
            if sample.get(field) is not None
        ]

        if values:
            metrics[field] = {
                "minimum": min(values),
                "average": sum(values) / len(values),
                "maximum": max(values),
            }

    summary = {
        "status": "completed",
        "started_at": datetime.fromtimestamp(
            started,
            tz=UTC,
        ).isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "elapsed_seconds": round(time.time() - started, 2),
        "sample_interval_seconds": args.interval,
        "sample_count": len(samples),
        "monitored_pid": args.pid,
        "metrics": metrics,
        "raw_csv": str(csv_path),
    }

    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Kaynak ölçümleri kaydedildi: {csv_path}")
    print(f"Kaynak özeti kaydedildi: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
