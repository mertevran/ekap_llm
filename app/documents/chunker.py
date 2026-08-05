from __future__ import annotations

import hashlib

from app.domain import TenderChunk, TenderDocument, TenderSection


class TenderChunker:
    def __init__(
        self,
        max_chars: int = 1800,
        overlap_chars: int = 200,
    ) -> None:
        if max_chars < 200:
            raise ValueError("max_chars en az 200 olmalıdır.")

        if overlap_chars < 0:
            raise ValueError("overlap_chars negatif olamaz.")

        if overlap_chars >= max_chars:
            raise ValueError("overlap_chars, max_chars değerinden küçük olmalıdır.")

        self.max_chars = max_chars
        self.overlap_chars = overlap_chars

    def chunk_document(
        self,
        document: TenderDocument,
    ) -> list[TenderChunk]:
        chunks: list[TenderChunk] = []

        for section in document.sections:
            chunks.extend(self._chunk_section(section))

        return chunks

    def _chunk_section(
        self,
        section: TenderSection,
    ) -> list[TenderChunk]:
        text = section.text.strip()

        if not text:
            return []

        ranges = self._calculate_ranges(text)
        chunks: list[TenderChunk] = []

        for chunk_index, (start, end) in enumerate(ranges):
            chunk_text = text[start:end].strip()

            if not chunk_text:
                continue

            actual_start = text.find(chunk_text, start, end)

            if actual_start < 0:
                actual_start = start

            actual_end = actual_start + len(chunk_text)

            chunk_id = self._build_chunk_id(
                section_id=section.section_id,
                chunk_index=chunk_index,
                start=actual_start,
                end=actual_end,
                text=chunk_text,
            )

            chunks.append(
                TenderChunk(
                    chunk_id=chunk_id,
                    tender_id=section.tender_id,
                    ikn=section.ikn,
                    section_id=section.section_id,
                    source_table=section.source_table,
                    source_record_ids=section.source_record_ids,
                    title=section.title,
                    text=chunk_text,
                    chunk_index=chunk_index,
                    char_start=actual_start,
                    char_end=actual_end,
                    metadata=dict(section.metadata),
                )
            )

        return chunks

    def _calculate_ranges(
        self,
        text: str,
    ) -> list[tuple[int, int]]:
        ranges: list[tuple[int, int]] = []
        text_length = len(text)
        start = 0

        while start < text_length:
            maximum_end = min(start + self.max_chars, text_length)

            if maximum_end >= text_length:
                end = text_length
            else:
                segment = text[start:maximum_end]
                minimum_cut = max(int(self.max_chars * 0.55), 1)
                relative_end = self._find_boundary(
                    segment=segment,
                    minimum_cut=minimum_cut,
                )
                end = start + relative_end

            if end <= start:
                end = min(start + self.max_chars, text_length)

            ranges.append((start, end))

            if end >= text_length:
                break

            next_start = max(
                end - self.overlap_chars,
                start + 1,
            )

            while next_start < end and next_start < text_length and not text[next_start].isspace():
                next_start += 1

            while next_start < text_length and text[next_start].isspace():
                next_start += 1

            if next_start <= start:
                next_start = end

            start = next_start

        return ranges

    @staticmethod
    def _find_boundary(
        segment: str,
        minimum_cut: int,
    ) -> int:
        markers = [
            "\n\n",
            "\n",
            ". ",
            "; ",
            ", ",
            " ",
        ]

        best_position = -1

        for marker in markers:
            position = segment.rfind(marker, minimum_cut)

            if position >= 0:
                candidate = position + len(marker)
                best_position = max(best_position, candidate)

        if best_position <= 0:
            return len(segment)

        return best_position

    @staticmethod
    def _build_chunk_id(
        section_id: str,
        chunk_index: int,
        start: int,
        end: int,
        text: str,
    ) -> str:
        identity = f"{section_id}|{chunk_index}|{start}|{end}|{text}"

        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()

        return digest[:24]
