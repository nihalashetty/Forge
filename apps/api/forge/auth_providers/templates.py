"""Token-template rendering for auth recipes.

Supports `{{a.b}}` references resolved against a vars dict, e.g.
`{{cred.username}}`, `{{ctx.csrf}}`, `{{extracted.session}}`. Non-string leaves
pass through; whole-string matches preserve the resolved value's native type.

`render_value` walks a parsed JSON structure (dict/list) and additionally honors a
`{"$each": "{{input.rows}}", "$as": "row", "$do": {...}}` loop directive, so a JSON body
template can build a variable-length array from one list-valued arg (see `_render_each`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from typing import Any

_TOKEN = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


class MissingTemplateVar(ValueError):
    """A template referenced a variable in a STRICT namespace that isn't defined.

    Raised for `{{env.*}}` when the key isn't in FORGE_TOOL_VARS for the current environment,
    so a misconfigured deploy fails loudly at call/test time instead of sending a broken URL.
    The lenient namespaces (ctx/input) keep their historic behavior: a missing key renders empty
    / is dropped."""


def _lookup(path: str, vars: dict, strict_ns: Collection[str] = ()) -> Any:
    parts = path.split(".")
    cur: Any = vars
    for part in parts:
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = None
            break
    # A missing value under a STRICT namespace (e.g. env) is an error, not an empty string.
    if cur is None and parts and parts[0] in strict_ns:
        raise MissingTemplateVar(f"undefined template variable {{{{{path}}}}} (namespace '{parts[0]}')")
    return cur


def _sub_one(mm: re.Match, vars: dict, strict_ns: Collection[str] = (),
             escape_json: bool = False) -> str:
    # Embedded token (not a whole-string match): stringify the resolved value. Only a missing
    # value (None) becomes empty - a falsy-but-real value like 0 or False must render as "0"/
    # "False", not "" (an `x or ""` here would silently drop legitimate zeros/booleans).
    v = _lookup(mm.group(1), vars, strict_ns)
    if v is None:
        return ""
    # Inside a JSON body template the token sits between quotes, so the CONTENT has to be
    # JSON-escaped: a newline, a double quote or a backslash in model-written text (a note, an
    # email body, a search string) otherwise terminates the string early and the whole body stops
    # being JSON. dumps()[1:-1] escapes the content without adding a second pair of quotes.
    if escape_json and isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)[1:-1]
    # A list/dict interpolated into a body template is being placed into JSON - `str()` there
    # yields Python's repr (single quotes, True/None), which is not JSON, so the body fails to
    # parse and gets sent as raw text. `[[1, 2]]` happens to be valid JSON and `[['a', 'b']]` is
    # not, which is why this only ever showed up on string data. Scalars keep str(): a bare
    # `{{input.flag}}` in a query string renders "False", which is what that context wants.
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def render_template(s: str, vars: dict, *, strict_ns: Collection[str] = (),
                    escape_json: bool = False) -> Any:
    # `strict_ns` names namespaces whose missing keys raise MissingTemplateVar instead of
    # rendering empty (used for {{env.*}}). Defaults to lenient for all namespaces.
    # `escape_json` JSON-escapes substituted strings, for a template that is a JSON document
    # (the REST body path opts in). Whole-string single token -> preserve native type (numbers,
    # objects), which needs no escaping because the value is never re-parsed from text.
    m = _TOKEN.fullmatch(s.strip())
    if m:
        return _lookup(m.group(1), vars, strict_ns)
    return _TOKEN.sub(lambda mm: _sub_one(mm, vars, strict_ns, escape_json), s)


#: Directives that need STRUCTURAL rendering (parse the JSON, then walk it) rather than plain
#: string substitution, because they produce a value the surrounding text can't express.
DIRECTIVES = ("$each", "$mime")


def has_structural_directive(obj: Any) -> bool:
    """True if `obj` (a parsed JSON structure) contains a `$each` or `$mime` directive anywhere -
    i.e. a dict that has one as a KEY. Used to decide whether a body template needs structural
    rendering; a literal "$each" appearing inside a string value is NOT a directive and must not
    trigger it (that would silently change type coercion for unrelated templates)."""
    if isinstance(obj, dict):
        if any(d in obj for d in DIRECTIVES):
            return True
        return any(has_structural_directive(v) for v in obj.values())
    if isinstance(obj, list):
        return any(has_structural_directive(v) for v in obj)
    return False


def render_value(obj: Any, vars: dict, *, allow_each: bool = False, strict_ns: Collection[str] = ()) -> Any:
    """Walk a parsed JSON structure, rendering `{{token}}` leaves. Structural directives (`$each`,
    `$mime`) are honored ONLY when `allow_each=True` (the REST body-template path opts in); every
    other caller - auth token_fetch/extract rules, data-node payloads - passes the default False,
    so a literal object key named "$each" stays an ordinary key instead of being reinterpreted.
    `strict_ns` propagates the fail-loud namespaces (e.g. env) to every leaf."""
    if isinstance(obj, str):
        return render_template(obj, vars, strict_ns=strict_ns)
    if isinstance(obj, dict):
        if allow_each and "$each" in obj:
            return _render_each(obj, vars, strict_ns=strict_ns)
        if allow_each and "$mime" in obj:
            return _render_mime(obj["$mime"], vars, strict_ns=strict_ns)
        return {k: render_value(v, vars, allow_each=allow_each, strict_ns=strict_ns) for k, v in obj.items()}
    if isinstance(obj, list):
        return [render_value(v, vars, allow_each=allow_each, strict_ns=strict_ns) for v in obj]
    return obj


#: Header name -> the key a `$mime` spec uses for it.
_MIME_HEADERS = (
    ("To", "to"), ("Cc", "cc"), ("Bcc", "bcc"), ("From", "from"), ("Reply-To", "reply_to"),
    ("Subject", "subject"), ("In-Reply-To", "in_reply_to"), ("References", "references"),
)


def _render_mime(spec: Any, vars: dict, *, strict_ns: Collection[str] = ()) -> str:
    """Build an RFC 2822 message from `{to, subject, text, ...}` and return it base64url-encoded.

    This exists because Gmail's send endpoint accepts ONLY a base64url-encoded MIME message.
    Declaring that as a tool argument means asking a language model to base64-encode by hand -
    which it cannot do reliably - and the malformed result comes back as an opaque HTTP 400 with
    nothing in it to debug. So the model supplies the fields a person would type and the encoding
    happens here, where stdlib `email` gets header encoding, non-ASCII subjects, address lists
    and CRLF line endings right.

    Address fields accept a string or a list. Supply `text` (and optionally `html` for a
    multipart/alternative). Empty/absent headers are omitted rather than sent blank.
    """
    import base64
    from email.message import EmailMessage
    from email.policy import SMTP

    if not isinstance(spec, dict):
        raise ValueError("$mime expects an object, e.g. {\"to\": \"…\", \"subject\": \"…\", \"text\": \"…\"}")
    resolved = {k: render_value(v, vars, strict_ns=strict_ns) for k, v in spec.items()}

    msg = EmailMessage()
    for header, key in _MIME_HEADERS:
        val = resolved.get(key)
        if isinstance(val, (list, tuple)):
            val = ", ".join(str(v).strip() for v in val if str(v).strip())
        if val is None or str(val).strip() == "":
            continue
        msg[header] = str(val).strip()
    msg.set_content(str(resolved.get("text") or resolved.get("body") or ""))
    html = resolved.get("html")
    if html:
        msg.add_alternative(str(html), subtype="html")
    # SMTP policy => CRLF line endings, as RFC 2822 requires.
    return base64.urlsafe_b64encode(msg.as_bytes(policy=SMTP)).decode()


def _render_each(directive: dict, vars: dict, *, strict_ns: Collection[str] = ()) -> list:
    """Expand a `{"$each": "{{input.rows}}", "$as": "row", "$do": {...}}` loop directive into a
    list: render `$do` once per item of the array `$each` resolves to, with the item bound under
    the `$as` name (default "item"). Outer vars (input/ctx/state) stay visible inside the loop, so
    a nested template can still read e.g. `{{input.orderId}}`. A missing/None `$each` yields [];
    a single non-list value is treated as one item.

    This lets a JSON body template build a variable-length array (e.g. one productRow per edited
    cell) WITHOUT string-concatenating JSON - so the output is always valid JSON with native types
    preserved, and one tool call can carry many rows instead of one call per row.
    """
    each = directive.get("$each")
    seq = render_value(each, vars, strict_ns=strict_ns) if isinstance(each, str) else each
    if seq is None:
        items: list = []
    elif isinstance(seq, list):
        items = seq
    else:
        items = [seq]
    as_name = directive.get("$as") or "item"
    body = directive.get("$do")
    # allow_each=True so a `$do` body can itself contain a nested `$each` (loops within loops).
    return [render_value(body, {**vars, as_name: item}, allow_each=True, strict_ns=strict_ns) for item in items]
