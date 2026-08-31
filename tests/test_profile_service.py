import os

import pytest

import app.services.profile_service as profile_service_module
from app.linkedin.exceptions import AuthenticationExpiredError, MissingProfileDataError
from app.services.profile_service import build_profile

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture(autouse=True)
def _no_activity_retry_delay(monkeypatch):
    # Synthetic fixtures are far smaller than any real LinkedIn
    # response, so they trip the same-request-retry heuristic (see
    # profile_service._EMPTY_ACTIVITY_RESPONSE_MAX_LEN) on every test
    # that supplies a short `activity` fixture. Zero the delay so tests
    # stay fast; the retry *logic* itself is still exercised.
    monkeypatch.setattr(profile_service_module, "_ACTIVITY_RETRY_DELAY_SECONDS", 0)


def _read(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


class FakeClient:
    """Duck-typed stand-in for LinkedInClient -- no network, no cookies.
    Each method returns a canned raw RSC body or raises, so these tests
    exercise build_profile's orchestration/degradation logic in
    isolation from HTTP/auth concerns (those are covered by
    test_url.py's SSRF-guard-adjacent checks and manual verification
    against client.py's header construction, per README Limitations --
    there is no live LinkedIn session in CI)."""

    def __init__(self, *, activity=None, above=None, below1=None, experience=None,
                 skills_pages=None, identity=None, raise_on=None, activity_sequence=None):
        self._activity = activity
        self._above = above
        self._below1 = below1
        self._experience = experience
        self._skills_pages = skills_pages or []
        self._identity = identity
        self._raise_on = raise_on or {}
        # When set, each successive call to the activity component pops
        # the next value off this list instead of always returning
        # `activity` -- used to simulate LinkedIn returning the
        # collapsed placeholder on one call and real content on a retry.
        self._activity_sequence = list(activity_sequence) if activity_sequence else None
        self.activity_call_count = 0

    async def get_component(self, component_id, vanity_name, is_self_view=False, extra_payload=None):
        if "profileCardsActivity" in component_id:
            if "activity" in self._raise_on:
                raise self._raise_on["activity"]
            self.activity_call_count += 1
            if self._activity_sequence is not None:
                return self._activity_sequence.pop(0) if self._activity_sequence else None
            return self._activity
        if "profileCardsAboveActivity" in component_id:
            if "above" in self._raise_on:
                raise self._raise_on["above"]
            return self._above
        if "profileCardsBelowActivityPart1WithoutExp" in component_id:
            if "below1" in self._raise_on:
                raise self._raise_on["below1"]
            return self._below1
        raise AssertionError(f"unexpected componentId in test: {component_id}")

    async def get_experience(self, vanity_name, viewee_profile_id):
        if "experience" in self._raise_on:
            raise self._raise_on["experience"]
        return self._experience

    async def get_skills_page(self, vanity_name, profile_id, start, count=10):
        if "skills" in self._raise_on:
            raise self._raise_on["skills"]
        idx = start // count
        if idx < len(self._skills_pages):
            return self._skills_pages[idx]
        return None

    async def get_honors_page(self, vanity_name, profile_id, start, count=10):
        if "honors" in self._raise_on:
            raise self._raise_on["honors"]
        return None

    async def get_profile_identity(self, vanity_name):
        if "identity" in self._raise_on:
            raise self._raise_on["identity"]
        return self._identity


@pytest.mark.asyncio
async def test_build_profile_happy_path_all_sections_present():
    activity_with_name = (
        '0:["$","div",null,{"aria-label":"Jane Doe Premium Profile 3rd+","children":["$L1"]}]\n'
        '1:["$","span",null,{"textProps":{"children":["Backend Engineer @Example"]}}]\n'
    )
    above = _read("about_synthetic.txt") + (
        'x:["$","span",null,{"componentKey":'
        '"com.linkedin.sdui.profile.card.refABC123XYZAbout"}]\n'
    )
    below1 = _read("education_synthetic.txt")
    experience = _read("experience_synthetic.txt")
    skills_page_0 = _read("skills_page_full_synthetic.txt")

    client = FakeClient(
        activity=activity_with_name,
        above=above,
        below1=below1,
        experience=experience,
        skills_pages=[skills_page_0],
    )

    profile = await build_profile(
        client, "jane-doe", "https://www.linkedin.com/in/jane-doe/", viewee_profile_id="ABC123XYZ"
    )

    assert profile.name == "Jane Doe"
    assert profile.headline == "Backend Engineer @Example"
    assert profile.about is not None
    assert profile.top_skills_summary == ["Python", "FastAPI", "Distributed Systems"]
    assert len(profile.education) == 1
    assert len(profile.experience) == 2
    assert len(profile.skills) == 10
    assert profile.confidence.about == "verified"
    assert profile.confidence.experience == "verified"


@pytest.mark.asyncio
async def test_activity_placeholder_response_is_retried_once():
    """LinkedIn renders a literal collapsed placeholder
    (`children: [false, null]`) for the activity feed on some requests
    and full content on the very next, identical request -- observed
    repeatedly. The first fetch returning that placeholder should
    trigger exactly one retry, and the retry's real content should be
    used instead of leaving name/headline/profile_image null."""
    placeholder = (
        '0:["$","div",null,{"data-sdui-component":'
        '"com.linkedin.sdui.generated.profile.dsl.impl.profileCardsActivity",'
        '"children":[false,null]}]'
    )
    real_activity = (
        '0:["$","div",null,{"aria-label":"Jane Doe Premium Profile 3rd+","children":["$L1"]}]\n'
        '1:["$","span",null,{"textProps":{"children":["Backend Engineer @Example"]}}]\n'
        # Padding so this fixture clears _EMPTY_ACTIVITY_RESPONSE_MAX_LEN
        # the same way any real (megabytes-sized) LinkedIn feed response
        # would -- the assertion below is about content, not size, but
        # the retry-acceptance check itself is size-based.
        + ("2:[\"$\",\"span\",null,{\"textProps\":{\"children\":[\"padding\"]}}]\n" * 15)
    )
    client = FakeClient(
        activity_sequence=[placeholder, real_activity],
        above=_read("about_synthetic.txt"),
        below1=_read("education_synthetic.txt"),
    )

    profile = await build_profile(
        client, "jane-doe", "https://www.linkedin.com/in/jane-doe/", viewee_profile_id="ABC123XYZ"
    )

    assert client.activity_call_count == 2
    assert profile.name == "Jane Doe"
    assert profile.headline == "Backend Engineer @Example"


@pytest.mark.asyncio
async def test_activity_placeholder_on_both_attempts_degrades_gracefully():
    """If the retry ALSO comes back as the placeholder, the request
    must still degrade cleanly (name/headline unavailable) rather than
    retrying forever or raising."""
    placeholder = (
        '0:["$","div",null,{"data-sdui-component":'
        '"com.linkedin.sdui.generated.profile.dsl.impl.profileCardsActivity",'
        '"children":[false,null]}]'
    )
    client = FakeClient(
        activity_sequence=[placeholder, placeholder],
        above=_read("about_synthetic.txt"),
        below1=_read("education_synthetic.txt"),
    )

    profile = await build_profile(
        client, "jane-doe", "https://www.linkedin.com/in/jane-doe/", viewee_profile_id="ABC123XYZ"
    )

    assert client.activity_call_count == 2
    assert profile.name is None
    assert profile.confidence.name == "unavailable"


@pytest.mark.asyncio
async def test_build_profile_degrades_gracefully_when_experience_fails():
    client = FakeClient(
        activity=None,
        above=_read("about_synthetic.txt"),
        below1=_read("education_synthetic.txt"),
        raise_on={"experience": AuthenticationExpiredError("session expired")},
    )

    profile = await build_profile(
        client, "jane-doe", "https://www.linkedin.com/in/jane-doe/", viewee_profile_id="ABC123XYZ"
    )

    # Experience failed, but the rest of the profile is still returned --
    # this is the core resilience requirement (#20): one section's
    # failure must not take down the whole response.
    assert profile.experience == []
    assert profile.confidence.experience == "unavailable"
    assert profile.about is not None
    assert len(profile.education) == 1


@pytest.mark.asyncio
async def test_build_profile_raises_missing_data_when_everything_empty():
    client = FakeClient(activity=None, above=None, below1=None, experience=None)

    with pytest.raises(MissingProfileDataError):
        await build_profile(client, "ghost-profile", "https://www.linkedin.com/in/ghost-profile/")


@pytest.mark.asyncio
async def test_build_profile_skips_experience_and_skills_without_profile_id():
    client = FakeClient(
        activity=None,
        above=_read("about_synthetic.txt"),  # no profileId ref in this fixture
        below1=_read("education_synthetic.txt"),
    )

    profile = await build_profile(
        client, "jane-doe", "https://www.linkedin.com/in/jane-doe/", viewee_profile_id=None
    )

    assert profile.experience == []
    assert profile.skills == []
    assert profile.confidence.experience == "unavailable"


@pytest.mark.asyncio
async def test_build_profile_bootstraps_profile_id_from_component_refs_when_not_supplied():
    """Cold request: caller supplies no viewee_profile_id at all. The
    profileId should be recovered from componentKey refs embedded in
    the aboveActivity response (ENDPOINT_MAP.md #6/#11 bootstrap path),
    which then unblocks the Experience and Skills fetches."""
    above_with_ref = _read("about_synthetic.txt") + (
        'x:["$","span",null,{"componentKey":'
        '"com.linkedin.sdui.profile.card.refBOOTSTRAPPEDID999About"}]\n'
    )
    seen_profile_ids = []

    class RecordingFakeClient(FakeClient):
        async def get_experience(self, vanity_name, viewee_profile_id):
            seen_profile_ids.append(viewee_profile_id)
            return _read("experience_synthetic.txt")

        async def get_skills_page(self, vanity_name, profile_id, start, count=10):
            seen_profile_ids.append(profile_id)
            if start == 0:
                return _read("skills_page_full_synthetic.txt")
            return _read("skills_page_last_synthetic.txt")

    client = RecordingFakeClient(
        activity=None,
        above=above_with_ref,
        below1=_read("education_synthetic.txt"),
    )

    profile = await build_profile(
        client, "jane-doe", "https://www.linkedin.com/in/jane-doe/", viewee_profile_id=None
    )

    assert profile.profile_id == "BOOTSTRAPPEDID999"
    assert all(pid == "BOOTSTRAPPEDID999" for pid in seen_profile_ids)
    assert len(profile.experience) == 2
    assert len(profile.skills) > 0


def _voyager_identity_json(vanity_name, *, name=True, headline=True, photo=True, about=True):
    entity = {
        "entityUrn": "urn:li:fsd_profile:JANEDOE123",
        "publicIdentifier": vanity_name,
        "$type": "com.linkedin.voyager.dash.identity.profile.Profile",
        "firstName": "Jane" if name else None,
        "lastName": "Doe" if name else None,
        "headline": "Voyager Headline" if headline else None,
        "summary": "Voyager About text." if about else None,
        "objectUrn": "urn:li:member:10138250",
        "locationName": None,
        "geoLocation": None,
        "profilePicture": (
            {
                "displayImageReference": {
                    "vectorImage": {
                        "rootUrl": "https://media.licdn.com/dms/image/v2/EXAMPLEID/profile-displayphoto-",
                        "artifacts": [
                            {
                                "width": 800,
                                "height": 800,
                                "fileIdentifyingUrlPathSegment": "800_800/profile-displayphoto-shrink_800_800/0/1700000000000?e=1800000000&v=beta&t=tokenB",
                            }
                        ],
                    }
                },
                "displayImageUrn": "urn:li:digitalmediaAsset:EXAMPLEID",
            }
            if photo
            else None
        ),
    }
    import json

    return json.dumps(
        {"data": {"*elements": [entity["entityUrn"]]}, "included": [entity]}
    )


@pytest.mark.asyncio
async def test_voyager_backfills_name_headline_photo_when_activity_feed_empty():
    """The activity feed can legitimately render no content at all
    (`children: [false, null]`, a real 200) -- when that happens, the
    Voyager identity response (already fetched for location) should
    backfill name/headline/profile_image rather than leaving them null."""
    client = FakeClient(
        activity=None,  # no activity-feed byline recoverable
        above=_read("about_synthetic.txt"),
        below1=_read("education_synthetic.txt"),
        identity=_voyager_identity_json("jane-doe"),
    )

    profile = await build_profile(
        client, "jane-doe", "https://www.linkedin.com/in/jane-doe/", viewee_profile_id="ABC123XYZ"
    )

    assert profile.name == "Jane Doe"
    assert profile.first_name == "Jane"
    assert profile.last_name == "Doe"
    assert profile.headline == "Voyager Headline"
    assert profile.profile_image is not None
    assert profile.profile_image.url.startswith("https://media.licdn.com/")
    assert profile.confidence.name == "verified"
    assert profile.confidence.headline == "verified"
    assert profile.confidence.profile_image == "verified"
    assert profile.member_id == "10138250"


@pytest.mark.asyncio
async def test_voyager_backfills_about_when_above_activity_has_no_about_text():
    """profileCardsAboveActivity can render with no About text present
    at all (a real, populated response for other sections, just none
    for About -- see build_profile's own About-fetch comment). When
    that happens, Voyager's `summary` field should backfill `about`."""
    above_without_about = (
        '0:["$","$L1",null,{"textProps":{"children":["Featured"]}}]\n'
    )
    client = FakeClient(
        activity=None,
        above=above_without_about,
        below1=_read("education_synthetic.txt"),
        identity=_voyager_identity_json("jane-doe"),
    )

    profile = await build_profile(
        client, "jane-doe", "https://www.linkedin.com/in/jane-doe/", viewee_profile_id="ABC123XYZ"
    )

    assert profile.about == "Voyager About text."
    assert profile.confidence.about == "verified"


@pytest.mark.asyncio
async def test_voyager_about_backfill_never_overrides_above_activity_value():
    client = FakeClient(
        activity=None,
        above=_read("about_synthetic.txt"),  # has real About text already
        below1=_read("education_synthetic.txt"),
        identity=_voyager_identity_json("jane-doe"),  # would report "Voyager About text."
    )

    profile = await build_profile(
        client, "jane-doe", "https://www.linkedin.com/in/jane-doe/", viewee_profile_id="ABC123XYZ"
    )

    assert profile.about != "Voyager About text."
    assert profile.confidence.about == "verified"  # from the RSC path's own "verified" default


@pytest.mark.asyncio
async def test_member_id_is_always_set_from_voyager_even_when_rsc_fields_all_succeed():
    """member_id has no RSC/SDUI source at all -- it must be set from
    Voyager's objectUrn regardless of whether name/headline/about/photo
    were already recovered from the activity-feed/aboveActivity paths."""
    activity_with_name = (
        '0:["$","div",null,{"aria-label":"Jane Doe Premium Profile 3rd+","children":["$L1"]}]\n'
        '1:["$","span",null,{"textProps":{"children":["Backend Engineer @Example"]}}]\n'
    )
    client = FakeClient(
        activity=activity_with_name,
        above=_read("about_synthetic.txt"),
        below1=_read("education_synthetic.txt"),
        identity=_voyager_identity_json("jane-doe"),
    )

    profile = await build_profile(
        client, "jane-doe", "https://www.linkedin.com/in/jane-doe/", viewee_profile_id="ABC123XYZ"
    )

    assert profile.member_id == "10138250"


@pytest.mark.asyncio
async def test_voyager_backfill_never_overrides_activity_feed_values():
    """When the activity feed DOES recover a name/headline, the Voyager
    backfill must not stomp on it, even if Voyager reports a different
    value -- the activity-feed path is left completely untouched."""
    activity_with_name = (
        '0:["$","div",null,{"aria-label":"Jane Doe Premium Profile 3rd+","children":["$L1"]}]\n'
        '1:["$","span",null,{"textProps":{"children":["Backend Engineer @Example"]}}]\n'
    )
    client = FakeClient(
        activity=activity_with_name,
        above=_read("about_synthetic.txt"),
        below1=_read("education_synthetic.txt"),
        identity=_voyager_identity_json("jane-doe"),  # would report "Jane Doe" / "Voyager Headline"
    )

    profile = await build_profile(
        client, "jane-doe", "https://www.linkedin.com/in/jane-doe/", viewee_profile_id="ABC123XYZ"
    )

    assert profile.name == "Jane Doe"
    assert profile.headline == "Backend Engineer @Example"
    # Untouched by the backfill -- still the activity-feed default.
    assert profile.confidence.name == "unverified"
    assert profile.confidence.headline == "unverified"


@pytest.mark.asyncio
async def test_voyager_identity_failure_degrades_gracefully():
    client = FakeClient(
        activity=None,
        above=_read("about_synthetic.txt"),
        below1=_read("education_synthetic.txt"),
        raise_on={"identity": AuthenticationExpiredError("session expired")},
    )

    profile = await build_profile(
        client, "jane-doe", "https://www.linkedin.com/in/jane-doe/", viewee_profile_id="ABC123XYZ"
    )

    assert profile.location is None
    assert profile.confidence.location == "unavailable"
    assert profile.name is None
    assert profile.confidence.name == "unavailable"
    # Rest of the profile is unaffected.
    assert len(profile.education) == 1
