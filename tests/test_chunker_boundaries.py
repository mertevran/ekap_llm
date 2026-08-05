from app.indexing.chunker import SectionAwareChunker
from app.indexing.text_utils import normalize_text


def document(text: str) -> dict:
    return {
        "title": "Örnek",
        "metadata": {},
        "sections": [
            {
                "section_id": "s1",
                "tender_id": "t1",
                "ikn": "2026/1",
                "source_table": "tender_announcements",
                "source_record_ids": ["a1"],
                "title": "İlan",
                "text": text,
                "metadata": {},
            }
        ],
    }


def test_chunks_do_not_start_inside_words() -> None:
    original_text = ("Birinci cümle burada. İkinci cümle burada ve uzundur. ") * 30

    normalized_text = normalize_text(original_text)

    chunker = SectionAwareChunker(
        chunk_target_chars=180,
        chunk_overlap_chars=45,
        chunk_hard_max_chars=200,
    )

    chunks = chunker.chunk_document(document(original_text))

    assert len(chunks) > 2

    for chunk in chunks:
        start = chunk["start_char"]
        chunk_text = chunk["text"]

        assert chunk_text
        assert not chunk_text[0].isspace()
        assert not chunk_text[-1].isspace()

        if start > 0:
            assert normalized_text[start - 1].isspace(), (
                "Parça kelimenin ortasından başlıyor: "
                f"start={start}, "
                f"önceki_karakter={normalized_text[start - 1]!r}, "
                f"başlangıç={chunk_text[:40]!r}"
            )


def test_chunk_ids_are_deterministic() -> None:
    text = "Yazılım geliştirme hizmeti alınacaktır. " * 50

    chunker = SectionAwareChunker(
        chunk_target_chars=160,
        chunk_overlap_chars=40,
        chunk_hard_max_chars=200,
    )

    first = chunker.chunk_document(document(text))
    second = chunker.chunk_document(document(text))

    assert [item["chunk_id"] for item in first] == [item["chunk_id"] for item in second]


def test_chunk_text_matches_normalized_source() -> None:
    original_text = ("Birinci   cümle burada.\xa0İkinci cümle burada.\n\n") * 20

    chunker = SectionAwareChunker(
        chunk_target_chars=140,
        chunk_overlap_chars=30,
        chunk_hard_max_chars=180,
    )

    chunks = chunker.chunk_document(document(original_text))

    assert chunks

    for chunk in chunks:
        start = chunk["start_char"]
        end = chunk["end_char"]

        expected_text = original_text[start:end].strip()

        assert chunk["text"] == expected_text
