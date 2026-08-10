#!/usr/bin/env bash

set -uo pipefail

PYTHON_BIN=".venv/bin/python3"
TS="$(date +%Y%m%d_%H%M%S)"
REPORT_DIR="reports/live_sequential_10_${TS}"

mkdir -p "$REPORT_DIR"

DECISION_LOG="$REPORT_DIR/decision_run.log"
RESOURCE_LOG="$REPORT_DIR/resource_monitor.csv"
SUMMARY_FILE="$REPORT_DIR/system_resources_summary.txt"

echo "============================================================"
echo "10 EN YENI AKTIF IHALE - CANLI TEST"
echo "============================================================"
echo "Rapor klasoru : $REPORT_DIR"
echo "Secim modu    : database-sequential"
echo "Kaynak modu   : database"
echo "Karar sayisi  : 10"
echo "============================================================"
echo


# ============================================================
# 1. TEST ONCESI SISTEM BILGISI
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
# 2. CALISTIRILACAK KOMUT
# ============================================================

cat > "$REPORT_DIR/test_command.txt" <<EOF
PYTHONPATH=. $PYTHON_BIN scripts/run_tender_decision_chain.py \
  --selection-mode database-sequential \
  --source-mode database \
  --max-decisions 10 \
  --report-dir $REPORT_DIR \
  --log-level INFO
EOF


# ============================================================
# 3. KAYNAK RAPORU BASLIGI
# ============================================================

echo \
"timestamp,total_cpu_percent,load1,load5,load15,mem_total_mb,mem_used_mb,mem_available_mb,mem_percent,swap_total_mb,swap_used_mb,swap_percent,python_cpu_percent,python_mem_percent,python_rss_mb,ollama_cpu_percent,ollama_mem_percent,ollama_rss_mb" \
> "$RESOURCE_LOG"


# ============================================================
# 4. CANLI TESTI BASLAT
# ============================================================

date --iso-8601=seconds > "$REPORT_DIR/start_time.txt"
START_EPOCH="$(date +%s)"

echo "[TEST] 10 en yeni aktif ihale karar zinciri baslatiliyor..."
echo

PYTHONPATH=. "$PYTHON_BIN" scripts/run_tender_decision_chain.py \
    --selection-mode database-sequential \
    --source-mode database \
    --max-decisions 10 \
    --report-dir "$REPORT_DIR" \
    --log-level INFO \
    > >(tee "$DECISION_LOG") 2>&1 &

TEST_PID=$!

echo "$TEST_PID" > "$REPORT_DIR/test_pid.txt"

echo
echo "Karar sureci PID : $TEST_PID"
echo "Kaynak olcumu    : 2 saniyede bir"
echo


# ============================================================
# 5. CPU / RAM / SWAP / PYTHON / OLLAMA IZLE
# ============================================================

while kill -0 "$TEST_PID" 2>/dev/null; do

    NOW="$(date --iso-8601=seconds)"

    # Genel CPU
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

    # Load average
    read -r LOAD1 LOAD5 LOAD15 _ < /proc/loadavg

    # RAM
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

    # Swap
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

    # Python karar sureci
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

    # Tum Ollama sureclerinin toplam kullanimi
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
# 6. TEST SONUCU
# ============================================================

wait "$TEST_PID"
TEST_EXIT=$?

END_EPOCH="$(date +%s)"
ELAPSED=$((END_EPOCH - START_EPOCH))

date --iso-8601=seconds > "$REPORT_DIR/end_time.txt"
echo "$TEST_EXIT" > "$REPORT_DIR/exit_code.txt"

ollama ps > "$REPORT_DIR/ollama_ps_after_test.txt" 2>&1 || true


# ============================================================
# 7. KAYNAK + MODEL PERFORMANS OZETI
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
    v = nums(field)
    return sum(v) / len(v) if v else 0.0


def peak(field):
    v = nums(field)
    return max(v) if v else 0.0


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
    if "[CONTEXT_DIAGNOSTICS]" not in line:
        continue

    m = re.search(
        r"\bfinal_prompt_chars=([0-9]+)",
        line,
    )

    if m:
        prompt_chars.append(float(m.group(1)))


def mean(v):
    return sum(v) / len(v) if v else 0.0


