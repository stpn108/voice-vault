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
