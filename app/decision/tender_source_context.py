"""PostgreSQL ihale kaydından eksiksiz ve kaynak izli karar bağlamı üretir."""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.decision.models import DecisionValidationContext
from app.domain import TenderRecord
from app.indexing.chunker import SectionAwareChunker
from app.indexing.document_builder import TenderDocumentBuilder
from app.retrieval.isbak_tender_retriever import ChunkEvidence

_HTML_BREAK = re.compile(r"(?i)</?(?:br|p|div|li|tr|h[1-6])\b[^>]*>")
_HTML_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"[ \t\f\v]+")
_NON_ALNUM = re.compile(r"[^0-9a-zçğıöşü]+", re.IGNORECASE)
_PREFILTER_TOKEN_GROUPS = (
    frozenset({"araç", "arac", "taşıt", "tasit", "otomobil", "minibüs", "minibus", "otobüs", "otobus", "kamyon", "ambulans"}),
    frozenset({"bakım", "bakim", "onarım", "onarim", "tamir", "servis"}),
    frozenset({"kiralama", "kiralık", "kiralik", "kira"}),
    frozenset({"organizasyon", "etkinlik", "festival"}),
    frozenset({"klima", "iklimlendirme"}),
)

_PART_PATTERNS = (
    re.compile(
        r"^\s*(?P<number>\d{1,3}|[ivxlcdm]{1,8})\s*[.)-]?\s*"
        r"(?:'?(?:inci|ıncı|nci|üncü|uncu))?\s*(?:kısım|kisim|lot)\s*"
        r"(?:no\.?\s*)?[:.)\-–—]*\s*(?P<name>.*)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:kısım|kisim|lot)\s*(?:no\.?\s*)?"
        r"(?P<number>\d{1,3}|[ivxlcdm]{1,8})\s*[:.)\-–—]+\s*"
        r"(?P<name>.*)$",
        re.IGNORECASE,
    ),
)


def _clean_source_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = _HTML_BREAK.sub("\n", text)
    text = _HTML_TAG.sub(" ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_SPACE.sub(" ", line).strip(" •\t") for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.translate(str.maketrans({"I": "ı", "İ": "i"}))
    text = text.lower().replace("\u0307", "")
    return " ".join(_NON_ALNUM.sub(" ", text).split())


def _tokens_match(expected: str, actual: str) -> bool:
    if expected == actual:
        return True
    if any(
        expected in group and actual in group
        for group in _PREFILTER_TOKEN_GROUPS
    ):
        return True
    if min(len(expected), len(actual)) < 5:
        return False
    common = 0
    for left, right in zip(expected, actual, strict=False):
        if left != right:
            break
        common += 1
    return common >= 5


def _contains_signal(text: str, signal: str) -> bool:
    """Ek/kök değişimlerine toleranslı, en az iki sözcüklü güvenli eşleşme."""

    expected = _normalize(signal).split()
    actual = _normalize(text).split()
    if not expected or not actual:
        return False

    if len(expected) == 1:
        return any(_tokens_match(expected[0], token) for token in actual)

    matched_positions: list[int] = []
    search_from = 0
    for token in expected:
        position = next(
            (
                index
                for index in range(search_from, len(actual))
                if _tokens_match(token, actual[index])
            ),
            None,
        )
        if position is None:
            return False
        matched_positions.append(position)
        search_from = position + 1

    # Bir kavramın sözcükleri aynı yakın bağlam içinde bulunmalıdır. Böylece
    # metnin uzak noktalarındaki iki genel sözcük yanlış eşleşme üretmez.
    return matched_positions[-1] - matched_positions[0] <= len(expected) + 4


def _regex_prefilter(text: str, signals: list[str]) -> list[str]:
    """Ucuz düzenli ifade taramasıyla ayrıntılı eşleşme adaylarını daraltır."""

    normalized_text = _normalize(text)
    candidates: list[str] = []
    for signal in signals:
        tokens = _normalize(signal).split()
        if not tokens:
            continue
        token_patterns: list[str] = []
        for token in tokens:
            group = next(
                (item for item in _PREFILTER_TOKEN_GROUPS if token in item),
                frozenset({token}),
            )
            alternatives = "|".join(
                rf"{re.escape(value)}\w*"
                for value in sorted(group, key=lambda value: (-len(value), value))
            )
            token_patterns.append(rf"(?:{alternatives})")
        gap = r"(?:\s+\w+){0,4}\s+"
        pattern = re.compile(r"\b" + gap.join(token_patterns) + r"\b")
        if pattern.search(normalized_text):
            candidates.append(signal)
    return candidates


def _ordered_signal_values(signals: dict[str, Any], key: str) -> list[str]:
    raw = signals.get(key, [])
    if not isinstance(raw, list):
        return []
    return list(
        dict.fromkeys(
            text
            for value in raw
            if (text := " ".join(str(value).split()).strip())
        )
    )


@dataclass(frozen=True)
class SourceTenderPart:
    part_number: str
    part_name: str
    source_table: str
    source_record_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "kisim_no": self.part_number,
            "kisim_adi": self.part_name,
            "kaynak_tablo": self.source_table,
            "kaynak_kayit_id": self.source_record_id,
        }


