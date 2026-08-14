"""Structural directives in JSON body templates - `$each` (batch rows) and `$mime` (email).

Both exist for the same reason: a value the model cannot be asked to produce as text.

* `{"$each": "{{input.rows}}", "$as": "row", "$do": {...}}` expands one list-valued arg into a
  variable-length JSON array, so an agent editing N rows makes ONE call instead of N (slow,
  context-heavy, and prone to blowing the graph recursion limit).
* `{"$mime": {"to": …, "subject": …, "text": …}}` builds an RFC 2822 message and base64url-encodes
  it, because Gmail's send endpoint accepts nothing else and a model cannot base64-encode by hand.

Rendering is structural (parse JSON, then walk with render_value): the output is always valid
JSON with native types preserved, unlike string-concatenating an array. The path is opt-in on the
directive marker, so existing string-substitution templates are untouched.
"""
from __future__ import annotations

import json

from forge.auth_providers.templates import has_structural_directive, render_template, render_value
from forge.tools.rest import _build_body


def test_render_value_each_expands_per_item():
    tmpl = {
        "$each": "{{input.rows}}",
        "$as": "row",
        "$do": {"col": "{{row.editedCol}}", "val": "{{row.editedValue}}"},
    }
    rows = [
        {"editedCol": "unitCost", "editedValue": "96"},
        {"editedCol": "unitCost", "editedValue": "33"},
    ]
    out = render_value(tmpl, {"input": {"rows": rows}}, allow_each=True)
    assert out == [
        {"col": "unitCost", "val": "96"},
        {"col": "unitCost", "val": "33"},
    ]


def test_render_value_each_preserves_native_types_and_outer_vars():
    tmpl = {
        "orderId": "{{input.orderId}}",
        "rows": {"$each": "{{input.rows}}", "$as": "r", "$do": {"n": "{{r.n}}"}},
    }
    out = render_value(tmpl, {"input": {"orderId": "ORD-001", "rows": [{"n": 1}, {"n": 2}]}}, allow_each=True)
    # line numbers stay ints (whole-string token preserves native type); orderId interpolated.
    assert out == {"orderId": "ORD-001", "rows": [{"n": 1}, {"n": 2}]}


def test_render_value_each_missing_yields_empty_list():
    tmpl = {"$each": "{{input.rows}}", "$as": "row", "$do": {"x": "{{row.x}}"}}
    assert render_value(tmpl, {"input": {}}, allow_each=True) == []


def test_render_value_each_single_value_treated_as_one_item():
    tmpl = {"$each": "{{input.row}}", "$as": "row", "$do": {"x": "{{row.x}}"}}
    assert render_value(tmpl, {"input": {"row": {"x": "only"}}}, allow_each=True) == [{"x": "only"}]


def test_render_value_each_is_opt_in_only():
    # WITHOUT allow_each (the default for auth token_fetch / data-node payload callers), a dict that
    # happens to have a "$each" KEY must stay an ordinary object, NOT be reinterpreted as a loop.
    tmpl = {"$each": "{{input.rows}}", "$as": "row", "$do": {"x": "{{row.x}}"}}
    out = render_value(tmpl, {"input": {"rows": [{"x": "a"}]}})
    assert out == {"$each": [{"x": "a"}], "$as": "row", "$do": {"x": None}}


def test_structural_directive_detection_ignores_literal_string_values():
    # A directive is a "$each" KEY; the literal text "$each" inside a string value is not one.
    assert has_structural_directive({"note": "use $each to loop", "qty": "{{input.qty}}"}) is False
    assert has_structural_directive({"rows": {"$each": "{{x}}", "$do": {}}}) is True


def test_build_body_literal_dollar_each_in_string_keeps_string_substitution():
    # A valid-JSON template that merely MENTIONS "$each" in a value must not switch to structural
    # rendering (which would change token type coercion). The quoted token stays a string "5".
    body_template = json.dumps({"qty": "{{input.qty}}", "note": "$each is a keyword"})
    body = _build_body({"body_template": body_template}, [], {"qty": 5}, {})
    assert body == {"qty": "5", "note": "$each is a keyword"}


def test_render_template_embedded_falsy_values_are_not_dropped():
    # A falsy-but-real value embedded in a larger string must render literally (0 -> "0"), not be
    # swallowed to "" - which is what a `_lookup(...) or ""` would wrongly do.
    assert render_template("qty={{input.qty}}", {"input": {"qty": 0}}) == "qty=0"
    assert render_template("on={{input.flag}}", {"input": {"flag": False}}) == "on=False"
    assert render_template("x={{input.missing}}", {"input": {}}) == "x="


