"""Channel adapters (email/teams parse+build)."""

from __future__ import annotations

from forge.channels import email as email_ch
from forge.channels import teams as teams_ch

# --- email parsing ---


def test_email_parse_provider_dict():
    p = email_ch.parse_inbound({"from": "Jane <jane@acme.com>", "subject": "Help", "text": "  my order is late  "})
    assert p["from_addr"] == "jane@acme.com" and p["from_name"] == "Jane" and p["text"] == "my order is late"


def test_email_parse_raw_mime():
    raw = b"From: Bob <bob@x.com>\r\nSubject: Hi\r\nMessage-ID: <m1>\r\nContent-Type: text/plain\r\n\r\nHello body\r\n"
    p = email_ch.parse_inbound(raw)
    assert p["from_addr"] == "bob@x.com" and "Hello body" in p["text"] and p["message_id"] == "<m1>"


def test_email_reply_threads_subject():
    msg = email_ch.build_reply(to_addr="a@b.com", subject="Order", body="done", from_addr="bot@x.com", in_reply_to="<m1>")
    assert msg["Subject"] == "Re: Order" and msg["In-Reply-To"] == "<m1>" and msg["To"] == "a@b.com"


# --- teams ---


def test_teams_parse_and_reply():
    act = {"type": "message", "text": "hi there", "id": "a1", "serviceUrl": "https://smba.x/",
           "from": {"id": "u1", "name": "User"}, "recipient": {"id": "b1", "name": "Bot"},
           "conversation": {"id": "c1"}}
    p = teams_ch.parse_activity(act)
    assert p["text"] == "hi there" and p["conversation_id"] == "c1" and p["service_url"] == "https://smba.x/"
    reply = teams_ch.build_reply_activity(p, "hello back")
    assert reply["text"] == "hello back" and reply["conversation"]["id"] == "c1"
    assert reply["from"]["id"] == "b1" and reply["recipient"]["id"] == "u1" and reply["replyToId"] == "a1"
