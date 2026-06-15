"""Outbound transactional email (SMTP) for platform notifications like team invites.

Distinct from `channels/email.py` (which is the per-project *agent* email channel, with
creds stored as project secrets). This sends from the platform's own configured relay
(`settings.smtp_*`). When SMTP is unconfigured it's a no-op that returns False, so callers
can fall back to surfacing a link the admin shares manually. SMTP runs in a worker thread
so it never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from forge.config import settings

log = logging.getLogger("forge.mailer")


def smtp_configured() -> bool:
    return bool(settings.smtp_host)


def _send_sync(msg: EmailMessage) -> None:
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
        if settings.smtp_use_tls:
            server.starttls()
        if settings.smtp_username and settings.smtp_password:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(msg)


async def send_email(*, to: str, subject: str, body: str, html: str | None = None) -> bool:
    """Send a plain-text (optionally multipart HTML) email. Returns True if actually sent,
    False when SMTP isn't configured (a no-op)."""
    if not smtp_configured():
        log.info("SMTP not configured; skipping email to %s (%r)", to, subject)
        return False
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    await asyncio.to_thread(_send_sync, msg)
    return True
