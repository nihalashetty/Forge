"""`extract`: pull an opaque id out of the URL a person actually pastes.

The failure this prevents: Google Sheets addresses a spreadsheet by an opaque key that lives
inside its link, and what a user says is "get testsheet" or "here's my sheet: <url>". A model
handed only a NAME will confidently pass the name as the id, and the 404 that comes back reads
like a permissions problem rather than "that argument was never knowable".

`extract` closes the URL half declaratively: paste the link, get the id. The name half is closed
by the field description telling the model to search Drive (or ask) instead of guessing.
"""

from __future__ import annotations

import httpx
import pytest

from forge.connectors.catalog import get_manifest
from forge.connectors.manifest import ManifestError, parse_manifest
from forge.tools.rest import execute_rest

SHEET_URL = "https://docs.google.com/spreadsheets/d/1AbC-dEf_23/edit#gid=0"


def _client(seen: dict) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"values": [["ok"]]})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _call(spreadsheet_id: str) -> str:
    """Run the real catalog action against a stubbed transport; return the URL it built."""
    m = get_manifest("google-sheets")
    action = next(a for a in m.backend.actions if a.name == "sheets_read_range")
    request = {**action.request, "url_template": m.backend.base_url + action.request["url_template"]}
    seen: dict = {}
    async with _client(seen) as client:
        await execute_rest(
            {"name": "sheets_read_range", "kind": "rest_api", "request": request},
            {"spreadsheet_id": spreadsheet_id, "range": "Sheet1!A1:D50"},
            tenant_id="t_x", project_id="p_x", client=client,
        )
    return seen["url"]


async def test_a_pasted_sheet_url_becomes_the_spreadsheet_id():
    assert "/spreadsheets/1AbC-dEf_23/values/" in await _call(SHEET_URL)


async def test_a_bare_id_passes_straight_through():
    """No match must leave the value alone - the common case is already an id."""
    assert "/spreadsheets/1AbC-dEf_23/values/" in await _call("1AbC-dEf_23")


async def test_a_name_is_left_alone_so_the_failure_stays_honest():
    """`extract` deliberately does NOT invent an id from a name. A wrong id that 404s is better
    than one that silently reads someone else's sheet, and the description tells the model to
    search Drive or ask for the link instead."""
    assert "/spreadsheets/testsheet/values/" in await _call("testsheet")


async def test_every_google_id_argument_accepts_a_pasted_link():
    """The regression guard: any opaque-id argument in the Google connectors must either be
    extractable from a URL or be discoverable from another action."""
    for slug, field in (("google-sheets", "spreadsheet_id"), ("google-drive", "file_id")):
        for action in get_manifest(slug).backend.actions:
            for f in action.request.get("fields", []):
                if f["path"] == field:
                    assert f.get("extract"), f"{slug}.{action.name}: {field} can't take a link"
                    assert "name" in (f.get("description") or "").lower(), (
                        f"{slug}.{action.name}: {field} should tell the model a name is not an id"
                    )


def test_a_broken_extract_regex_is_rejected_at_install_not_at_call_time():
    bad = {
        "format": "forge.connector/1", "slug": "bad-extract", "name": "Bad",
        "backend": {"type": "rest", "base_url": "https://api.test", "actions": [{
            "name": "x",
            "request": {"method": "GET", "url_template": "/{id}",
                        "fields": [{"path": "id", "in": "path", "extract": "/d/([a-z"}]},
        }]},
    }
    with pytest.raises(ManifestError, match="extract"):
        parse_manifest(bad)


async def test_extract_bounds_the_subject_it_matches():
    """The pattern is authored (trusted) but the value is model-supplied, and an unbounded subject
    is what turns a sloppy regex into a stall - so only the first 4KB is searched.

    Only the MATCH is bounded, not the value: a link buried past the cap simply isn't extracted,
    and the argument is sent as given. That fails visibly against the API rather than quietly
    substituting whatever happened to fall inside the window."""
    url = await _call("x" * 10000 + "/spreadsheets/d/TOO_LATE/edit")
    assert "/spreadsheets/TOO_LATE/values/" not in url, "matched past the bound"
    assert "/spreadsheets/xxx" in url, "the value should be passed through untouched"


# --- Sheets write shape ----------------------------------------------------------------------
#
# `values` in the Sheets API is ALWAYS a 2-D array (a list of rows). The append action used to
# take a 1-D "cells of the one new row" and wrap it, so asking for 100 rows - where the model
# naturally sends 2-D - produced a 3-D array and a 400.

def _write_body(action_name: str, args: dict) -> dict:
    from forge.tools.rest import _build_body

    a = next(x for x in get_manifest("google-sheets").backend.actions if x.name == action_name)
    return _build_body(a.request, a.request["fields"], args, {})


@pytest.mark.parametrize("action", ["sheets_append_row", "sheets_update_range"])
@pytest.mark.parametrize("rows", [
    [["one row"]],
    [["Test Data 1", "Test Data 2"], ["Test Data 5", "Test Data 6"]],
    [[f"row {i}", i] for i in range(100)],
])
def test_sheets_writes_are_always_a_2d_array(action: str, rows: list):
    body = _write_body(action, {"rows": rows})
    assert isinstance(body, dict), "body must parse as JSON, not fall through as raw text"
    assert body["values"] == rows, "rows must reach the API unchanged - not wrapped, not repr'd"


def test_sheets_append_takes_rows_not_a_single_row():
    """The regression guard for 'add random 100 row': one row and many rows are the same shape."""
    a = next(x for x in get_manifest("google-sheets").backend.actions if x.name == "sheets_append_row")
    args = {f["path"] for f in a.request["fields"]}
    assert "rows" in args and "values" not in args
    # Appending must not overwrite whatever sits below the table.
    insert = next(f for f in a.request["fields"] if f["path"] == "insertDataOption")
    assert insert["default"] == "INSERT_ROWS"
