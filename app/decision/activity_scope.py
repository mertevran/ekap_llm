"""İhale metnindeki profil kapsam sinyallerini deterministik olarak doğrular.

Hem pozitif hem negatif kapsam analizi aynı ortak eşleştirici üzerinden çalışır:
    app.matching.scope_terms

Tek genel kelime eşleşmesi kanıt sayılmaz.
"""

from __future__ import annotations

from typing import Any

from app.decision.models import (
    DecisionValidationContext,
    NegativeScopeAnalysis,
    PositiveScopeAnalysis,
)
from app.matching.scope_terms import (
    contains_term,
    matching_terms,
    normalize_code,
    normalize_text,
    ordered_strings,
    is_strong_term,
    token_matches,
    _WEAK_STANDALONE_TERMS,
    _WEAK_CAPABILITY_TERMS,
)

# Geriye uyumluluk — bazı dosyalar activity_scope içinden bu fonksiyonları import ediyor
_normalize_text = normalize_text
_normalize_code = normalize_code
_ordered_strings = ordered_strings
_contains_term = contains_term
_matching_terms = matching_terms


def _okas_matches(
    context: DecisionValidationContext,
    signals: dict[str, Any],
) -> list[str]:
    """İhale OKAS kodlarını profil OKAS sinyalleriyle karşılaştırır."""
    configured_codes = {
        normalize_code(code)
        for code in ordered_strings(signals.get("okas_kodlari"))
        if normalize_code(code)
    }
    configured_prefixes = {
        normalize_code(code)
        for code in ordered_strings(signals.get("okas_kod_on_ekleri"))
        if normalize_code(code)
    }
    tender_codes = [
        normalized
        for code in context.tender_okas_codes
        if (normalized := normalize_code(code))
    ]
    return [
        code
        for code in tender_codes
        if code in configured_codes
        or any(code.startswith(prefix) for prefix in configured_prefixes)
    ]


