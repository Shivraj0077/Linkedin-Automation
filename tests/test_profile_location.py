import os

from app.linkedin.parser import extract_profile_location, extract_voyager_identity_fields

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


# --- extract_voyager_identity_fields (name/headline/photo backfill) --------


def test_full_identity_recovers_name_headline_and_photo():
    raw = _read("voyager_full_identity_synthetic.json")

    fields = extract_voyager_identity_fields(raw, "jane-doe")

    assert fields is not None
    assert fields["name"] == "Jane Doe"
    assert fields["first_name"] == "Jane"
    assert fields["last_name"] == "Doe"
    assert fields["headline"] == "Backend Engineer @Example"
    assert fields["about"] == "I build backend systems and enjoy mentoring engineers."
    assert fields["member_id"] == "10138250"

    image = fields["profile_image"]
    assert image is not None
    # Highest-resolution artifact (800x800) selected deterministically.
    assert image["url"] == (
        "https://media.licdn.com/dms/image/v2/EXAMPLEID/profile-displayphoto-"
        "800_800/profile-displayphoto-shrink_800_800/0/1700000000000?e=1800000000&v=beta&t=tokenB"
    )
    assert image["root_url"] == "https://media.licdn.com/dms/image/v2/EXAMPLEID/profile-displayphoto-"


def test_minimal_identity_returns_none_fields_without_guessing():
    raw = _read("voyager_minimal_identity_synthetic.json")

    fields = extract_voyager_identity_fields(raw, "jane-doe")

    assert fields is not None
    assert fields["name"] is None
    assert fields["headline"] is None
    assert fields["about"] is None
    assert fields["member_id"] is None
    assert fields["profile_image"] is None


def test_identity_fields_return_none_when_profile_entity_not_found():
    raw = _read("voyager_full_identity_synthetic.json")

    assert extract_voyager_identity_fields(raw, "someone-else-entirely") is None


def test_identity_fields_missing_or_malformed_input_returns_none():
    assert extract_voyager_identity_fields(None, "jane-doe") is None
    assert extract_voyager_identity_fields("{not valid json", "jane-doe") is None