@dataclass(frozen=True)
class EvidenceSelection:
    case_type: str
    evidence_limit: int
    positive_terms: list[str] = field(default_factory=list)
    negative_terms: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass(frozen=True)
class TenderSourceContext:
    tender: TenderRecord
    okas_codes: list[str]
    technical_specifications: list[str]
    parts: list[SourceTenderPart]
    parts_extraction_complete: bool
    missing_required_fields: list[str]
    all_evidence_chunks: list[ChunkEvidence]
    selected_evidence_chunks: list[ChunkEvidence]
    selection: EvidenceSelection

    @property
    def is_partial_offer(self) -> bool:
        return bool(self.tender.kismi_teklif) or len(self.parts) > 1

    def validation_context(
        self,
        *,
        profile_signals: dict[str, Any],
        retrieval_score: float,
    ) -> DecisionValidationContext:
        return DecisionValidationContext(
            tender_name=str(self.tender.adi or ""),
            tender_type=str(self.tender.ihale_turu or ""),
            tender_okas_codes=list(self.okas_codes),
            evidence_text_by_chunk={
                item.chunk_id: item.text
                for item in self.all_evidence_chunks
                if item.chunk_id and item.text.strip()
            },
            profile_signals=dict(profile_signals),
            retrieval_score=retrieval_score,
            partial_offer=self.is_partial_offer,
            tender_parts=[part.to_dict() for part in self.parts],
            technical_specifications=list(self.technical_specifications),
            source_origin="postgresql",
            source_complete=not self.missing_required_fields,
            source_missing_fields=list(self.missing_required_fields),
            evidence_strategy=self.selection.case_type,
            selected_evidence_limit=self.selection.evidence_limit,
        )


class TenderContextTooLargeError(ValueError):
    """Zorunlu yapılandırılmış veri bağlam bütçesine sığmadığında üretilir."""


def extract_tender_parts(tender: TenderRecord) -> list[SourceTenderPart]:
    """İlan, kapsam ve özellik metinlerindeki açık kısım başlıklarını çıkarır."""

    sources: list[tuple[str, str, str]] = []
    if tender.kapsam:
        sources.append(("public.tenders", str(tender.id), str(tender.kapsam)))
    sources.extend(
        (
            "public.tender_announcements",
            str(item.id),
            "\n".join(part for part in (item.baslik, item.icerik) if part),
        )
        for item in tender.announcements
    )
    sources.extend(
        ("public.tender_characteristics", str(item.id), str(item.ozellik))
        for item in tender.characteristics
    )

    parts: list[SourceTenderPart] = []
    seen: set[tuple[str, str]] = set()
    for source_table, record_id, raw_text in sources:
        lines = _clean_source_text(raw_text).splitlines()
        for index, line in enumerate(lines):
            match = next(
                (pattern.match(line) for pattern in _PART_PATTERNS if pattern.match(line)),
                None,
            )
            if match is None:
                continue
            number = str(match.group("number") or "").strip().upper()
            name = str(match.group("name") or "").strip(" :.-–—")
            if not name and index + 1 < len(lines):
                candidate = lines[index + 1].strip(" :.-–—")
                if candidate and not any(pattern.match(candidate) for pattern in _PART_PATTERNS):
                    name = candidate
            if not name:
                name = f"{number}. kısım"
            key = (_normalize(number), _normalize(name))
            if key in seen:
                continue
            seen.add(key)
            parts.append(
                SourceTenderPart(
                    part_number=number,
                    part_name=name[:500],
                    source_table=source_table,
                    source_record_id=record_id,
                )
            )

    def sort_key(part: SourceTenderPart) -> tuple[int, str, str]:
        number = part.part_number
        return (int(number) if number.isdigit() else 10_000, number, part.part_name)

    return sorted(parts, key=sort_key)