def test_build_body_batches_multiple_rows_into_one_body():
    body_template = json.dumps({
        "orderId": "{{input.orderId}}",
        "items": {
            "$each": "{{input.rows}}",
            "$as": "row",
            "$do": {
                "editedCol": "{{row.editedCol}}",
                "editedValue": "{{row.editedValue}}",
                "applyConversion": True,
                "lineNums": ["{{row.lineNum}}"],
                "enforcePolicy": False,
            },
        },
    })
    values = {
        "orderId": "ORD-001",
        "rows": [
            {"editedCol": "unitCost", "editedValue": "96", "lineNum": 1},
            {"editedCol": "unitCost", "editedValue": "33", "lineNum": 2},
        ],
    }
    body = _build_body({"body_template": body_template}, [], values, {})
    assert body["orderId"] == "ORD-001"
    assert len(body["items"]) == 2
    assert body["items"][0] == {
        "editedCol": "unitCost", "editedValue": "96",
        "applyConversion": True, "lineNums": [1], "enforcePolicy": False,
    }
    assert body["items"][1]["lineNums"] == [2]


def test_build_body_passthrough_rows_and_injects_constants():
    # Mirrors the live agent input: the model sends items items carrying an lineNums
    # ARRAY and no constants; the template passes each row's fields through (preserving the array)
    # and injects the fixed applyConversion/enforcePolicy server-side.
    body_template = json.dumps({
        "orderId": "{{input.orderId}}",
        "items": {
            "$each": "{{input.items}}",
            "$as": "row",
            "$do": {
                "editedCol": "{{row.editedCol}}",
                "editedValue": "{{row.editedValue}}",
                "applyConversion": True,
                "lineNums": "{{row.lineNums}}",
                "enforcePolicy": False,
            },
        },
    })
    values = {
        "orderId": "ORD-001",
        "items": [
            {"editedCol": "unitCost", "editedValue": "777", "lineNums": [1]},
            {"editedCol": "unitCost", "editedValue": "777", "lineNums": [2]},
        ],
    }
    body = _build_body({"body_template": body_template}, [], values, {})
    assert body["orderId"] == "ORD-001"
    assert body["items"] == [
        {"editedCol": "unitCost", "editedValue": "777", "applyConversion": True,
         "lineNums": [1], "enforcePolicy": False},
        {"editedCol": "unitCost", "editedValue": "777", "applyConversion": True,
         "lineNums": [2], "enforcePolicy": False},
    ]


def test_build_body_without_each_is_unchanged():
    # Legacy unquoted-token template (not valid JSON as text) still uses string substitution and
    # keeps producing a number for the bare {{token}}.
    body_template = '{ "orderId": "{{input.orderId}}", "lineNums": [ {{input.lineNum}} ] }'
    body = _build_body({"body_template": body_template}, [], {"orderId": "Q", "lineNum": 7}, {})
    assert body == {"orderId": "Q", "lineNums": [7]}


# --- $mime: build an RFC 2822 message server-side --------------------------------------------
#
# Gmail's send endpoint accepts ONLY a base64url-encoded MIME message. Declaring that as a tool
# argument means asking a model to base64-encode by hand; it can't, and the malformed result
# comes back as an opaque HTTP 400. So the model supplies to/subject/body and this does the rest.

def _decoded(body: dict):
    import base64
    import email

    raw = body["raw"]
    blob = base64.urlsafe_b64decode(raw)
    return blob, email.message_from_bytes(blob)


def test_mime_directive_builds_a_decodable_message():
    tmpl = ('{"raw": {"$mime": {"to": "{{input.to}}", "subject": "{{input.subject}}", '
            '"text": "{{input.body}}"}}}')
    body = _build_body({"body_template": tmpl}, [],
                       {"to": "a@example.com", "subject": "Hello", "body": "Line one\nLine two"}, {})
    assert list(body) == ["raw"]
    blob, msg = _decoded(body)
    assert msg["To"] == "a@example.com"
    assert msg["Subject"] == "Hello"
    # RFC 2822 wants CRLF everywhere, headers and body alike, and the SMTP policy normalises the
    # body's newlines to match. Mail clients render that as ordinary line breaks.
    assert msg.get_payload(decode=True).decode().replace("\r\n", "\n").strip() == "Line one\nLine two"
    assert b"\r\n" in blob


def test_mime_omits_empty_headers_rather_than_sending_them_blank():
    """A model that leaves cc out sends "" - and `Cc: ` on the wire is what makes Gmail answer
    400 rather than simply having no Cc."""
    tmpl = ('{"raw": {"$mime": {"to": "{{input.to}}", "cc": "{{input.cc}}", '
            '"subject": "{{input.subject}}", "text": "{{input.body}}"}}}')
    body = _build_body({"body_template": tmpl}, [],
                       {"to": "a@example.com", "cc": "", "subject": "S", "body": "B"}, {})
    _blob, msg = _decoded(body)
    assert msg["Cc"] is None
    assert msg["To"] == "a@example.com"


