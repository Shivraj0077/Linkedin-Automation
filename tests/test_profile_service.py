import os

import pytest

from app.linkedin.exceptions import AuthenticationExpiredError, MissingProfileDataError
from app.services.profile_service import build_profile

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


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
                 skills_pages=None, identity=None, raise_on=None):
        self._activity = activity
        self._above = above
        self._below1 = below1
        self._experience = experience
        self._skills_pages = skills_pages or []
        self._identity = identity
        self._raise_on = raise_on or {}

    async def get_component(self, component_id, vanity_name, is_self_view=False, extra_payload=None):
        if "profileCardsActivity" in component_id:
            if "activity" in self._raise_on:
                raise self._raise_on["activity"]
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
