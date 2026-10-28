"""Glossary of spellings (D-062)."""
import inspect

import pytest
from sqlalchemy import select

import glossary_admin as admin
import glossary_service as gl
from database import GlossarySuggestion


@pytest.fixture
def terms(db_session):
    admin.add_term(db_session, "Glycania", "SOC-Dienstleister", "Glynkania, Glück+Kamia")
    admin.add_term(db_session, "Stefan Zimmermann", "Leitung IT", "Stefan")
    admin.add_term(db_session, "Stephan Simmang", "Vorstand", "Stefan, Stephan")
    return db_session


def test_d062_unambiguous_misspellings_are_fixed_whole_words_only(terms):
    fixer = gl.Fixer(terms)
    assert fixer.fix("Vertrag mit glynkania und Glück+Kamia prüfen, nicht Glynkanias") == \
        "Vertrag mit Glycania und Glycania prüfen, nicht Glynkanias"
    assert fixer.replaced == {"glynkania": "Glycania", "Glück+Kamia": "Glycania"}
    assert "Glycania" in fixer.report() and not fixer.ambiguous


def test_d062_ambiguous_names_stay_and_full_names_win(terms):
    fixer = gl.Fixer(terms)
    text = "Stefan fragen, Stefan Zimmermann informieren"
    assert fixer.fix(text) == text
    assert fixer.ambiguous == {"Stefan": ["Stefan Zimmermann", "Stephan Simmang"]}
    assert "Ambiguous" in fixer.report()


def test_d062_resolve_replaces_only_the_ambiguous_word(terms):
    text = "Stefan fragen, Stefan Zimmermann informieren"
    assert gl.resolve(terms, text, "stefan", "Stephan Simmang") == "Stephan Simmang fragen, Stefan Zimmermann informieren"


@pytest.mark.parametrize("old, new, expected", [
    ("Vertrag mit Glucania klären", "Vertrag mit Glycania klären", [("Glucania", "Glycania")]),
    ("Termin am Montag", "Termin am Dienstag", []),       # a different word, not a spelling fix
    ("Call mit wiz", "Call mit Wiz", []),                 # only the case changed
    ("Ticket 4711 anlegen", "Ticket 4712 anlegen", []),   # numbers
    ("Mit Stefan reden", "Mit Stephan reden", [("Stefan", "Stephan")]),
])
def test_d062_owner_corrections_become_suggestions(db_session, old, new, expected):
    gl.learn(db_session, old, new, source="task:1")
    assert [(s.wrong, s.right) for s in db_session.scalars(select(GlossarySuggestion))] == expected


def test_d062_suggestions_are_accepted_or_rejected_once(db_session):
    assert gl.suggest(db_session, "Glucania", "Glycania", source="claude", hint="SOC") is True
    assert gl.suggest(db_session, "glucania", "glycania", source="claude") is False
    suggestion = admin.open_suggestions(db_session)[0]
    term = admin.accept(db_session, suggestion.id)
    assert (term.canonical, term.hint) == ("Glycania", "SOC") and gl.mapping(db_session)["glucania"] == ["Glycania"]
    assert gl.suggest(db_session, "Glucania", "Glycania", source="claude") is False  # already known
    gl.suggest(db_session, "Wiss", "Wiz", source="claude")
    admin.reject(db_session, admin.open_suggestions(db_session)[0].id)
    assert gl.suggest(db_session, "Wiss", "Wiz", source="task:3") is False and admin.open_suggestions(db_session) == []


def test_d062_search_uses_every_spelling(terms):
    assert gl.variants(terms, "glycania") == ["Glycania", "Glynkania", "Glück+Kamia"]
    assert gl.variants(terms, "Budget") == ["Budget"]


def test_d062_admin_edits_and_deletes_terms(terms):
    term = next(t for t, _ in gl.terms(terms) if t.canonical == "Glycania")
    admin.add_alias(terms, term.id, "Glucania")
    admin.remove_alias(terms, term.id, "glynkania")
    admin.update_term(terms, term.id, "Glycania", "neuer Hinweis")
    assert next(a for t, a in gl.terms(terms) if t.id == term.id) == ["Glucania", "Glück+Kamia"]
    with pytest.raises(gl.GlossaryError, match="already a term"):
        admin.update_term(terms, term.id, "Stephan Simmang", "")
    admin.delete_term(terms, term.id)
    assert "glucania" not in gl.mapping(terms)


def test_d062_the_mcp_side_of_the_glossary_deletes_nothing():
    source = inspect.getsource(gl)
    assert "session.delete" not in source and "delete(" not in source
