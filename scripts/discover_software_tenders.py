from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from app.database.tender_repository import TenderRepository
from app.domain import TenderRecord

# İhale konusunu doğrudan yazılım/bilişim alanına taşıyan ifadeler.
# Genel mevzuat metinlerinde sık geçen "e-imza", "elektronik imza" ve
# bağlamsız "lisans" bilinçli olarak bu listede yer almaz.
DIRECT_TERMS = (
    "yazılım",
    "yazılım geliştirme",
    "uygulama geliştirme",
    "web uygulaması",
    "mobil uygulama",
    "yazılım lisansı",
    "lisanslı yazılım",
    "kullanıcı lisansı",
    "veritabanı lisansı",
    "ürün lisansı",
    "antivirüs lisansı",
    "lisans yenileme hizmeti",
    "bilgi sistemi",
    "bilişim sistemi",
    "veri tabanı",
    "veritabanı",
    "siber güvenlik",
    "bilgi güvenliği",
    "bulut hizmeti",
    "bulut bilişim",
    "sistem entegrasyonu",
    "yazılım entegrasyonu",
    "api entegrasyonu",
    "yazılım bakım",
    "yazılım destek",
    "doküman yönetim sistemi",
    "kurumsal kaynak planlama",
    "coğrafi bilgi sistemi",
    "karar destek sistemi",
    "hastane bilgi yönetim sistemi",
    "sbys",
    "ybbys",
    "elektronik belge yönetim sistemi",
    "e-posta güvenlik sistemi",
)

# Kısa teknik terimler yalnızca bağımsız sözcük olarak eşleştirilir.
SHORT_DIRECT_TERMS = (
    "erp",
    "crm",
    "api",
)

# Yalnızca güçlü kaynaklarda geçtiğinde doğrudan kanıt sayılır.
TITLE_ONLY_DIRECT_TERMS = (
    "e-imza hizmeti",
    "elektronik imza hizmeti",
    "elektronik sertifika hizmeti",
)

# Donanım ve altyapı konuları tek başına doğrudan yazılım ihalesi sayılmaz.
# "donanım" sözcüğü tek başına kullanılmaz; böylece koruyucu giyim donanımı
# ve sıhhi tesisat donanımı gibi yanlış eşleşmeler engellenir.
CONDITIONAL_TERMS = (
    "bilgisayar",
    "sunucu",
    "veri merkezi",
    "ağ cihazı",
    "ağ anahtarı",
    "network cihazı",
    "depolama sistemi",
    "yedekleme sistemi",
    "güvenlik cihazı",
    "kamera sistemi",
    "kamera güvenlik",
    "görüntüleme sistemi",
    "pdks",
    "geçiş sistemi",
    "kartlı geçiş",
    "erişim kontrol",
    "kgys",
    "terminal",
    "iş istasyonu",
    "firewall",
    "güvenlik duvarı",
    "bilgisayar donanımı",
    "bilişim donanımı",
    "sunucu donanımı",
    "ağ donanımı",
    "veri depolama donanımı",
    "donanım altyapısı",
)

SUPPORT_TERMS = (
    "kurulum",
    "entegrasyon",
    "konfigürasyon",
    "yapılandırma",
    "yazılım",
    "yazılım lisansı",
    "lisanslı yazılım",
    "kullanıcı lisansı",
    "yazılım bakım",
    "sistem bakım",
    "sunucu bakım",
    "donanım bakım",
    "teknik bakım",
    "bakım ve destek",
    "teknik destek",
    "destek hizmeti",
    "devreye alma",
    "kullanıcı eğitimi",
    "sistem eğitimi",
    "yazılım eğitimi",
    "teknik eğitim",
    "personel eğitimi",
    "danışmanlık",
    "güncelleme",
    "uyarlama",
    "migrasyon",
    "veri aktarımı",
)

DIRECT_OKAS_PREFIXES = (
    "481",
    "482",
    "483",
    "484",
    "485",
    "486",
    "487",
    "4890",
    "72",
)

CONDITIONAL_OKAS_PREFIXES = (
    "302",
    "324",
    "325",
    "4882",
)

