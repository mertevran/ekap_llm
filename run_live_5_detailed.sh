#!/usr/bin/env bash

set -uo pipefail

PYTHON_BIN=".venv/bin/python3"
SEED="20260810"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
REPORT_DIR="reports/live_db_random_5_${TIMESTAMP}"

mkdir -p "$REPORT_DIR"

DECISION_LOG="$REPORT_DIR/decision_run.log"
RESOURCE_LOG="$REPORT_DIR/resource_monitor.csv"
SUMMARY_FILE="$REPORT_DIR/system_resources_summary.txt"
START_FILE="$REPORT_DIR/start_time.txt"
END_FILE="$REPORT_DIR/end_time.txt"
EXIT_FILE="$REPORT_DIR/exit_code.txt"
OLLAMA_BEFORE="$REPORT_DIR/ollama_ps_before_test.txt"
OLLAMA_AFTER="$REPORT_DIR/ollama_ps_after_test.txt"
SYSTEM_INFO="$REPORT_DIR/system_info.txt"
COMMAND_FILE="$REPORT_DIR/test_command.txt"

echo "============================================================"
echo "5 CANLI IHALE + SISTEM KAYNAK TESTI"
echo "============================================================"
echo "Rapor klasoru : $REPORT_DIR"
echo "Random seed    : $SEED"
echo "Model          : mevcut sistem modeli"
echo "Selection      : database-random"
echo "Source         : database"
echo "Karar sayisi   : 5"
echo "============================================================"
echo


# ============================================================
# 1. SISTEM BILGISI
# ============================================================

{
    echo "=== TARIH ==="
    date --iso-8601=seconds
    echo

    echo "=== KERNEL ==="
    uname -a
    echo

    echo "=== CPU ==="
    lscpu
    echo

    echo "=== BELLEK ==="
    free -h
    echo

    echo "=== DISK ==="
    df -h .
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
} > "$SYSTEM_INFO"


# ============================================================
# 2. TEST ONCESI OLLAMA DURUMU
# ============================================================

ollama ps > "$OLLAMA_BEFORE" 2>&1 || true


# ============================================================
# 3. CALISTIRILACAK KOMUTU RAPORA YAZ
# ============================================================

cat > "$COMMAND_FILE" <<EOF
PYTHONPATH=. $PYTHON_BIN scripts/run_tender_decision_chain.py \
  --selection-mode database-random \
  --source-mode database \
  --max-decisions 5 \
  --random-seed $SEED \
  --report-dir $REPORT_DIR \
  --log-level INFO
EOF


# ============================================================
# 4. KAYNAK IZLEME DOSYASI BASLIGI
# ============================================================

echo \
"timestamp,total_cpu_percent,load1,load5,load15,mem_total_mb,mem_used_mb,mem_available_mb,mem_percent,swap_total_mb,swap_used_mb,swap_percent,python_pid,python_cpu_percent,python_mem_percent,python_rss_mb,ollama_pid,ollama_cpu_percent,ollama_mem_percent,ollama_rss_mb" \
> "$RESOURCE_LOG"


# ============================================================
# 5. BASLANGIC ZAMANI
# ============================================================

START_EPOCH="$(date +%s)"
date --iso-8601=seconds > "$START_FILE"


# ============================================================
# 6. 5 IHALELIK KARAR TESTINI ARKA PLANDA BASLAT
# ============================================================

echo "[TEST] 5 canlı ihale karar zinciri baslatiliyor..."
echo

PYTHONPATH=. "$PYTHON_BIN" scripts/run_tender_decision_chain.py \
  --selection-mode database-random \
  --source-mode database \
  --max-decisions 5 \
  --random-seed "$SEED" \
  --report-dir "$REPORT_DIR" \
  --log-level INFO \
  > >(tee "$DECISION_LOG") 2>&1 &

TEST_PID=$!

echo "$TEST_PID" > "$REPORT_DIR/test_pid.txt"

echo
echo "[TEST PID] $TEST_PID"
echo "[IZLEME] Sistem kaynaklari 2 saniyede bir kaydediliyor."
echo


# ============================================================
# 7. TEST CALISIRKEN SISTEM KAYNAKLARINI IZLE
# ============================================================

