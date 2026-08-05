from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil

STOP_REQUESTED = False


def handle_stop(signum, frame):
    global STOP_REQUESTED
    STOP_REQUESTED = True


def safe_process(pid: int | None):
    if not pid:
        return None
    try:
        return psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def process_tree_stats(root_process):
    if root_process is None:
        return {
            "test_cpu_percent": 0.0,
            "test_rss_mb": 0.0,
            "test_vms_mb": 0.0,
            "test_process_count": 0,
        }

    processes = [root_process]

    try:
        processes.extend(root_process.children(recursive=True))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    cpu_percent = 0.0
    rss = 0
    vms = 0
    count = 0

    for process in processes:
        try:
            memory = process.memory_info()
            cpu_percent += process.cpu_percent(interval=None)
            rss += memory.rss
            vms += memory.vms
            count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return {
        "test_cpu_percent": round(cpu_percent, 2),
        "test_rss_mb": round(rss / 1024**2, 2),
        "test_vms_mb": round(vms / 1024**2, 2),
        "test_process_count": count,
    }


def ollama_stats():
    cpu_percent = 0.0
    rss = 0
    count = 0

    for process in psutil.process_iter(
        attrs=["name", "cmdline", "memory_info"]
    ):
        try:
            name = (process.info.get("name") or "").lower()
            cmdline = " ".join(process.info.get("cmdline") or []).lower()

            if "ollama" not in name and "ollama" not in cmdline:
                continue

            cpu_percent += process.cpu_percent(interval=None)
            memory = process.info.get("memory_info")

            if memory:
                rss += memory.rss

            count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return {
        "ollama_cpu_percent": round(cpu_percent, 2),
        "ollama_rss_mb": round(rss / 1024**2, 2),
        "ollama_process_count": count,
    }


def gpu_stats():
    if shutil.which("nvidia-smi") is None:
        return {
            "gpu_util_percent": None,
            "gpu_memory_used_mb": None,
            "gpu_memory_total_mb": None,
            "gpu_temperature_c": None,
            "gpu_power_w": None,
        }

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
            timeout=5,
            check=False,
        )

        if result.returncode != 0 or not result.stdout.strip():
            raise RuntimeError(result.stderr.strip())

        first_gpu = result.stdout.strip().splitlines()[0]
        values = [item.strip() for item in first_gpu.split(",")]

        return {
            "gpu_util_percent": float(values[0]),
            "gpu_memory_used_mb": float(values[1]),
            "gpu_memory_total_mb": float(values[2]),
            "gpu_temperature_c": float(values[3]),
            "gpu_power_w": float(values[4]),
        }
    except Exception:
        return {
            "gpu_util_percent": None,
            "gpu_memory_used_mb": None,
            "gpu_memory_total_mb": None,
            "gpu_temperature_c": None,
            "gpu_power_w": None,
        }


