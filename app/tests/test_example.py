"""Strings tests."""
from strings import get_text


class TestStrings:
    def test_get_text_de(self):
        assert "Willkommen" in get_text("welcome", "de", name="Max")

    def test_get_text_en(self):
        assert "Welcome" in get_text("welcome", "en", name="Max")

    def test_get_text_fallback(self):
        result = get_text("nonexistent_key", "de")
        assert result == "nonexistent_key"