# Bu kodlar, ihale adı veya ilan başlığında ek bir yazılım ifadesi
# bulunmasa da doğrudan yazılım kanıtı sayılabilecek kadar belirgindir.
# Genel 48 ve 72 aileleri bu listede değildir; onlar yalnızca adaylık
# oluşturur ve başlık desteği yoksa "inceleme_gerekli" sonucuna gider.
STRONG_DIRECT_OKAS_PREFIXES = (
    "481",
    "48218",
    "48451",
    "4873",
    "4890",
    "72212",
    "7226",
    "72267",
)

SOURCE_WEIGHTS = {
    "tender_title": 5,
    "tender_metadata": 1,
    "announcement_titles": 4,
    "announcement_content": 1,
    "tender_characteristics": 2,
    "tender_okas_codes": 5,
}

STRONG_SOURCES = {
    "tender_title",
    "announcement_titles",
    "tender_okas_codes",
}


@dataclass(frozen=True)
class DiscoveryResult:
    ikn: str
    tender_id: str
    title: str
    tender_date: str
    tender_type: str
    authority: str
    classification: str
    evidence_score: int
    direct_matches: list[str]
    conditional_matches: list[str]
    support_matches: list[str]
    direct_okas_matches: list[str]
    conditional_okas_matches: list[str]
    matched_sources: list[str]
    evidence_reasons: list[str]
    okas_codes: list[str]
    announcement_titles: list[str]


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))

    text = text.translate(
        str.maketrans(
            {
                "I": "ı",
                "İ": "i",
            }
        )
    ).lower()

    text = text.replace("\u0307", "")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def term_pattern(term: str) -> re.Pattern[str]:
    normalized_term = normalize(term)
    escaped = re.escape(normalized_term)

    return re.compile(
        rf"(?<!\w){escaped}(?!\w)",
        flags=re.UNICODE,
    )


def find_terms(
    text: str,
    terms: Iterable[str],
) -> list[str]:
    normalized_text = normalize(text)

    return [term for term in terms if term_pattern(term).search(normalized_text)]


def build_source_texts(
    tender: TenderRecord,
) -> dict[str, str]:
    tender_title = str(tender.adi or "")

    tender_metadata = "\n".join(
        filter(
            None,
            [
                tender.kapsam,
                tender.ihale_turu,
                tender.ihale_usulu,
                tender.idare_adi,
            ],
        )
    )

    announcement_titles = "\n".join(
        str(announcement.baslik) for announcement in tender.announcements if announcement.baslik
    )

    announcement_content = "\n".join(
        filter(
            None,
            [
                value
                for announcement in tender.announcements
                for value in (
                    announcement.icerik,
                    announcement.ilan_tipi,
                )
            ],
        )
    )

    characteristic_text = "\n".join(item.ozellik for item in tender.characteristics if item.ozellik)

    okas_text = "\n".join(
        " ".join(
            filter(
                None,
                [
                    item.kod,
                    item.ad,
                ],
            )
        )
        for item in tender.okas_codes
        if item.kod or item.ad
    )

    return {
        "tender_title": tender_title,
        "tender_metadata": tender_metadata,
        "announcement_titles": announcement_titles,
        "announcement_content": announcement_content,
        "tender_characteristics": characteristic_text,
        "tender_okas_codes": okas_text,
    }