def numeric_summary(rows, field):
    values = [
        float(row[field])
        for row in rows
        if row.get(field) not in (None, "")
    ]

    if not values:
        return None

    return {
        "minimum": round(min(values), 2),
        "average": round(sum(values) / len(values), 2),
        "maximum": round(max(values), 2),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, default=None)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_path = output_dir / "system_resources.csv"
    summary_path = output_dir / "system_resources_summary.json"

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    root_process = safe_process(args.pid)

    psutil.cpu_percent(interval=None)

    if root_process:
        try:
            root_process.cpu_percent(interval=None)
            for child in root_process.children(recursive=True):
                child.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    disk_start = psutil.disk_io_counters()
    network_start = psutil.net_io_counters()
    start_time = time.time()

    rows = []

    fieldnames = [
        "timestamp",
        "elapsed_seconds",
        "system_cpu_percent",
        "load_1m",
        "load_5m",
        "load_15m",
        "ram_used_mb",
        "ram_available_mb",
        "ram_percent",
        "swap_used_mb",
        "swap_percent",
        "disk_read_mb_total",
        "disk_write_mb_total",
        "disk_read_mb_per_sec",
        "disk_write_mb_per_sec",
        "network_sent_mb_total",
        "network_received_mb_total",
        "network_sent_mb_per_sec",
        "network_received_mb_per_sec",
        "test_cpu_percent",
        "test_rss_mb",
        "test_vms_mb",
        "test_process_count",
        "ollama_cpu_percent",
        "ollama_rss_mb",
        "ollama_process_count",
        "gpu_util_percent",
        "gpu_memory_used_mb",
        "gpu_memory_total_mb",
        "gpu_temperature_c",
        "gpu_power_w",
    ]

    previous_time = time.time()
    previous_disk = psutil.disk_io_counters()
    previous_network = psutil.net_io_counters()

    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()

        while not STOP_REQUESTED:
            now = time.time()
            elapsed = now - start_time
            interval = max(now - previous_time, 0.001)

            memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
            disk = psutil.disk_io_counters()
            network = psutil.net_io_counters()

            try:
                load_1m, load_5m, load_15m = os.getloadavg()
            except (AttributeError, OSError):
                load_1m = load_5m = load_15m = None

            test_stats = process_tree_stats(root_process)
            current_ollama_stats = ollama_stats()
            current_gpu_stats = gpu_stats()

            row = {
                "timestamp": datetime.now(UTC).isoformat(),
                "elapsed_seconds": round(elapsed, 2),
                "system_cpu_percent": psutil.cpu_percent(interval=None),
                "load_1m": load_1m,
                "load_5m": load_5m,
                "load_15m": load_15m,
                "ram_used_mb": round(memory.used / 1024**2, 2),
                "ram_available_mb": round(memory.available / 1024**2, 2),
                "ram_percent": memory.percent,
                "swap_used_mb": round(swap.used / 1024**2, 2),
                "swap_percent": swap.percent,
                "disk_read_mb_total": round(
                    (disk.read_bytes - disk_start.read_bytes) / 1024**2, 2
                ),
                "disk_write_mb_total": round(
                    (disk.write_bytes - disk_start.write_bytes) / 1024**2, 2
                ),
                "disk_read_mb_per_sec": round(
                    (disk.read_bytes - previous_disk.read_bytes)
                    / 1024**2
                    / interval,
                    2,
                ),
                "disk_write_mb_per_sec": round(
                    (disk.write_bytes - previous_disk.write_bytes)
                    / 1024**2
                    / interval,
                    2,
                ),
                "network_sent_mb_total": round(
                    (network.bytes_sent - network_start.bytes_sent)
                    / 1024**2,
                    2,
                ),
                "network_received_mb_total": round(
                    (network.bytes_recv - network_start.bytes_recv)
                    / 1024**2,
                    2,
                ),
                "network_sent_mb_per_sec": round(
                    (network.bytes_sent - previous_network.bytes_sent)
                    / 1024**2
                    / interval,
                    2,
                ),
                "network_received_mb_per_sec": round(
                    (network.bytes_recv - previous_network.bytes_recv)
                    / 1024**2
                    / interval,
                    2,
                ),
                **test_stats,
                **current_ollama_stats,
                **current_gpu_stats,
            }

            writer.writerow(row)
            csv_file.flush()
            rows.append(row)

            previous_time = now
            previous_disk = disk
            previous_network = network

            if root_process is not None and not root_process.is_running():
                break

            time.sleep(args.interval)

    end_disk = psutil.disk_io_counters()
    end_network = psutil.net_io_counters()

    summary_fields = [
        "system_cpu_percent",
        "ram_used_mb",
        "ram_percent",
        "swap_used_mb",
        "disk_read_mb_per_sec",
        "disk_write_mb_per_sec",
        "network_sent_mb_per_sec",
        "network_received_mb_per_sec",
        "test_cpu_percent",
        "test_rss_mb",
        "ollama_cpu_percent",
        "ollama_rss_mb",
        "gpu_util_percent",
        "gpu_memory_used_mb",
        "gpu_temperature_c",
        "gpu_power_w",
    ]

    summary = {
        "status": "completed",
        "started_at": rows[0]["timestamp"] if rows else None,
        "finished_at": datetime.now(UTC).isoformat(),
        "elapsed_seconds": round(time.time() - start_time, 2),
        "sample_interval_seconds": args.interval,
        "sample_count": len(rows),
        "monitored_pid": args.pid,
        "logical_cpu_count": psutil.cpu_count(logical=True),
        "physical_cpu_count": psutil.cpu_count(logical=False),
        "total_ram_mb": round(psutil.virtual_memory().total / 1024**2, 2),
        "total_swap_mb": round(psutil.swap_memory().total / 1024**2, 2),
        "total_disk_read_mb": round(
            (end_disk.read_bytes - disk_start.read_bytes) / 1024**2, 2
        ),
        "total_disk_write_mb": round(
            (end_disk.write_bytes - disk_start.write_bytes) / 1024**2, 2
        ),
        "total_network_sent_mb": round(
            (end_network.bytes_sent - network_start.bytes_sent) / 1024**2,
            2,
        ),
        "total_network_received_mb": round(
            (end_network.bytes_recv - network_start.bytes_recv) / 1024**2,
            2,
        ),
        "metrics": {
            field: numeric_summary(rows, field)
            for field in summary_fields
        },
        "raw_csv": str(csv_path),
    }

    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Kaynak kullanım kayıtları: {csv_path}")
    print(f"Kaynak kullanım özeti: {summary_path}")


if __name__ == "__main__":
    main()
