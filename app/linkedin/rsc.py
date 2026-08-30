"""
Generic decoder for LinkedIn's flagship-web RSC ("React Server
Components" flight-stream) responses.

Confirmed format from the HAR captures (app/flagship-web/rsc-action/...
responses, mimeType application/octet-stream, brotli-decoded by httpx
transparently):

    <id>:<payload>\\n
    <id>:<payload>\\n
    ...

Each line's payload is one of:
  - "I[...]"           -> a module/import reference record, e.g.
                           7:I["hash",[],"ComponentName"]
  - a JSON array/object -> the actual rendered node, e.g.
                           0:["$","div",null,{"children":[...]}]
  - a bare JSON scalar

Nodes reference each other via string placeholders of the form "$L<id>"
embedded inside the JSON (as a plain string value, not real JSON
recursion) — RSC's way of saying "the child in this slot is node <id>,
resolve it lazily". We resolve these by string substitution during
`get(id)`, not by writing a bespoke mini-parser for every shape LinkedIn
might send, because this needs to keep working as LinkedIn's exact
component tree changes.

This module deliberately does NOT know anything about LinkedIn profile
semantics — that belongs in app/linkedin/sdui.py and
app/linkedin/parser.py. This is purely "decode the wire format into a
dict of id -> parsed node".
"""
import json
import re
from typing import Any, Dict, Optional

from app.linkedin.exceptions import RscDecodeError

_LINE_RE = re.compile(r"^([0-9a-fA-F]+):(.*)$", re.DOTALL)
_REF_RE = re.compile(r"\$L([0-9a-fA-F]+)")


class RscDocument:
    """A parsed RSC flight stream: id -> raw parsed JSON node (unresolved refs)."""

    def __init__(self, nodes: Dict[str, Any]):
        self._nodes = nodes

    def raw(self, node_id: str) -> Any:
        return self._nodes.get(node_id)

    def ids(self):
        return self._nodes.keys()

    def resolve(self, node_id: str, _seen: Optional[set] = None) -> Any:
        """Return the node with $L<id> string references recursively resolved
        into the referenced node's value. Cycles are broken defensively
        (LinkedIn's trees are trees, not graphs, in every sample we saw,
        but we don't trust that blindly)."""
        _seen = _seen or set()
        if node_id in _seen:
            return None
        _seen = _seen | {node_id}

        node = self._nodes.get(node_id)
        return self._resolve_value(node, _seen)

    def _resolve_value(self, value: Any, seen: set) -> Any:
        if isinstance(value, str):
            m = re.fullmatch(r"\$L([0-9a-fA-F]+)", value)
            if m:
                return self.resolve(m.group(1), seen)
            return value
        if isinstance(value, list):
            return [self._resolve_value(v, seen) for v in value]
        if isinstance(value, dict):
            return {k: self._resolve_value(v, seen) for k, v in value.items()}
        return value


def parse_rsc_stream(raw_text: str) -> RscDocument:
    """Parse a decoded (already brotli/gzip-decompressed, UTF-8) RSC
    response body into an RscDocument.

    Raises RscDecodeError on structurally invalid input rather than
    silently returning an empty document — callers need to be able to
    tell "LinkedIn changed the format" apart from "this section is
    genuinely empty for this profile".
    """
    if not raw_text or not raw_text.strip():
        raise RscDecodeError("Empty RSC response body")

    nodes: Dict[str, Any] = {}
    line_count = 0

    for line in raw_text.split("\n"):
        line = line.rstrip("\r")
        if not line:
            continue
        m = _LINE_RE.match(line)
        if not m:
            # Not every line in a flight stream is guaranteed to be a
            # clean id:payload pair (continuation lines for very long
            # strings can happen). Skip rather than hard-fail on a
            # single malformed line.
            continue

        node_id, payload = m.group(1), m.group(2)
        line_count += 1

        parsed = _parse_payload(payload)
        nodes[node_id] = parsed

    if line_count == 0:
        raise RscDecodeError("No parsable id:payload lines found in RSC response")

    return RscDocument(nodes)


def _parse_payload(payload: str) -> Any:
    payload = payload.strip()
    if not payload:
        return None

    # Import/module reference records: I[...]
    if payload.startswith("I["):
        try:
            return {"$rsc_import": json.loads(payload[1:])}
        except json.JSONDecodeError:
            return {"$rsc_import_raw": payload}

    # HTML/text metadata prefixes seen in some flight streams (e.g. "T<n>,")
    # are not present in the LinkedIn captures we analyzed — if encountered,
    # fall through to raw storage rather than guessing.
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {"$raw": payload}


def find_all_text(document: RscDocument, min_len: int = 2, max_len: int = 300):
    """Yield every literal string value found anywhere in the resolved
    tree, across all root nodes. This is the fallback extraction path
    used by parser.py for sections whose exact field structure is
    UNKNOWN (see ENDPOINT_MAP.md) — it recovers rendered text without
    assuming a schema, at the cost of losing which field a string came
    from. Prefer targeted extraction (by key name) wherever the schema
    is confirmed; use this only as a documented fallback.
    """
    seen_strings = []

    def walk(value):
        if isinstance(value, str):
            s = value.strip()
            if min_len <= len(s) <= max_len and not s.startswith("$") and not s.startswith("proto."):
                seen_strings.append(s)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)

    for node_id in document.ids():
        walk(document.resolve(node_id))

    # de-dupe while preserving order
    out = []
    seen = set()
    for s in seen_strings:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out