def normalized_okas_code(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def okas_prefix_matches(
    tender: TenderRecord,
    prefixes: Iterable[str],
) -> list[str]:
    matches: list[str] = []

    for item in tender.okas_codes:
        code = normalized_okas_code(item.kod)

        if not code:
            continue

        if any(code.startswith(prefix) for prefix in prefixes):
            label = " - ".join(
                filter(
                    None,
                    [
                        str(item.kod or "").strip(),
                        str(item.ad or "").strip(),
                    ],
                )
            )
            matches.append(label)

    return list(dict.fromkeys(matches))


def source_term_matches(
    source_texts: dict[str, str],
    terms: Iterable[str],
) -> dict[str, list[str]]:
    return {
        source_name: find_terms(source_text, terms)
        for source_name, source_text in source_texts.items()
    }


def merged_matches(
    matches_by_source: dict[str, list[str]],
) -> list[str]:
    return list(
        dict.fromkeys(
            term for source_matches in matches_by_source.values() for term in source_matches
        )
    )


def sources_with_matches(
    *match_groups: dict[str, list[str]],
) -> list[str]:
    return [
        source_name
        for source_name in SOURCE_WEIGHTS
        if any(group.get(source_name) for group in match_groups)
    ]


def weighted_score(
    direct_by_source: dict[str, list[str]],
    conditional_by_source: dict[str, list[str]],
    support_by_source: dict[str, list[str]],
    *,
    direct_okas_matches: list[str],
    conditional_okas_matches: list[str],
) -> int:
    score = 0

    for source_name, weight in SOURCE_WEIGHTS.items():
        if direct_by_source.get(source_name):
            score += weight * 3

        if conditional_by_source.get(source_name):
            score += weight * 2

        if support_by_source.get(source_name):
            score += weight

    if direct_okas_matches:
        score += 15

    if conditional_okas_matches:
        score += 8

    return score


def classify_tender(
    tender: TenderRecord,
) -> DiscoveryResult:
    source_texts = build_source_texts(tender)

    # OKAS açıklamaları doğal dil anahtar kelime taramasına dahil edilmez.
    # Örneğin "yazılım paketleri hariç" gibi açıklamalar yanlış pozitif
    # üretebilir. OKAS kanıtı yalnızca kod ön ekleriyle değerlendirilir.
    term_source_texts = {
        source_name: ("" if source_name == "tender_okas_codes" else source_text)
        for source_name, source_text in source_texts.items()
    }

    direct_by_source = source_term_matches(
        term_source_texts,
        DIRECT_TERMS,
    )
    short_direct_by_source = source_term_matches(
        term_source_texts,
        SHORT_DIRECT_TERMS,
    )

    for source_name, matches in short_direct_by_source.items():
        direct_by_source[source_name].extend(matches)
        direct_by_source[source_name] = list(dict.fromkeys(direct_by_source[source_name]))

    title_only_by_source = {
        source_name: (
            find_terms(source_text, TITLE_ONLY_DIRECT_TERMS)
            if source_name in STRONG_SOURCES
            else []
        )
        for source_name, source_text in source_texts.items()
    }

    for source_name, matches in title_only_by_source.items():
        direct_by_source[source_name].extend(matches)
        direct_by_source[source_name] = list(dict.fromkeys(direct_by_source[source_name]))

    conditional_by_source = source_term_matches(
        term_source_texts,
        CONDITIONAL_TERMS,
    )
    support_by_source = source_term_matches(
        term_source_texts,
        SUPPORT_TERMS,
    )

    direct_okas_matches = okas_prefix_matches(
        tender,
        DIRECT_OKAS_PREFIXES,
    )
    conditional_okas_matches = okas_prefix_matches(
        tender,
        CONDITIONAL_OKAS_PREFIXES,
    )
    strong_direct_okas_matches = okas_prefix_matches(
        tender,
        STRONG_DIRECT_OKAS_PREFIXES,
    )

    direct_strong_sources = [
        source_name for source_name in STRONG_SOURCES if direct_by_source[source_name]
    ]
    direct_weak_sources = [
        source_name
        for source_name in SOURCE_WEIGHTS
        if (source_name not in STRONG_SOURCES and direct_by_source[source_name])
    ]
    conditional_strong_sources = [
        source_name for source_name in STRONG_SOURCES if conditional_by_source[source_name]
    ]
    support_sources = [
        source_name for source_name in SOURCE_WEIGHTS if support_by_source[source_name]
    ]

    evidence_reasons: list[str] = []

    if strong_direct_okas_matches:
        evidence_reasons.append("Kesin yazılım OKAS kodu eşleşti.")
    elif direct_okas_matches:
        evidence_reasons.append("Genel bilişim/yazılım OKAS kod ailesi eşleşti.")

    if direct_strong_sources:
        evidence_reasons.append(
            "Doğrudan bilişim ifadesi güçlü kaynakta eşleşti: " + ", ".join(direct_strong_sources)
        )

    if direct_weak_sources:
        evidence_reasons.append(
            "Doğrudan bilişim ifadesi yalnızca ikincil kaynakta eşleşti: "
            + ", ".join(direct_weak_sources)
        )

    if conditional_okas_matches:
        evidence_reasons.append("Bilgisayar/ağ ekipmanı OKAS kod ailesi eşleşti.")

    if conditional_strong_sources:
        evidence_reasons.append(
            "Donanım/altyapı ifadesi güçlü kaynakta eşleşti: "
            + ", ".join(conditional_strong_sources)
        )

    if support_sources:
        evidence_reasons.append(
            "Kurulum/yazılım/destek kanıtı eşleşti: " + ", ".join(support_sources)
        )

    has_direct_strong_evidence = bool(strong_direct_okas_matches or direct_strong_sources)
    has_general_direct_okas_evidence = bool(direct_okas_matches)
    has_direct_weak_evidence = bool(direct_weak_sources)
    has_conditional_evidence = bool(conditional_okas_matches or conditional_strong_sources)
    has_support_evidence = bool(support_sources)

    if has_direct_strong_evidence:
        classification = "doğrudan_uygun"
    elif has_conditional_evidence and has_support_evidence:
        classification = "koşullu_uygun"
    elif has_general_direct_okas_evidence or has_conditional_evidence:
        classification = "inceleme_gerekli"
    elif has_direct_weak_evidence and conditional_strong_sources:
        classification = "inceleme_gerekli"
    else:
        classification = "ilgisiz"

    evidence_score = weighted_score(
        direct_by_source,
        conditional_by_source,
        support_by_source,
        direct_okas_matches=direct_okas_matches,
        conditional_okas_matches=conditional_okas_matches,
    )

    matched_sources = sources_with_matches(
        direct_by_source,
        conditional_by_source,
        support_by_source,
    )

    if direct_okas_matches or conditional_okas_matches:
        matched_sources = list(
            dict.fromkeys(
                [
                    *matched_sources,
                    "tender_okas_codes",
                ]
            )
        )

    return DiscoveryResult(
        ikn=str(tender.ikn),
        tender_id=str(tender.id),
        title=str(tender.adi or ""),
        tender_date=str(tender.ihale_tarihi or ""),
        tender_type=str(tender.ihale_turu or ""),
        authority=str(tender.idare_adi or ""),
        classification=classification,
        evidence_score=evidence_score,
        direct_matches=merged_matches(direct_by_source),
        conditional_matches=merged_matches(conditional_by_source),
        support_matches=merged_matches(support_by_source),
        direct_okas_matches=direct_okas_matches,
        conditional_okas_matches=conditional_okas_matches,
        matched_sources=matched_sources,
        evidence_reasons=evidence_reasons,
        okas_codes=[
            " - ".join(
                filter(
                    None,
                    [
                        str(item.kod or "").strip(),
                        str(item.ad or "").strip(),
                    ],
                )
            )
            for item in tender.okas_codes
            if item.kod or item.ad
        ],
        announcement_titles=[
            str(item.baslik).strip() for item in tender.announcements if item.baslik
        ],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Aktif ihaleleri yazılım ve bilişim uygunluğu açısından okuma amaçlı sınıflandırır."
        )
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="İncelenecek en fazla aktif ihale sayısı.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=25,
        help="Ayrıntı sorgusu grup büyüklüğü.",
    )
    parser.add_argument(
        "--show",
        type=int,
        default=50,
        help="Terminalde gösterilecek uygun kayıt sayısı.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmark_results"),
        help="JSON ve CSV sonuç klasörü.",
    )

    return parser.parse_args()


