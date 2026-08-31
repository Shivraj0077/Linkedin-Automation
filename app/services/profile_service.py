"""
Orchestration layer: turns a validated vanity name into a Profile.

Updated after the 4th HAR capture confirmed real field structures for
About, Experience, and Education (see ENDPOINT_MAP.md). Each fetch is
still wrapped defensively -- a parse failure or schema change in one
section degrades that section to empty/None rather than failing the
whole request (assignment requirement #20).
"""
import asyncio
import logging
from typing import Optional

from app.linkedin.client import LinkedInClient
from app.linkedin.exceptions import LinkedInApiError, MissingProfileDataError
from app.linkedin.parser import (
    extract_about,
    extract_best_effort_name_and_headline,
    extract_education,
    extract_experience_entries,
    extract_profile_image,
    extract_profile_location,
    extract_voyager_identity_fields,
    extract_skills,
    extract_honors_awards,
    safe_parse_component,
    _walk_literal_strings,
)
from app.linkedin.sdui import extract_profile_id_from_component_refs
from app.models.profile import DataConfidence, Profile, ProfileImage


logger = logging.getLogger(__name__)

_ABOVE_ACTIVITY = "com.linkedin.sdui.generated.profile.dsl.impl.profileCardsAboveActivity"
_ACTIVITY = "com.linkedin.sdui.generated.profile.dsl.impl.profileCardsActivity"
_BELOW_PART1 = "com.linkedin.sdui.generated.profile.dsl.impl.profileCardsBelowActivityPart1WithoutExp"

# LinkedIn's activity-feed component renders a literal collapsed
# placeholder (`children: [false, null]`, ~135 bytes) on some requests
# and full feed content (megabytes) on the very next, otherwise
# identical request -- observed repeatedly, for the same vanity_name
# and session, seconds apart. Any real feed render is many KB+, so a
# response shorter than this is unambiguously the placeholder, never a
# real-but-small feed -- safe to retry on, unlike e.g. "About" coming
# back empty, which can legitimately mean the profile has no About
# section at all.
_EMPTY_ACTIVITY_RESPONSE_MAX_LEN = 500
_ACTIVITY_RETRY_DELAY_SECONDS = 1.5


