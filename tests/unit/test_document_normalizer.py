from app.documents import normalize_text


def test_normalizer_removes_html_and_extra_spaces() -> None:
    source = """
    <p>İhale   ilanı</p>

    <div>Teknik&nbsp;şartlar</div>
    """

    result = normalize_text(source)

    assert "<p>" not in result
    assert "<div>" not in result
    assert "İhale ilanı" in result
    assert "Teknik şartlar" in result


def test_normalizer_handles_empty_values() -> None:
    assert normalize_text(None) == ""
    assert normalize_text("") == ""
    assert normalize_text("   ") == ""
