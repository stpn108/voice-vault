"""Glossary of spellings (D-062): the right spelling of names and terms and their known misspellings.

Reachable from MCP, so this module only reads and adds; changing and deleting entries is in glossary_admin.
Transcripts are never changed: the glossary is applied to what Claude writes.
"""
import difflib
import logging
import re
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import GlossaryAlias, GlossarySuggestion, GlossaryTerm

log = logging.getLogger(__name__)

TERM_MAX, HINT_MAX = 120, 300
SIMILARITY_MIN = 0.5  # a replaced word this similar to its replacement is probably a spelling fix ...
SIMILARITY_OTHER_START = 0.75  # ... or this similar if it starts with another letter ("Montag" -> "Dienstag" is not)
RUN_MAX = 3  # words on either side of a replacement that can still be a spelling fix
OPEN_SUGGESTIONS_MAX = 300
WORD = re.compile(r"[\w][\w+.'\-]*[\w]|[\w]")


class GlossaryError(Exception):
    pass


def clean(value: str, name: str = "text", limit: int = TERM_MAX, required: bool = True) -> str:
    if not isinstance(value, str):
        raise GlossaryError(f"{name} must be text")
    value = " ".join(value.split())
    if required and len(value) < 2:
        raise GlossaryError(f"{name} must have at least 2 characters")
    if len(value) > limit:
        raise GlossaryError(f"{name} must have at most {limit} characters")
    return value


def terms(session: Session) -> list:
    """(term, [aliases]) sorted by spelling."""
    found = session.scalars(select(GlossaryTerm).order_by(func.lower(GlossaryTerm.canonical))).all()
    aliases = {}
    for alias in session.scalars(select(GlossaryAlias).order_by(GlossaryAlias.alias)):
        aliases.setdefault(alias.term_id, []).append(alias.alias)
    return [(term, aliases.get(term.id, [])) for term in found]


def mapping(session: Session) -> dict:
    """lower-case spelling -> right spellings it stands for (one: unambiguous, several: ambiguous)."""
    result = {}
    for term, aliases in terms(session):
        for spelling in [term.canonical] + aliases:
            result.setdefault(spelling.lower(), [])
            if term.canonical not in result[spelling.lower()]:
                result[spelling.lower()].append(term.canonical)
    return {key: sorted(value) for key, value in result.items()}


def _pattern(known: dict) -> Optional[re.Pattern]:
    if not known:
        return None
    # Longest first, so a full name ("Stefan Zimmermann") wins over a part of it ("Stefan").
    keys = sorted(known, key=len, reverse=True)
    return re.compile(r"(?<!\w)(" + "|".join(re.escape(k) for k in keys) + r")(?!\w)", re.IGNORECASE)


class Fixer:
    """Applies one snapshot of the glossary to several texts and collects what it did."""

    def __init__(self, session: Session):
        self.known = mapping(session)
        self.pattern = _pattern(self.known)
        self.replaced = {}  # found -> right
        self.ambiguous = {}  # found -> candidates

    def fix(self, text: str) -> str:
        if not self.pattern or not isinstance(text, str) or not text:
            return text

        def swap(match):
            found = match.group(0)
            targets = self.known[found.lower()]
            if len(targets) > 1:
                self.ambiguous[found] = targets
                return found
            if found != targets[0]:
                self.replaced[found] = targets[0]
            return targets[0]
        return self.pattern.sub(swap, text)

    def report(self) -> str:
        parts = []
        if self.replaced:
            parts.append("Spelling fixed from the owner's glossary: "
                         + ", ".join(f"'{a}' -> '{b}'" for a, b in sorted(self.replaced.items())) + ".")
        if self.ambiguous:
            parts.append("Ambiguous, left as written: "
                         + "; ".join(f"'{a}' ({' | '.join(b)})" for a, b in sorted(self.ambiguous.items()))
                         + ". Write the full name only if the recording makes clear who is meant; otherwise leave it.")
        return " ".join(parts)