h = elapsed // 3600
m = (elapsed % 3600) // 60
s = elapsed % 60

summary = f"""
============================================================
10 EN YENI IHALE - CANLI TEST OZETI
============================================================

Toplam sure:
  {elapsed} saniye
  {h:02d}:{m:02d}:{s:02d}

Ihale basina ortalama duvar saati:
  {elapsed / 10:.2f} saniye

Kaynak ornek sayisi:
  {len(rows)}

------------------------------------------------------------
CPU
------------------------------------------------------------

Ortalama toplam CPU:
  %{avg('total_cpu_percent'):.2f}

Tepe toplam CPU:
  %{peak('total_cpu_percent'):.2f}

------------------------------------------------------------
RAM
------------------------------------------------------------

Ortalama RAM:
  {avg('mem_used_mb'):.2f} MB

Tepe RAM:
  {peak('mem_used_mb'):.2f} MB

Ortalama RAM:
  %{avg('mem_percent'):.2f}

Tepe RAM:
  %{peak('mem_percent'):.2f}

------------------------------------------------------------
SWAP
------------------------------------------------------------

Ortalama swap:
  {avg('swap_used_mb'):.2f} MB

Tepe swap:
  {peak('swap_used_mb'):.2f} MB

Tepe swap:
  %{peak('swap_percent'):.2f}

------------------------------------------------------------
PYTHON
------------------------------------------------------------

Ortalama CPU:
  %{avg('python_cpu_percent'):.2f}

Tepe CPU:
  %{peak('python_cpu_percent'):.2f}

Ortalama RSS:
  {avg('python_rss_mb'):.2f} MB

Tepe RSS:
  {peak('python_rss_mb'):.2f} MB

------------------------------------------------------------
OLLAMA
------------------------------------------------------------

Ortalama CPU:
  %{avg('ollama_cpu_percent'):.2f}

Tepe CPU:
  %{peak('ollama_cpu_percent'):.2f}

Ortalama RSS:
  {avg('ollama_rss_mb'):.2f} MB

Tepe RSS:
  {peak('ollama_rss_mb'):.2f} MB

------------------------------------------------------------
QWEN
------------------------------------------------------------

Model cagrisi:
  {len(diagnostics)}

Ortalama prompt token:
  {mean(prompt_tokens):.2f}

Ortalama output token:
  {mean(output_tokens):.2f}

Ortalama model suresi:
  {mean([v / 1_000_000_000 for v in total_ns]):.2f} saniye

Ortalama prompt hizi:
  {mean(prompt_speed):.2f} token/s

Ortalama generation hizi:
  {mean(generation_speed):.2f} token/s

------------------------------------------------------------
CONTEXT
------------------------------------------------------------

Ortalama final prompt karakteri:
  {mean(prompt_chars):.2f}

En buyuk final prompt:
  {max(prompt_chars) if prompt_chars else 0:.0f}

============================================================
"""

summary_file.write_text(
    summary.strip() + "\n",
    encoding="utf-8",
)

print(summary)
PY


# ============================================================
# 8. HIZLI TEST OZETI
# ============================================================

echo
echo "============================================================"
echo "SECILEN IHALELER"
echo "============================================================"

grep '\[DATABASE_SEQUENTIAL_SELECTION\]' "$DECISION_LOG" || true

echo
echo "============================================================"
echo "COMPACT CONTEXT"
echo "============================================================"

grep '\[COMPANY_CONTEXT_MODE\]' "$DECISION_LOG" || true

echo
echo "============================================================"
echo "NIHAI KARARLAR"
echo "============================================================"

grep -E 'karar=(uygun|uygun_degil|inceleme_gerekli)' \
    "$DECISION_LOG" || true

echo
echo "============================================================"
echo "TEST TAMAMLANDI"
echo "============================================================"
echo "Cikis kodu   : $TEST_EXIT"
echo "Toplam sure  : $ELAPSED saniye"
echo "Rapor klasoru: $REPORT_DIR"
echo
echo "Olusan dosyalar:"
find "$REPORT_DIR" -maxdepth 1 -type f -printf '  %f\n' | sort
echo
echo "Terminal acik kalacak."
echo "============================================================"

