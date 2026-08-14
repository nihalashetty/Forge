r'''Every catalog write action must build a valid JSON body from realistic model output.

Three shipped connectors were broken in ways inspection missed, all the same shape - a template
that produced text the API could not parse, surfacing as an opaque HTTP 400:

  * a list interpolated with str() became Python repr (single quotes), so `[['a','b']]` was not
    JSON while `[[1, 2]]` was - the bug only appeared on string data;
  * Sheets' append wrapped an already-2-D `values` into a 3-D array;
  * a note body containing a newline terminated its JSON string early.

So this sweeps the whole catalog with deliberately awkward values - newlines, double quotes,
backslashes, multiple recipients, many rows - and asserts the body parses. It is a shape test,
not a mock of the vendor: what it guards is that Forge sends JSON at all.
'''

from __future__ import annotations

import pytest

from forge.connectors.catalog import list_manifests
from forge.connectors.manifest import RestBackend
from forge.tools.rest import _build_body

#: What a model plausibly produces, chosen to be hostile to naive string interpolation.
SAMPLE: dict = {
    "rows": [["a", "b"], ["c", "d"]],
    "to": ["a@example.com", "b@example.com"],
    "cc": ["c@example.com"],
    "bcc": [],
    "subject": 'Re: "urgent" plan',
    "body": 'Line one\nHe said "hi"\\done',
    "comment": "thanks!\nbye",
    "fields": {"Name": "Ada", "Count": 3},
    "query": 'acme "corp"',
    "limit": 10,
    "timestamp": "2026-08-14T10:00:00Z",
    "summary": "Sync",
    "description": "line one\nline two",
    "start": "2026-08-20T14:00:00+05:30",
    "end": "2026-08-20T15:00:00+05:30",
    "attendees": ["a@example.com", "b@example.com"],
    "timeMin": "2026-08-20T00:00:00Z",
    "timeMax": "2026-08-21T00:00:00Z",
    "calendars": ["primary"],
    "title": 'Bug: "crash" on save',
    "labels": ["bug", "p1"],
    "addLabelIds": ["INBOX"],
    "removeLabelIds": ["UNREAD"],
}


def _write_actions():
    for m in list_manifests():
        if not isinstance(m.backend, RestBackend):
            continue
        for a in m.backend.actions:
            body_fields = [f for f in a.request.get("fields", []) if f.get("in") == "body"]
            if body_fields or a.request.get("body_template"):
                yield pytest.param(m.slug, a, id=f"{m.slug}:{a.name}")


@pytest.mark.parametrize("slug,action", list(_write_actions()))
def test_write_action_builds_parseable_json(slug: str, action):
    body_fields = [f for f in action.request.get("fields", []) if f.get("in") == "body"]
    unknown = [f["path"] for f in body_fields if f["path"] not in SAMPLE]
    assert not unknown, (
        f"{slug}.{action.name}: no sample for {unknown} - add one so this action stays covered"
    )
    args = {f["path"]: SAMPLE[f["path"]] for f in body_fields}
    built = _build_body(action.request, action.request.get("fields", []), args, {})
    assert isinstance(built, (dict, list)), (
        f"{slug}.{action.name} produced text, not JSON - the API will reject it: {built!r}"
    )


def _action(slug: str, name: str):
    m = next(x for x in list_manifests() if x.slug == slug)
    return next(a for a in m.backend.actions if a.name == name)


def test_awkward_text_survives_the_round_trip_unmangled():
    """Parsing is necessary but not sufficient - the text must also arrive as written."""
    a = _action("hubspot", "hubspot_create_note")
    body = _build_body(a.request, a.request["fields"],
                       {"body": SAMPLE["body"], "timestamp": SAMPLE["timestamp"]}, {})
    assert body["properties"]["hs_note_body"] == SAMPLE["body"]


def test_outlook_send_takes_every_recipient_not_just_the_first():
    """toRecipients was a hardcoded single object, so "send this to alice and bob" was impossible."""
    a = _action("outlook", "outlook_send_message")
    body = _build_body(a.request, a.request["fields"],
                       {"to": SAMPLE["to"], "cc": SAMPLE["cc"], "bcc": [],
                        "subject": "s", "body": "b"}, {})
    msg = body["message"]
    assert [r["emailAddress"]["address"] for r in msg["toRecipients"]] == SAMPLE["to"]
    assert [r["emailAddress"]["address"] for r in msg["ccRecipients"]] == SAMPLE["cc"]
    assert msg["bccRecipients"] == []


def test_calendar_attendees_become_email_objects():
    a = _action("google-calendar", "gcal_create_event")
    body = _build_body(a.request, a.request["fields"],
                       {"summary": "s", "description": "", "start": SAMPLE["start"],
                        "end": SAMPLE["end"], "attendees": SAMPLE["attendees"]}, {})
    assert body["attendees"] == [{"email": e} for e in SAMPLE["attendees"]]
    assert body["start"] == {"dateTime": SAMPLE["start"]}
