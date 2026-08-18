"""Ortak deterministik terim eşleştirici.

activity_scope.py ve score_aggregator.py tarafından paylaşılan tek eşleştirme
altyapısı. Pozitif ve negatif kapsam analizi aynı mantığı kullanır.

Tek genel kelime eşleşme kanıtı sayılmaz.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from functools import lru_cache
from typing import Any

_NON_ALNUM = re.compile(r"[^0-9a-zçğıöşü]+", re.IGNORECASE)

# Generik son ekler — tek başına bir terimi tamamlayan anlamsız sözcükler
GENERIC_TRAILING_TOKENS = frozenset({
    "alım",
    "alımı",
    "hizmet",
    "hizmeti",
    "kontrollük",
    "kontrollüğü",
    "satınalma",
    "temin",
    "temini",
    "tedarik",
    "tedariki",
})

# Anlamsal eşdeğer token grupları
SEMANTIC_TOKEN_GROUPS = (
    frozenset({"araç", "arac", "taşıt", "tasit", "otomobil", "minibüs", "minibus", "otobüs", "otobus", "kamyon", "ambulans"}),
    frozenset({"bakım", "bakim", "onarım", "onarim", "tamir", "servis"}),
    frozenset({"kiralama", "kiralık", "kiralik", "kira"}),
    frozenset({"yapım", "yapim", "inşaat", "insaat"}),
    frozenset({"taşıma", "tasima", "taşımacılık", "tasimacilik", "nakliye"}),
    frozenset({"levha", "tabela"}),
    frozenset({"organizasyon", "etkinlik", "festival"}),
    frozenset({"klima", "iklimlendirme"}),
)

# Tek başına güçlü eşleşme sayılamayacak genel kelimeler
# Bu set, pozitif kapsam doğrulamada tek genel kelimeyi filtreler.
_WEAK_STANDALONE_TERMS = frozenset({
    "kamera",
    "yazılım",
    "bakım",
    "bakim",
    "onarım",
    "onarim",
    "tamir",
    "kurulum",
    "montaj",
    "işletme",
    "isletme",
    "işletim",
    "isletim",
    "yenileme",
    "iyileştirme",
    "iyilestirme",
    "entegrasyon",
    "sistem",
    "sistemi",
    "sistemleri",
    "hizmet",
    "yönetim",
    "kontrol",
    "destek",
    "uygulama",
    "proje",
    "alım",
    "alim",
    "temin",
    "tedarik",
    "montaj",
    "donanım",
    "donanim",
    "ekipman",
    "cihaz",
    "altyapı",
    "altyapi",
    "altyapısı",
    "analiz",
})

# Yalnızca hizmet, işletim veya destek ifade eden, sektörel nesnesi olmayan
# jenerik faaliyet yetkinlik tanımları. Bunlar tek başına güçlü pozitif eşleşme
# (verified=True) üretemez.
# Özellikle "bakım" ve "onarım" gibi domain-agnostik eylem fiillerini içeren
# ifadeler buraya dahil edilmiştir — domain/nesne kanıtı olmadan eşleşme yapmaz.
_WEAK_CAPABILITY_TERMS = frozenset({
    "kesintisiz hizmet",
    "kesintisiz hizmet altyapısı",
    "hizmet altyapısı",
    "operasyon sürekliliği",
    "teknik destek",
    "bakım desteği",
    "bakım ve onarım",
    "bakim ve onarim",
    "bakım onarım",
    "bakim onarim",
    "onarım ve bakım",
    "onarim ve bakim",
    "bakım, onarım ve teknik destek",
    "bakim onarim ve teknik destek",
    "sistem yönetimi",
    "saha desteği",
    "saha destek",
    "genel bakım",
    "periyodik bakım",
    "koruyucu bakım",
})

def normalize_text(value: Any) -> str:
    """Metni karşılaştırma için normalleştirir."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.translate(str.maketrans({"I": "ı", "İ": "i"}))
    text = text.lower().replace("\u0307", "")
    return " ".join(_NON_ALNUM.sub(" ", text).split())


def normalize_code(value: Any) -> str:
    """OKAS/CPV kodunu normalleştirir."""
    return "".join(char for char in str(value or "") if char.isalnum()).upper()


def ordered_strings(values: Any) -> list[str]:
    """Tekrar eden boş olmayan string listesi üretir."""
    if not isinstance(values, list):
        return []
    return list(
        dict.fromkeys(
            text
            for item in values
            if (text := " ".join(str(item).split()).strip())
        )
    )


def contains_phrase(normalized_text: str, phrase: str) -> bool:
    """Tam cümle eşleşmesi — kelime sınırına duyarlı."""
    normalized_phrase = normalize_text(phrase)
    if not normalized_text or not normalized_phrase:
        return False
    return f" {normalized_phrase} " in f" {normalized_text} "


