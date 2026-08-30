"""
Top-level profile assembly: takes raw RSC response bodies for each
fetched component and produces the models.profile.Profile object.

Every extraction call is wrapped defensively — a parse failure in one
section must not take down the whole profile response (assignment
requirement #20). Sections with no confirmed schema (Experience, About,
top-card name/headline/photo, Part5, Part7) return empty/None and are
reflected in `Profile.confidence`, not silently guessed at.
"""
import logging
from typing import Any, Dict, List, Optional

from app.linkedin.exceptions import RscDecodeError
from app.linkedin.rsc import parse_rsc_stream
from app.linkedin.sdui import (
    extract_name_and_headline_from_activity,
    extract_skills_page,
    find_section_anchor_ids,
)
from app.models.profile import EducationEntry, ExperienceEntry, SkillEntry

logger = logging.getLogger(__name__)


def safe_parse_component(raw_text: Optional[str], label: str):
    """Returns an RscDocument or None. Logs (header/label only, never
    body content) and swallows RscDecodeError so callers can degrade
    gracefully per-section."""
    if not raw_text:
        return None
    try:
        return parse_rsc_stream(raw_text)
    except RscDecodeError as exc:
        logger.warning("RSC decode failed for section '%s': %s", label, exc)
        return None


def extract_education(document, raw_text: Optional[str] = None) -> List["EducationEntry"]:
    """CONFIRMED shape (4th HAR capture; see ENDPOINT_MAP.md #4, updated).
    Verified against a real 2-entry response: LinkedIn renders each
    education block as a fixed sequence of 4 literal strings, in order:

        1. Institution name     e.g. "L.D. College of Engineering"
        2. "<Degree>, <Field>"  e.g. "Bachelor of Engineering, Information Technology"
        3. "<DateRange>"        e.g. "Jul 2024 \u2013 Jul 2028"
        4. Grade line           e.g. "SPI : 8.33" (kept as raw grade text, not parsed further)

    `raw_text` is the preferred input now (positional literal-string
    parsing, same approach as extract_experience) -- pass the decoded
    RSC text directly. The old `document`-based (RscDocument) path is
    kept as a fallback for callers still on the earlier capture, and
    returns date-ranges-only entries with institution=None, matching
    its documented lower-confidence behaviour from before this update.
    """
    from app.models.profile import EducationEntry

    if raw_text:
        from app.linkedin.sdui import _CHILDREN_LITERAL_RE, _DATE_RANGE_RE

        literals = [v for v in _CHILDREN_LITERAL_RE.findall(raw_text) if not v.startswith("$")]
        literals = [s for s in literals if s.strip() != "Education"]

        entries: List[EducationEntry] = []
        i = 0
        while i + 2 < len(literals):
            institution = literals[i]
            degree_line = literals[i + 1]
            date_line = literals[i + 2]

            if not _DATE_RANGE_RE.match(date_line):
                break

            grade = None
            consumed = 3
            if i + 3 < len(literals) and not _DATE_RANGE_RE.match(literals[i + 3]):
                # Heuristic: a 4th literal that isn't itself a date range
                # (i.e. not the *next* entry's date) is this entry's
                # grade/notes line. If it IS date-shaped, it must belong
                # to the next entry (rare: entry with no grade line).
                nxt = literals[i + 3]
                if not (i + 4 < len(literals) and _looks_like_institution_pair(nxt, literals[i + 4] if i + 4 < len(literals) else "")):
                    grade = nxt
                    consumed = 4

            degree, _, field_of_study = degree_line.partition(", ")
            from app.utils.dates import split_date_range
            start_date, end_date = split_date_range(date_line)

            entries.append(
                EducationEntry(
                    institution=institution.strip() if institution else None,
                    degree=degree.strip() if degree else None,
                    field_of_study=field_of_study.strip() if field_of_study else None,
                    start_date=start_date,
                    end_date=end_date,
                )
            )
            i += consumed

        return entries

    # --- fallback path for the earlier (pre-4th-capture) fixture ----------
    if document is None:
        return []
    anchors = find_section_anchor_ids(document, "educationTopLevelSection")
    if not anchors:
        return []
    from app.utils.dates import split_date_range

    out: List[EducationEntry] = []
    for weight, text in _walk_text_nodes(document):
        if weight != "normal":
            continue
        if text.strip().lower() in _SECTION_HEADER_WORDS:
            continue
        start, end = split_date_range(text)
        if start or end:
            out.append(EducationEntry(start_date=start, end_date=end))
    return out


def _looks_like_institution_pair(a: str, b: str) -> bool:
    """Best-effort disambiguator: true if (a, b) plausibly reads as
    (next institution name, next degree line) rather than (this
    entry's grade line, something else). Used only to avoid
    mis-consuming a 4th slot in rare cases with no grade line."""
    return ", " in b or "Bachelor" in b or "Master" in b or "Diploma" in b


