"""Owner-only care of the glossary (D-062): add, change and delete terms and misspellings, accept or turn
down suggestions. Kept apart from glossary_service, like people_admin: only the web UI imports this module.
"""
import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import GlossaryAlias, GlossarySuggestion, GlossaryTerm
from glossary_service import HINT_MAX, GlossaryError, clean, mapping

log = logging.getLogger(__name__)
ALIASES_PER_TERM = 40


def _term(session: Session, term_id: int) -> GlossaryTerm:
    found = session.get(GlossaryTerm, term_id)
    if found is None:
        raise GlossaryError("unknown term")
    return found


def _by_spelling(session: Session, canonical: str):
    return session.scalar(select(GlossaryTerm).where(func.lower(GlossaryTerm.canonical) == canonical.lower()))


def add_term(session: Session, canonical: str, hint: str = "", aliases: str = "") -> GlossaryTerm:
    """A new term; `aliases` is a comma-separated list of misspellings. An existing spelling gets the aliases."""
    canonical = clean(canonical, "spelling")
    found = _by_spelling(session, canonical)
    if found is None:
        found = GlossaryTerm(canonical=canonical, hint=clean(hint, "hint", HINT_MAX, required=False))
        session.add(found)
        session.flush()
    elif hint.strip():
        found.hint = clean(hint, "hint", HINT_MAX, required=False)
    for alias in [a for a in aliases.split(",") if a.strip()]:
        _add_alias(session, found, alias)
    session.commit()
    log.info("Glossary term saved id=%s", found.id)
    return found


def _add_alias(session: Session, term: GlossaryTerm, alias: str) -> bool:
    alias = clean(alias, "misspelling")
    if alias.lower() == term.canonical.lower():
        return False
    own = session.scalars(select(GlossaryAlias).where(GlossaryAlias.term_id == term.id)).all()
    if any(a.alias.lower() == alias.lower() for a in own):
        return False
    if len(own) >= ALIASES_PER_TERM:
        raise GlossaryError(f"at most {ALIASES_PER_TERM} misspellings per term")
    session.add(GlossaryAlias(term_id=term.id, alias=alias))
    return True


def add_alias(session: Session, term_id: int, alias: str) -> bool:
    added = _add_alias(session, _term(session, term_id), alias)
    session.commit()
    return added


def remove_alias(session: Session, term_id: int, alias: str) -> None:
    for row in session.scalars(select(GlossaryAlias).where(GlossaryAlias.term_id == term_id)).all():
        if row.alias.lower() == " ".join(alias.split()).lower():
            session.delete(row)
    session.commit()


def update_term(session: Session, term_id: int, canonical: str, hint: str) -> None:
    term = _term(session, term_id)
    canonical = clean(canonical, "spelling")
    other = _by_spelling(session, canonical)
    if other is not None and other.id != term.id:
        raise GlossaryError(f"'{canonical}' is already a term")
    term.canonical, term.hint = canonical, clean(hint, "hint", HINT_MAX, required=False)
    session.commit()


def delete_term(session: Session, term_id: int) -> None:
    session.delete(_term(session, term_id))
    session.commit()
    log.info("Glossary term deleted id=%s", term_id)


def open_suggestions(session: Session) -> list:
    return session.scalars(select(GlossarySuggestion).where(GlossarySuggestion.status == "open")
                           .order_by(GlossarySuggestion.id)).all()


def accept(session: Session, suggestion_id: int) -> GlossaryTerm:
    """The right spelling becomes a term (or the existing one), the wrong one its misspelling."""
    found = session.get(GlossarySuggestion, suggestion_id)
    if found is None or found.status != "open":
        raise GlossaryError("unknown suggestion")
    term = _by_spelling(session, found.right)
    if term is None:
        term = GlossaryTerm(canonical=found.right, hint=found.hint)
        session.add(term)
        session.flush()
    elif found.hint and not term.hint:
        term.hint = found.hint
    _add_alias(session, term, found.wrong)
    session.delete(found)
    session.commit()
    return term


def reject(session: Session, suggestion_id: int) -> None:
    """Turned down; kept so the same pair is not suggested again."""
    found = session.get(GlossarySuggestion, suggestion_id)
    if found is None or found.status != "open":
        raise GlossaryError("unknown suggestion")
    found.status = "rejected"
    session.commit()


def ambiguous_spellings(session: Session) -> dict:
    """Misspellings that stand for several terms, for showing them on the admin page."""
    return {key: value for key, value in mapping(session).items() if len(value) > 1}
