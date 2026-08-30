"""
Section-specific extraction over a parsed RscDocument (app.linkedin.rsc).

Every function here is tied to a specific, confirmed observation in the
HAR captures — see /ENDPOINT_MAP.md at the repo root for exactly which
component each of these was pulled from and how confident the shape is.
Where the HAR did not capture enough to confirm a field shape, the
function returns an empty result rather than guessing (see
`extract_experience`, `extract_education`, `extract_top_card` below).

Design note: LinkedIn's internal component tree WILL change over time.
Each extractor is independent and defensive (try/except around each
node's field access) precisely so that a schema change in one section
degrades that one section to an empty list, instead of raising and
killing the whole profile response.
"""
import re
from typing import Any, Dict, List, Optional

from app.linkedin.rsc import RscDocument

_ENDORSED_RE = re.compile(r"^Endorsed by\b", re.IGNORECASE)


def _walk_text_nodes(document: RscDocument, root_id: str = "0"):
    """Yield (font_weight, text) for every text-leaf node reachable from
    the document root, in document order. This matches the confirmed
    shape:
    ["$", "$L<id>", null, {"textProps": {"fontWeight": "...", "children": ["text"]}}]
    Nodes that don't match this exact shape are skipped, not guessed at.

    IMPORTANT: walked from the single root id ("0" in every capture
    analyzed), not from every id in the document — most non-root ids
    are shared sub-references pointed to by multiple parents, so
    resolving from each of them independently double/triple-counts the
    same rendered content. Confirmed by comparing output against the
    known count=10 page size in the skills pagination capture.
    """
    if root_id not in document.ids():
        # Defensive fallback: root id naming could change; scanning
        # every id is safer (if slower/duplicative) than yielding nothing.
        for node_id in document.ids():
            yield from _walk_value(document.resolve(node_id))
        return
    yield from _walk_value(document.resolve(root_id))


def _walk_value(value: Any):
    if isinstance(value, list):
        if (
            len(value) == 4
            and value[0] == "$"
            and isinstance(value[3], dict)
            and "textProps" in value[3]
        ):
            text_props = value[3]["textProps"]
            children = text_props.get("children")
            weight = text_props.get("fontWeight")
            if isinstance(children, list) and children and isinstance(children[0], str):
                yield weight, children[0]
        for v in value:
            yield from _walk_value(v)
    elif isinstance(value, dict):
        for v in value.values():
            yield from _walk_value(v)


def extract_skills_page(document: RscDocument) -> List[Dict[str, Optional[str]]]:
    """CONFIRMED shape for name + context (see ENDPOINT_MAP.md #12).

    Verified against the real capture: a "bold" text node is the skill
    name, and the very next "normal" text node in document order is the
    job/context line under which the skill is listed (e.g. "Technical
    Lead at VYOMA LEARNING SYSTEMS Pvt. Ltd.") — NOT an endorsement
    count as originally assumed. Re-tested against rsc2_19_skills.txt:
    walking from the tree root never surfaced an "Endorsed by ..." node
    adjacent to a skill name, even though that text exists elsewhere in
    the same raw payload (see extract_unattributed_endorsement_texts) —
    so endorsement_count is intentionally left unset here rather than
    guessed at by proximity.
    """
    results: List[Dict[str, Optional[str]]] = []
    pending_name: Optional[str] = None

    for weight, text in _walk_text_nodes(document):
        if weight == "bold":
            if pending_name is not None:
                results.append({"name": pending_name, "context": None})
            pending_name = text
        elif weight == "normal" and pending_name is not None:
            results.append({"name": pending_name, "context": text})
            pending_name = None

    if pending_name is not None:
        results.append({"name": pending_name, "context": None})

    return results


def extract_unattributed_endorsement_texts(document: RscDocument) -> List[str]:
    """Best-effort, NOT attributed to a specific skill. "Endorsed by ..."
    text nodes exist in the raw skills-pagination payload but were not
    reachable from the tree root alongside their skill name in the
    capture analyzed — see extract_skills_page docstring. Exposed
    separately so callers can surface the raw counts without falsely
    claiming which skill each belongs to.
    """
    out = []
    for node_id in document.ids():
        raw = document.raw(node_id)
        s = str(raw)
        for m in re.finditer(r'"([^"]{0,5}Endorsed by [^"]{0,80})"', s):
            if m.group(1) not in out:
                out.append(m.group(1))
    return out