while kill -0 "$TEST_PID" 2>/dev/null; do

    TS="$(date --iso-8601=seconds)"

    # --------------------------------------------------------
    # Genel CPU
    # --------------------------------------------------------

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

    [ -z "$CPU_PERCENT" ] && CPU_PERCENT="0"


    # --------------------------------------------------------
    # Load average
    # --------------------------------------------------------

    read -r LOAD1 LOAD5 LOAD15 _ < /proc/loadavg


    # --------------------------------------------------------
    # RAM
    # --------------------------------------------------------

    MEM_TOTAL_KB="$(awk '/MemTotal:/ {print $2}' /proc/meminfo)"
    MEM_AVAILABLE_KB="$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)"

    MEM_USED_KB=$(( MEM_TOTAL_KB - MEM_AVAILABLE_KB ))

    MEM_TOTAL_MB=$(( MEM_TOTAL_KB / 1024 ))
    MEM_USED_MB=$(( MEM_USED_KB / 1024 ))
    MEM_AVAILABLE_MB=$(( MEM_AVAILABLE_KB / 1024 ))

    MEM_PERCENT="$(
        awk -v u="$MEM_USED_KB" -v t="$MEM_TOTAL_KB" \
        'BEGIN {
            if (t > 0)
                printf "%.2f", (u/t)*100
            else
                print "0"
        }'
    )"


    # --------------------------------------------------------
    # Swap
    # --------------------------------------------------------

    SWAP_TOTAL_KB="$(awk '/SwapTotal:/ {print $2}' /proc/meminfo)"
    SWAP_FREE_KB="$(awk '/SwapFree:/ {print $2}' /proc/meminfo)"

    SWAP_USED_KB=$(( SWAP_TOTAL_KB - SWAP_FREE_KB ))

    SWAP_TOTAL_MB=$(( SWAP_TOTAL_KB / 1024 ))
    SWAP_USED_MB=$(( SWAP_USED_KB / 1024 ))

    SWAP_PERCENT="$(
        awk -v u="$SWAP_USED_KB" -v t="$SWAP_TOTAL_KB" \
        'BEGIN {
            if (t > 0)
                printf "%.2f", (u/t)*100
            else
                print "0"
        }'
    )"


    # --------------------------------------------------------
    # Ana Python karar sureci
    # --------------------------------------------------------

    PY_PID="$TEST_PID"

    if ps -p "$PY_PID" >/dev/null 2>&1; then
        read -r PY_CPU PY_MEM PY_RSS_KB <<< "$(
            ps -p "$PY_PID" -o %cpu=,%mem=,rss= 2>/dev/null |
            awk '{print $1, $2, $3}'
        )"
    else
        PY_CPU="0"
        PY_MEM="0"
        PY_RSS_KB="0"
    fi

    PY_RSS_KB="${PY_RSS_KB:-0}"
    PY_RSS_MB=$(( PY_RSS_KB / 1024 ))


    # --------------------------------------------------------
    # Ollama sureci
    # --------------------------------------------------------

    OLLAMA_PID="$(
        pgrep -f 'ollama runner|ollama_llama_server|ollama.*runner' |
        head -1
    )"

    if [ -n "${OLLAMA_PID:-}" ] && ps -p "$OLLAMA_PID" >/dev/null 2>&1; then
        read -r OLLAMA_CPU OLLAMA_MEM OLLAMA_RSS_KB <<< "$(
            ps -p "$OLLAMA_PID" -o %cpu=,%mem=,rss= 2>/dev/null |
            awk '{print $1, $2, $3}'
        )"

        OLLAMA_RSS_KB="${OLLAMA_RSS_KB:-0}"
        OLLAMA_RSS_MB=$(( OLLAMA_RSS_KB / 1024 ))
    else
        OLLAMA_PID=""
        OLLAMA_CPU="0"
        OLLAMA_MEM="0"
        OLLAMA_RSS_MB="0"
    fi


    # --------------------------------------------------------
    # CSV'YE YAZ
    # --------------------------------------------------------

    echo \
"$TS,$CPU_PERCENT,$LOAD1,$LOAD5,$LOAD15,$MEM_TOTAL_MB,$MEM_USED_MB,$MEM_AVAILABLE_MB,$MEM_PERCENT,$SWAP_TOTAL_MB,$SWAP_USED_MB,$SWAP_PERCENT,$PY_PID,${PY_CPU:-0},${PY_MEM:-0},$PY_RSS_MB,${OLLAMA_PID:-},${OLLAMA_CPU:-0},${OLLAMA_MEM:-0},$OLLAMA_RSS_MB" \
    >> "$RESOURCE_LOG"

    sleep 2

