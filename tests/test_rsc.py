import pytest

from app.linkedin.exceptions import RscDecodeError
from app.linkedin.rsc import parse_rsc_stream


def test_parses_simple_id_payload_lines():
    raw = '0:["$","div",null,{"foo":"bar"}]\n1:I["hash",[],"Comp"]\n'
    doc = parse_rsc_stream(raw)
    assert doc.raw("0") == ["$", "div", None, {"foo": "bar"}]
    assert doc.raw("1") == {"$rsc_import": ["hash", [], "Comp"]}


def test_resolves_reference_placeholders():
    raw = (
        '0:["$","div",null,{"child":"$L1"}]\n'
        '1:["$","span",null,{"textProps":{"children":["hello"]}}]\n'
    )
    doc = parse_rsc_stream(raw)
    resolved = doc.resolve("0")
    assert resolved[3]["child"] == ["$", "span", None, {"textProps": {"children": ["hello"]}}]


def test_empty_body_raises():
    with pytest.raises(RscDecodeError):
        parse_rsc_stream("")


def test_no_parsable_lines_raises():
    with pytest.raises(RscDecodeError):
        parse_rsc_stream("this is not an rsc stream at all, no colons here either")


def test_skips_malformed_lines_without_failing_whole_parse():
    raw = '0:["$","div",null,{}]\nnot a valid line\n1:["$","span",null,{}]\n'
    doc = parse_rsc_stream(raw)
    assert doc.raw("0") is not None
    assert doc.raw("1") is not None


def test_breaks_reference_cycles_defensively():
    # Pathological input: two nodes reference each other. Real LinkedIn
    # captures analyzed were always trees, not graphs, but the resolver
    # must not infinite-loop if that ever changes.
    raw = '0:"$L1"\n1:"$L0"\n'
    doc = parse_rsc_stream(raw)
    assert doc.resolve("0") is None