def find_section_anchor_ids(document: RscDocument, observability_id: str) -> List[str]:
    """Find node ids whose raw payload contains the given
    observabilityIdentifier (e.g.
    'com.linkedin.sdui.impl.profile.components.educationTopLevelSection').
    Confirmed present in profileCardsBelowActivityPart1WithoutActivity
    responses (ENDPOINT_MAP.md #4) as a marker of section boundaries,
    but the HAR did not capture enough of the surrounding tree to
    confirm field-level structure (institution/degree/dates) below that
    anchor — see extract_education().
    """
    matches = []
    for node_id in document.ids():
        raw = document.raw(node_id)
        if raw is not None and observability_id in str(raw):
            matches.append(node_id)
    return matches


_BYLINE_RE = re.compile(r'"aria-label":"([A-Za-z][^"]{2,60}?) Premium Profile[^"]*"')
_CHILDREN_RE = re.compile(r'"children":\["([^"]{2,150})"\]')

_NAME_LOADING_STATE_RE = re.compile(
    r'"id":"profile_name_loading_state"\}\},"namespace":"LoadingNamespace"\},'
    r'"value":\{"\$case":"stringValue","stringValue":"([^"]*)"'
)
_HEADLINE_LOADING_STATE_RE = re.compile(
    r'"id":"profile_headline_loading_state"\}\},"namespace":"LoadingNamespace"\},'
    r'"value":\{"\$case":"stringValue","stringValue":"([^"]*)"'
)


def extract_name_and_headline_from_activity(raw_text: Optional[str]):
    """Best-effort, from the activity feed component (no authoritative
    top-card render is present in any HAR analyzed; see
    ENDPOINT_MAP.md #2). Only exists at all if the profile has recent
    activity that renders an actor byline.

    Primary source: each feed-actor block embeds the viewed profile's
    name/headline directly as SetState actions
    (profile_name_loading_state / profile_headline_loading_state) used
    to hydrate the top card client-side. This is present regardless of
    Premium/badge status and is preferred over the aria-label scrape.

    Fallback: the aria-label byline ("<Name> Premium Profile ...")
    with the first non-reference `children` string after it as the
    headline. Only matches profiles whose badge text is literally
    "Premium Profile" (e.g. misses "Executive Top Voice" and other
    badge variants), so it's kept as a fallback rather than removed.
    """
    if not raw_text:
        return None, None

    name_m = _NAME_LOADING_STATE_RE.search(raw_text)
    headline_m = _HEADLINE_LOADING_STATE_RE.search(raw_text)
    if name_m or headline_m:
        name = name_m.group(1).strip() if name_m else None
        headline = headline_m.group(1).strip() if headline_m else None
        return name or None, headline or None

    m = _BYLINE_RE.search(raw_text)
    if not m:
        return None, None
    name = m.group(1)
    window = raw_text[m.end(): m.end() + 4000]
    headline = None
    for cm in _CHILDREN_RE.finditer(window):
        val = cm.group(1)
        if val.startswith("$"):
            continue
        headline = val
        break
    return name, headline


_PROFILE_URL_VANITY_RE = re.compile(r"linkedin\.com/in/([^/?]+)/?", re.IGNORECASE)


def _vanity_from_profile_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    m = _PROFILE_URL_VANITY_RE.search(url)
    return m.group(1) if m else None


def _iter_action_lists(value: Any):
    """Recursively yield every 'actions' list reachable from a resolved
    RSC node. Each such list is the group of SetState/Navigate actions
    fired together by a single clickable actor row (byline, comment
    author, reactor, ...) -- walking structurally (dict/list traversal
    by key name) rather than regex over raw text keeps each group's
    fields (name/headline/photo/navigate-url) correctly associated
    with each other and never crosses actor boundaries."""
    if isinstance(value, dict):
        actions = value.get("actions")
        if isinstance(actions, list):
            yield actions
        for v in value.values():
            yield from _iter_action_lists(v)
    elif isinstance(value, list):
        for v in value:
            yield from _iter_action_lists(v)


