#!/usr/bin/env bash

set -uo pipefail

PYTHON_BIN=".venv/bin/python3"
TS="$(date +%Y%m%d_%H%M%S)"
REPORT_DIR="reports/faiss_random_20_${TS}"

mkdir -p "$REPORT_DIR"

DECISION_LOG="$REPORT_DIR/decision_run.log"
RESOURCE_LOG="$REPORT_DIR/resource_monitor.csv"
SUMMARY_FILE="$REPORT_DIR/system_resources_summary.txt"

echo "============================================================"
echo "20 RANDOM FAISS IHALE - CANLI TEST + SISTEM IZLEME"
echo "============================================================"
echo "Rapor klasoru : $REPORT_DIR"
echo "Selection mode : candidate-pool"
echo "Source mode    : faiss"
echo "Karar sayisi   : 20"
echo "Random seed    : 20260810"
echo "============================================================"
echo


# ============================================================
# 1. SISTEM BILGISI
# ============================================================

{
    echo "=== TARIH ==="
    date --iso-8601=seconds
    echo

    echo "=== CPU ==="
    lscpu
    echo

    echo "=== RAM / SWAP ==="
    free -h
    echo

    echo "=== PYTHON ==="
    "$PYTHON_BIN" --version
    echo

    echo "=== OLLAMA ==="
    ollama --version 2>&1 || true
    echo

    echo "=== OLLAMA MODELLERI ==="
    ollama list 2>&1 || true
    echo

    echo "=== OLLAMA PS BEFORE ==="
    ollama ps 2>&1 || true

} > "$REPORT_DIR/system_info.txt"


# ============================================================
# 2. KULLANILAN KOMUTU KAYDET
# ============================================================

cat > "$REPORT_DIR/test_command.txt" <<EOF
PYTHONPATH=. $PYTHON_BIN scripts/run_tender_decision_chain.py \
  --selection-mode candidate-pool \
  --source-mode faiss \
  --max-decisions 20 \
  --random-seed 20260810 \
  --report-dir $REPORT_DIR \
  --log-level INFO
EOF


# ============================================================
# 3. KAYNAK CSV BASLIGI
# ============================================================

echo \
"timestamp,total_cpu_percent,load1,load5,load15,mem_total_mb,mem_used_mb,mem_available_mb,mem_percent,swap_total_mb,swap_used_mb,swap_percent,python_cpu_percent,python_mem_percent,python_rss_mb,ollama_cpu_percent,ollama_mem_percent,ollama_rss_mb" \
> "$RESOURCE_LOG"


# ============================================================
# 4. TESTI BASLAT
# ============================================================

date --iso-8601=seconds > "$REPORT_DIR/start_time.txt"
START_EPOCH="$(date +%s)"

echo "[TEST] 20 FAISS ihale karar testi baslatiliyor..."
echo

PYTHONPATH=. "$PYTHON_BIN" scripts/run_tender_decision_chain.py \
    --selection-mode candidate-pool \
    --source-mode faiss \
    --max-decisions 20 \
    --random-seed 20260810 \
    --report-dir "$REPORT_DIR" \
    --log-level INFO \
    > >(tee "$DECISION_LOG") 2>&1 &

TEST_PID=$!

echo "$TEST_PID" > "$REPORT_DIR/test_pid.txt"

echo "Karar sureci PID : $TEST_PID"
echo "Kaynak olcumu    : 2 saniyede bir"
echo


# ============================================================
# 5. SISTEM KAYNAKLARINI IZLE
# ============================================================

