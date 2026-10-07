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
        "de": "Zusammenfassung, Transkript und Titel werden hier endgültig gelöscht. Das lässt sich nicht rückgängig machen.",
        "en": "Summary, transcript and title are deleted here for good. This cannot be undone.",
    },
    "ui_discard_plaud_on": {
        "de": "Bei Plaud wird die Aufnahme ebenfalls gelöscht: zuerst in den Papierkorb (frühestens {min_age_hours} Stunden nach der Aufnahme), nach {wait_hours} Stunden endgültig.",
        "en": "The recording is deleted at Plaud as well: first into the trash (no earlier than {min_age_hours} hours after the recording), permanently after {wait_hours} hours.",
    },
    "ui_discard_plaud_off": {
        "de": "Das Löschen bei Plaud ist derzeit ausgeschaltet. Die Aufnahme bleibt dort liegen, wird hier aber nicht erneut importiert.",
        "en": "Deleting at Plaud is currently switched off. The recording stays there but is not imported again here.",
    },
    "ui_discard_confirm": {"de": "Ja, endgültig verwerfen", "en": "Yes, discard for good"},
    "ui_cancel": {"de": "Abbrechen", "en": "Cancel"},
    "ui_cleanup": {"de": "Aufräumen", "en": "Clean up"},
    "ui_cleanup_text": {"de": "Aufnahmen verwerfen, die älter sind als diese Zahl von Tagen:", "en": "Discard recordings older than this many days:"},
    "ui_cleanup_show": {"de": "Anzeigen", "en": "Show"},
    "ui_cleanup_count": {"de": "Betroffen: {count} Aufnahme(n), älter als {days} Tage.", "en": "Affected: {count} recording(s), older than {days} days."},
    "ui_cleanup_changed": {"de": "Die Anzahl hat sich geändert. Es wurde nichts verworfen. Bitte erneut prüfen.", "en": "The count changed. Nothing was discarded. Please check again."},
    "oauth_title": {"de": "voice-vault: Zugriff freigeben", "en": "voice-vault: grant access"},
    "oauth_intro": {
        "de": "Claude möchte Deine Aufnahmen lesen (Zusammenfassungen und Transkripte) und Aufgaben, Themen und Tagesübersichten pflegen. Aufnahmen verändern oder löschen und Aufgaben löschen ist nicht möglich.",
        "en": "Claude wants to read your recordings (summaries and transcripts) and maintain tasks, topics and daily overviews. Changing or deleting recordings and deleting tasks is not possible.",
    },
    "oauth_redirect_note": {"de": "Nach der Freigabe geht es zurück zu {host}.", "en": "After you approve, you are sent back to {host}."},
    "oauth_password": {"de": "Freigabe-Passwort", "en": "Approval password"},
    "oauth_allow": {"de": "Freigeben", "en": "Approve"},
    "oauth_deny": {"de": "Ablehnen", "en": "Deny"},
    "oauth_wrong_password": {"de": "Das Passwort stimmt nicht.", "en": "The password is wrong."},
    "oauth_bad_request": {"de": "Die Anfrage ist ungültig. Starte die Verbindung in Claude neu.", "en": "The request is invalid. Start the connection in Claude again."},
    "oauth_expired": {"de": "Die Anmeldung ist abgelaufen. Starte die Verbindung in Claude neu.", "en": "The sign-in expired. Start the connection in Claude again."},
    "ui_nav_recordings": {"de": "Aufnahmen", "en": "Recordings"},
    "ui_nav_todos": {"de": "Aufgaben", "en": "Tasks"},
    "ui_nav_topics": {"de": "Themen", "en": "Topics"},
    "ui_nav_digests": {"de": "Tagesübersicht", "en": "Daily overview"},
    "ui_topic": {"de": "Thema", "en": "Topic"},
    "ui_todo_new": {"de": "Neue Aufgabe", "en": "New task"},
    "ui_todo_add": {"de": "Hinzufügen", "en": "Add"},
    "ui_col_prio": {"de": "Priorität", "en": "Priority"},
    "ui_col_task": {"de": "Aufgabe", "en": "Task"},
    "ui_col_due": {"de": "Fällig", "en": "Due"},
    "ui_col_age": {"de": "Alter", "en": "Age"},
    "ui_col_open": {"de": "Offen", "en": "Open"},
    "ui_col_notes": {"de": "Notizen", "en": "Notes"},
    "ui_col_activity": {"de": "Letzte Aktivität", "en": "Last activity"},
    "ui_days": {"de": "Tage", "en": "days"},
    "ui_todo_check": {"de": "Erledigt", "en": "Done"},
    "ui_todo_drop": {"de": "Nicht mehr relevant", "en": "No longer relevant"},
    "ui_todo_reopen": {"de": "Wieder öffnen", "en": "Reopen"},
    "ui_todo_empty": {"de": "Keine Aufgaben.", "en": "No tasks."},
    "ui_todo_truncated": {"de": "Es gibt mehr Aufgaben als angezeigt. Nach Thema filtern.", "en": "More tasks exist than shown. Filter by topic."},
    "ui_back_todos": {"de": "Zurück zu den Aufgaben", "en": "Back to tasks"},
    "ui_back_topics": {"de": "Zurück zu den Themen", "en": "Back to topics"},
    "ui_created_by": {"de": "angelegt von", "en": "created by"},
    "ui_recording": {"de": "Aufnahme", "en": "Recording"},
    "ui_save": {"de": "Speichern", "en": "Save"},
    "ui_history": {"de": "Verlauf", "en": "History"},
    "ui_undo": {"de": "Rückgängig", "en": "Undo"},
    "ui_open_tasks": {"de": "Offene Aufgaben", "en": "Open tasks"},
    "ui_timeline": {"de": "Verlauf des Themas", "en": "Topic timeline"},
    "ui_note": {"de": "Notiz", "en": "Note"},
    "ui_topics_empty": {"de": "Noch keine Themen.", "en": "No topics yet."},
    "ui_digests_empty": {"de": "Noch keine Tagesübersicht.", "en": "No daily overview yet."},
    "todo_status_open": {"de": "Offen", "en": "Open"},
    "todo_status_done": {"de": "Erledigt", "en": "Done"},
    "todo_status_dropped": {"de": "Verworfen", "en": "Dropped"},
    "todo_prio_1": {"de": "Dringend", "en": "Urgent"},
    "todo_prio_2": {"de": "Hoch", "en": "High"},
    "todo_prio_3": {"de": "Normal", "en": "Normal"},
    "todo_prio_4": {"de": "Niedrig", "en": "Low"},
    "todo_event_created": {"de": "angelegt", "en": "created"},
    "todo_event_updated": {"de": "geändert", "en": "changed"},
    "todo_event_completed": {"de": "erledigt", "en": "completed"},
    "todo_event_reopened": {"de": "wieder geöffnet", "en": "reopened"},
    "todo_event_dropped": {"de": "verworfen", "en": "dropped"},
    "todo_event_undone": {"de": "rückgängig gemacht", "en": "undone"},
    "ui_topic_excluded": {"de": "ausgeschlossen", "en": "excluded"},
    "ui_topic_exclude": {"de": "Ausschließen", "en": "Exclude"},
    "ui_topic_allow": {"de": "Wieder zulassen", "en": "Allow again"},
    "ui_topic_exclude_hint": {"de": "Ausgeschlossene Themen bekommen keine neuen Aufgaben und Notizen. Ihre Aufgaben erscheinen nicht in der Liste. Claude liest die Aufnahmen trotzdem.", "en": "Excluded topics get no new tasks or notes. Their tasks are hidden from the list. Claude still reads the recordings."},
    "ui_topic_exclude_name": {"de": "Thema ausschließen (Name)", "en": "Exclude a topic (name)"},
    "ui_filter_clear": {"de": "Filter aufheben", "en": "Clear filter"},
    "ui_due": {"de": "Fällig", "en": "Due"},
    "ui_overdue": {"de": "Überfällig", "en": "Overdue"},
    "ui_todo_drop_short": {"de": "Nicht relevant", "en": "Not relevant"},
    "ui_detail": {"de": "Details", "en": "Details"},
    "ui_status": {"de": "Status", "en": "Status"},
    "ui_older": {"de": "Älter", "en": "Older"},
    "ui_newer": {"de": "Neuer", "en": "Newer"},
    "ui_day_recordings": {"de": "Aufnahmen dieses Tages", "en": "Recordings of this day"},
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