def _extract_actor_identity_block(actions: List[Any]) -> Optional[Dict[str, Any]]:
    """Given one 'actions' list, pull out the actor-identity SetState
    fields (name/headline/photo-asset) and the Navigate action's target
    profile URL, all confirmed (from real captures) to be emitted
    together for whichever single actor that clickable row represents.
    Returns None if this actions list carries none of those state ids
    (i.e. it isn't an actor-identity block at all)."""
    name = None
    headline = None
    photo_asset = None
    profile_url = None
    found = False

    for action in actions:
        if not isinstance(action, dict):
            continue
        action_type = action.get("$type")

        if action_type == "proto.sdui.actions.core.SetState":
            state = action.get("value", {}).get("state", {})
            state_id = (
                state.get("key", {})
                .get("key", {})
                .get("value", {})
                .get("id")
            )
            state_value = state.get("value", {})
            case = state_value.get("$case")

            if state_id == "profile_name_loading_state" and case == "stringValue":
                name = state_value.get("stringValue")
                found = True
            elif state_id == "profile_headline_loading_state" and case == "stringValue":
                headline = state_value.get("stringValue")
                found = True
            elif state_id == "profile_photo_loading_state" and case == "imageAssetValue":
                photo_asset = state_value.get("imageAssetValue")
                found = True

        elif action_type == "proto.sdui.actions.core.Navigate":
            content = action.get("value", {}).get("content", {})
            if content.get("$case") == "url":
                url_value = content.get("url", {}).get("urlValue", {})
                if url_value.get("$case") == "url":
                    profile_url = url_value.get("url")

    if not found:
        return None
    return {
        "name": name,
        "headline": headline,
        "photo_asset": photo_asset,
        "profile_url": profile_url,
    }


def _build_image_from_client_image_asset(asset: Any) -> Optional[Dict[str, Optional[str]]]:
    """Build a complete image URL from a resolved ClientImageAsset node,
    using only fields LinkedIn actually supplied -- never inventing a
    CDN path, media id, timestamp, expiry, version, or `t` token.

    Two shapes observed/used across this codebase's `$case`/same-named-
    field convention (see e.g. Navigate's "$case":"url","url":{...} and
    SetState's "$case":"stringValue","stringValue":...):
      - {"$case": "url", "url": "<complete CDN URL>"} -> returned as-is.
      - {"$case": "renderPayload", "renderPayload": {"rootUrl": ...,
         "imageRenditions": [{"width":.., "suffixUrl": ".."}, ...]}}
        -> rootUrl + suffixUrl of the highest-resolution rendition
        supplied, concatenated exactly (no separator invented; the
        suffixUrl fragments observed already start mid-path).
    Returns None if the required fields aren't present.
    """
    if not isinstance(asset, dict):
        return None
    source = asset.get("source")
    if not isinstance(source, dict):
        return None

    case = source.get("$case")

    if case == "url":
        url = source.get("url")
        if isinstance(url, str) and url:
            return {"url": url, "root_url": None}
        return None

    if case == "renderPayload":
        payload = source.get("renderPayload")
        if not isinstance(payload, dict):
            return None
        root_url = payload.get("rootUrl")
        renditions = payload.get("imageRenditions")
        if not isinstance(root_url, str) or not root_url or not isinstance(renditions, list):
            return None

        valid = [
            r for r in renditions
            if isinstance(r, dict) and isinstance(r.get("suffixUrl"), str) and r.get("suffixUrl")
        ]
        if not valid:
            return None

        best = max(valid, key=lambda r: r.get("width") or 0)
        return {"url": root_url + best["suffixUrl"], "root_url": root_url}

    return None