def classification_counts(
    results: list[DiscoveryResult],
) -> dict[str, int]:
    classes = (
        "doğrudan_uygun",
        "koşullu_uygun",
        "inceleme_gerekli",
        "ilgisiz",
    )

    return {
        classification: sum(item.classification == classification for item in results)
        for classification in classes
    }


def write_json(
    path: Path,
    *,
    total_active: int,
    results: list[DiscoveryResult],
) -> None:
    payload = {
        "total_active_snapshot": total_active,
        "processed": len(results),
        "counts": classification_counts(results),
        "rules": {
            "source_weights": SOURCE_WEIGHTS,
            "strong_sources": sorted(STRONG_SOURCES),
            "direct_okas_prefixes": DIRECT_OKAS_PREFIXES,
            "strong_direct_okas_prefixes": STRONG_DIRECT_OKAS_PREFIXES,
            "conditional_okas_prefixes": CONDITIONAL_OKAS_PREFIXES,
        },
        "results": [asdict(item) for item in results],
    }

    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def write_csv(
    path: Path,
    results: list[DiscoveryResult],
) -> None:
    fieldnames = [
        "ikn",
        "tender_id",
        "title",
        "tender_date",
        "tender_type",
        "authority",
        "classification",
        "evidence_score",
        "direct_matches",
        "conditional_matches",
        "support_matches",
        "direct_okas_matches",
        "conditional_okas_matches",
        "matched_sources",
        "evidence_reasons",
        "okas_codes",
        "announcement_titles",
    ]

    with path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )
        writer.writeheader()

        for result in results:
            row = asdict(result)

            for key in (
                "direct_matches",
                "conditional_matches",
                "support_matches",
                "direct_okas_matches",
                "conditional_okas_matches",
                "matched_sources",
                "evidence_reasons",
                "okas_codes",
                "announcement_titles",
            ):
                row[key] = " | ".join(row[key])

            writer.writerow(row)