async def build_profile(
    client: LinkedInClient,
    vanity_name: str,
    normalized_url: str,
    viewee_profile_id: Optional[str] = None,
) -> Profile:
    confidence = DataConfidence()
    profile = Profile(url=normalized_url, vanity_name=vanity_name, confidence=confidence)

    # --- Name / headline (best-effort, from activity feed byline) ---------
    # This remains the weakest field: no HAR (including the 4th capture)
    # contains the initial page-load document where the authoritative
    # top card renders. See ENDPOINT_MAP.md #2.
    activity_raw = None
    try:
        activity_raw = await client.get_component(_ACTIVITY, vanity_name)
        if activity_raw is not None and len(activity_raw) < _EMPTY_ACTIVITY_RESPONSE_MAX_LEN:
            logger.info(
                "Activity fetch for %s looked like the collapsed empty-state "
                "placeholder (%d bytes) -- retrying once",
                vanity_name,
                len(activity_raw),
            )
            await asyncio.sleep(_ACTIVITY_RETRY_DELAY_SECONDS)
            retry_raw = await client.get_component(_ACTIVITY, vanity_name)
            if retry_raw and len(retry_raw) >= _EMPTY_ACTIVITY_RESPONSE_MAX_LEN:
                activity_raw = retry_raw
        name, headline = extract_best_effort_name_and_headline(activity_raw)
        if name:
            profile.name = name
            parts = name.split(" ", 1)
            profile.first_name = parts[0]
            profile.last_name = parts[1] if len(parts) > 1 else None
        else:
            confidence.name = "unavailable"
        if headline:
            profile.headline = headline
        else:
            confidence.headline = "unavailable"
    except LinkedInApiError as exc:
        logger.warning("Activity/name-headline fetch failed for %s: %s", vanity_name, exc)
        confidence.name = "unavailable"
        confidence.headline = "unavailable"

    # --- About + top-skills summary (CONFIRMED, 4th capture) ---------------
    # This call also doubles as our profileId resolution step (see
    # ENDPOINT_MAP.md #6/#11 update): profileCardsAboveActivity's
    # componentKey strings embed the profileId, and this endpoint only
    # requires vanityName -- so a cold request (no viewee_profile_id
    # supplied by the caller) can still bootstrap Experience/Skills.
    resolved_profile_id = viewee_profile_id
    above_raw = None
    try:
        above_raw = await client.get_component(_ABOVE_ACTIVITY, vanity_name)
        about_text, top_skills = extract_about(above_raw)
        profile.about = about_text
        profile.top_skills_summary = top_skills
        if not about_text:
            confidence.about = "unavailable"
        if not resolved_profile_id:
            resolved_profile_id = extract_profile_id_from_component_refs(above_raw)
            if resolved_profile_id:
                profile.profile_id = resolved_profile_id
    except LinkedInApiError as exc:
        logger.warning("aboveActivity fetch failed for %s: %s", vanity_name, exc)
        confidence.about = "unavailable"

    # --- Profile image (deterministic, scoped to this profile) -------------
    try:
        profile.profile_image = extract_profile_image(above_raw, activity_raw, vanity_name)
        if profile.profile_image is None:
            confidence.profile_image = "unavailable"
    except LinkedInApiError as exc:
        logger.warning("Profile image extraction failed for %s: %s", vanity_name, exc)
        confidence.profile_image = "unavailable"

    # --- Location (Voyager fallback -- RSC/SDUI doesn't reliably expose it)-
    # Separate endpoint/response shape from everything above; only used
    # because profileCardsAboveActivity does not reliably surface
    # location (see ENDPOINT_MAP.md #2 for the analogous name/headline
    # gap). Never derived from experience/education/company text.
    identity_raw = None
    try:
        identity_raw = await client.get_profile_identity(vanity_name)
        profile.location = extract_profile_location(identity_raw, vanity_name)
        if not profile.location:
            confidence.location = "unavailable"
    except LinkedInApiError as exc:
        logger.warning("Voyager identity fetch failed for %s: %s", vanity_name, exc)
        confidence.location = "unavailable"

    # --- Name/headline/about/photo backfill from the same Voyager response -
    # The activity-feed byline and aboveActivity About section above are
    # only present when LinkedIn renders that content for this profile
    # in this specific request -- confirmed (by repeated identical
    # requests against the same profile) to legitimately come back empty
    # on one call and populated on the next, both as real 200s, not
    # errors. Voyager carries these fields directly on the profile
    # entity regardless of that variability, so it's used here ONLY to
    # fill in whatever the activity/aboveActivity paths didn't recover
    # -- never to override a value already found there, and neither of
    # those paths is altered.
    if identity_raw:
        voyager_identity = extract_voyager_identity_fields(identity_raw, vanity_name)
        if voyager_identity:
            if not profile.name and voyager_identity.get("name"):
                profile.name = voyager_identity["name"]
                profile.first_name = voyager_identity.get("first_name")
                profile.last_name = voyager_identity.get("last_name")
                confidence.name = "verified"
            if not profile.headline and voyager_identity.get("headline"):
                profile.headline = voyager_identity["headline"]
                confidence.headline = "verified"
            if not profile.about and voyager_identity.get("about"):
                profile.about = voyager_identity["about"]
                confidence.about = "verified"
            if not profile.profile_image and voyager_identity.get("profile_image"):
                image = voyager_identity["profile_image"]
                profile.profile_image = ProfileImage(url=image["url"], root_url=image["root_url"])
                confidence.profile_image = "verified"
            # member_id has no RSC/SDUI source at all (see
            # ENDPOINT_MAP.md) -- Voyager's objectUrn is the only place
            # this codebase can recover it from, so it's set outright
            # rather than gated behind "if not already set".
            if voyager_identity.get("member_id"):
                profile.member_id = voyager_identity["member_id"]

    # --- Education (CONFIRMED, 4th capture) ---------------------------------
    try:
        below1_raw = await client.get_component(_BELOW_PART1, vanity_name)
        profile.education = extract_education(None, raw_text=below1_raw)
        if not profile.education:
            confidence.education = "unavailable"
    except LinkedInApiError as exc:
        logger.warning("Education fetch failed for %s: %s", vanity_name, exc)
        confidence.education = "unavailable"
    # NOTE: profileCardsBelowActivityPart1WithoutExp also contains
    # Certifications/Projects/Volunteer/Connected-accounts section
    # anchors (ENDPOINT_MAP.md #4), but no populated example of any of
    # those was captured for this profile -- they render as empty
    # sections. extract_education's literal-string cadence approach
    # would need a populated example of each before it's safe to build
    # the same kind of positional parser; left as [] rather than
    # guessed at (confidence.certifications/languages = "unavailable"
    # by default -- see models/profile.py).

    # --- Experience (CONFIRMED, 4th capture) --------------------------------
    if resolved_profile_id:
        try:
            exp_raw = await client.get_experience(vanity_name, resolved_profile_id)
            profile.experience = extract_experience_entries(exp_raw)
            if not profile.experience:
                confidence.experience = "unavailable"
        except LinkedInApiError as exc:
            logger.warning("Experience fetch failed for %s: %s", vanity_name, exc)
            confidence.experience = "unavailable"
    else:
        logger.info("Skipping experience fetch for %s: profileId could not be resolved", vanity_name)
        confidence.experience = "unavailable"

    # --- Skills (CONFIRMED, fully paginated) --------------------------------
    try:
        profile.skills = await _fetch_all_skills(client, vanity_name, resolved_profile_id)
        if not profile.skills:
            confidence.skills = "unavailable"
    except LinkedInApiError as exc:
        logger.warning("Skills fetch failed for %s: %s", vanity_name, exc)
        confidence.skills = "unavailable"

    # --- Honors & Awards -----------------------------------------------
    try:
        profile.honors_awards = await _fetch_all_honors(
            client,
            vanity_name,
            resolved_profile_id,
        )
    except LinkedInApiError as exc:
        logger.warning(
            "Honors fetch failed for %s: %s",
            vanity_name,
            exc,
        )

    if not any(
        [profile.name, profile.headline, profile.about, profile.education,
         profile.skills, profile.experience]
    ):
        raise MissingProfileDataError(
            "No profile data could be recovered for this vanity name "
            "(profile may be private, blocked, or the account session "
            "may be invalid)"
        )

    return profile