def extract_profile_image_asset(
    above_activity_raw: Optional[str],
    activity_raw: Optional[str],
    vanity_name: str,
) -> Optional[Dict[str, Optional[str]]]:
    """Deterministic profile-image extraction, scoped to the requested
    profile. Returns {"url", "root_url", "source"} or None.

    Step 1 -- profileCardsAboveActivity: traverse the resolved
    component tree for a ClientImageAsset whose sibling "shape" is
    "circle" (the only structural signal, observed consistently across
    captures, that distinguishes an avatar render from unrelated post
    /company-logo media rendered elsewhere in the same response, which
    carry no "shape" key at all). Not seen populated in any capture
    analyzed for this profile's own header (see ENDPOINT_MAP.md #2 --
    no HAR ever captured the authoritative top-card render), but
    implemented in case a future/different response shape includes it
    here, per the required traversal order.

    Step 2 -- profileCardsActivity fallback: every feed-actor row
    (byline author, commenter, reactor, ...) emits an identical-shaped
    SetState/Navigate action group (see _extract_actor_identity_block).
    These are NOT unique to the requested profile, so each candidate
    block's own Navigate URL is matched against vanity_name before its
    photo is ever trusted -- this is what prevents a commenter's photo
    or a company page's logo from being selected.
    """
    from app.linkedin.rsc import parse_rsc_stream
    from app.linkedin.exceptions import RscDecodeError

    # --- Step 1: profileCardsAboveActivity -----------------------------
    if above_activity_raw:
        try:
            above_doc = parse_rsc_stream(above_activity_raw)
        except RscDecodeError:
            above_doc = None
        if above_doc is not None:
            # Not every node is reachable purely by resolving from "0" --
            # RSC flight streams contain many independently-rooted
            # top-level chunks (see rsc.find_all_text, which walks every
            # id for the same reason) -- so each id is checked as its
            # own traversal root.
            for node_id in above_doc.ids():
                found = _find_circle_image_asset(above_doc.resolve(node_id))
                if found is not None:
                    image = _build_image_from_client_image_asset(found)
                    if image is not None:
                        return {**image, "source": "above_activity"}

    # --- Step 2: profileCardsActivity, scoped to vanity_name -----------
    if activity_raw:
        try:
            activity_doc = parse_rsc_stream(activity_raw)
        except RscDecodeError:
            activity_doc = None
        if activity_doc is not None:
            for node_id in activity_doc.ids():
                for actions in _iter_action_lists(activity_doc.resolve(node_id)):
                    block = _extract_actor_identity_block(actions)
                    if block is None or block["photo_asset"] is None:
                        continue
                    if _vanity_from_profile_url(block["profile_url"]) != vanity_name:
                        continue
                    image = _build_image_from_client_image_asset(block["photo_asset"])
                    if image is not None:
                        return {**image, "source": "activity_feed"}

    return None


def _find_circle_image_asset(value: Any) -> Optional[Dict[str, Any]]:
    """Structurally find the first ClientImageAsset node (by "$type")
    whose containing object marks it as a circular render ("shape":
    "circle") -- LinkedIn's avatar-specific rendering, as opposed to
    rectangular post/article/company-logo media found elsewhere in the
    same tree."""
    if isinstance(value, dict):
        if value.get("$type") == "ClientImageAsset" and _sibling_shape_is_circle(value):
            return value
        for v in value.values():
            found = _find_circle_image_asset(v)
            if found is not None:
                return found
    elif isinstance(value, list):
        for v in value:
            found = _find_circle_image_asset(v)
            if found is not None:
                return found
    return None


def _sibling_shape_is_circle(image_asset_value: Dict[str, Any]) -> bool:
    # The "shape" field observed in captures sits alongside the
    # ClientImageAsset dict itself (same parent object), e.g.
    # {"$type": "ClientImageAsset", "source": {...}, "shape": "circle", ...}
    return image_asset_value.get("shape") == "circle"


_CHILDREN_LITERAL_RE = re.compile(r'"children":\["([^"]{1,300})"\]')
_DATE_RANGE_RE = re.compile(
    r"^([A-Za-z]{3,9}\.?\s+\d{4}|\d{4})\s*(?:[\u2013\u2014-]\s*(Present|[A-Za-z]{3,9}\.?\s+\d{4}|\d{4}))?"
    r"(?:\s*\u00b7\s*(.+))?$"
)


_KNOWN_WORK_TYPES = {"remote", "hybrid", "on-site", "onsite"}
_TOP_SKILLS_HEADER = "top skills"
_ABOUT_HEADER = "about"
_FEATURED_HEADER = "featured"


def extract_about_and_top_skills(raw_text: Optional[str]):
    """CONFIRMED (4th HAR capture): profileCardsAboveActivity DOES
    contain literal About text and a bullet-separated "Top skills"
    summary line, when the About section is expanded in the source
    page view -- the earlier capture we analyzed had it collapsed, so
    only component references were visible (see ENDPOINT_MAP.md #3,
    now updated). Verified sequence of literal `children` strings:

        "About"
        "Top skills"
        "<skill> \u2022 <skill> \u2022 ..."
        "Featured"
        "<about paragraph text>"

    i.e. the About paragraph is NOT the string immediately after the
    "About" header -- it comes after the top-skills line and the
    "Featured" header, due to how this component's stream is ordered.
    This walks literals in order and picks: the bullet-separated line
    right after "Top skills" as top_skills, and the first sufficiently
    long non-header string after that as the about text. Returns
    (about_text, top_skills_list) -- either may be None/[].
    """
    if not raw_text:
        return None, []

    literals = [v for v in _CHILDREN_LITERAL_RE.findall(raw_text) if not v.startswith("$")]

    top_skills: List[str] = []
    about_text: Optional[str] = None

    for idx, s in enumerate(literals):
        if s.strip().lower() == _TOP_SKILLS_HEADER and idx + 1 < len(literals):
            candidate = literals[idx + 1]
            if "\u2022" in candidate:
                top_skills = [p.strip() for p in candidate.split("\u2022") if p.strip()]

    # About paragraph: first literal long enough to be prose, that isn't
    # itself a header or the bullet-separated skills line.
    for s in literals:
        s_norm = s.strip().lower()
        if s_norm in (_ABOUT_HEADER, _TOP_SKILLS_HEADER, _FEATURED_HEADER):
            continue
        if "\u2022" in s:
            continue
        if len(s) >= 40:
            about_text = s
            break

    return about_text, top_skills