def test_mime_handles_address_lists_and_non_ascii_subjects():
    tmpl = ('{"raw": {"$mime": {"to": "{{input.to}}", "subject": "{{input.subject}}", '
            '"text": "{{input.body}}"}}}')
    body = _build_body({"body_template": tmpl}, [],
                       {"to": ["a@example.com", "b@example.com"], "subject": "Update ✅", "body": "x"}, {})
    _blob, msg = _decoded(body)
    assert msg["To"] == "a@example.com, b@example.com"
    # Non-ASCII must be RFC 2047 encoded, and must decode back to what was asked for.
    from email.header import decode_header, make_header
    assert str(make_header(decode_header(msg["Subject"]))) == "Update ✅"


def test_mime_is_inert_without_the_structural_opt_in():
    """Every other caller (auth token_fetch rules, data-node payloads) must keep treating a
    literal "$mime" key as an ordinary key rather than executing it."""
    from forge.auth_providers.templates import render_value

    tmpl = {"raw": {"$mime": {"to": "{{input.to}}"}}}
    out = render_value(tmpl, {"input": {"to": "a@example.com"}})
    assert out == {"raw": {"$mime": {"to": "a@example.com"}}}


def test_a_literal_dollar_mime_string_does_not_trigger_structural_rendering():
    body_template = '{ "note": "use $mime for email", "qty": {{input.qty}} }'
    body = _build_body({"body_template": body_template}, [], {"qty": 3}, {})
    assert body == {"note": "use $mime for email", "qty": 3}


# --- interpolating a list/dict into a JSON body ----------------------------------------------
#
# `str()` on a list yields Python's repr - single quotes, True/None - which is not JSON. The body
# then fails to parse and is sent as raw text, and the API answers 400. It only ever showed up on
# STRING data: `[[1, 2]]` is valid JSON by coincidence, `[['a', 'b']]` is not.

def test_embedded_list_renders_as_json_not_python_repr():
    body = _build_body({"body_template": '{"values":{{input.rows}}}'},
                       [{"path": "rows", "in": "body", "type": "array"}],
                       {"rows": [["Test Data 1", "Test Data 2"]]}, {})
    assert body == {"values": [["Test Data 1", "Test Data 2"]]}


def test_embedded_dict_renders_as_json():
    body = _build_body({"body_template": '{"fields":{{input.fields}}}'},
                       [{"path": "fields", "in": "body", "type": "object"}],
                       {"fields": {"Name": "Ada", "Active": True, "Notes": None}}, {})
    assert body == {"fields": {"Name": "Ada", "Active": True, "Notes": None}}


def test_embedded_list_of_numbers_still_works():
    """The case that accidentally passed before, which is why this went unnoticed."""
    body = _build_body({"body_template": '{"values":{{input.rows}}}'},
                       [{"path": "rows", "in": "body", "type": "array"}],
                       {"rows": [[1, 2]]}, {})
    assert body == {"values": [[1, 2]]}


def test_embedded_scalars_keep_their_plain_string_form():
    """Only containers change. A bare token in a query string still renders "False"/"0", which is
    what that context wants - see test_render_template_embedded_falsy_values_are_not_dropped."""
    assert render_template("on={{input.flag}}", {"input": {"flag": False}}) == "on=False"
    assert render_template("qty={{input.qty}}", {"input": {"qty": 0}}) == "qty=0"


def test_non_ascii_in_an_interpolated_list_is_not_escaped_away():
    body = _build_body({"body_template": '{"values":{{input.rows}}}'},
                       [{"path": "rows", "in": "body", "type": "array"}],
                       {"rows": [["café", "naïve"]]}, {})
    assert body == {"values": [["café", "naïve"]]}


def test_form_encoded_template_starting_with_a_token_is_not_json_escaped():
    """A JSON body opens with `{`, but so does a form template whose first thing is a token.
    Escaping that one puts a literal backslash into the form body."""
    body = _build_body({"body_template": "{{input.q}}=1&note={{input.n}}"}, [],
                       {"q": 'a"b', "n": "x"}, {})
    assert body == 'a"b=1&note=x'


def test_a_json_template_is_still_escaped():
    body = _build_body({"body_template": '{"q":"{{input.q}}"}'}, [], {"q": 'a"b'}, {})
    assert body == {"q": 'a"b'}


def test_a_bare_token_template_keeps_its_native_value():
    """`{{input.payload}}` alone is a whole-string match, so it resolves to the object itself and
    never goes near the escaping path - even though it also starts with `{`."""
    body = _build_body({"body_template": "{{input.payload}}"}, [],
                       {"payload": {"a": 'q"uote', "b": [1, 2]}}, {})
    assert body == {"a": 'q"uote', "b": [1, 2]}
