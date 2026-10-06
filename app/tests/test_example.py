"""
Example tests — replace with your own.
"""
import database
from strings import get_text


class TestDatabase:
    def test_create_item(self, db_session):
        item = database.ExampleItem(name="Test", description="A test item")
        db_session.add(item)
        db_session.commit()

        result = db_session.query(database.ExampleItem).first()
        assert result.name == "Test"
        assert result.description == "A test item"

    def test_item_without_description(self, db_session):
        item = database.ExampleItem(name="Minimal")
        db_session.add(item)
        db_session.commit()

        result = db_session.query(database.ExampleItem).first()
        assert result.name == "Minimal"
        assert result.description is None


class TestStrings:
    def test_get_text_de(self):
        assert "Willkommen" in get_text("welcome", "de", name="Max")

    def test_get_text_en(self):
        assert "Welcome" in get_text("welcome", "en", name="Max")

    def test_get_text_fallback(self):
        result = get_text("nonexistent_key", "de")
        assert result == "nonexistent_key"