def _looks_like_title(s: str) -> bool:
    """Heuristic used only to decide whether the 4th literal in an
    experience block is a location (skip) or actually the *next*
    entry's title (don't consume it as a location). A location has a
    comma (city, region, country), a ' \u00b7 ' work-type suffix, or is one
    of LinkedIn's bare work-type labels (e.g. "Remote") with no comma at
    all -- confirmed in the real capture. A title has none of these."""
    s_norm = s.strip().lower()
    if s_norm in _KNOWN_WORK_TYPES:
        return False
    return not ("," in s or " \u00b7 " in s)


def extract_experience(raw_text: Optional[str]) -> List[Dict[str, Optional[str]]]:
    """CONFIRMED shape (see ENDPOINT_MAP.md #11, updated after the 4th
    HAR capture returned a populated profileCardsExperienceOnly body).
    Verified against a real 3-entry response: LinkedIn renders each
    experience block as a fixed sequence of literal text strings in
    document order:

        1. Job title                          e.g. "Technical Lead"
        2. "<Company> \u00b7 <EmploymentType>"     e.g. "Kumi Studio \u00b7 Internship"
        3. "<DateRange> \u00b7 <Duration>"          e.g. "Jul 2026 - Present \u00b7 2 mos"
        4. "<Location>" or "<Location> \u00b7 <WorkType>"   (optional)

    This reads the raw decoded RSC text directly (not the resolved
    node tree) using that fixed cadence, skipping the literal
    "Experience" section header. Each date-line is validated against
    _DATE_RANGE_RE before being accepted as slot 3 -- if a future
    profile's rendering doesn't match this cadence, extraction stops
    at that point instead of misattributing the remaining text.
    """
    if not raw_text:
        return []

    literals = [v for v in _CHILDREN_LITERAL_RE.findall(raw_text) if not v.startswith("$")]
    literals = [s for s in literals if s.strip() != "Experience"]

    entries: List[Dict[str, Optional[str]]] = []
    i = 0
    while i + 2 < len(literals):
        title = literals[i]
        company_line = literals[i + 1]
        date_line = literals[i + 2]

        if not _DATE_RANGE_RE.match(date_line):
            break

        location = None
        consumed = 3
        if i + 3 < len(literals) and not _looks_like_title(literals[i + 3]):
            location = literals[i + 3]
            consumed = 4

        company, _, employment_type = company_line.partition(" \u00b7 ")
        date_range, _, duration = date_line.partition(" \u00b7 ")
        start_date_raw, end_date_raw = date_range, None
        m = re.match(r"^(.+?)\s*[\u2013\u2014-]\s*(.+)$", date_range)
        if m:
            start_date_raw, end_date_raw = m.group(1).strip(), m.group(2).strip()
        is_current = (end_date_raw or "").strip().lower() == "present"
        if is_current:
            end_date_raw = None

        work_type = None
        if location and " \u00b7 " in location:
            location, _, work_type = location.partition(" \u00b7 ")

        entries.append(
            {
                "title": title,
                "company": company.strip() if company else None,
                "employment_type": employment_type.strip() if employment_type else None,
                "start_date_raw": start_date_raw,
                "end_date_raw": end_date_raw,
                "duration": duration.strip() if duration else None,
                "is_current": is_current,
                "location": location.strip() if location else None,
                "work_type": work_type.strip() if work_type else None,
            }
        )
        i += consumed

    return entries