class TenderSourceContextBuilder:
    """Gerçek DB kaydını parçalar ve vakaya göre 1/3/4 kanıt seçer."""

    def __init__(
        self,
        *,
        document_builder: TenderDocumentBuilder | None = None,
        chunker: SectionAwareChunker | None = None,
    ) -> None:
        self.document_builder = document_builder or TenderDocumentBuilder()
        self.chunker = chunker or SectionAwareChunker()

    def build(
        self,
        tender: TenderRecord,
        *,
        profile_signals: dict[str, Any] | None = None,
    ) -> TenderSourceContext:
        signals = profile_signals if isinstance(profile_signals, dict) else {}
        okas_codes = [
            " — ".join(part for part in (str(item.kod or "").strip(), str(item.ad or "").strip()) if part)
            for item in tender.okas_codes
            if str(item.kod or "").strip() or str(item.ad or "").strip()
        ]
        technical_specifications = list(
            dict.fromkeys(
                text
                for item in tender.characteristics
                if (text := _clean_source_text(item.ozellik))
            )
        )
        parts = extract_tender_parts(tender)
        partial_flag = bool(tender.kismi_teklif)
        parts_complete = not partial_flag or bool(parts)
        missing_required_fields: list[str] = []
        if not str(tender.adi or "").strip():
            missing_required_fields.append("adi")
        if not str(tender.ihale_turu or "").strip():
            missing_required_fields.append("ihale_turu")
        if tender.kismi_teklif is None:
            missing_required_fields.append("kismi_teklif")
        if not parts_complete:
            missing_required_fields.append("kisim_listesi")

        document = self.document_builder.build(tender)
        raw_chunks = self.chunker.chunk_document(document)
        evidence_chunks = [
            ChunkEvidence(
                chunk_id=str(chunk.get("chunk_id") or ""),
                section_id=str(chunk.get("section_type") or chunk.get("section_id") or ""),
                chunk_title=str(chunk.get("section_type") or "İhale kaynağı"),
                semantic_score=0.0,
                text=str(chunk.get("text") or "").strip(),
                text_preview=str(chunk.get("text") or "").strip()[:200],
            )
            for chunk in raw_chunks
            if str(chunk.get("chunk_id") or "").strip()
            and str(chunk.get("text") or "").strip()
        ]

        selection, selected_chunks = self._select_evidence(
            tender=tender,
            chunks=evidence_chunks,
            signals=signals,
            parts=parts,
        )
        return TenderSourceContext(
            tender=tender,
            okas_codes=list(dict.fromkeys(okas_codes)),
            technical_specifications=technical_specifications,
            parts=parts,
            parts_extraction_complete=parts_complete,
            missing_required_fields=missing_required_fields,
            all_evidence_chunks=evidence_chunks,
            selected_evidence_chunks=selected_chunks,
            selection=selection,
        )

    @staticmethod
    def _select_evidence(
        *,
        tender: TenderRecord,
        chunks: list[ChunkEvidence],
        signals: dict[str, Any],
        parts: list[SourceTenderPart],
    ) -> tuple[EvidenceSelection, list[ChunkEvidence]]:
        positive_signals = [
            *_ordered_signal_values(signals, "guclu_terimler"),
            *_ordered_signal_values(signals, "destekleyici_terimler"),
        ]
        positive_signals = list(dict.fromkeys(positive_signals))
        negative_signals = _ordered_signal_values(signals, "negatif_terimler")
        corpus = "\n".join(
            [
                str(tender.adi or ""),
                str(tender.kapsam or ""),
                *(chunk.text for chunk in chunks),
            ]
        )
        positive_matches = [
            term
            for term in _regex_prefilter(corpus, positive_signals)
            if _contains_signal(corpus, term)
        ]
        negative_matches = [
            term
            for term in _regex_prefilter(corpus, negative_signals)
            if _contains_signal(corpus, term)
        ]

        is_partial = bool(tender.kismi_teklif) or len(parts) > 1
        if is_partial:
            case_type = "partial"
            limit = 4
            reason = "Kısmi teklif veya birden fazla kısım bulundu; dört kanıt seçildi."
        elif positive_matches and negative_matches:
            case_type = "mixed"
            limit = 4
            reason = "Olumlu ve olumsuz kapsam sinyalleri birlikte bulundu; dört kanıt seçildi."
        elif not positive_matches and not negative_matches:
            case_type = "ambiguous"
            limit = 3
            reason = "Açık faaliyet sinyali bulunamadı; üç farklı kanıt seçildi."
        else:
            case_type = "clear"
            limit = 1
            reason = "Tek yönlü açık faaliyet sinyali bulundu; en güçlü kanıt seçildi."

        scored: list[tuple[float, int, ChunkEvidence]] = []
        section_priority = {
            "scope_and_location": 5.0,
            "characteristics": 4.0,
            "tender_summary": 3.0,
            "announcement": 2.0,
            "okas": 1.0,
        }
        for index, chunk in enumerate(chunks):
            score = section_priority.get(chunk.section_id, 0.0)
            score += 4.0 * sum(_contains_signal(chunk.text, term) for term in positive_matches)
            score += 5.0 * sum(_contains_signal(chunk.text, term) for term in negative_matches)
            scored.append((score, index, chunk))
        scored.sort(key=lambda item: (-item[0], item[1], item[2].chunk_id))

        selected: list[ChunkEvidence] = []
        selected_sections: set[str] = set()
        # Belirsiz/kısmi vakalarda önce bölüm çeşitliliğini koru.
        if limit > 1:
            for _score, _index, chunk in scored:
                if chunk.section_id in selected_sections:
                    continue
                selected.append(chunk)
                selected_sections.add(chunk.section_id)
                if len(selected) >= limit:
                    break
        if len(selected) < limit:
            selected_ids = {item.chunk_id for item in selected}
            selected.extend(
                chunk
                for _score, _index, chunk in scored
                if chunk.chunk_id not in selected_ids
            )
        selected = selected[:limit]

        return (
            EvidenceSelection(
                case_type=case_type,
                evidence_limit=limit,
                positive_terms=positive_matches,
                negative_terms=negative_matches,
                reason=reason,
            ),
            selected,
        )