_SECTION_HEADER_WORDS = {
    "education", "certifications", "certification", "projects", "project",
    "volunteering", "volunteer experience", "connected accounts", "skills",
    "languages", "organizations", "recommendations", "honors & awards",
    "honors and awards", "publications", "patents", "courses", "test scores",
}


def extract_skills(pages_raw: List[str]) -> List[SkillEntry]:
    """CONFIRMED — see ENDPOINT_MAP.md #12."""
    all_skills: List[SkillEntry] = []
    seen = set()
    for raw in pages_raw:
        doc = safe_parse_component(raw, "skills-page")
        if doc is None:
            continue
        for entry in extract_skills_page(doc):
            key = entry["name"]
            if key in seen:
                continue
            seen.add(key)
            all_skills.append(SkillEntry(name=entry["name"], context=entry.get("context")))
    return all_skills


def extract_experience_entries(raw_text: Optional[str]) -> List[ExperienceEntry]:
    """CONFIRMED — see ENDPOINT_MAP.md #11 (updated). Wraps
    sdui.extract_experience()'s raw dicts into typed models with
    normalized dates."""
    from app.linkedin.sdui import extract_experience as _extract_experience
    from app.utils.dates import normalize_date

    out: List[ExperienceEntry] = []
    for raw in _extract_experience(raw_text):
        out.append(
            ExperienceEntry(
                title=raw.get("title"),
                company=raw.get("company"),
                employment_type=raw.get("employment_type"),
                location=raw.get("location"),
                work_type=raw.get("work_type"),
                start_date=normalize_date(raw.get("start_date_raw")),
                end_date=normalize_date(raw.get("end_date_raw")),
                duration=raw.get("duration"),
                is_current=bool(raw.get("is_current")),
            )
        )
    return out


_VOYAGER_PROFILE_TYPE = "com.linkedin.voyager.dash.identity.profile.Profile"


def _voyager_entity_by_urn(included: List[Any], urn: str) -> Optional[Dict[str, Any]]:
    for entity in included:
        if isinstance(entity, dict) and entity.get("entityUrn") == urn:
            return entity
    return None


def _voyager_profile_entity_matches(entity: Any, vanity_name: str) -> bool:
    if not isinstance(entity, dict):
        return False
    if entity.get("$type") != _VOYAGER_PROFILE_TYPE:
        return False
    public_identifier = entity.get("publicIdentifier")
    return (
        isinstance(public_identifier, str)
        and public_identifier.lower() == vanity_name.lower()
    )


def _find_voyager_profile_entity(
    data: Dict[str, Any], included: List[Any], vanity_name: str
) -> Optional[Dict[str, Any]]:
    """Structurally locate the Profile entity for `vanity_name` within a
    decoded Voyager /voyager/api/identity/dash/profiles response.

    The response is restli's normalized shape: `data` points at the
    requested entity via a `*elements` urn reference into the flat
    `included` list. That reference is followed first, but its target
    is only trusted if the resolved entity's own `publicIdentifier`
    actually matches the requested vanity_name -- if the response ever
    contains more than one profile-typed object in `included` (e.g. a
    differently-shaped decoration), a mismatched target is not used;
    instead every entity in `included` is checked structurally by its
    own publicIdentifier field, never by first-match or string search.
    """
    top = data.get("data")
    if isinstance(top, dict):
        elements = top.get("*elements")
        if isinstance(elements, list) and elements and isinstance(elements[0], str):
            target = _voyager_entity_by_urn(included, elements[0])
            if _voyager_profile_entity_matches(target, vanity_name):
                return target

    for entity in included:
        if _voyager_profile_entity_matches(entity, vanity_name):
            return entity

    return None


def extract_profile_location(
    raw_text: Optional[str], vanity_name: str
) -> Optional[str]:
    """Deterministic profile-level location extraction from LinkedIn's
    Voyager identity-profile endpoint (see
    app.linkedin.client.LinkedInClient.get_profile_identity).

    Used only as a fallback: the RSC/SDUI profileCardsAboveActivity
    response does not reliably expose location (see ENDPOINT_MAP.md #2
    for the equivalent name/headline gap). This is a wholly separate
    endpoint/response shape and does not touch or alter any RSC-based
    extraction already in place.

    Structural algorithm only -- never a substring/regex search for
    "location" over the raw response body:

      1. Locate the Profile entity belonging to `vanity_name` (see
         `_find_voyager_profile_entity`).
      2. If that entity's `locationName` field is a non-empty string,
         return it.
      3. Else, if it has `geoLocation.geoUrn`, resolve that urn against
         another entity in `included` and return its
         `defaultLocalizedName` field, if present.
      4. Otherwise return None.

    Never falls back to experience.location, education, company,
    activity posts, or any other section -- those are separate,
    unrelated data and would misattribute a company's or a former
    employer's location to the profile.
    """
    import json

    if not raw_text:
        return None

    try:
        data = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            "Voyager identity response for '%s' was not valid JSON", vanity_name
        )
        return None

    if not isinstance(data, dict):
        return None

    included = data.get("included")
    if not isinstance(included, list):
        return None

    profile_entity = _find_voyager_profile_entity(data, included, vanity_name)
    if profile_entity is None:
        return None

    location_name = profile_entity.get("locationName")
    if isinstance(location_name, str) and location_name.strip():
        return location_name.strip()

    geo_location = profile_entity.get("geoLocation")
    if isinstance(geo_location, dict):
        geo_urn = geo_location.get("geoUrn")
        if isinstance(geo_urn, str) and geo_urn:
            geo_entity = _voyager_entity_by_urn(included, geo_urn)
            if isinstance(geo_entity, dict):
                localized_name = geo_entity.get("defaultLocalizedName")
                if isinstance(localized_name, str) and localized_name.strip():
                    return localized_name.strip()

    return None