while kill -0 "$TEST_PID" 2>/dev/null; do

    NOW="$(date --iso-8601=seconds)"

    CPU_PERCENT="$(
        top -bn1 |
        awk '/Cpu\(s\)/ {
            for (i=1; i<=NF; i++) {
                if ($i ~ /id,/) {
                    gsub(",", "", $(i-1));
                    printf "%.2f", 100-$(i-1);
                    exit
                }
            }
        }'
    )"

    [ -z "${CPU_PERCENT:-}" ] && CPU_PERCENT="0"

    read -r LOAD1 LOAD5 LOAD15 _ < /proc/loadavg

    MEM_TOTAL_KB="$(awk '/MemTotal:/ {print $2}' /proc/meminfo)"
    MEM_AVAILABLE_KB="$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)"
    MEM_USED_KB=$((MEM_TOTAL_KB - MEM_AVAILABLE_KB))

    MEM_TOTAL_MB=$((MEM_TOTAL_KB / 1024))
    MEM_USED_MB=$((MEM_USED_KB / 1024))
    MEM_AVAILABLE_MB=$((MEM_AVAILABLE_KB / 1024))

    MEM_PERCENT="$(
        awk -v u="$MEM_USED_KB" -v t="$MEM_TOTAL_KB" \
        'BEGIN {
            if (t > 0) printf "%.2f", (u/t)*100;
            else print "0"
        }'
    )"

    SWAP_TOTAL_KB="$(awk '/SwapTotal:/ {print $2}' /proc/meminfo)"
    SWAP_FREE_KB="$(awk '/SwapFree:/ {print $2}' /proc/meminfo)"
    SWAP_USED_KB=$((SWAP_TOTAL_KB - SWAP_FREE_KB))

    SWAP_TOTAL_MB=$((SWAP_TOTAL_KB / 1024))
    SWAP_USED_MB=$((SWAP_USED_KB / 1024))

    SWAP_PERCENT="$(
        awk -v u="$SWAP_USED_KB" -v t="$SWAP_TOTAL_KB" \
        'BEGIN {
            if (t > 0) printf "%.2f", (u/t)*100;
            else print "0"
        }'
    )"

    if ps -p "$TEST_PID" >/dev/null 2>&1; then
        read -r PY_CPU PY_MEM PY_RSS_KB <<< "$(
            ps -p "$TEST_PID" -o %cpu=,%mem=,rss= |
            awk '{print $1, $2, $3}'
        )"
    else
        PY_CPU="0"
        PY_MEM="0"
        PY_RSS_KB="0"
    fi

    PY_CPU="${PY_CPU:-0}"
    PY_MEM="${PY_MEM:-0}"
    PY_RSS_KB="${PY_RSS_KB:-0}"
    PY_RSS_MB=$((PY_RSS_KB / 1024))

    read -r OLLAMA_CPU OLLAMA_MEM OLLAMA_RSS_KB <<< "$(
        ps -eo comm=,%cpu=,%mem=,rss= |
        awk '
            tolower($1) == "ollama" {
                cpu += $2
                mem += $3
                rss += $4
            }
            END {
                printf "%.2f %.2f %.0f", cpu+0, mem+0, rss+0
            }
        '
    )"

    OLLAMA_CPU="${OLLAMA_CPU:-0}"
    OLLAMA_MEM="${OLLAMA_MEM:-0}"
    OLLAMA_RSS_KB="${OLLAMA_RSS_KB:-0}"
    OLLAMA_RSS_MB=$((OLLAMA_RSS_KB / 1024))

    echo \
"$NOW,$CPU_PERCENT,$LOAD1,$LOAD5,$LOAD15,$MEM_TOTAL_MB,$MEM_USED_MB,$MEM_AVAILABLE_MB,$MEM_PERCENT,$SWAP_TOTAL_MB,$SWAP_USED_MB,$SWAP_PERCENT,$PY_CPU,$PY_MEM,$PY_RSS_MB,$OLLAMA_CPU,$OLLAMA_MEM,$OLLAMA_RSS_MB" \
    >> "$RESOURCE_LOG"

    sleep 2

done


# ============================================================
# 6. TEST SONU
# ============================================================

wait "$TEST_PID"
TEST_EXIT=$?

END_EPOCH="$(date +%s)"
ELAPSED=$((END_EPOCH - START_EPOCH))

date --iso-8601=seconds > "$REPORT_DIR/end_time.txt"
echo "$TEST_EXIT" > "$REPORT_DIR/exit_code.txt"

ollama ps > "$REPORT_DIR/ollama_ps_after_test.txt" 2>&1 || true


# ============================================================
# 7. OTOMATIK SISTEM OZETI
# ============================================================

"$PYTHON_BIN" - \
    "$RESOURCE_LOG" \
    "$DECISION_LOG" \
    "$SUMMARY_FILE" \
    "$ELAPSED" <<'PY'

import csv
import re
import sys
from pathlib import Path

resource_file = Path(sys.argv[1])
decision_log = Path(sys.argv[2])
summary_file = Path(sys.argv[3])
elapsed = int(sys.argv[4])

with resource_file.open(encoding="utf-8") as f:
    rows = list(csv.DictReader(f))