def ambiguous_words(known: dict, text: str) -> dict:
    """Words in `text` that stand for several terms -> candidates (for marking them in the UI)."""
    pattern = _pattern(known)
    found = {}
    for match in pattern.finditer(text or "") if pattern else []:
        targets = known[match.group(0).lower()]
        if len(targets) > 1:
            found[match.group(0)] = targets
    return found


def resolve(session: Session, text: str, word: str, choice: str) -> str:
    """Replace the ambiguous `word` (whole word, any case) by `choice`; longer known names stay untouched."""
    known = mapping(session)
    pattern = _pattern(known)
    if not pattern:
        return text
    return pattern.sub(lambda m: choice if m.group(0).lower() == word.lower() else m.group(0), text)


def variants(session: Session, query: str) -> list:
    """All spellings of the term(s) `query` is a spelling of; just [query] if it is none."""
    wanted = " ".join(query.split()).lower()
    targets = mapping(session).get(wanted)
    if not targets:
        return [query]
    spellings = {}
    for term, aliases in terms(session):
        if term.canonical in targets:
            spellings.update({s.lower(): s for s in [term.canonical] + aliases})
    return sorted(spellings.values(), key=str.lower)


def suggest(session: Session, wrong: str, right: str, *, source: str, hint: str = "") -> bool:
    """Queue a correction for the owner. False if it is known, already queued, turned down or no correction."""
    wrong, right = clean(wrong, "wrong"), clean(right, "right")
    hint = clean(hint, "hint", HINT_MAX, required=False)
    if wrong.lower() == right.lower():
        return False
    if right in mapping(session).get(wrong.lower(), []):
        return False
    known = session.scalar(select(GlossarySuggestion.id).where(func.lower(GlossarySuggestion.wrong) == wrong.lower(),
                                                               func.lower(GlossarySuggestion.right) == right.lower()))
    if known:
        return False
    if session.scalar(select(func.count()).select_from(GlossarySuggestion)
                      .where(GlossarySuggestion.status == "open")) >= OPEN_SUGGESTIONS_MAX:
        raise GlossaryError("too many open suggestions; the owner has to go through them first")
    session.add(GlossarySuggestion(wrong=wrong, right=right, source=source[:40], hint=hint))
    session.commit()
    log.info("Glossary suggestion queued source=%s", source[:40])
    return True


def _looks_like_fix(wrong: str, right: str) -> bool:
    if wrong.lower() == right.lower() or any(c.isdigit() for c in wrong + right):
        return False
    ratio = difflib.SequenceMatcher(None, wrong.lower(), right.lower()).ratio()
    return ratio >= (SIMILARITY_MIN if wrong[0].lower() == right[0].lower() else SIMILARITY_OTHER_START)


def learn(session: Session, old: str, new: str, *, source: str) -> int:
    """Compare a text before and after the owner's edit; queue word replacements that look like spelling fixes."""
    if not old or not new or old == new:
        return 0
    before, after = WORD.findall(old), WORD.findall(new)
    queued = 0
    for op, a1, a2, b1, b2 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if op != "replace" or not (1 <= a2 - a1 <= RUN_MAX and 1 <= b2 - b1 <= RUN_MAX):
            continue
        wrong, right = " ".join(before[a1:a2]), " ".join(after[b1:b2])
        if len(wrong) < 2 or len(right) < 2 or len(wrong) > TERM_MAX or len(right) > TERM_MAX:
            continue
        if _looks_like_fix(wrong, right):
            try:
                queued += suggest(session, wrong, right, source=source)
            except GlossaryError:
                return queued
    return queued


def glossary_text(session: Session) -> str:
    """The glossary as Claude reads it."""
    rows = terms(session)
    if not rows:
        return "The glossary is empty."
    lines = []
    for term, aliases in rows:
        line = term.canonical
        if aliases:
            line += " | misspellings: " + ", ".join(aliases)
        if term.hint:
            line += " | " + term.hint
        lines.append(line)
    return "\n".join(lines)