async def _fetch_all_skills(client, vanity_name: str, profile_id: Optional[str]):
    from app.config import settings
    from app.linkedin.sdui import extract_skills_page

    if not profile_id:
        # See ENDPOINT_MAP.md #12 -- the skills pager requires a
        # profileId (URN fragment). Without a way to resolve this from
        # vanityName alone (not found in any capture -- see
        # ENDPOINT_MAP.md #2/#6 identity-resolution gap), skills
        # pagination cannot run for a cold vanityName-only request.
        logger.info("Skipping skills fetch for %s: no profile_id available", vanity_name)
        return []

    all_pages_raw = []
    start = 0
    count = 10
    for _ in range(settings.max_skills_pages):
        raw = await client.get_skills_page(vanity_name, profile_id, start, count)
        all_pages_raw.append(raw)

        doc = safe_parse_component(raw, "skills-page")
        page_items = 0 if doc is None else len(extract_skills_page(doc))
        if page_items < count:
            break
        start += count

    return extract_skills(all_pages_raw)

async def _fetch_all_honors(
    client,
    vanity_name: str,
    profile_id: Optional[str],
):
    from app.config import settings

    if not profile_id:
        logger.info(
            "Skipping honors fetch for %s: no profile_id available",
            vanity_name,
        )
        return []

    pages = []

    start = 0
    count = 10

    for _ in range(settings.max_skills_pages):
        raw = await client.get_honors_page(
            vanity_name,
            profile_id,
            start,
            count,
        )

        if not raw:
            break

        pages.append(raw)

        # The captured honors response exposes partial-page state.
        # We conservatively stop when the decoded page contains fewer
        # than the requested number of rendered items.
        doc = safe_parse_component(raw, "honors-page")

        if doc is None:
            break

        literals = list(_walk_literal_strings(doc))

        item_count = sum(
            1
            for value in literals
            if value.strip().lower().startswith("issued by ")
        )

        if item_count < count:
            break

        start += count

    return extract_honors_awards(pages)