done


# ============================================================
# 8. KARAR SURECININ CIKIS KODUNU AL
# ============================================================

wait "$TEST_PID"
TEST_EXIT=$?

echo "$TEST_EXIT" > "$EXIT_FILE"

END_EPOCH="$(date +%s)"
date --iso-8601=seconds > "$END_FILE"

ELAPSED=$(( END_EPOCH - START_EPOCH ))


# ============================================================
# 9. TEST SONRASI OLLAMA DURUMU
# ============================================================

ollama ps > "$OLLAMA_AFTER" 2>&1 || true


# ============================================================
# 10. SISTEM KAYNAK OZETI
# ============================================================

"$PYTHON_BIN" - "$RESOURCE_LOG" "$SUMMARY_FILE" "$ELAPSED" <<'PY'
import csv
import sys
from pathlib import Path

csv_path = Path(sys.argv[1])
summary_path = Path(sys.argv[2])
elapsed = int(sys.argv[3])

rows = []

with csv_path.open(encoding="utf-8") as f:
    reader = csv.DictReader(f)
    rows = list(reader)


def values(field):
    result = []
    for row in rows:
        try:
            result.append(float(row[field]))
        except (ValueError, TypeError, KeyError):
            pass
    return result


def avg(field):
    vals = values(field)
    return sum(vals) / len(vals) if vals else 0.0


def maximum(field):
    vals = values(field)
    return max(vals) if vals else 0.0


hours = elapsed // 3600
minutes = (elapsed % 3600) // 60
seconds = elapsed % 60

text = f"""
============================================================
5 CANLI IHALE - SISTEM KAYNAK OZETI
============================================================

Toplam sure:
  {elapsed} saniye
  {hours:02d}:{minutes:02d}:{seconds:02d}

Ornek sayisi:
  {len(rows)}

CPU:
  Ortalama toplam CPU : %{avg('total_cpu_percent'):.2f}
  Tepe toplam CPU     : %{maximum('total_cpu_percent'):.2f}

RAM:
  Ortalama kullanim   : {avg('mem_used_mb'):.2f} MB
  Tepe kullanim       : {maximum('mem_used_mb'):.2f} MB
  Ortalama yuzde      : %{avg('mem_percent'):.2f}
  Tepe yuzde          : %{maximum('mem_percent'):.2f}

Swap:
  Ortalama kullanim   : {avg('swap_used_mb'):.2f} MB
  Tepe kullanim       : {maximum('swap_used_mb'):.2f} MB
  Tepe yuzde          : %{maximum('swap_percent'):.2f}

Python karar sureci:
  Ortalama CPU        : %{avg('python_cpu_percent'):.2f}
  Tepe CPU            : %{maximum('python_cpu_percent'):.2f}
  Ortalama RSS        : {avg('python_rss_mb'):.2f} MB
  Tepe RSS            : {maximum('python_rss_mb'):.2f} MB

Ollama:
  Ortalama CPU        : %{avg('ollama_cpu_percent'):.2f}
  Tepe CPU            : %{maximum('ollama_cpu_percent'):.2f}
  Ortalama RSS        : {avg('ollama_rss_mb'):.2f} MB
  Tepe RSS            : {maximum('ollama_rss_mb'):.2f} MB

Load average:
  Ortalama 1 dk       : {avg('load1'):.2f}
  Tepe 1 dk           : {maximum('load1'):.2f}

============================================================
"""

summary_path.write_text(text.strip() + "\n", encoding="utf-8")

print()
print(text)
PY


# ============================================================
# 11. RAPOR DOSYALARINI LISTELE
# ============================================================

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
echo "============================================================"
echo "ONEMLI RAPORLAR"
echo "============================================================"
echo "Karar logu       : $DECISION_LOG"
echo "Kaynak CSV       : $RESOURCE_LOG"
echo "Kaynak ozeti     : $SUMMARY_FILE"
echo "Sistem bilgisi   : $SYSTEM_INFO"
echo "Ollama once      : $OLLAMA_BEFORE"
echo "Ollama sonra     : $OLLAMA_AFTER"
echo "============================================================"

# Terminali kapatacak exit komutu kullanilmiyor.