def render_database_tender_context(
    source: TenderSourceContext,
    *,
    max_chars: int,
) -> str:
    """Zorunlu alanları kesmeden, seçilmiş kanıtları bütçe içinde sunar."""

    tender = source.tender
    partial_text = (
        "Evet" if source.is_partial_offer else "Hayır" if tender.kismi_teklif is not None else "Bilinmiyor"
    )
    structured_sections = [
        "[POSTGRESQL GERÇEK İHALE KAYDI]",
        f"İhale ID: {tender.id}",
        f"İKN: {tender.ikn}",
        f"İhale adı: {tender.adi or ''}",
        f"İdare: {tender.idare_adi or ''}",
        f"Gerçek ihale türü: {tender.ihale_turu or 'Bilinmiyor'}",
        f"İhale usulü: {tender.ihale_usulu or 'Bilinmiyor'}",
        f"İhale durumu: {tender.ihale_durumu or 'Bilinmiyor'}",
        f"Kısmi teklif: {partial_text}",
        f"Kapsam: {tender.kapsam or 'Belirtilmemiş'}",
        "",
        "[BÜTÜN OKAS KAYITLARI]",
        *(source.okas_codes or ["Kayıt yok"]),
        "",
        "[KISIM LİSTESİ]",
        *(
            f"{part.part_number}: {part.part_name} "
            f"(kaynak={part.source_table}:{part.source_record_id})"
            for part in source.parts
        ),
        *(
            ["Kısmi teklif açık, ancak açık kısım başlığı kaynak metinden ayrıştırılamadı."]
            if source.is_partial_offer and not source.parts
            else []
        ),
        *(["Kısmi teklif bulunmuyor."] if not source.is_partial_offer else []),
        "",
        "[BÜTÜN TEKNİK ÖZELLİKLER]",
        *(source.technical_specifications or ["Kayıt yok"]),
        "",
        "[KANIT SEÇİMİ]",
        f"Vaka: {source.selection.case_type}",
        f"Kanıt sayısı: {len(source.selected_evidence_chunks)}",
        f"Gerekçe: {source.selection.reason}",
        f"Eksik zorunlu kaynak alanları: {', '.join(source.missing_required_fields) or 'Yok'}",
    ]
    structured = "\n".join(str(item) for item in structured_sections)
    if len(structured) > max_chars:
        raise TenderContextTooLargeError(
            "Gerçek ihale türü, OKAS, kısım ve teknik özellikler kesilmeden "
            f"taşınamadı: zorunlu_karakter={len(structured)}, sınır={max_chars}."
        )

    parts = [structured]
    current_length = len(structured)
    for chunk in source.selected_evidence_chunks:
        header = (
            f"[KAYNAK | chunk_id: {chunk.chunk_id} | bölüm: "
            f"{chunk.section_id or chunk.chunk_title}]"
        )
        entry = f"{header}\n{chunk.text.strip()}"
        if current_length + len(entry) + 2 > max_chars:
            break
        parts.append(entry)
        current_length += len(entry) + 2
    return "\n\n".join(parts)


__all__ = [
    "EvidenceSelection",
    "SourceTenderPart",
    "TenderContextTooLargeError",
    "TenderSourceContext",
    "TenderSourceContextBuilder",
    "extract_tender_parts",
    "render_database_tender_context",
]