def analyze_positive_scope(
    context: DecisionValidationContext | None,
) -> PositiveScopeAnalysis:
    """Pozitif faaliyet terimlerini ihale başlığı ve kanıtlarında doğrular.

    Çok seviyeli (1-4) kanıt hiyerarşisi uygular.
    Tek genel kelime eşleşme kanıtı sayılmaz.
    """
    if context is None:
        return PositiveScopeAnalysis()

    signals = context.profile_signals
    if not isinstance(signals, dict):
        return PositiveScopeAnalysis()

    # Seviye 1 (Güçlü)
    strong_terms = ordered_strings(signals.get("guclu_terimler", []))

    # Filter weak/generic primary capabilities
    raw_caps = ordered_strings(context.primary_capabilities)
    primary_caps = [
        cap for cap in raw_caps
        if normalize_text(cap) not in _WEAK_CAPABILITY_TERMS
    ]

    level_1 = list(dict.fromkeys(strong_terms + primary_caps))

    # Seviye 2 (Teknik/Destekleyici)
    supporting_terms = ordered_strings(signals.get("destekleyici_terimler", []))
    equipment = []
    generic_equipment_names = {"kamera", "sensör", "sunucu", "kablo", "switch", "router", "bilgisayar"}
    for eq in context.technical_equipment:
        name = eq.get("name", "")
        if name:
            equipment.append(name)
        equipment.extend(eq.get("aliases", []))
    equipment = ordered_strings(equipment)
    level_2 = list(dict.fromkeys(supporting_terms + equipment))

    # Seviye 3 (Bağlamsal)
    level_3 = []
    if context.profile_name:
        level_3.append(context.profile_name)
    if context.profile_description:
        # Just use description conceptually if needed, or exact phrases (not ideal for exact match, but let's keep name and jargon)
        pass
    jargon = []
    for j in context.abbreviations_and_jargon:
        term = j.get("term", "")
        if term:
            jargon.append(term)
        expanded = j.get("expanded_form", "")
        if expanded:
            jargon.append(expanded)
        jargon.extend(j.get("aliases", []))
    jargon = ordered_strings(jargon)
    level_3 = list(dict.fromkeys(level_3 + jargon))

    # Seviye 4 (Yetersiz)
    action_verbs = ordered_strings(context.action_verbs)

    okas_text_support_required = bool(signals.get("okas_metin_destegi_zorunlu", False))
    normalized_title = normalize_text(context.tender_name)
    all_evidence = list(context.evidence_text_by_chunk.values())

    # Eşleşme Fonksiyonu
    def get_matches(terms: list[str], texts: list[str]) -> list[str]:
        matched = []
        for text in texts:
            norm_text = normalize_text(text)
            for t in terms:
                if contains_term(norm_text, t):
                    matched.append(t)
        return list(dict.fromkeys(matched))

    title_texts = [context.tender_name]

    def get_token_overlap(term: str, text: str, any_match: bool = False) -> bool:
        term_tokens = {t for t in term.split() if len(t) > 3 and t not in _WEAK_STANDALONE_TERMS}
        text_tokens = {t for t in text.split() if len(t) > 3 and t not in _WEAK_STANDALONE_TERMS}
        if not term_tokens or not text_tokens:
            return False
        if any_match:
            return bool(term_tokens.intersection(text_tokens))
        # If all strong tokens of the term are in the text, it's a match
        return term_tokens.issubset(text_tokens)

    # Başlık eşleşmeleri
    title_l1 = [t for t in get_matches(level_1, title_texts) if is_strong_term(t)]
    # Check token overlap for primary caps if not exactly matched
    for cap in primary_caps:
        if cap not in title_l1 and get_token_overlap(normalize_text(cap), normalized_title):
            title_l1.append(cap)

    title_l2 = get_matches(level_2, title_texts)
    for eq in equipment:
        if eq not in title_l2 and get_token_overlap(normalize_text(eq), normalized_title, any_match=True):
            title_l2.append(eq)

    title_l3 = get_matches(level_3, title_texts)
    title_l4 = get_matches(action_verbs, title_texts)

    # Kanıt metni eşleşmeleri
    evidence_l1 = []
    evidence_l2 = []
    evidence_l3 = []
    evidence_l4 = []
    evidence_chunk_ids = []

    for chunk_id, text in context.evidence_text_by_chunk.items():
        norm_ev = normalize_text(text)
        c_l1 = [t for t in level_1 if contains_term(norm_ev, t) and is_strong_term(t)]
        for cap in primary_caps:
            if cap not in c_l1 and get_token_overlap(normalize_text(cap), norm_ev):
                c_l1.append(cap)

        c_l2 = [t for t in level_2 if contains_term(norm_ev, t)]
        for eq in equipment:
            if eq not in c_l2 and get_token_overlap(normalize_text(eq), norm_ev, any_match=True):
                c_l2.append(eq)

        c_l3 = [t for t in level_3 if contains_term(norm_ev, t)]
        c_l4 = [t for t in action_verbs if contains_term(norm_ev, t)]
        if c_l1 or c_l2 or c_l3 or c_l4:
            evidence_chunk_ids.append(str(chunk_id))
            evidence_l1.extend(c_l1)
            evidence_l2.extend(c_l2)
            evidence_l3.extend(c_l3)
            evidence_l4.extend(c_l4)

    all_l1 = list(dict.fromkeys(title_l1 + evidence_l1))
    all_l2 = list(dict.fromkeys(title_l2 + evidence_l2))
    all_l3 = list(dict.fromkeys(title_l3 + evidence_l3))

    # Kategori ayrımları
    primary_capability_matches = [t for t in all_l1 if t in primary_caps]
    strong_matched_terms = [t for t in all_l1 if t in strong_terms]
    equipment_matches = [t for t in all_l2 if t in equipment]
    supporting_matched_terms = [t for t in all_l2 if t in supporting_terms]
    contextual_matches = [t for t in all_l3 if t == context.profile_name]
    if context.profile_description and contains_term(normalize_text(context.profile_description), context.tender_name):
        # Very simple contextual match if title is in description or vice versa
        pass
    abbreviation_matches = [t for t in all_l3 if t in jargon]
    matched_action_terms = list(dict.fromkeys(title_l4 + evidence_l4))

    # OKAS
    matched_okas_codes = _okas_matches(context, signals)
    okas_supported = bool(matched_okas_codes)

    # Verified kuralları (A, B, C)
    # Kural A: Seviye 1 eşleşmesi
    rule_a = bool(all_l1)

    # Kural B: Seviye 2 eşleşmesi + bağımsız destek
    # Destekler: Farklı bir profil sinyali, OKAS desteği, Context (L3) desteği
    rule_b = False
    if all_l2:
        # Eğer eq sadece jenerikse ("kamera", vb.) ve L2'de başka bir şey yoksa daha dikkatli olmalıyız.
        # generic_equipment_names kontrolü:
        has_specific_l2 = any(t.lower() not in generic_equipment_names for t in all_l2)
        if has_specific_l2 and (okas_supported or all_l3 or len(all_l2) > 1 or matched_action_terms):
            rule_b = True

    # Kural C: Seviye 3 eşleşmesi + bağımsız destek
    rule_c = False
    if all_l3:
        if okas_supported or all_l2 or matched_action_terms:
            rule_c = True

    # Özel bağlam kontrolleri (description_expanded ile ihale başlığı uyumu)
    if not rule_a and not rule_b and not rule_c:
        norm_title = normalize_text(context.tender_name)
        # Başlıktan güçlü domain token'ları: kısa (<= 3 char) ve _WEAK_STANDALONE_TERMS dışında olanlar
        title_tokens = {t for t in norm_title.split() if len(t) > 3 and t not in _WEAK_STANDALONE_TERMS}

        desc_match = False
        if context.profile_description and title_tokens:
            norm_desc = normalize_text(context.profile_description)
            # Tüm domain tokenlerinin description'da bulunması gerekiyor
            if all(t in norm_desc for t in title_tokens):
                desc_match = True

        name_match = False
        if context.profile_name and title_tokens:
            norm_name = normalize_text(context.profile_name)
            name_tokens = {t for t in norm_name.split() if len(t) > 3 and t not in _WEAK_STANDALONE_TERMS}
            common_tokens = name_tokens.intersection(title_tokens)
            # Ortak token var, ANCAK tümü _WEAK_CAPABILITY_TERMS içindeyse name_match geçersiz.
            # Gerçek domain token'ı (nesne/cihaz/alan) gerektir — yalnız generic eylem fiilleri yetmez.
            normalized_cap_terms_all = {normalize_text(c) for c in _WEAK_CAPABILITY_TERMS}
            # Ortak tokenlerden oluşan ifadenin _WEAK_CAPABILITY_TERMS ile örtüşüp örtüşmediğini kontrol et
            # Basit yaklaşım: ortak token kümesindeki her token _WEAK_STANDALONE_TERMS içinde mi?
            # Eğer common_tokens içindeki token'ların tamamı generik eylem sözcüğüyse name_match False
            if common_tokens and not all(t in _WEAK_STANDALONE_TERMS for t in common_tokens):
                name_match = True

        # For rule_b support from name_match
        if name_match and all_l2:
            all_l3.append(context.profile_name)

        if desc_match or name_match:
            # Bağlamsal eşleşme yalnız gerçek domain kanıtı içeriyorsa geçerlidir.
            # name_match yalnız action_term eşleşmesiyse (matched_action_terms var ama L1/L2/L3 yok)
            # bu geçerli bir domain kanıtı sayılmaz.
            has_real_support = okas_supported or bool(all_l2) or desc_match
            if has_real_support or (name_match and not matched_action_terms):
                rule_c = True
                contextual_matches.append(context.tender_name)

    verified = rule_a or rule_b or rule_c

    # Evidence strength
    evidence_strength = "none"
    if rule_a:
        evidence_strength = "strong"
    elif rule_b:
        evidence_strength = "supporting"
    elif rule_c:
        evidence_strength = "contextual"

    okas_text_support_verified = bool(all_l1 or all_l2 or all_l3)

    return PositiveScopeAnalysis(
        verified=verified,
        strong_matched_terms=strong_matched_terms,
        supporting_matched_terms=supporting_matched_terms,
        primary_capability_matches=primary_capability_matches,
        equipment_matches=equipment_matches,
        contextual_matches=contextual_matches,
        abbreviation_matches=abbreviation_matches,
        title_matched_terms=list(dict.fromkeys(title_l1 + title_l2 + title_l3 + title_l4)),
        evidence_matched_terms=list(dict.fromkeys(evidence_l1 + evidence_l2 + evidence_l3 + evidence_l4)),
        evidence_chunk_ids=list(dict.fromkeys(evidence_chunk_ids)),
        matched_okas_codes=list(dict.fromkeys(matched_okas_codes)),
        okas_supported=okas_supported,
        okas_text_support_required=okas_text_support_required,
        okas_text_support_verified=okas_text_support_verified,
        evidence_strength=evidence_strength,
        matched_equipment_terms=equipment_matches,
        matched_action_terms=matched_action_terms,
    )


