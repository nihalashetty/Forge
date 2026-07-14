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
import logging
import re
import smtplib
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr
from typing import Any

from forge.channels.retry import retry_send
from forge.secrets.store import SecretStore

log = logging.getLogger("forge.channels.email")

_TAG_RE = re.compile(r"(?s)<(script|style).*?>.*?</\1>")
_BR_RE = re.compile(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>")
_ANY_TAG_RE = re.compile(r"(?s)<[^>]+>")


def _html_to_text(html: str) -> str:
    """Best-effort HTML -> plain text WITHOUT a new dependency: drop script/style, turn common
    block-enders into newlines, strip remaining tags, and unescape a few entities. Good enough to
    feed an HTML-only email's body to the workflow instead of an empty string (audit F)."""
    if not html:
        return ""
    import html as _html

    text = _TAG_RE.sub(" ", html)
    text = _BR_RE.sub("\n", text)
    text = _ANY_TAG_RE.sub("", text)
    text = _html.unescape(text)
    # collapse runs of whitespace but keep line breaks
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _thread_ref(references: str | None, in_reply_to: str | None, message_id: str | None) -> str | None:
    """A stable per-conversation key for threading replies (audit F6): the ROOT of the
    References chain (shared by every message in the thread), else the In-Reply-To parent,
    else this message's own id (a brand-new thread)."""
    if references:
        first = references.split()[0].strip() if references.split() else ""
        if first:
            return first
    return (in_reply_to or "").strip() or (message_id or "").strip() or None


def _merge_references(references: str | None, parent_id: str | None) -> str:
    """The outbound `References` header: the inbound References chain with the parent's
    Message-ID appended (RFC 5322). Clients thread on the full References chain, not just
    In-Reply-To, so preserving the whole chain keeps deep threads together (audit G)."""
    ids: list[str] = []
    for tok in (references or "").split():
        tok = tok.strip()
        if tok and tok not in ids:
            ids.append(tok)
    if parent_id:
        pid = parent_id.strip()
        if pid and pid not in ids:
            ids.append(pid)
    return " ".join(ids)


def parse_inbound(payload: Any) -> dict:
    """Normalize an inbound email (raw MIME or provider dict) to a common shape."""
    if isinstance(payload, dict):
        # Provider inbound-parse fields vary; accept the common ones.
        sender = payload.get("from") or payload.get("sender") or payload.get("From", "")
        subject = payload.get("subject") or payload.get("Subject", "")
        text = payload.get("text") or payload.get("body-plain") or payload.get("stripped-text") or payload.get("TextBody") or ""
        if not (text or "").strip():
            # HTML-only email: fall back to the provider's HTML field, stripped to text, so the
            # workflow gets the body instead of an empty message (audit F).
            html = payload.get("html") or payload.get("body-html") or payload.get("HtmlBody") or payload.get("stripped-html") or ""
            text = _html_to_text(html)
        msg_id = payload.get("message-id") or payload.get("Message-Id") or payload.get("MessageID")
        references = payload.get("References") or payload.get("references")
        in_reply_to = payload.get("In-Reply-To") or payload.get("in-reply-to")
        return {"from_addr": parseaddr(sender)[1] or sender, "from_name": parseaddr(sender)[0],
                "subject": subject, "text": (text or "").strip(), "message_id": msg_id,
                "references": references,
                "thread_ref": _thread_ref(references, in_reply_to, msg_id)}

    raw = payload.encode() if isinstance(payload, str) else payload
    msg = email.message_from_bytes(raw)
    body = ""
    html_body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if "attachment" in str(part.get("Content-Disposition", "")):
                continue
            # decode=True returns None for a part with no decodable payload - guard it so a
            # malformed MIME message can't 500 the public inbound webhook (audit F-low).
            if ctype == "text/plain" and not body:
                raw_payload = part.get_payload(decode=True) or b""
                body = raw_payload.decode(part.get_content_charset() or "utf-8", "replace")
            elif ctype == "text/html" and not html_body:
                raw_payload = part.get_payload(decode=True) or b""
                html_body = raw_payload.decode(part.get_content_charset() or "utf-8", "replace")
    else:
        payload_bytes = msg.get_payload(decode=True) or b""
        decoded = payload_bytes.decode(msg.get_content_charset() or "utf-8", "replace")
        if msg.get_content_type() == "text/html":
            html_body = decoded
        else:
            body = decoded
    # No text/plain part -> fall back to the HTML part, stripped to text (audit F).
    if not body.strip() and html_body:
        body = _html_to_text(html_body)
    name, addr = parseaddr(msg.get("From", ""))
    return {"from_addr": addr, "from_name": name, "subject": msg.get("Subject", ""),
            "text": body.strip(), "message_id": msg.get("Message-ID"),
            "references": msg.get("References"),
            "thread_ref": _thread_ref(msg.get("References"), msg.get("In-Reply-To"), msg.get("Message-ID"))}


def build_input_text(parsed: dict, include_subject: bool = True) -> str:
    if include_subject and parsed.get("subject"):
        return f"Subject: {parsed['subject']}\n\n{parsed.get('text', '')}"
    return parsed.get("text", "")


def build_reply(*, to_addr: str, subject: str, body: str, from_addr: str,
                in_reply_to: str | None = None, references: str | None = None,
                message_id: str | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}" if subject else "Re:"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    # References = the inbound chain + the parent id, so deep threads stay grouped (audit G).
    refs = _merge_references(references, in_reply_to)
    if refs:
        msg["References"] = refs
    # Set an explicit Message-ID so a downstream reply can reference THIS message and clients
    # can dedupe (a missing Message-ID makes some MTAs generate an unstable one) (audit G).
    try:
        msg["Message-ID"] = message_id or make_msgid()
    except Exception:  # noqa: BLE001 - make_msgid can fail on odd hostnames; header is optional
        pass
    msg.set_content(body)
    return msg


def _send_sync(host: str, port: int, username: str | None, password: str | None, use_tls: bool, msg: EmailMessage) -> None:
    with smtplib.SMTP(host, port, timeout=30) as server:
        if use_tls:
            server.starttls()
        if username and password:
            server.login(username, password)
        server.send_message(msg)


async def send_reply(channel, parsed: dict, answer: str) -> bool:
    """Send the workflow's answer as a threaded SMTP reply, with bounded retry + backoff.

    Returns True when an email was actually sent, False when SMTP isn't configured / there's
    nowhere to send (a no-op, NOT a failure). Raises after exhausting retries so the caller can
    record a real delivery status and avoid marking a handoff 'answered' on a failed send (E)."""
    cfg = channel.config or {}
    smtp = cfg.get("smtp") or {}
    host = smtp.get("host")
    if not host or not parsed.get("from_addr"):
        return False
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
                      from_addr=from_addr, in_reply_to=parsed.get("message_id"),
                      references=parsed.get("references"))
    port = int(smtp.get("port", 587))
    use_tls = bool(smtp.get("use_tls", True))
    pw = str(password) if password else None

    async def _attempt(_n: int) -> bool:
        await asyncio.to_thread(_send_sync, host, port, username, pw, use_tls, msg)
        return True

    await retry_send(_attempt, label=f"email->{parsed['from_addr']}")
    return True
