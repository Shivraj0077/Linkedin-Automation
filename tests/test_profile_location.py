import os

from app.linkedin.parser import extract_profile_location

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _read(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def test_location_name_present_is_used_directly():
    raw = _read("voyager_location_name_synthetic.json")

    location = extract_profile_location(raw, "jane-doe")

    assert location == "Mumbai, Maharashtra, India"


def test_geo_urn_fallback_resolves_localized_geo_name():
    raw = _read("voyager_geo_only_synthetic.json")

    location = extract_profile_location(raw, "jane-doe")

    assert location == "Bengaluru, Karnataka, India"


def test_no_location_data_returns_none():
    raw = _read("voyager_no_location_synthetic.json")

    location = extract_profile_location(raw, "jane-doe")

    assert location is None


def test_multiple_profile_objects_selects_only_requested_vanity_name():
    """The `data.*elements` pointer in this fixture targets the WRONG
    profile's urn on purpose. Extraction must not trust that pointer
    blindly -- it has to validate the resolved entity's own
    publicIdentifier against vanity_name, and fall back to scanning
    `included` by publicIdentifier when the pointer doesn't match."""
    raw = _read("voyager_multiple_profiles_synthetic.json")

    assert extract_profile_location(raw, "jane-doe") == "Pune, Maharashtra, India"
    assert extract_profile_location(raw, "someone-else") == "Wrong City, Wrong Country"


def test_missing_or_empty_response_returns_none_without_raising():
    assert extract_profile_location(None, "jane-doe") is None
    assert extract_profile_location("", "jane-doe") is None


def test_malformed_json_returns_none_without_raising():
    assert extract_profile_location("{not valid json", "jane-doe") is None


def test_unknown_vanity_name_returns_none():
    raw = _read("voyager_location_name_synthetic.json")

    assert extract_profile_location(raw, "nobody-with-this-vanity-name") is None