_PROFILE_ID_REF_RE = re.compile(
    r"com\.linkedin\.sdui\.profile\.card\.ref([A-Za-z0-9_-]+?)"
    r"(?:About|Featured|Services|SalesInsightsOrHighlights|SuggestedForYou"
    r"|ExperienceTopLevelSection|EducationTopLevelSection|Recommendations"
    r"TopLevel|PublicationTopLevelSection|Patents|CourseTopLevelSection"
    r"|HonorsTopLevel|TestScoresTopLevel|LanguageTopLevel|Organizations"
    r"|Causes)"
)


def extract_profile_id_from_component_refs(raw_text: Optional[str]) -> Optional[str]:
    """CONFIRMED (4th HAR capture) -- solves the vanityName -> profileId
    bootstrapping gap flagged in ENDPOINT_MAP.md #6/#11. Every
    profileCardsAboveActivity (and profileCardsActivity) response
    contains componentKey strings of the form
    'com.linkedin.sdui.profile.card.ref<PROFILE_ID><SectionName>'
    (e.g. '...refACoAAFCaN74BSXTToQM_SHU_nGmwpUadyTJL9EAAbout'). This
    means the profileId/vieweeProfileId required by the Experience and
    Skills-pagination endpoints can be recovered from a component call
    that only needs vanityName as input -- no separate identity-
    resolution endpoint is required. Verified: every section-suffix
    match in the same response yields the identical profile ID string.
    Returns None if no match is found (e.g. LinkedIn changes this
    naming scheme), which callers must handle by skipping
    profileId-dependent sections rather than guessing.
    """
    if not raw_text:
        return None
    m = _PROFILE_ID_REF_RE.search(raw_text)
    return m.group(1) if m else None


_SKILLS_FOR_RE = re.compile(r'"title":"Skills for ([^"]{2,150})"')
_LOGO_ALT_RE = re.compile(r'"aria-label":"([^"]{2,100}) logo"')


def extract_skills_for_entities(raw_text: Optional[str]) -> List[Dict[str, Optional[str]]]:
    """CONFIRMED against real captures (profileCardsExperienceOnly and
    profileCardsBelowActivityPart1WithoutExp bodies in
    www_linkedin_rsc-action4.har).

    LinkedIn's "skill association" modal title metadata takes the form
    `"title":"Skills for <Position> at <Company>"` for experience
    entries and `"title":"Skills for <Institution>"` (no " at ") for
    education entries. This is NOT a rendered UI text node -- it's the
    title of a lazily-opened modal -- but it reliably contains the
    entity name(s) we actually want, in a place a simple render-tree
    text walk (_walk_text_nodes) does not reach. Company names are
    also independently visible as logo `aria-label`s ("<Company>
    logo") but not every entity has a logo, so this is the primary
    extraction path and extract_logo_alt_texts() is a supplementary
    cross-check only.

    Operates on the raw decoded text directly (regex over the full
    string), not the parsed node tree, matching how this was actually
    found. Returns entries in document order, which in the captures
    analyzed matched the on-page (most-recent-first) order -- but this
    is not independently guaranteed by any explicit ordering field, so
    treat position-based pairing with date ranges as best-effort (see
    parser.extract_experience / parser.extract_education).
    """
    out = []
    for m in _SKILLS_FOR_RE.finditer(raw_text or ""):
        text = m.group(1)
        if " at " in text:
            title, company = text.split(" at ", 1)
            out.append({"title": title.strip(), "company": company.strip(), "institution": None})
        else:
            out.append({"title": None, "company": None, "institution": text.strip()})
    return out


def extract_logo_alt_texts(raw_text: Optional[str]) -> List[str]:
    """Supplementary cross-check only -- see extract_skills_for_entities.
    Not every experience/education entry has a logo, so this list is
    frequently shorter than the entity list and must not be assumed to
    align 1:1 with it."""
    return [m.group(1).strip() for m in _LOGO_ALT_RE.finditer(raw_text or "")]


def extract_date_location_lines(document) -> List[str]:
    """CONFIRMED shape: plain 'normal'-weight text nodes under Experience
    and Education sections are exclusively date ranges (e.g.
    "Jul 2026 - Present . 2 mos") and location/type strings (e.g.
    "Ahmedabad, Gujarat, India . Hybrid"), in that alternating order in
    every capture analyzed. No entity name ever appears in this list --
    see extract_skills_for_entities for names.
    """
    if document is None:
        return []
    return [text for weight, text in _walk_text_nodes(document) if weight == "normal"]
