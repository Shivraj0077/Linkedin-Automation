import os

from app.linkedin.parser import extract_profile_image
from app.linkedin.sdui import extract_profile_image_asset

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _read(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


# --- sdui.extract_profile_image_asset (structural, low-level) --------------


def test_asset_found_via_activity_feed_actor_block_scoped_to_vanity_name():
    activity_raw = _read("activity_with_own_photo_synthetic.txt")

    asset = extract_profile_image_asset(None, activity_raw, "jane-doe")

    assert asset is not None
    assert asset["source"] == "activity_feed"
    assert asset["root_url"] == "https://media.licdn.com/dms/image/v2/EXAMPLEID/profile-displayphoto-"
    # Highest-resolution rendition (400x400) is selected deterministically,
    # not the first one encountered (100x100).
    assert asset["url"] == (
        "https://media.licdn.com/dms/image/v2/EXAMPLEID/profile-displayphoto-"
        "scale_400_400/EXAMPLE2/0/1700000000000?e=1800000000&v=beta&t=tokenB"
    )


def test_asset_found_via_above_activity_avatar_component():
    above_raw = _read("above_with_avatar_synthetic.txt")

    asset = extract_profile_image_asset(above_raw, None, "jane-doe")

    assert asset is not None
    assert asset["source"] == "above_activity"
    assert asset["url"] == (
        "https://media.licdn.com/dms/image/v2/AVATARID/profile-displayphoto-"
        "scale_400_400/AV2/0/1700000000000?e=1800000000&v=beta&t=tokenF"
    )


def test_no_image_data_anywhere_returns_none():
    above_raw = _read("about_synthetic.txt")  # no image fields at all
    activity_raw = _read("activity_no_photo_synthetic.txt")  # name/headline only

    asset = extract_profile_image_asset(above_raw, activity_raw, "jane-doe")

    assert asset is None


def test_missing_inputs_return_none_without_raising():
    assert extract_profile_image_asset(None, None, "jane-doe") is None


def test_company_logo_in_above_activity_is_never_selected():
    """A non-circle image (company logo / featured-post art) must not be
    mistaken for the profile avatar just because it's the only image
    present."""
    above_raw = _read("above_with_company_logo_synthetic.txt")

    asset = extract_profile_image_asset(above_raw, None, "jane-doe")

    assert asset is None


def test_other_actors_photos_in_activity_feed_are_never_selected():
    """A commenter's own photo (same SetState/Navigate shape as the
    requested profile's) and an unrelated post's company-logo image both
    appear in this feed, but neither is tied to the requested vanity_name
    -- the extractor must return None rather than fall back to whichever
    image it finds first."""
    activity_raw = _read("activity_unrelated_media_synthetic.txt")

    asset = extract_profile_image_asset(None, activity_raw, "jane-doe")

    assert asset is None

    # Sanity check: the commenter's own photo IS recoverable when *their*
    # vanity name is requested -- proving the extractor is scoping
    # correctly rather than being structurally unable to find anything.
    other_asset = extract_profile_image_asset(None, activity_raw, "someone-else")
    assert other_asset is not None
    assert other_asset["source"] == "activity_feed"


# --- parser.extract_profile_image (typed wrapper) ---------------------------


def test_parser_wraps_activity_feed_result_with_fallback_note():
    activity_raw = _read("activity_with_own_photo_synthetic.txt")

    image = extract_profile_image(None, activity_raw, "jane-doe")

    assert image is not None
    assert image.url.startswith("https://media.licdn.com/")
    assert image.root_url == "https://media.licdn.com/dms/image/v2/EXAMPLEID/profile-displayphoto-"
    assert image.note is not None  # flagged as activity-feed fallback, not top-card


def test_parser_wraps_above_activity_result_without_fallback_note():
    above_raw = _read("above_with_avatar_synthetic.txt")

    image = extract_profile_image(above_raw, None, "jane-doe")

    assert image is not None
    assert image.note is None


def test_parser_returns_none_when_profile_has_no_image():
    above_raw = _read("about_synthetic.txt")
    activity_raw = _read("activity_no_photo_synthetic.txt")

    image = extract_profile_image(above_raw, activity_raw, "jane-doe")

    assert image is None


def test_parser_returns_none_rather_than_an_unrelated_image():
    activity_raw = _read("activity_unrelated_media_synthetic.txt")

    image = extract_profile_image(None, activity_raw, "jane-doe")

    assert image is None
