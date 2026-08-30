# Verified endpoint map

Built from 7 HAR captures across this conversation. Status legend:
**VERIFIED** (real response body decoded, field structure confirmed by
me independently, not just asserted) / **PARTIAL** (request confirmed,
response structure only partially confirmed) / **UNKNOWN** (name
suggests purpose, no usable data recovered).

## 1. Identity resolution (legacy Voyager)
`GET /voyager/api/graphql?queryId=voyagerIdentityDashProfiles.<hash>&variables=(memberIdentity:<urn>)`
Returns almost nothing (just entityUrn + versionTag). **Not used** --
superseded by #6 below, which needs only `vanityName`.

## 2. Name / headline / photo (top card)
**PARTIAL, best-effort only.** No HAR across any of the 7 captures
contained the initial page-load HTML/RSC document, where LinkedIn's
authoritative top card actually renders. The implementation falls back
to the activity feed's post-author byline
(`profileCardsActivity` component), which contains real name + headline
text (verified: `"Parth Parmar"` / `"Tech Lead @CURIO.AI"`) but only
if the profile has recent activity, and was never cross-checked
against the real top card. `Profile.confidence.name`/`headline` stay
`"unverified"` for this reason -- see README Limitations.

## 3. About + Top Skills summary
`POST /flagship-web/rsc-action/actions/component?componentId=...profileCardsAboveActivity`
**VERIFIED.** When the About section has content, its literal text and
a bullet-separated "Top skills" line are both present as literal
`children` strings, in a fixed but non-obvious order (About header →
"Top skills" header → bullet list → "Featured" header → the actual
About paragraph). `app.linkedin.sdui.extract_about_and_top_skills`
implements this exact sequence and was tested against a real decoded
response, not just the synthetic fixture.

This same response also contains the `profileId` (as a substring of
`componentKey` strings like
`...refACoAAFCaN74BSXTToQM_SHU_nGmwpUadyTJL9EAAbout`), which solves
the vanityName→profileId bootstrap problem: Experience and Skills
pagination need a profileId, and this lets a cold request (URL only,
no prior identity call) obtain one. Implemented in
`extract_profile_id_from_component_refs`, verified against real data.

## 4. Experience
`POST .../component?componentId=...profileCardsExperienceOnly`
Input: `{"clientArguments":{"payload":{"vanityName","isSelfView","replaceableSectionArgs":{"vanityName","vieweeProfileId"}}}}`
**VERIFIED** against a real 3-entry response. Each entry renders as a
fixed literal-string cadence: title → `"<Company> · <EmploymentType>"`
→ `"<DateRange> · <Duration>"` → optional `"<Location>[· <WorkType>]"`.
Company/institution names are *also* independently recoverable from
`"title":"Skills for <Position> at <Company>"` modal metadata and from
logo `aria-label`s (`"<Company> logo"`) -- used as a documented,
not-guaranteed-aligned cross-check, not the primary source.
Implemented in `app.linkedin.sdui.extract_experience`, tested against
real captured bytes before the synthetic fixture was written.

## 5. Education (+ Certifications / Projects / Volunteer / Connected accounts, same component)
`POST .../component?componentId=...profileCardsBelowActivityPart1WithoutExp`
**VERIFIED for Education** against a real 2-entry response: institution
→ `"<Degree>, <Field>"` → date range → grade/notes line, same
positional-cadence approach as Experience. The other four sections
this component covers (Certifications, Projects, Volunteer Experience,
Connected Accounts) have **confirmed section anchors** (via
`observabilityIdentifier`) but no populated example was captured for
this profile -- they render as empty in every capture available, so no
field-cadence could be confirmed. Implemented as `[]` rather than
guessed; `Profile.confidence.certifications`/`languages` reflect this.

## 6. profileCardsBelowActivityPart2 — Recommendations
Section anchor confirmed (`recommendationsTopLevelSection`). No
populated example captured. Not implemented (would return `[]`).

## 7. profileCardsBelowActivityPart3 — Publications / Patents / Courses / Honors / Test scores
Section anchors confirmed. No populated examples captured. Not
implemented.

## 8. profileCardsBelowActivityPart4 — Languages / Organizations
Section anchors confirmed (`languageTopLevelSection`,
`organizationsTopLevelSection`). No populated example captured for
this profile. Not implemented.

## 9. profileCardsBelowActivityPart5 — UNKNOWN
Response body still empty across every capture (7 requests, all with
nonzero size, zero bytes of text preserved by the capture tool). Not
implemented; purpose genuinely unknown.

## 10. profileCardsBelowActivityPart6 — Volunteer Causes
Section anchor confirmed (`volunteerCausesSection`). No populated
example. Not implemented.

## 11. profileCardsBelowActivityPart7 — NOT a distinct section
Initially assumed unknown; the 4th capture's populated body showed
this is a **partial/duplicate rendering of the Skills section**
(`skillsSection` anchor, contains the same skill names visible via
the dedicated skills pager, #12). Not implemented separately --
skills come from #12 instead, which is complete and paginated.

## 12. Skills (paginated, fully confirmed)
`POST /flagship-web/rsc-action/actions/pagination`
Body: `{"pagerId":"com.linkedin.sdui.pagers.profile.details.skills","clientArguments":{"payload":{"vanityName","profileId","start","count","filter":"ProfileSkillCategory_ALL"}}}`
**VERIFIED** across 8+ sequential pages (start=0,10,...,70+) in
multiple captures. Skill name = bold text node; the very next
`fontWeight:"normal"` text node in document order is a job/context
line (e.g. `"Technical Lead at VYOMA LEARNING SYSTEMS Pvt. Ltd."`),
*not* an endorsement count as first assumed -- endorsement-count
strings exist in the payload but were not reachable adjacent to their
skill name, so they're intentionally left unattributed rather than
guessed. No explicit `hasMore`/`nextStart` field was found; the
implementation stops paginating when a page returns fewer than
`count` items.

A later capture (`www_linkedin_rsc-action4.har`) also showed
`filter` accepts other values (`ProfileSkillCategory_INDUSTRY_KNOWLEDGE`,
`ProfileSkillCategory_TOOLS_AND_TECHNOLOGIES`,
`ProfileSkillCategory_INTERPERSONAL`) -- **not implemented**: the
current client only requests `ProfileSkillCategory_ALL`, which in the
captures analyzed appeared to be a superset already containing skills
from every category. Filtering by category was not verified to add
skills beyond what `ALL` already returns, so implementing it would be
speculative; noted here as a possible enhancement, not a gap.

## 13. Server-request calls (profilePolicyNotice, fetchProfileDiscoveryDrawer)
UI policy banners / discovery-drawer prefetch. Not relevant to profile
data extraction, not implemented.

## 14. Navigation calls (ProfileTopSkillsDetailsScreen, ProfileSkillAssociationDetailsScreen)
`POST /flagship-web/rsc-action/actions/navigation`
These are the modal-opening calls whose *response* contains the
`"title":"Skills for X [at Y]"` metadata used in #4/#5 above. Not
called directly by this implementation -- the same metadata is present
inline in the Experience/Education component responses themselves
(#4/#5), so a separate navigation round-trip isn't needed for the
fields this API returns. Documented here because that's where the
pattern was actually discovered.