def extract_profile_image(
    above_activity_raw: Optional[str],
    activity_raw: Optional[str],
    vanity_name: str,
):
    """Deterministic profile-image extraction. See
    sdui.extract_profile_image_asset for the full algorithm: it
    traverses the decoded profileCardsAboveActivity component tree
    first, then falls back to the profileCardsActivity feed -- scoping
    the result to `vanity_name` via each candidate actor block's own
    Navigate URL so a commenter's photo or a company logo can never be
    selected. Returns a ProfileImage, or None if no image field could
    be recovered without guessing at CDN path/id/timestamp/token
    values LinkedIn did not supply.
    """
    from app.linkedin.sdui import extract_profile_image_asset
    from app.models.profile import ProfileImage

    asset = extract_profile_image_asset(above_activity_raw, activity_raw, vanity_name)
    if asset is None:
        return None

    note = None
    if asset.get("source") == "activity_feed":
        note = (
            "Recovered from the activity-feed actor row, not the "
            "profile's own top-card/avatar component (never observed "
            "populated in any capture analyzed -- see ENDPOINT_MAP.md #2)."
        )

    return ProfileImage(
        url=asset.get("url"),
        root_url=asset.get("root_url"),
        note=note,
    )


def extract_about(raw_text: Optional[str]):
    """CONFIRMED — see ENDPOINT_MAP.md #3 (updated). Returns
    (about_text, top_skills_list)."""
    from app.linkedin.sdui import extract_about_and_top_skills

    return extract_about_and_top_skills(raw_text)

def _walk_literal_strings(document):
    """
    Yield literal text values from an RSC document.
    """

    def walk(value):
        if isinstance(value, str):
            yield value

        elif isinstance(value, list):
            for item in value:
                yield from walk(item)

        elif isinstance(value, dict):
            for item in value.values():
                yield from walk(item)

    yield from walk(document.resolve("0"))

def extract_honors_awards(
    pages_raw: List[str],
) -> List["HonorAwardEntry"]:
    """
    Parse LinkedIn Honors & Awards pagination responses.

    Captured response structure renders each item as:

        title
        Issued by <issuer> · <date>

    Example captured from the HAR:

        Comeback award - Economic Times
        Issued by Economic Times · Jan 2016

    The values above are examples only and are never hardcoded.
    """

    from app.models.profile import HonorAwardEntry
    from app.linkedin.sdui import _CHILDREN_LITERAL_RE

    results = []
    seen = set()

    for raw in pages_raw:
        if not raw:
            continue

        literals = []

        for value in _CHILDREN_LITERAL_RE.findall(raw):
            if value.startswith("$"):
                continue

            value = value.strip()

            if not value:
                continue

            if value in {
                "Honors & awards",
                "Honors and awards",
            }:
                continue

            literals.append(value)

        i = 0

        while i < len(literals):
            title = literals[i]

            if i + 1 < len(literals):
                metadata = literals[i + 1]

                if metadata.lower().startswith("issued by "):
                    issuer = metadata[len("Issued by "):]

                    issue_date = None

                    if " · " in issuer:
                        issuer, issue_date = issuer.rsplit(
                            " · ",
                            1,
                        )

                    key = (
                        title.lower(),
                        issuer.lower(),
                        (issue_date or "").lower(),
                    )

                    if key not in seen:
                        seen.add(key)

                        results.append(
                            HonorAwardEntry(
                                title=title,
                                issuer=issuer.strip() or None,
                                issue_date=issue_date.strip()
                                if issue_date
                                else None,
                            )
                        )

                    i += 2
                    continue

            i += 1

    return results


def extract_best_effort_name_and_headline(activity_raw: Optional[str]):
    """Returns (name, headline), either of which may be None. See
    ENDPOINT_MAP.md #2 and sdui.extract_name_and_headline_from_activity
    for why this is a fallback, not a primary source."""
    return extract_name_and_headline_from_activity(activity_raw)
