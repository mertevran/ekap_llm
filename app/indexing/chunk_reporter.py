import csv
import json
from pathlib import Path
from typing import Any


class ChunkReporter:
    def __init__(self, output_dir: str = "reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.total_tenders = 0
        self.chunks: list[dict[str, Any]] = []
        self.duplicates: list[dict[str, Any]] = []
        self.table_splits: list[dict[str, Any]] = []
        self.oversized: list[dict[str, Any]] = []
        self.short: list[dict[str, Any]] = []
        self.missing_source_ids: list[dict[str, Any]] = []

    def add_tender(self):
        self.total_tenders += 1

    def add_chunk(
        self, chunk: dict[str, Any], is_duplicate: bool = False, table_split_issue: bool = False
    ):
        if is_duplicate:
            self.duplicates.append(chunk)
            return  # do not add to main chunks if duplicate

        self.chunks.append(chunk)

        text = chunk.get("text", "")
        if len(text) > 1800:
            self.oversized.append(chunk)
        elif len(text) < 500:
            self.short.append(chunk)

        if not chunk.get("source_record_ids"):
            self.missing_source_ids.append(chunk)

        if table_split_issue:
            self.table_splits.append(chunk)

    def generate_reports(self):
        if not self.chunks:
            return

        lengths = [len(c.get("text", "")) for c in self.chunks]
        lengths.sort()
        avg_len = sum(lengths) / len(lengths) if lengths else 0
        median_len = lengths[len(lengths) // 2] if lengths else 0

        sections_counts = {}
        for c in self.chunks:
            stype = c.get("section_type", "unknown")
            sections_counts[stype] = sections_counts.get(stype, 0) + 1

        summary = {
            "total_tenders": self.total_tenders,
            "total_chunks": len(self.chunks),
            "average_length": avg_len,
            "median_length": median_len,
            "shortest_length": lengths[0] if lengths else 0,
            "longest_length": lengths[-1] if lengths else 0,
            "sections_distribution": sections_counts,
            "oversized_count": len(self.oversized),
            "short_count": len(self.short),
            "missing_source_ids_count": len(self.missing_source_ids),
            "duplicate_count": len(self.duplicates),
            "table_split_issues": len(self.table_splits),
        }

        with open(self.output_dir / "chunking_summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        # Deduplication report
        with open(
            self.output_dir / "chunking_deduplication_report.json", "w", encoding="utf-8"
        ) as f:
            json.dump(
                [
                    {"chunk_id": c.get("chunk_id"), "tender_id": c.get("tender_id")}
                    for c in self.duplicates
                ],
                f,
                indent=2,
                ensure_ascii=False,
            )

        # Source integrity
        with open(self.output_dir / "chunking_source_integrity.json", "w", encoding="utf-8") as f:
            json.dump(
                [
                    {
                        "chunk_id": c.get("chunk_id"),
                        "tender_id": c.get("tender_id"),
                        "section_type": c.get("section_type"),
                    }
                    for c in self.missing_source_ids
                ],
                f,
                indent=2,
                ensure_ascii=False,
            )

        # Oversized chunks CSV
        self._write_csv(self.output_dir / "chunking_oversized_chunks.csv", self.oversized)

        # Short chunks CSV
        self._write_csv(self.output_dir / "chunking_short_chunks.csv", self.short)

        # Table splits CSV
        self._write_csv(self.output_dir / "chunking_table_splits.csv", self.table_splits)

        # Distribution CSV
        with open(
            self.output_dir / "chunking_distribution.csv", "w", newline="", encoding="utf-8"
        ) as f:
            writer = csv.writer(f)
            writer.writerow(["section_type", "count"])
            for st, count in sections_counts.items():
                writer.writerow([st, count])

        # Markdown samples
        with open(self.output_dir / "chunking_samples.md", "w", encoding="utf-8") as f:
            f.write("# Chunking Samples\n\n")
            samples_per_section = {}
            for c in self.chunks:
                st = c.get("section_type", "unknown")
                if st not in samples_per_section:
                    samples_per_section[st] = []
                if len(samples_per_section[st]) < 3:
                    samples_per_section[st].append(c)

            for st, samples in samples_per_section.items():
                f.write(f"## Section: {st}\n")
                for i, s in enumerate(samples):
                    f.write(f"### Sample {i + 1} (ID: {s.get('chunk_id')})\n")
                    f.write(f"**Embedding Text:**\n```\n{s.get('embedding_text', '')}\n```\n\n")
                    f.write(f"**Raw Text:**\n```\n{s.get('text', '')}\n```\n\n")

    def _write_csv(self, filepath: Path, chunks: list[dict[str, Any]]):
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["chunk_id", "tender_id", "section_type", "length", "text_preview"])
            for c in chunks:
                text = c.get("text", "")
                writer.writerow(
                    [
                        c.get("chunk_id", ""),
                        c.get("tender_id", ""),
                        c.get("section_type", ""),
                        len(text),
                        text[:100].replace("\n", " ") + "...",
                    ]
                )
