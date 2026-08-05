"""
PostgreSQL ihale durumu dağılımı ve İSBAK idare adı denetimi.
Bu betik gerçek DB'ye bağlanarak:
1. ihale_durumu değer dağılımını raporlar
2. takip_durumu değer dağılımını raporlar
3. İSBAK benzeri idare adlarını listeler
4. Sonuçları reports/ altına yazar
"""

import csv
import json
import re
import sys
import unicodedata
from pathlib import Path

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    print("HATA: psycopg yüklü değil.")
    sys.exit(1)

# DB bağlantısı .env'den
import os

from dotenv import load_dotenv

load_dotenv()

HOST = os.environ.get("DATABASE_HOST", "")
PORT = os.environ.get("DATABASE_PORT", "5432")
DBNAME = os.environ.get("DATABASE_NAME", "")
USER = os.environ.get("DATABASE_USER", "")
PASSWORD = os.environ.get("DATABASE_PASSWORD", "")

MISSING_VARS = []
for name, val in [
    ("DATABASE_HOST", HOST),
    ("DATABASE_NAME", DBNAME),
    ("DATABASE_USER", USER),
    ("DATABASE_PASSWORD", PASSWORD),
]:
    if not val:
        MISSING_VARS.append(name)

if MISSING_VARS:
    print(f"ORTAM BAĞIMLILIĞI NEDENİYLE DOĞRULANAMADI. Eksik değişkenler: {MISSING_VARS}")
    sys.exit(2)

REPORTS = Path("reports")
REPORTS.mkdir(exist_ok=True)


def connect():
    return psycopg.connect(
        f"host={HOST} port={PORT} dbname={DBNAME} user={USER} password={PASSWORD} connect_timeout=10"
    )


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return " ".join(t.split())


# ─── 1. ihale_durumu dağılımı ─────────────────────────────────────────────────
print("1/5 ihale_durumu dağılımı okunuyor...")
with connect() as conn:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT ihale_durumu, COUNT(*) AS sayi
            FROM public.tenders
            GROUP BY ihale_durumu
            ORDER BY sayi DESC
        """)
        ihale_durumu_rows = cur.fetchall()

with open(REPORTS / "tender_status_distribution.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["ihale_durumu", "sayi"])
    for row in ihale_durumu_rows:
        w.writerow([row["ihale_durumu"], row["sayi"]])

print(f"  → {len(ihale_durumu_rows)} farklı ihale_durumu değeri bulundu.")
for r in ihale_durumu_rows:
    print(f"     {r['sayi']:>6}  {r['ihale_durumu']}")

# ─── 2. takip_durumu dağılımı ─────────────────────────────────────────────────
print("\n2/5 takip_durumu dağılımı okunuyor...")
with connect() as conn:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT takip_durumu, COUNT(*) AS sayi
            FROM public.tenders
            GROUP BY takip_durumu
            ORDER BY sayi DESC
        """)
        takip_durumu_rows = cur.fetchall()

print(f"  → {len(takip_durumu_rows)} farklı takip_durumu değeri bulundu.")
for r in takip_durumu_rows:
    print(f"     {r['sayi']:>6}  {r['takip_durumu']}")

# ─── 3. İSBAK idare adı varyasyonları ─────────────────────────────────────────
print("\n3/5 İSBAK benzeri idare adları okunuyor...")
with connect() as conn:
    with conn.cursor(row_factory=dict_row) as cur:
        try:
            cur.execute("""
                SELECT DISTINCT idare_adi, COUNT(*) AS ihale_sayisi
                FROM public.tenders
                WHERE 
                    idare_adi ILIKE '%isbak%'
                    OR idare_adi ILIKE '%istanbul bilişim%'
                    OR idare_adi ILIKE '%akıllı kent%'
                GROUP BY idare_adi
                ORDER BY ihale_sayisi DESC
                LIMIT 50
            """)
            isbak_rows = cur.fetchall()
        except Exception:
            isbak_rows = []

# unaccent yoksa fallback
if not isbak_rows:
    with connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT DISTINCT idare_adi, COUNT(*) AS ihale_sayisi
                FROM public.tenders
                WHERE idare_adi ILIKE '%isbak%' OR idare_adi ILIKE '%istanbul bilişim%'
                GROUP BY idare_adi
                ORDER BY ihale_sayisi DESC
                LIMIT 50
            """)
            isbak_rows = cur.fetchall()

print(f"  → {len(isbak_rows)} İSBAK benzeri idare adı bulundu:")
for r in isbak_rows:
    print(f"     {r['ihale_sayisi']:>6}  {r['idare_adi']}")

with open(REPORTS / "isbak_authority_variants.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["idare_adi", "ihale_sayisi", "normalized"])
    for r in isbak_rows:
        w.writerow([r["idare_adi"], r["ihale_sayisi"], _norm(r["idare_adi"])])

# ─── 4. Aktif ihale sayıları ──────────────────────────────────────────────────
print("\n4/5 Toplam ve aktif ihale sayısı okunuyor...")
with connect() as conn:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT COUNT(*) AS toplam FROM public.tenders")
        total = cur.fetchone()["toplam"]

        # En olası aktif değerleri bul
        active_candidates = [
            r["ihale_durumu"]
            for r in ihale_durumu_rows
            if r["ihale_durumu"]
            and (
                "açık" in (r["ihale_durumu"] or "").lower()
                or "yayım" in (r["ihale_durumu"] or "").lower()
                or "aktif" in (r["ihale_durumu"] or "").lower()
            )
        ]

        cur.execute(
            """
            SELECT ihale_durumu, COUNT(*) AS sayi
            FROM public.tenders
            WHERE ihale_durumu = ANY(%s)
            GROUP BY ihale_durumu
        """,
            (active_candidates,),
        )
        active_rows = cur.fetchall()
        active_count = sum(r["sayi"] for r in active_rows)

print(f"  → Toplam ihale: {total}")
print(f"  → Aday aktif durum değerleri: {active_candidates}")
print(f"  → Bu değerlere sahip ihale sayısı: {active_count}")

# ─── 5. Özet rapor ────────────────────────────────────────────────────────────
print("\n5/5 Özet rapor yazılıyor...")
summary = {
    "toplam_ihale": total,
    "ihale_durumu_dagilimi": [
        {"deger": r["ihale_durumu"], "sayi": r["sayi"]} for r in ihale_durumu_rows
    ],
    "takip_durumu_dagilimi": [
        {"deger": r["takip_durumu"], "sayi": r["sayi"]} for r in takip_durumu_rows
    ],
    "isbak_idare_variasyonlari": [
        {"idare_adi": r["idare_adi"], "ihale_sayisi": r["ihale_sayisi"]} for r in isbak_rows
    ],
    "aday_aktif_degerler": active_candidates,
    "aday_aktif_ihale_sayisi": active_count,
}

with open(REPORTS / "postgresql_schema_validation.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)

print("TAMAMLANDI. Raporlar reports/ dizininde.")
