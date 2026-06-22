"""Email channel - inbound parsing and outbound (SMTP) replies.

Inbound supports two shapes:
- A raw RFC-822 message (bytes/str), e.g. from an IMAP poll.
- A provider inbound-parse payload (Mailgun/SendGrid/Postmark post form/JSON fields).

Outbound sends a threaded reply via SMTP (creds resolved from the channel's secret
refs). SMTP/IMAP run in worker threads so they don't block the event loop.
"""

from __future__ import annotations

import asyncio
import email
import smtplib
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any

from forge.secrets.store import SecretStore


def _thread_ref(references: str | None, in_reply_to: str | None, message_id: str | None) -> str | None:
    """A stable per-conversation key for threading replies (audit F6): the ROOT of the
    References chain (shared by every message in the thread), else the In-Reply-To parent,
    else this message's own id (a brand-new thread)."""
    if references:
        first = references.split()[0].strip() if references.split() else ""
        if first:
            return first
    return (in_reply_to or "").strip() or (message_id or "").strip() or None


def parse_inbound(payload: Any) -> dict:
    """Normalize an inbound email (raw MIME or provider dict) to a common shape."""
    if isinstance(payload, dict):
        # Provider inbound-parse fields vary; accept the common ones.
        sender = payload.get("from") or payload.get("sender") or payload.get("From", "")
        subject = payload.get("subject") or payload.get("Subject", "")
        text = payload.get("text") or payload.get("body-plain") or payload.get("stripped-text") or payload.get("TextBody") or ""
        msg_id = payload.get("message-id") or payload.get("Message-Id") or payload.get("MessageID")
        references = payload.get("References") or payload.get("references")
        in_reply_to = payload.get("In-Reply-To") or payload.get("in-reply-to")
        return {"from_addr": parseaddr(sender)[1] or sender, "from_name": parseaddr(sender)[0],
                "subject": subject, "text": (text or "").strip(), "message_id": msg_id,
                "thread_ref": _thread_ref(references, in_reply_to, msg_id)}

    raw = payload.encode() if isinstance(payload, str) else payload
    msg = email.message_from_bytes(raw)
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                # decode=True returns None for a part with no decodable payload - guard it
                # so a malformed MIME message can't 500 the public inbound webhook (audit F-low).
                raw_payload = part.get_payload(decode=True) or b""
                body = raw_payload.decode(part.get_content_charset() or "utf-8", "replace")
                break
    else:
        body = (msg.get_payload(decode=True) or b"").decode(msg.get_content_charset() or "utf-8", "replace")
    name, addr = parseaddr(msg.get("From", ""))
    return {"from_addr": addr, "from_name": name, "subject": msg.get("Subject", ""),
            "text": body.strip(), "message_id": msg.get("Message-ID"),
            "thread_ref": _thread_ref(msg.get("References"), msg.get("In-Reply-To"), msg.get("Message-ID"))}


def build_input_text(parsed: dict, include_subject: bool = True) -> str:
    if include_subject and parsed.get("subject"):
        return f"Subject: {parsed['subject']}\n\n{parsed.get('text', '')}"
    return parsed.get("text", "")


def build_reply(*, to_addr: str, subject: str, body: str, from_addr: str, in_reply_to: str | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}" if subject else "Re:"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    return msg


def _send_sync(host: str, port: int, username: str | None, password: str | None, use_tls: bool, msg: EmailMessage) -> None:
    with smtplib.SMTP(host, port, timeout=30) as server:
        if use_tls:
            server.starttls()
        if username and password:
            server.login(username, password)
        server.send_message(msg)


async def send_reply(channel, parsed: dict, answer: str) -> None:
    """Send the workflow's answer as a threaded SMTP reply. No-op if SMTP isn't configured."""
    cfg = channel.config or {}
    smtp = cfg.get("smtp") or {}
    host = smtp.get("host")
    if not host or not parsed.get("from_addr"):
        return
    secrets = SecretStore()
    username = smtp.get("username")
    password = None
    if smtp.get("password_ref"):
        try:
            password = await secrets.read_ref(tenant_id=channel.tenant_id, project_id=channel.project_id, ref=smtp["password_ref"])
        except Exception:  # noqa: BLE001
            password = None
    from_addr = smtp.get("from") or username or "bot@forge.local"
    msg = build_reply(to_addr=parsed["from_addr"], subject=parsed.get("subject", ""), body=answer,
                      from_addr=from_addr, in_reply_to=parsed.get("message_id"))
    await asyncio.to_thread(_send_sync, host, int(smtp.get("port", 587)), username,
                            str(password) if password else None, bool(smtp.get("use_tls", True)), msg)
