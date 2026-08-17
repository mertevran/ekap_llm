#!/usr/bin/env bash

set -u

ROOT_DIR="$(pwd)"
BENCH_TS="$(date '+%Y%m%d_%H%M%S')"
BENCH_ROOT="$ROOT_DIR/reports/4model_benchmark_${BENCH_TS}"

mkdir -p "$BENCH_ROOT"

MODELS=(
  "qwen3.5:4b-q4_K_M"
  "phi4-mini:3.8b-q4_K_M"
  "gemma3:4b-it-q4_K_M"
  "hf.co/ytu-ce-cosmos/Turkish-Gemma-9b-T1-GGUF:Q4_K_M"
)

SLUGS=(
  "qwen35_4b"
  "phi4mini_38b"
  "gemma3_4b"
  "turkish_gemma_9b"
)

RANDOM_SEED=20260814
PROFILE_CODE="AUS-04"
MAX_DECISIONS=10

echo "============================================================"
echo "4 MODEL BENCHMARK BAŞLADI"
echo "Başlangıç: $(date '+%Y-%m-%d %H:%M:%S %z')"
echo "Rapor: $BENCH_ROOT"
echo "============================================================"

cat > "$BENCH_ROOT/benchmark_settings.txt" <<EOF
benchmark_start=$(date '+%Y-%m-%d %H:%M:%S %z')
profile_code=$PROFILE_CODE
max_decisions=$MAX_DECISIONS
random_seed=$RANDOM_SEED
selection_mode=candidate-pool
source_mode=database
num_ctx=8192
num_predict=512
ollama_num_thread=4
ollama_num_batch=64
compact_context=true
gpu=0
EOF

for i in "${!MODELS[@]}"; do

    MODEL="${MODELS[$i]}"
    SLUG="${SLUGS[$i]}"

    RUN_DIR="$BENCH_ROOT/$SLUG"
    DECISION_DIR="$RUN_DIR/decision_reports"

    mkdir -p "$RUN_DIR" "$DECISION_DIR"

    echo
    echo "============================================================"
    echo "MODEL $((i+1))/4"
    echo "$MODEL"
    echo "============================================================"

    # Önce bellekteki modelleri boşalt.
    for M in "${MODELS[@]}"; do
        ollama stop "$M" >/dev/null 2>&1 || true
    done

    sleep 3

    date '+%Y-%m-%d %H:%M:%S %z' \
      > "$RUN_DIR/system_start_time.txt"

    date +%s.%N \
      > "$RUN_DIR/start_epoch.txt"

    {
        echo "MODEL=$MODEL"
        echo "SLUG=$SLUG"
        echo "PROFILE_CODE=$PROFILE_CODE"
        echo "MAX_DECISIONS=$MAX_DECISIONS"
        echo "RANDOM_SEED=$RANDOM_SEED"
        echo "NUM_CTX=8192"
        echo "NUM_PREDICT=512"
        echo "OLLAMA_NUM_THREAD=4"
        echo "OLLAMA_NUM_BATCH=64"
        echo "GPU=0"
        echo
        echo "=== SYSTEM ==="
        uname -a
        echo
        lscpu
        echo
        free -h
        echo
        ollama ps || true
    } > "$RUN_DIR/system_info_before.txt" 2>&1

    echo \
'timestamp,python_rss_mb,ollama_runner_rss_mb,system_ram_used_mb,swap_used_mb,cpu_percent' \
      > "$RUN_DIR/resource_monitor.csv"

    # ---------------------------------------------------------
    # Kaynak izleyici
    # ---------------------------------------------------------
    python -u - "$RUN_DIR/resource_monitor.csv" <<'PY' &
import csv
import os
import sys
import time

import psutil

output = sys.argv[1]

with open(output, "a", encoding="utf-8", newline="") as f:
    writer = csv.writer(f)

    while True:
        python_rss = 0
        ollama_runner_rss = 0

        for proc in psutil.process_iter(
            ["pid", "cmdline", "memory_info"]
        ):
            try:
                cmd = " ".join(proc.info.get("cmdline") or [])
                rss = proc.info["memory_info"].rss

                if (
                    "scripts/run_tender_decision_chain.py"
                    in cmd
                ):
                    python_rss += rss

                if (
                    "ollama" in cmd.lower()
                    and "runner" in cmd.lower()
                ):
                    ollama_runner_rss += rss

            except (
                psutil.NoSuchProcess,
                psutil.AccessDenied,
                KeyError,
            ):
                continue

        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        cpu = psutil.cpu_percent(interval=None)

        writer.writerow([
            time.strftime("%Y-%m-%d %H:%M:%S"),
            round(python_rss / 1024 / 1024, 2),
            round(ollama_runner_rss / 1024 / 1024, 2),
            round(vm.used / 1024 / 1024, 2),
            round(swap.used / 1024 / 1024, 2),
            round(cpu, 2),
        ])

        f.flush()
        time.sleep(1)