@lru_cache(maxsize=32768)
def token_matches(expected: str, actual: str) -> bool:
    """İki tokenin anlamsal eşdeğer olup olmadığını döndürür.

    Aynı token çiftleri puanlama boyunca çok sık tekrarlandığı için sonuç
    sınırlı bir LRU önbelleğinde tutulur. Eşleştirme mantığı değişmez.
    """
    if expected == actual:
        return True
    if any(expected in group and actual in group for group in SEMANTIC_TOKEN_GROUPS):
        return True
    if min(len(expected), len(actual)) < 5:
        return False
    common_prefix_length = 0
    for expected_char, actual_char in zip(expected, actual, strict=False):
        if expected_char != actual_char:
            break
        common_prefix_length += 1
    return common_prefix_length >= max(5, min(len(expected), len(actual)) - 2)


def term_variants(term: str) -> list[list[str]]:
    """Bir terimin olası token varyantlarını döndürür (generik son ekler çıkarılarak)."""
    tokens = normalize_text(term).split()
    if not tokens:
        return []
    variants = [tokens]
    shortened = list(tokens)
    while len(shortened) > 2 and shortened[-1] in GENERIC_TRAILING_TOKENS:
        shortened = shortened[:-1]
        variants.append(list(shortened))
    return variants


def contains_term(normalized_text: str, term: str) -> bool:
    """Terim metinde geçiyor mu?

    Tek genel sözcük eşleşmesi ret üretmez — en az 2 token gerekir.
    Pencere tabanlı token eşleştirme anlamsal varyantları yakalar.

    Performans notu:
    Eski uygulama her başlangıç konumu için metnin sonuna kadar yeniden
    tarama yapıyordu. Oysa geçerli bir eşleşmenin son ve ilk token konumu
    arasındaki fark maximum_window değerini aşamaz.

    Bu nedenle yalnız ilk tokenin gerçekten eşleştiği konumlardan başlanır
    ve arama izin verilen pencere ile sınırlandırılır. Eşleşme semantiği
    korunurken gereksiz O(n²) benzeri tarama ortadan kaldırılır.
    """
    if contains_phrase(normalized_text, term):
        return True

    text_tokens = normalized_text.split()

    for variant in term_variants(term):
        # Tek genel sözcük → büyük bir ihaleyi reddetmek için yetmez
        if len(variant) < 2 or len(text_tokens) < len(variant):
            continue

        maximum_window = len(variant) + 4
        first_expected = variant[0]

        # Eski algoritma her start değeri için aynı ilk eşleşmeyi yeniden
        # keşfediyordu. Yalnız gerçekten eşleşen ilk token konumlarından başla.
        for first_position, actual_token in enumerate(text_tokens):
            if not token_matches(first_expected, actual_token):
                continue

            # Son geçerli token konumu:
            # last_position - first_position <= maximum_window
            search_from = first_position + 1
            search_end = min(
                len(text_tokens),
                first_position + maximum_window + 1,
            )

            matched_count = 1

            for expected in variant[1:]:
                position = next(
                    (
                        index
                        for index in range(search_from, search_end)
                        if token_matches(
                            expected,
                            text_tokens[index],
                        )
                    ),
                    None,
                )

                if position is None:
                    break

                matched_count += 1
                search_from = position + 1

            if matched_count == len(variant):
                return True

    return False


def matching_terms(texts: Iterable[str], terms: list[str]) -> list[str]:
    """Birden fazla metin içinde eşleşen terimleri döndürür."""
    normalized_texts = [normalize_text(text) for text in texts if str(text).strip()]
    return [
        term
        for term in terms
        if any(contains_term(text, term) for text in normalized_texts)
    ]


def is_strong_term(term: str) -> bool:
    """Terimin güçlü eşleşme sayılabilecek uzunluk ve bileşimde olup olmadığını kontrol eder.

    Tek başına geniş tek kelimeler (kamera, yazılım, bakım, vs.)
    güçlü pozitif eşleşme sayılmaz.
    """
    tokens = normalize_text(term).split()
    if len(tokens) >= 2:
        return True
    # Tek token — genel kelimeler listesinde mi?
    single = tokens[0] if tokens else ""
    return bool(single) and single not in _WEAK_STANDALONE_TERMS


__all__ = [
    "normalize_text",
    "normalize_code",
    "ordered_strings",
    "contains_phrase",
    "token_matches",
    "term_variants",
    "contains_term",
    "matching_terms",
    "is_strong_term",
    "GENERIC_TRAILING_TOKENS",
    "SEMANTIC_TOKEN_GROUPS",
]
