"""
Internationalisation (i18n) — all user-facing strings.

Usage:
    from strings import get_text
    msg = get_text("welcome", lang, name=user_name)

Add new keys as needed. Every key must have at least "de" and "en".
"""

TEXTS = {
    "welcome": {
        "de": "Willkommen, {name}!",
        "en": "Welcome, {name}!",
    },
    "error_generic": {
        "de": "Ein Fehler ist aufgetreten. Bitte versuche es erneut.",
        "en": "An error occurred. Please try again.",
    },
    "ui_title": {"de": "voice-vault: Aufnahmen", "en": "voice-vault: recordings"},
    "ui_search": {"de": "Suchen", "en": "Search"},
    "ui_search_placeholder": {"de": "Titel, Zusammenfassung oder Transkript", "en": "Title, summary or transcript"},
    "ui_empty": {"de": "Keine Aufnahmen gefunden.", "en": "No recordings found."},
    "ui_next": {"de": "Weiter", "en": "Next"},
    "ui_back": {"de": "Zurück zur Liste", "en": "Back to the list"},
    "ui_col_title": {"de": "Titel", "en": "Title"},
    "ui_col_date": {"de": "Datum", "en": "Date"},
    "ui_col_duration": {"de": "Dauer", "en": "Duration"},
    "ui_col_state": {"de": "Zustand", "en": "State"},
    "ui_untitled": {"de": "(ohne Titel)", "en": "(untitled)"},
    "state_waiting": {"de": "wartend", "en": "waiting"},
    "state_imported": {"de": "importiert", "en": "imported"},
    "state_verified": {"de": "verifiziert", "en": "verified"},
    "state_trashed": {"de": "im Plaud-Papierkorb", "en": "in the Plaud trash"},
    "state_deleted": {"de": "bei Plaud gelöscht", "en": "deleted at Plaud"},
    "ui_summary": {"de": "Zusammenfassung", "en": "Summary"},
    "ui_transcript": {"de": "Transkript", "en": "Transcript"},
    "ui_discard": {"de": "Verwerfen", "en": "Discard"},
    "ui_discard_title": {"de": "Aufnahme verwerfen?", "en": "Discard this recording?"},
    "ui_discard_text": {
        "de": "Zusammenfassung, Transkript und Titel werden hier endgültig gelöscht. Der Eintrag bei Plaud bleibt unverändert und wird nicht erneut importiert.",
        "en": "Summary, transcript and title are deleted here for good. The entry at Plaud stays as it is and is not imported again.",
    },
    "ui_discard_confirm": {"de": "Ja, endgültig verwerfen", "en": "Yes, discard for good"},
    "ui_cancel": {"de": "Abbrechen", "en": "Cancel"},
    "ui_cleanup": {"de": "Aufräumen", "en": "Clean up"},
    "ui_cleanup_text": {"de": "Aufnahmen verwerfen, die älter sind als diese Zahl von Tagen:", "en": "Discard recordings older than this many days:"},
    "ui_cleanup_show": {"de": "Anzeigen", "en": "Show"},
    "ui_cleanup_count": {"de": "Betroffen: {count} Aufnahme(n), älter als {days} Tage.", "en": "Affected: {count} recording(s), older than {days} days."},
    "ui_cleanup_changed": {"de": "Die Anzahl hat sich geändert. Es wurde nichts verworfen. Bitte erneut prüfen.", "en": "The count changed. Nothing was discarded. Please check again."},
    "ui_cleanup_confirm": {"de": "{count} Aufnahme(n) endgültig verwerfen", "en": "Discard {count} recording(s) for good"},
}


def get_text(key: str, lang: str = "de", **kwargs) -> str:
    """
    Returns the localised string for *key* in *lang*.
    Falls back to 'en', then returns the key itself.
    """
    entry = TEXTS.get(key)
    if not entry:
        return key
    text = entry.get(lang, entry.get("en", key))
    if kwargs:
        try:
            return text.format(**kwargs)
        except KeyError:
            return text
    return text