def analyze_negative_scope(
    context: DecisionValidationContext | None,
) -> NegativeScopeAnalysis:
    """Negatif terimleri yalnızca gerçek ihale başlığı ve kanıtlarında arar.

    OKAS kodları negatif terimi tek başına kanıtlamaz. Kodlar, bulunan metinsel
    sinyalin profil kategorisiyle ilişkisini raporlamak için ayrı tutulur.
    """
    if context is None:
        return NegativeScopeAnalysis()

    signals = context.profile_signals
    if not isinstance(signals, dict):
        return NegativeScopeAnalysis()

    negative_terms = ordered_strings(signals.get("negatif_terimler"))
    positive_terms = ordered_strings(signals.get("guclu_terimler"))
    positive_terms.extend(
        term
        for term in ordered_strings(signals.get("destekleyici_terimler"))
        if term not in positive_terms
    )

    normalized_title = normalize_text(context.tender_name)
    title_matches = [
        term
        for term in negative_terms
        if contains_term(normalized_title, term)
    ]

    evidence_terms: list[str] = []
    evidence_chunk_ids: list[str] = []
    for chunk_id, text in context.evidence_text_by_chunk.items():
        normalized_evidence = normalize_text(text)
        chunk_matches = [
            term
            for term in negative_terms
            if contains_term(normalized_evidence, term)
        ]
        if not chunk_matches:
            continue
        evidence_chunk_ids.append(str(chunk_id))
        evidence_terms.extend(chunk_matches)

    matched_okas_codes = _okas_matches(context, signals)

    all_evidence_texts = list(context.evidence_text_by_chunk.values())
    positive_matches = matching_terms(
        [context.tender_name, *all_evidence_texts],
        positive_terms,
    )
    matched_terms = list(dict.fromkeys([*title_matches, *evidence_terms]))
    
    out_of_scope_verified = False
    negative_verification_method = "none"
    out_of_scope_reasons = []

    if matched_terms:
        out_of_scope_verified = True
        negative_verification_method = "profile_negative_term"
        if positive_matches:
            scope_type = "mixed"
        elif title_matches:
            scope_type = "full"
        else:
            scope_type = "ambiguous"
    else:
        scope_type = "none"
        # YOL B: Proven out of scope
        positive_scope = analyze_positive_scope(context)
        if not positive_scope.verified:
            _GENERIC_WORDS = _WEAK_STANDALONE_TERMS.union({
                "araç", "arac", "malzeme", "enerji", "ihale", "ihalesi", "işi",
                "yılı", "aylık", "yıllık", "günlük", "kapsamında", "alımı", "satın",
                "alınması", "yapım", "yapımı", "taşıt", "otomobil", "kamyon", "minibüs",
                "otobüs", "hizmeti", "kiralama", "kiralık", "kira", "parçası", "parçaları",
                "yedek", "makinesi", "motoru", "cihazı", "tesisi", "hizmetleri", "motor", "makine",
                "sistem", "destek", "bakım"
            })
            _TITLE_STOPWORDS = {
                "ve", "ile", "için", "olan", "dair", "ait", "veya",
                "belediyesi", "müdürlüğü", "başkanlığı", "genel", "dairesi",
                "bakanlığı", "müdür", "başkan", "kurumu"
            }
            
            title_tokens = []
            for t in normalize_text(context.tender_name).split():
                if len(t) <= 2 or t in _TITLE_STOPWORDS or t.isdigit():
                    continue
                title_tokens.append(t)
            
            candidates = []
            for i in range(len(title_tokens)):
                for j in range(i + 1, min(i + 3, len(title_tokens))):
                    candidates.append([title_tokens[i], title_tokens[j]])
                    for k in range(j + 1, min(j + 2, len(title_tokens))):
                        candidates.append([title_tokens[i], title_tokens[j], title_tokens[k]])
            
            valid_candidates = []
            for cand in candidates:
                all_generic = True
                for t in cand:
                    is_gen = False
                    for gen in _GENERIC_WORDS:
                        if token_matches(gen, t):
                            is_gen = True
                            break
                    if not is_gen:
                        all_generic = False
                        break
                if not all_generic:
                    valid_candidates.append(" ".join(cand))
            
            valid_candidates = list(dict.fromkeys(valid_candidates))
            proven_expressions = []
            for chunk_id, text in context.evidence_text_by_chunk.items():
                norm_text = normalize_text(text)
                for cand in valid_candidates:
                    if contains_term(norm_text, cand):
                        proven_expressions.append(cand)
                        if str(chunk_id) not in evidence_chunk_ids:
                            evidence_chunk_ids.append(str(chunk_id))
            
            if proven_expressions:
                proven_expressions = list(dict.fromkeys(proven_expressions))
                out_of_scope_verified = True
                negative_verification_method = "proven_out_of_scope"
                out_of_scope_reasons = proven_expressions
                matched_terms.extend(proven_expressions)
                scope_type = "full" if not positive_matches else "mixed"

    return NegativeScopeAnalysis(
        verified=bool(matched_terms),
        matched_terms=matched_terms,
        title_matched_terms=list(dict.fromkeys(title_matches)),
        evidence_matched_terms=list(dict.fromkeys(evidence_terms)),
        evidence_chunk_ids=list(dict.fromkeys(evidence_chunk_ids)),
        matched_okas_codes=list(dict.fromkeys(matched_okas_codes)),
        profile_okas_supported=bool(matched_okas_codes),
        matched_positive_terms=list(dict.fromkeys(positive_matches)),
        scope_type=scope_type,
        out_of_scope_verified=out_of_scope_verified,
        negative_verification_method=negative_verification_method,
        out_of_scope_reasons=out_of_scope_reasons,
    )


__all__ = ["analyze_negative_scope", "analyze_positive_scope"]
