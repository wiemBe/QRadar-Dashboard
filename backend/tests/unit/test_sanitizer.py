"""Output sanitization — stored-XSS defence for QRadar-sourced text."""

from __future__ import annotations

from app.security.sanitizer import sanitize_mapping, sanitize_text


def test_script_tag_is_stripped() -> None:
    dirty = "<script>alert('xss')</script>login failure"
    clean = sanitize_text(dirty)
    assert clean is not None
    assert "<script>" not in clean
    assert "alert('xss')" in clean  # text preserved, tag removed
    assert "login failure" in clean


def test_img_onerror_is_neutralised() -> None:
    clean = sanitize_text('<img src=x onerror="steal()">')
    assert clean is not None
    assert "onerror" not in clean
    assert "<img" not in clean


def test_none_passes_through() -> None:
    assert sanitize_text(None) is None


def test_control_characters_removed() -> None:
    clean = sanitize_text("bad\x00value\x07here")
    assert clean is not None
    # The security property is that no raw control characters survive, not the
    # exact substitution (bleach may map some to a placeholder).
    assert "\x00" not in clean
    assert "\x07" not in clean
    assert all(ch >= " " or ch in "\t\n" for ch in clean)
    assert clean.startswith("bad") and clean.endswith("here")


def test_newlines_and_tabs_preserved() -> None:
    assert sanitize_text("line1\nline2\tend") == "line1\nline2\tend"


def test_mapping_is_recursively_sanitized() -> None:
    data = {
        "description": "<b>offense</b>",
        "nested": {"user": "<script>x</script>admin"},
        "addresses": ["<i>10.0.0.1</i>", "10.0.0.2"],
        "count": 42,
    }
    out = sanitize_mapping(data)
    assert "<b>" not in out["description"]
    assert "<script>" not in out["nested"]["user"]
    assert out["addresses"][0] == "10.0.0.1"
    assert out["count"] == 42  # non-strings untouched
