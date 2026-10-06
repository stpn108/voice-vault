"""Optional e-mail notification via SMTP. Without SMTP settings it does nothing."""
import logging
import smtplib
from email.message import EmailMessage

from config import Config

log = logging.getLogger(__name__)


def send_mail(cfg: Config, subject: str, body: str) -> bool:
    """Send a plain-text mail. Returns False when SMTP is not configured or sending fails."""
    if not (cfg.smtp_host and cfg.mail_to and cfg.mail_from):
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, cfg.mail_from, cfg.mail_to
    msg.set_content(body)
    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as smtp:
            smtp.starttls()
            if cfg.smtp_user:
                smtp.login(cfg.smtp_user, cfg.smtp_password)
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        log.error("Mail not sent subject=%r reason=%s", subject, exc)
        return False
    return True
