"""
Kullanıcının etiketlemesi için rastgele, çeşitli sorgulardan toplam 30 adetlik bir aday alt kümesi oluşturur.
Geri kalan tüm adayları 'skipped' olarak işaretler.
"""

import argparse
import random
from collections import defaultdict

from app.evaluation.relevance_dataset import RelevanceDataset


def main():
    parser = argparse.ArgumentParser(description="Etiketleme için rastgele alt küme ayarlar.")
    parser.add_argument("--input", required=True, help="JSON etiket dosyası")
    parser.add_argument("--count", type=int, default=30, help="Toplam etiketlenecek kayıt sayısı")
    args = parser.parse_args()

    dataset = RelevanceDataset(args.input)

    # Sadece pending olanları al
    pending_labels = [L for L in dataset.labels if L.label_status == "pending"]
    if not pending_labels:
        print("Tüm kayıtlar zaten etiketlenmiş veya atlanmış.")
        return

    # Sorgu bazında grupla
    by_query = defaultdict(list)
    for L in pending_labels:
        by_query[L.query_id].append(L)

    selected = []

    # Çeşitlilik için her sorgudan sırayla 1'er 1'er çek
    queries = list(by_query.keys())
    random.shuffle(queries)

    while len(selected) < args.count and queries:
        for q in list(queries):
            if len(selected) >= args.count:
                break
            if by_query[q]:
                # Rastgele bir aday seç ve çıkar
                chosen = random.choice(by_query[q])
                by_query[q].remove(chosen)
                selected.append(chosen)
            else:
                queries.remove(q)

    # Seçilenleri bir kümeye (Set) koy
    selected_ids = {(L.query_id, L.ikn) for L in selected}

    # Kalan her şeyi 'skipped' yap
    for L in dataset.labels:
        if L.label_status == "pending":
            if (L.query_id, L.ikn) not in selected_ids:
                L.label_status = "skipped"

    # Seçilenleri JSON'ın en başına taşı (kullanıcı beklememesi için)
    # dataset.labels listesini yeniden sırala: pending olanlar başta olsun
    dataset.labels.sort(key=lambda x: 0 if x.label_status == "pending" else 1)

    dataset.save()
    print(f"Toplam {len(selected)} adet rastgele ve çeşitli aday 'pending' olarak bırakıldı.")
    print("Geri kalan tüm adaylar 'skipped' olarak işaretlendi.")
    print("Artık etiketleme betiğini çalıştırdığınızda doğrudan bu 30 adayı göreceksiniz.")


if __name__ == "__main__":
    main()
