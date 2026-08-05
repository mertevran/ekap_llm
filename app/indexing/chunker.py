import os
from datetime import datetime
from hashlib import sha256
from typing import Any

from app.indexing.chunk_reporter import ChunkReporter


class SectionAwareChunker:
    """
    Belge bölümlerini kaynak bilgilerini koruyarak parçalara ayırır.
    """

    def __init__(
        self,
        chunk_target_chars: int = int(os.getenv("CHUNK_TARGET_CHARS", "1200")),
        chunk_min_chars: int = int(os.getenv("CHUNK_MIN_CHARS", "500")),
        chunk_soft_max_chars: int = int(os.getenv("CHUNK_SOFT_MAX_CHARS", "1500")),
        chunk_hard_max_chars: int = int(os.getenv("CHUNK_HARD_MAX_CHARS", "1800")),
        chunk_overlap_chars: int = int(os.getenv("CHUNK_OVERLAP_CHARS", "150")),
        chunking_version: str = "2.0.0",
        reporter: ChunkReporter | None = None,
    ) -> None:
        self.chunk_target_chars = chunk_target_chars
        self.chunk_min_chars = chunk_min_chars
        self.chunk_soft_max_chars = chunk_soft_max_chars
        self.chunk_hard_max_chars = chunk_hard_max_chars
        self.chunk_overlap_chars = chunk_overlap_chars
        self.chunking_version = chunking_version

        self.reporter = reporter
        self._seen_hashes = set()

    def chunk_document(self, document: dict[str, Any]) -> list[dict[str, Any]]:
        if self.reporter:
            self.reporter.add_tender()

        chunks: list[dict[str, Any]] = []
        # Reset seen hashes for deduplication per tender
        self._seen_hashes = set()

        for section in document.get("sections", []):
            section_chunks = self._chunk_section(section, document)

            # Deduplication
            for c in section_chunks:
                text_hash = sha256(c["text"].encode("utf-8")).hexdigest()
                is_duplicate = text_hash in self._seen_hashes
                if not is_duplicate:
                    self._seen_hashes.add(text_hash)
                    chunks.append(c)

                if self.reporter:
                    self.reporter.add_chunk(c, is_duplicate=is_duplicate)

        return chunks

    def _chunk_section(
        self, section: dict[str, Any], document: dict[str, Any]
    ) -> list[dict[str, Any]]:
        stype = section.get("metadata", {}).get("section_type")

        if stype == "tender_summary":
            return self._chunk_single(section, document)
        elif stype == "okas":
            return self._chunk_lines(section, document, prefix="")
        elif stype == "characteristics":
            return self._chunk_characteristics(section, document)
        elif stype == "scope_and_location":
            return self._chunk_text_with_overlap(section, document)
        elif stype == "announcement":
            return self._chunk_text_with_overlap(section, document)
        else:
            return self._chunk_text_with_overlap(section, document)

    def _create_chunk_metadata(
        self,
        section: dict[str, Any],
        document: dict[str, Any],
        text: str,
        start_char: int,
        end_char: int,
        embedding_prefix: str,
        source_record_ids: list[str],
    ) -> dict[str, Any]:
        tender_id = document.get("tender_id", "")
        section_id = section.get("section_id", "")
        section_type = section.get("metadata", {}).get("section_type", "")

        # Determine deterministic chunk_id
        hash_input = f"{tender_id}:{section_type}:{section_id}:{','.join(source_record_ids)}:{start_char}:{end_char}:{self.chunking_version}"
        chunk_hash = sha256(hash_input.encode("utf-8")).hexdigest()
        chunk_id = f"chk_{chunk_hash[:16]}"

        embedding_text = f"{embedding_prefix}\n\n{text}" if embedding_prefix else text

        metadata = {
            "chunk_id": chunk_id,
            "tender_id": tender_id,
            "ikn": document.get("ikn", ""),
            "title": document.get("title", ""),
            "authority_name": document.get("metadata", {}).get("idare_adi", ""),
            "section_type": section_type,
            "section_id": section_id,
            "source_table": section.get("source_table", ""),
            "source_record_ids": source_record_ids,
            "start_char": start_char,
            "end_char": end_char,
            "text": text,
            "embedding_text": embedding_text,
            "source_hash": sha256(text.encode("utf-8")).hexdigest(),
            "chunk_hash": chunk_hash,
            "chunking_version": self.chunking_version,
            "created_at": datetime.utcnow().isoformat(),
        }

        if section_type == "announcement":
            metadata["announcement_type"] = section.get("metadata", {}).get("announcement_type", "")
            metadata["announcement_date"] = section.get("metadata", {}).get("announcement_date", "")

        if section_type == "okas":
            metadata["okas_codes"] = section.get("metadata", {}).get("okas_codes", [])

        return metadata

    def _get_embedding_prefix(self, document: dict[str, Any], section: dict[str, Any]) -> str:
        ikn = document.get("ikn", "")
        stype = section.get("metadata", {}).get("section_type", "")

        title_map = {
            "tender_summary": "İhale Özeti",
            "scope_and_location": "Kapsam ve Konum",
            "okas": "OKAS Kodları",
            "characteristics": "Teknik Özellikler",
            "announcement": f"İlan: {section.get('metadata', {}).get('heading', '')}",
        }

        return f"İKN: {ikn}\nBölüm: {title_map.get(stype, stype)}"

    def _chunk_single(
        self, section: dict[str, Any], document: dict[str, Any]
    ) -> list[dict[str, Any]]:
        text = section.get("text", "")
        if not text:
            return []

        prefix = self._get_embedding_prefix(document, section)
        meta = self._create_chunk_metadata(
            section, document, text, 0, len(text), prefix, section.get("source_record_ids", [])
        )
        return [meta]

    def _chunk_lines(
        self, section: dict[str, Any], document: dict[str, Any], prefix: str = ""
    ) -> list[dict[str, Any]]:
        text = section.get("text", "")
        if not text:
            return []

        prefix = self._get_embedding_prefix(document, section)
        lines = text.split("\n")

        chunks = []
        current_chunk = []
        current_length = 0
        start_char = 0
        current_char = 0

        for line in lines:
            line_len = len(line) + 1  # +1 for newline
            if current_length + line_len > self.chunk_target_chars and current_chunk:
                chunk_text = "\n".join(current_chunk)
                meta = self._create_chunk_metadata(
                    section,
                    document,
                    chunk_text,
                    start_char,
                    current_char,
                    prefix,
                    section.get("source_record_ids", []),
                )
                chunks.append(meta)
                current_chunk = [line]
                start_char = current_char
                current_length = line_len
            else:
                current_chunk.append(line)
                current_length += line_len
            current_char += line_len

        if current_chunk:
            chunk_text = "\n".join(current_chunk)
            meta = self._create_chunk_metadata(
                section,
                document,
                chunk_text,
                start_char,
                current_char,
                prefix,
                section.get("source_record_ids", []),
            )
            chunks.append(meta)

        return chunks

    def _chunk_characteristics(
        self, section: dict[str, Any], document: dict[str, Any]
    ) -> list[dict[str, Any]]:
        items = section.get("metadata", {}).get("items", [])
        if not items:
            return []

        prefix = self._get_embedding_prefix(document, section)

        chunks = []
        current_chunk = []
        current_ids = []
        current_length = 0
        start_char = 0

        for item in items:
            text = item["text"]
            rid = item["id"]

            # If a single item is massive, chunk it via overlap strategy
            if len(text) > self.chunk_hard_max_chars:
                if current_chunk:
                    chunk_text = "\n".join(current_chunk)
                    meta = self._create_chunk_metadata(
                        section,
                        document,
                        chunk_text,
                        start_char,
                        start_char + len(chunk_text),
                        prefix,
                        current_ids,
                    )
                    chunks.append(meta)
                    current_chunk = []
                    current_ids = []
                    current_length = 0
                    start_char += len(chunk_text)

                # Chunk this large item separately
                large_item_section = dict(section)
                large_item_section["text"] = text
                large_item_section["source_record_ids"] = [rid]
                large_chunks = self._chunk_text_with_overlap(large_item_section, document)
                chunks.extend(large_chunks)
                start_char += len(text)
                continue

            item_len = len(text) + 1
            if current_length + item_len > self.chunk_target_chars and current_chunk:
                chunk_text = "\n".join(current_chunk)
                meta = self._create_chunk_metadata(
                    section,
                    document,
                    chunk_text,
                    start_char,
                    start_char + len(chunk_text),
                    prefix,
                    current_ids,
                )
                chunks.append(meta)
                start_char += len(chunk_text)
                current_chunk = [text]
                current_ids = [rid]
                current_length = item_len
            else:
                current_chunk.append(text)
                current_ids.append(rid)
                current_length += item_len

        if current_chunk:
            chunk_text = "\n".join(current_chunk)
            meta = self._create_chunk_metadata(
                section,
                document,
                chunk_text,
                start_char,
                start_char + len(chunk_text),
                prefix,
                current_ids,
            )
            chunks.append(meta)

        return chunks

    def _chunk_text_with_overlap(
        self, section: dict[str, Any], document: dict[str, Any]
    ) -> list[dict[str, Any]]:
        text = section.get("text", "")
        if not text:
            return []

        prefix = self._get_embedding_prefix(document, section)
        source_ids = section.get("source_record_ids", [])

        # Custom logic for splitting markdown tables correctly can be complex,
        # fallback to simpler boundary finding based on \n\n, \n, and sentence ends
        chunks = []
        start = 0

        while start < len(text):
            target_end = min(start + self.chunk_target_chars, len(text))

            # If we reached the end
            if target_end == len(text):
                chunk_text = text[start:].strip()
                if chunk_text:
                    meta = self._create_chunk_metadata(
                        section, document, chunk_text, start, len(text), prefix, source_ids
                    )
                    chunks.append(meta)
                break

            # Find a good boundary
            end = self._find_boundary(text, start, target_end)

            chunk_text = text[start:end].strip()
            if chunk_text:
                meta = self._create_chunk_metadata(
                    section, document, chunk_text, start, end, prefix, source_ids
                )
                chunks.append(meta)

            # Calculate overlap start
            if end < len(text):
                overlap_start = max(start, end - self.chunk_overlap_chars)
                # Align overlap to sentence or word
                overlap_start = self._find_boundary_backward(text, start, overlap_start)
                start = overlap_start if overlap_start < end else end
            else:
                start = end

        return chunks

    def _find_boundary(self, text: str, start: int, target_end: int) -> int:
        if target_end >= len(text):
            return len(text)

        # Try finding a double newline
        search_window = text[start : min(start + self.chunk_hard_max_chars, len(text))]

        last_double_newline = search_window.rfind("\n\n", 0, target_end - start)
        if last_double_newline > 0 and (last_double_newline + start) - start > self.chunk_min_chars:
            return start + last_double_newline

        # Try single newline
        last_newline = search_window.rfind("\n", 0, target_end - start)
        if last_newline > 0 and (last_newline + start) - start > self.chunk_min_chars:
            return start + last_newline

        # Try sentence end
        last_period = search_window.rfind(". ", 0, target_end - start)
        if last_period > 0 and (last_period + start) - start > self.chunk_min_chars:
            return start + last_period + 1

        # Hard limit fallback
        return min(start + self.chunk_hard_max_chars, len(text))

    def _find_boundary_backward(self, text: str, min_start: int, target_pos: int) -> int:
        search_window = text[min_start:target_pos]
        last_period = search_window.rfind(". ")
        if last_period > 0:
            return min_start + last_period + 2
        last_newline = search_window.rfind("\n")
        if last_newline > 0:
            return min_start + last_newline + 1
        last_space = search_window.rfind(" ")
        if last_space > 0:
            return min_start + last_space + 1
        return target_pos