PY

    MON_PID=$!
    echo "$MON_PID" > "$RUN_DIR/monitor_pid.txt"

    # ---------------------------------------------------------
    # Asıl model testi
    # ---------------------------------------------------------
    set +e

    QWEN_MODEL="$MODEL" \
    QWEN_DECISION_NUM_CTX=8192 \
    QWEN_DECISION_NUM_PREDICT=512 \
    OLLAMA_NUM_THREAD=4 \
    OLLAMA_NUM_BATCH=64 \
    OLLAMA_NUM_GPU=0 \
    PYTHONPATH=. \
    python -u scripts/run_tender_decision_chain.py \
      --profile-code "$PROFILE_CODE" \
      --limit-per-profile 10 \
      --max-decisions "$MAX_DECISIONS" \
      --random-seed "$RANDOM_SEED" \
      --selection-mode candidate-pool \
      --source-mode database \
      --report-dir "$DECISION_DIR" \
      --log-level INFO \
      2>&1 | tee "$RUN_DIR/test_terminal.log"

    TEST_EXIT=${PIPESTATUS[0]}

    set -e

    echo "$TEST_EXIT" > "$RUN_DIR/exit_code.txt"

    kill "$MON_PID" 2>/dev/null || true
    wait "$MON_PID" 2>/dev/null || true

    date '+%Y-%m-%d %H:%M:%S %z' \
      > "$RUN_DIR/system_end_time.txt"

    date +%s.%N \
      > "$RUN_DIR/end_epoch.txt"

    {
        echo "=== SYSTEM AFTER ==="
        free -h
        echo
        echo "=== OLLAMA AFTER ==="
        ollama ps || true
    } > "$RUN_DIR/system_info_after.txt" 2>&1

    # ---------------------------------------------------------
    # Kaynak özeti
    # ---------------------------------------------------------
    python - "$RUN_DIR" "$TEST_EXIT" <<'PY'
import csv
import json
import sys
from pathlib import Path

run_dir = Path(sys.argv[1])
exit_code = int(sys.argv[2])

start = float(
    (run_dir / "start_epoch.txt").read_text().strip()
)

end = float(
    (run_dir / "end_epoch.txt").read_text().strip()
)

with (
    run_dir / "resource_monitor.csv"
).open(
    encoding="utf-8",
    newline="",
) as f:
    rows = list(csv.DictReader(f))


def vals(name):
    result = []

    for row in rows:
        try:
            result.append(float(row[name]))
        except Exception:
            pass

    return result


python_rss = vals("python_rss_mb")
ollama_rss = vals("ollama_runner_rss_mb")
ram = vals("system_ram_used_mb")
swap = vals("swap_used_mb")
cpu = vals("cpu_percent")

summary = {
    "exit_code": exit_code,
    "system_start_time": (
        run_dir / "system_start_time.txt"
    ).read_text().strip(),
    "system_end_time": (
        run_dir / "system_end_time.txt"
    ).read_text().strip(),
    "system_elapsed_seconds": round(
        end - start,
        3,
    ),
    "samples": len(rows),
    "peak_python_rss_mb": round(
        max(python_rss, default=0),
        2,
    ),
    "peak_ollama_runner_rss_mb": round(
        max(ollama_rss, default=0),
        2,
    ),
    "peak_system_ram_used_mb": round(
        max(ram, default=0),
        2,
    ),
    "peak_swap_used_mb": round(
        max(swap, default=0),
        2,
    ),
    "peak_cpu_percent": round(
        max(cpu, default=0),
        2,
    ),
    "avg_system_ram_used_mb": round(
        sum(ram) / len(ram),
        2,
    ) if ram else 0,
    "avg_cpu_percent": round(
        sum(cpu) / len(cpu),
        2,
    ) if cpu else 0,
}

(
    run_dir / "system_monitor_summary.json"
).write_text(
    json.dumps(
        summary,
        indent=2,
        ensure_ascii=False,
    ),
    encoding="utf-8",
)

print(json.dumps(summary, indent=2))
PY

    # DIAGNOSTICS satırlarını ayrıca ayır.
    grep '\[DIAGNOSTICS\]' \
      "$RUN_DIR/test_terminal.log" \
      > "$RUN_DIR/ollama_diagnostics.log" \
      || true

    # Kesilmiş çıktıları ayrıca ayır.
    grep -E \
      'done_reason=length|TruncatedModelOutput|Kesilmiş çıktı' \
      "$RUN_DIR/test_terminal.log" \
      > "$RUN_DIR/truncated_outputs.log" \
      || true

    echo
    echo "MODEL TAMAMLANDI: $MODEL"
    echo "EXIT_CODE=$TEST_EXIT"
    echo "RAPOR=$RUN_DIR"

    # Koşudan sonra model belleğini boşalt.
    ollama stop "$MODEL" >/dev/null 2>&1 || true
    sleep 5

done

date '+%Y-%m-%d %H:%M:%S %z' \
  > "$BENCH_ROOT/benchmark_end_time.txt"

echo
echo "============================================================"
echo "4 MODEL BENCHMARK TAMAMLANDI"
echo "Bitiş: $(cat "$BENCH_ROOT/benchmark_end_time.txt")"
echo "Rapor kökü:"
echo "$BENCH_ROOT"
echo "============================================================"
