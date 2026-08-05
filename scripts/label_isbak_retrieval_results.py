"""
Terminal üzerinden interaktif etiketleme aracı.
"""

import argparse
import logging
from datetime import datetime

from app.evaluation.relevance_dataset import RelevanceDataset

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def print_separator():
    print("=" * 80)


def print_progress(dataset: RelevanceDataset):
    total = len(dataset.labels)
    if total == 0:
        print("Veri setinde kayıt yok.")
        return

    labeled = sum(1 for L in dataset.labels if L.label_status == "reviewed")
    pending = total - labeled
    pct = (labeled / total) * 100

    # Profil grubu dağılımı
    group_stats = {}
    for L in dataset.labels:
        g = L.query_id.split("-")[0]
        if g not in group_stats:
            group_stats[g] = {"total": 0, "labeled": 0}
        group_stats[g]["total"] += 1
        if L.label_status == "reviewed":
            group_stats[g]["labeled"] += 1

    print("\n--- ETİKETLEME DURUMU ---")
    print(f"Toplam kayıt:        {total}")
    print(f"Etiketlenen kayıt:   {labeled}")
    print(f"Etiketlenmeyen:      {pending}")
    print(f"Tamamlanma:          %{pct:.1f}")

    print("\nProfil Grubu Bazında Durum:")
    for g, stats in sorted(group_stats.items()):
        g_pct = (stats["labeled"] / stats["total"]) * 100 if stats["total"] > 0 else 0
        print(f"  {g}: {stats['labeled']}/{stats['total']} (%{g_pct:.1f})")
    print("-------------------------\n")


def label_results(input_path: str, reset: bool):
    dataset = RelevanceDataset(input_path)

    if reset:
        print("Tüm etiketler sıfırlanıyor...")
        for label in dataset.labels:
            label.relevance_grade = None
            label.label_status = "pending"
            label.reviewed_at = ""
        dataset.save()
        print("Sıfırlandı.")

    print_progress(dataset)

    # Pending listesini çıkar
    pending_indices = [i for i, L in enumerate(dataset.labels) if L.label_status != "reviewed"]

    if not pending_indices:
        print("Tebrikler, tüm etiketlemeler tamamlanmış!")
        return

    print("Etiketleme seçenekleri:")
    print("  2 = Doğrudan Uygun (İSBAK için tam hedef ihale)")
    print("  1 = İnceleme Gerekli (Benzerlik var, detaylı incelenmeli)")
    print("  0 = İlgisiz (İSBAK'ın alanına girmiyor)")
    print("  s = Atla (skip)")
    print("  n = Sonraki sorguya geç (skip to next query)")
    print("  g = Geri (önceki kayda dön)")
    print("  q = Çıkış ve kaydet (quit)")

    current_idx = 0
    while current_idx < len(pending_indices):
        real_idx = pending_indices[current_idx]
        item = dataset.labels[real_idx]

        print_separator()
        print(
            f"[{current_idx + 1}/{len(pending_indices)}] Sorgu ID: {item.query_id} (Küme: {item.dataset_split})"
        )
        print(f"Sorgu: {item.query}")
        print("-" * 80)
        print(f"İKN:           {item.ikn}")
        print(f"İhale Adı:     {item.tender_name}")
        print(f"İdare:         {item.idare_adi or 'Bilinmiyor'}")
        print(
            f"Profil Kodu:   {item.primary_profile_code} (Tüm Kodlar: {', '.join(item.profile_codes)})"
        )

        if getattr(item, "rationale", None):
            print("\n[İHALE METNİ ÖZETİ]")
            import textwrap

            wrapped = textwrap.fill(item.rationale, width=80)
            print(wrapped)
            print("[/İHALE METNİ ÖZETİ]\n")

        s = item.scores
        print(
            f"Puanlar:       Sem:{s.get('semantic', 0):.2f} Lex:{s.get('lexical', 0):.2f} "
            f"Pro:{s.get('profile', 0):.2f} Meta:{s.get('metadata', 0):.2f} "
            f"Final:{s.get('final', 0):.2f}"
        )

        if "negative_penalty" in s and s["negative_penalty"] > 0:
            print(f"               Negatif Ceza: -{s['negative_penalty']:.2f}")

        print("-" * 80)

        while True:
            val = input("Derece (0, 1, 2) veya Komut (s, n, g, q): ").strip().lower()

            if val == "q":
                print("Çıkış yapılıyor...")
                dataset.save()
                print_progress(dataset)
                return
            elif val == "s":
                print("Atlandı.")
                current_idx += 1
                break
            elif val == "n":
                print("Sonraki sorguya geçiliyor...")
                current_query_id = item.query_id
                while (
                    current_idx < len(pending_indices)
                    and dataset.labels[pending_indices[current_idx]].query_id == current_query_id
                ):
                    current_idx += 1
                break
            elif val == "g":
                if current_idx > 0:
                    current_idx -= 1
                    print("Geri dönüldü.")
                else:
                    print("Zaten ilk kayıttasınız.")
                break
            elif val in ("0", "1", "2"):
                grade = int(val)
                item.relevance_grade = grade
                item.label_status = "reviewed"
                item.reviewed_at = datetime.now().isoformat()
                dataset.save()
                print(f"Kaydedildi: {grade}")
                current_idx += 1
                break
            else:
                print("Geçersiz giriş. Lütfen (0, 1, 2, s, n, g, q) girin.")

    print("Tüm pending kayıtlar için tarama bitti.")
    print_progress(dataset)


def main():
    parser = argparse.ArgumentParser(description="Adayları manuel etiketleme aracı.")
    parser.add_argument(
        "--input",
        required=True,
        help="Etiketlenecek JSON dosyası (örn. isbak_relevance_labels.json)",
    )
    parser.add_argument("--reset", action="store_true", help="Tüm mevcut etiketleri sıfırlar.")

    args = parser.parse_args()
    label_results(args.input, args.reset)


if __name__ == "__main__":
    main()