def nums(field):
    out = []
    for row in rows:
        try:
            out.append(float(row[field]))
        except Exception:
            pass
    return out


def avg(field):
    vals = nums(field)
    return sum(vals) / len(vals) if vals else 0.0


def peak(field):
    vals = nums(field)
    return max(vals) if vals else 0.0


log = decision_log.read_text(
    encoding="utf-8",
    errors="replace",
)

diagnostics = [
    line for line in log.splitlines()
    if "[DIAGNOSTICS]" in line
]


def collect(name):
    values = []

    for line in diagnostics:
        m = re.search(
            rf"\b{name}=([0-9.]+)",
            line,
        )

        if m:
            values.append(float(m.group(1)))

    return values


prompt_tokens = collect("prompt_eval_count")
output_tokens = collect("eval_count")
total_ns = collect("total_duration")
prompt_speed = collect("prompt_tokens_per_second")
generation_speed = collect("generation_tokens_per_second")

prompt_chars = []

for line in log.splitlines():
    if "[CONTEXT_DIAGNOSTICS]" in line:
        m = re.search(
            r"\bfinal_prompt_chars=([0-9]+)",
            line,
        )

        if m:
            prompt_chars.append(float(m.group(1)))


def mean(values):
    return sum(values) / len(values) if values else 0.0


hours = elapsed // 3600
minutes = (elapsed % 3600) // 60
seconds = elapsed % 60

summary = f"""
============================================================
20 FAISS RANDOM IHALE - SISTEM KAYNAK OZETI
============================================================

Toplam sure:
  {elapsed} saniye
  {hours:02d}:{minutes:02d}:{seconds:02d}

Ihale basina ortalama duvar saati:
  {elapsed / 20:.2f} saniye

Kaynak ornek sayisi:
  {len(rows)}

CPU:
  Ortalama toplam CPU : %{avg('total_cpu_percent'):.2f}
  Tepe toplam CPU     : %{peak('total_cpu_percent'):.2f}

RAM:
  Ortalama            : {avg('mem_used_mb'):.2f} MB
  Tepe                : {peak('mem_used_mb'):.2f} MB
  Ortalama yuzde      : %{avg('mem_percent'):.2f}
  Tepe yuzde          : %{peak('mem_percent'):.2f}

Swap:
  Ortalama            : {avg('swap_used_mb'):.2f} MB
  Tepe                : {peak('swap_used_mb'):.2f} MB
  Tepe yuzde          : %{peak('swap_percent'):.2f}

Python:
  Ortalama CPU        : %{avg('python_cpu_percent'):.2f}
  Tepe CPU            : %{peak('python_cpu_percent'):.2f}
  Ortalama RSS        : {avg('python_rss_mb'):.2f} MB
  Tepe RSS            : {peak('python_rss_mb'):.2f} MB

Ollama:
  Ortalama CPU        : %{avg('ollama_cpu_percent'):.2f}
  Tepe CPU            : %{peak('ollama_cpu_percent'):.2f}
  Ortalama RSS        : {avg('ollama_rss_mb'):.2f} MB
  Tepe RSS            : {peak('ollama_rss_mb'):.2f} MB

Qwen:
  Model cagrisi       : {len(diagnostics)}
  Ortalama prompt     : {mean(prompt_tokens):.2f} token
  Ortalama output     : {mean(output_tokens):.2f} token
  Ortalama model sure : {mean([x / 1_000_000_000 for x in total_ns]):.2f} saniye
  Ortalama prompt hiz : {mean(prompt_speed):.2f} token/s
  Ortalama uretim hiz : {mean(generation_speed):.2f} token/s

Context:
  Ortalama prompt karakteri : {mean(prompt_chars):.2f}
  En buyuk prompt            : {max(prompt_chars) if prompt_chars else 0:.0f}

============================================================
"""

summary_file.write_text(
    summary.strip() + "\n",
    encoding="utf-8",
)

print(summary)
PY


echo
echo "============================================================"
echo "20 IHALE TESTI TAMAMLANDI"
echo "============================================================"
echo "Cikis kodu   : $TEST_EXIT"
echo "Toplam sure  : $ELAPSED saniye"
echo "Rapor klasoru: $REPORT_DIR"
echo
echo "Dosyalar:"
find "$REPORT_DIR" -maxdepth 1 -type f -printf '  %f\n' | sort
echo
echo "Terminal acik kalacak."
echo "============================================================"