def main() -> int:
    args = parse_args()

    if args.limit is not None and args.limit <= 0:
        raise ValueError("--limit pozitif olmalıdır.")
    if args.batch_size <= 0:
        raise ValueError("--batch-size pozitif olmalıdır.")
    if args.show < 0:
        raise ValueError("--show negatif olamaz.")

    repository = TenderRepository()
    total_active = repository.count_active_tenders()

    results: list[DiscoveryResult] = []

    for batch_number, tenders in enumerate(
        repository.iter_active_tender_batches(
            batch_size=args.batch_size,
            limit=args.limit,
        ),
        start=1,
    ):
        for tender in tenders:
            results.append(classify_tender(tender))

        print(f"Grup {batch_number} tamamlandı | işlenen={len(results)}")

    priority = {
        "doğrudan_uygun": 0,
        "koşullu_uygun": 1,
        "inceleme_gerekli": 2,
        "ilgisiz": 3,
    }

    results.sort(
        key=lambda item: (
            priority[item.classification],
            -item.evidence_score,
            item.tender_date,
            item.ikn,
        )
    )

    args.output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    json_path = args.output_dir / "software_tender_discovery.json"
    csv_path = args.output_dir / "software_tender_discovery.csv"

    write_json(
        json_path,
        total_active=total_active,
        results=results,
    )
    write_csv(
        csv_path,
        results,
    )

    counts = classification_counts(results)

    print()
    print("=" * 100)
    print("YAZILIM VE BİLİŞİM İHALESİ KEŞİF ÖZETİ")
    print("=" * 100)
    print(f"Aktif ihale anlık görüntüsü : {total_active}")
    print(f"İşlenen kayıt               : {len(results)}")
    print(f"Doğrudan uygun              : {counts['doğrudan_uygun']}")
    print(f"Koşullu uygun               : {counts['koşullu_uygun']}")
    print(f"İnceleme gerekli            : {counts['inceleme_gerekli']}")
    print(f"İlgisiz                     : {counts['ilgisiz']}")

    suitable = [item for item in results if item.classification != "ilgisiz"]

    print()
    print(f"İlk {min(args.show, len(suitable))} aday kayıt")
    print("-" * 100)

    for index, item in enumerate(
        suitable[: args.show],
        start=1,
    ):
        print(
            f"{index:>3}. [{item.classification}] "
            f"puan={item.evidence_score} | "
            f"{item.ikn} | {item.title}"
        )

        matches = item.direct_matches or item.conditional_matches

        print("     Eşleşmeler : " + ", ".join(matches))

        if item.direct_okas_matches:
            print("     Güçlü OKAS : " + " | ".join(item.direct_okas_matches))

        if item.conditional_okas_matches:
            print("     Koşullu OKAS: " + " | ".join(item.conditional_okas_matches))

        if item.support_matches:
            print("     Destek     : " + ", ".join(item.support_matches))

        print("     Kaynaklar  : " + ", ".join(item.matched_sources))

    print()
    print(f"JSON çıktı: {json_path}")
    print(f"CSV çıktı : {csv_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
