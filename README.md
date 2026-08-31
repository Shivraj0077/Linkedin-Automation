# LinkedIn Profile API

A reverse-engineered, **browserless** HTTP client for LinkedIn's
`flagship-web` SDUI endpoints, wrapped in a small FastAPI service. Built
for the Tross hiring assignment.

```
POST /v1/linkedin/profile
{ "url": "https://www.linkedin.com/in/<vanity-name>/" }
```

## 1. Assignment interpretation

The brief asked for a hosted API that accepts a LinkedIn profile URL and
returns structured profile data, built via **direct HTTP calls to
LinkedIn's own endpoints** — no Playwright/Selenium/Puppeteer/Chromium
at runtime. This repo does that: `app/linkedin/client.py` talks to
LinkedIn over plain `httpx`, replaying the exact request shapes captured
from a real authenticated browser session (see §4).

**What this actually is, in plain terms:** an authenticated client that
impersonates a logged-in browser session well enough to pull structured
personal data (name, work history, education, skills) off a LinkedIn
profile via endpoints LinkedIn built for its own frontend, not for
third-party programmatic use. That's inherent to what the assignment
asked for, not a bug — but it's worth being explicit about because it's
the single biggest fact anyone evaluating or deploying this should know.
See §15 (Limitations) and §16 (Security/Legal) before using this beyond
the assignment's own scope.

## 2. Architecture

```
Client
  │  POST /v1/linkedin/profile {"url": "..."}
  ▼
FastAPI route (app/api/routes.py)
  │  validates URL, extracts vanity name
  ▼
ProfileService (app/services/profile_service.py)
  │  orchestrates one LinkedInClient per request
  ▼
LinkedInClient (app/linkedin/client.py)
  │  authenticated httpx calls to LinkedIn's flagship-web endpoints
  ▼
RSC decoder (app/linkedin/rsc.py)
  │  parses the React-Server-Components flight-stream wire format
  ▼
SDUI extractors (app/linkedin/sdui.py)
  │  section-specific field extraction (Experience, Education, About, Skills, ...)
  ▼
Profile model (app/models/profile.py)
  │  typed, normalized JSON response, with a `confidence` block per field
```

Every extractor is independent and defensively wrapped: a schema change
or missing section degrades that one field to `null`/`[]` rather than
failing the whole request (see `app/linkedin/parser.py`'s module
docstring, and requirement #20 from the brief).

### File guide

One line per file: what it does, and where it sits in the flow above.

| File | Role |
|---|---|
| `app/main.py` | FastAPI app instance + logging setup. Entrypoint for `uvicorn`. |
| `app/api/routes.py` | `GET /health` and `POST /v1/linkedin/profile`. Thin — validates the URL, calls `build_profile`, maps exceptions to HTTP status codes. No LinkedIn-specific logic here. |
| `app/config.py` | Loads `.env` and exposes a frozen `Settings` (session cookies, timeouts, page limits) as the module-level `settings` singleton. |
| `app/models/profile.py` | Every Pydantic model in the API: request/response envelopes, `Profile` and its sub-entries (`ExperienceEntry`, `EducationEntry`, ...), and `DataConfidence`. Pure data shapes, no logic. |
| `app/services/profile_service.py` | Orchestration layer. `build_profile()` is the one function that knows the *order* of LinkedIn calls to make for a single profile request, and how to degrade gracefully when any one of them fails. |
| `app/linkedin/client.py` | The only module that makes HTTP calls to LinkedIn. Builds the exact headers/cookies a real browser session sends, enforces a path allowlist (SSRF guard), and maps LinkedIn's HTTP responses onto this repo's typed exceptions. |
| `app/linkedin/rsc.py` | Generic decoder for LinkedIn's RSC ("React Server Components" flight-stream) wire format. Knows nothing about profiles — just turns the byte stream into an addressable tree of nodes. |
| `app/linkedin/sdui.py` | Section-specific extraction *primitives* that walk a decoded RSC tree or raw response text: Experience, About + Top Skills, Skills, the activity-feed name/headline/photo fallback, and the vanityName→profileId bootstrap. |
| `app/linkedin/parser.py` | Wraps the `sdui.py` primitives (plus the separate Voyager identity response) into typed `models.profile` entries. This is where the Voyager backfill for name/headline/about/photo/location/member_id lives. |
| `app/linkedin/exceptions.py` | The exception hierarchy every LinkedIn-facing module raises. Each carries a stable `code` + `status_code` used directly in the API's error response. |
| `app/utils/url.py` | Validates and normalizes the incoming profile URL; the other half of the SSRF guard (only `linkedin.com`/`www.linkedin.com` hosts are ever accepted). |
| `app/utils/dates.py` | Free-text date parsing (`"Jan 2024"` → `"2024-01"`) and range splitting (`"Jul 2024 – Jul 2028"` → start/end). No LinkedIn-specific knowledge. |
| `tests/` | One file per module above (`test_rsc.py`, `test_parser.py`, `test_dates.py`, ...), all against synthetic fixtures in `tests/fixtures/` — never real captured personal data. |

## 3. Reverse-engineering approach

LinkedIn's current profile page is **not** the classic `voyager/api/...`
REST/GraphQL API most public write-ups describe. It's now served via a
Server-Driven-UI architecture, streamed as React Server Components
("RSC") flight payloads — the same wire format Next.js uses for its App
Router, repurposed here for LinkedIn's own frontend framework
("flagship-web").

The approach, in order:
1. Captured real browser traffic (HAR + one attempted mitmproxy session)
   while browsing an authenticated test profile.
2. Identified that profile data lives behind
   `POST /flagship-web/rsc-action/actions/component?componentId=...`
   calls, one per page section, not a single profile-fetch endpoint.
3. Decoded the RSC flight-stream format (`<id>:<payload>` lines, with
   `$L<id>` cross-references) into a generic parseable document
   (`app/linkedin/rsc.py`) — deliberately kept free of any LinkedIn-
   specific assumptions, since that format itself is stable even as
   LinkedIn's component tree changes.
4. For each section, found the actual literal-text rendering pattern by
   diffing real decoded responses against what appeared on the live
   page, rather than guessing from field names. Several early
   assumptions were wrong and were caught and corrected this way (see
   `ENDPOINT_MAP.md` for the specific corrections — e.g. the "endorsement
   count" assumption for Skills, and a `Part7` component initially
   assumed to be a new section that turned out to be a partial
   re-render of Skills).
5. Wrote the extractors as pattern-matching against the *real* captured
   bytes first, then wrapped them in synthetic (non-real-person) test
   fixtures matching the same exact shape, so the test suite doesn't
   ship or depend on a real third party's personal data.

Full endpoint-by-endpoint findings, including what's confirmed vs.
still unknown, are in **`ENDPOINT_MAP.md`** at the repo root — read that
before trusting any specific field.

## 4. LinkedIn request flow (per profile request)

For one `POST /v1/linkedin/profile` call, `build_profile()`
(`app/services/profile_service.py`) makes up to 7 outbound calls to
LinkedIn, in this order:

1. `profileCardsActivity` component → best-effort name/headline (see
   Limitations — this is the weakest field; retried once if the feed
   comes back as the collapsed empty-state placeholder)
2. `profileCardsAboveActivity` component → About text, Top Skills
   summary, and bootstraps the internal `profileId` from embedded
   `componentKey` references (no separate identity-lookup call needed)
3. `/voyager/api/identity/dash/profiles` (legacy Voyager API, retried
   once on a redirect) → `location`, `member_id`, and a backfill for
   whichever of name/headline/about/profile_image steps 1–2 didn't
   recover. See Limitations — this endpoint is markedly less reliable
   than the RSC ones above.
4. `profileCardsBelowActivityPart1WithoutExp` component → Education
5. `profileCardsExperienceOnly` component (needs the `profileId` from
   step 2) → Experience
6. `pagination` (skills pager), repeated with `start += 10` until a page
   returns fewer than 10 items → full Skills list, deduplicated
7. `pagination` (honors pager), same repeat-until-partial-page pattern
   → Honors & Awards

Every request carries the exact header set confirmed from the HAR
captures (`csrf-token`, `x-li-*` tracking headers, `x-restli-protocol-
version`, etc.) — see `app/linkedin/client.py` for which values are
static (copied from the capture) vs. randomly regenerated per-request
(tracking IDs whose exact generation algorithm wasn't reverse
engineered, but confirmed live to be accepted in this form).

## 5. API documentation

### `GET /health`
```json
{ "status": "ok", "linkedin_session_configured": true }
```

### `POST /v1/linkedin/profile`

Request:
```json
{ "url": "https://www.linkedin.com/in/some-vanity-name/" }
```

Response (`200`):
```json
{
  "success": true,
  "profile": {
    "url": "https://www.linkedin.com/in/some-vanity-name/",
    "vanity_name": "some-vanity-name",
    "profile_id": "ACoAA...",
    "member_id": "10138250",
    "name": "...",
    "first_name": "...",
    "last_name": "...",
    "headline": "...",
    "location": "...",
    "about": "...",
    "top_skills_summary": ["...", "..."],
    "profile_image": { "url": "...", "root_url": "...", "note": null },
    "experience": [
      {
        "title": "...", "company": "...", "employment_type": "...",
        "location": "...", "work_type": "...",
        "start_date": "2026-07", "end_date": null,
        "duration": "2 mos", "is_current": true, "description": null
      }
    ],
    "education": [
      { "institution": "...", "degree": "...", "field_of_study": "...",
        "start_date": "2024-07", "end_date": "2028-07" }
    ],
    "skills": [ { "name": "...", "context": "..." } ],
    "certifications": [],
    "languages": [],
    "honors_awards": [],
    "confidence": {
      "name": "unverified", "headline": "unverified",
      "profile_image": "unverified", "location": "verified",
      "about": "verified", "experience": "verified",
      "education": "verified", "skills": "verified",
      "certifications": "unavailable", "languages": "unavailable"
    }
  },
  "meta": { "source": "linkedin", "retrieved_at": "2026-08-30T12:00:00Z" }
}
```

The **`confidence`** block is not cosmetic — read it, per field:
- `"verified"` — extracted from a source whose structure was confirmed
  against real LinkedIn response data (RSC component text, or the
  Voyager identity endpoint's typed profile entity).
- `"unverified"` — recovered via the activity-feed best-effort fallback
  (name/headline/profile_image, when the Voyager backfill in
  `app.linkedin.parser.extract_voyager_identity_fields` wasn't needed
  because the activity feed already had it) — no confirmed
  authoritative source, since no capture ever contained the profile
  page's initial top-card document (see Limitations).
- `"unavailable"` — nothing was found for this profile/session at
  request time. May mean the section is genuinely empty, or that its
  schema was never captured with real data — `ENDPOINT_MAP.md` says
  which, per section.

`name`/`headline`/`profile_image`/`about` are recovered from two
independent sources and merged: the activity-feed byline first (best-
effort, `"unverified"`), then — only for whatever the feed didn't
recover — a fallback call to LinkedIn's Voyager identity endpoint
(`"verified"` when it fills a gap). `location` and `member_id` have no
RSC/SDUI source at all and come from that same Voyager call exclusively.
See `LINKEDIN_EXTRA_COOKIES` below — the Voyager endpoint enforces much
stricter session validation than the RSC endpoints used for everything
else and needs a fuller browser cookie set to reach reliably.

Error response (`4xx`/`5xx`):
```json
{ "success": false, "error_code": "AUTHENTICATION_EXPIRED", "message": "..." }
```

## 6. Error codes

| Code | HTTP | Meaning |
|---|---|---|
| `INVALID_LINKEDIN_URL` | 400 | Not a `linkedin.com/in/<vanity-name>` URL |
| `PROFILE_NOT_FOUND` | 404 | LinkedIn returned 404 for this profile/component |
| `AUTHENTICATION_EXPIRED` | 401 | Session cookies rejected (401/403 from LinkedIn) |
| `RATE_LIMITED` | 429 | LinkedIn rate-limited this session |
| `LINKEDIN_REQUEST_FAILED` | 502 | Transport error or unexpected LinkedIn HTTP status |
| `RSC_DECODE_FAILED` | 502 | A response didn't match the expected RSC wire format |
| `PROFILE_PARSE_FAILED` | 502 | Reserved for parse-level failures beyond RSC decoding |
| `TIMEOUT` | 504 | LinkedIn didn't respond within `REQUEST_TIMEOUT_SECONDS` |
| `MISSING_PROFILE_DATA` | 200 | Not fatal by design — see below |

`MISSING_PROFILE_DATA` is intentionally **not** surfaced as an HTTP
error: if the URL is valid but nothing could be recovered (private
profile, blocked account, or every section degraded), the API returns
`200` with `success: true` and an almost-empty profile, since the
request itself succeeded.

## 7. Environment variables

See `.env.example`. Required:
- `LINKEDIN_LI_AT` — the `li_at` session cookie value
- `LINKEDIN_JSESSIONID` — the `JSESSIONID` cookie value (quotes included,
  exactly as LinkedIn sets it — also used as the `csrf-token` header)

Optional but strongly recommended:
- `LINKEDIN_EXTRA_COOKIES` — the rest of a real browser's LinkedIn
  cookie jar (`bcookie`, `bscookie`, `lidc`, `lang`, ...), pasted
  verbatim as one `name=value; name2=value2` string (DevTools → Network
  → any `linkedin.com` request → Request Headers → copy the whole
  `cookie` header). `li_at`/`JSESSIONID` alone are enough for every RSC
  endpoint (experience/education/skills/about), but the legacy Voyager
  identity endpoint — the only source for `name`/`first_name`/
  `last_name`/`profile_image`/`location`/`member_id` backfill — applies
  much stricter session validation and will redirect back to itself (a
  checkpoint challenge) most of the time without these present. Pull
  **all** of these, including `li_at`/`JSESSIONID`, from one single
  copy at the same moment — mixing an older `li_at` with a newer
  browser session's other cookies looks like session hijacking to
  LinkedIn and can get the session flagged/revoked outright.

Use a **dedicated test account**, not a personal one — see §8.

## 8. Local setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in real values, never commit this file
uvicorn app.main:app --reload
```

## 9. Docker

```bash
docker build -t linkedin-profile-api .
docker run -p 8000:8000 \
  -e LINKEDIN_LI_AT="..." \
  -e LINKEDIN_JSESSIONID="..." \
  linkedin-profile-api
```
(Not built/run inside the sandbox this was developed in — no Docker
daemon available there — but the image follows a standard slim-Python
pattern and was sanity-checked by running the exact `uvicorn` command
from the `CMD` line directly, which booted and served both endpoints
correctly.)

## 10. Deployment

Any container-friendly host works (Render, Railway, Fly.io). Steps are
the same everywhere:
1. Push this repo (with `.env`/`*.har` excluded — check `.gitignore`).
2. Point the platform at the `Dockerfile`.
3. Set `LINKEDIN_LI_AT` / `LINKEDIN_JSESSIONID` as platform-level secret
   environment variables — never in the repo.
4. Expose port 8000 over HTTPS (most platforms do this automatically).

## 11. Testing

```bash
pytest
```
73 tests, all passing at time of writing. Tests use **synthetic
fixtures** matching the exact byte-level shape confirmed from real
captured LinkedIn responses (see `tests/fixtures/*_synthetic.*`) —
deliberately not the real captured third party's actual profile data,
even though that data was available during development, out of respect
for that person's privacy (see §16). Coverage: URL validation, RSC
decoding (including malformed input and reference-cycle safety),
date normalization, Experience/Education/About/Skills/Honors-Awards
extraction, skills pagination and dedup, Voyager identity/location
extraction, and the profile-service orchestration layer's degradation
behavior (a duck-typed fake client is used here — no live LinkedIn
session in this test suite or in CI).

## 12. What's genuinely verified vs. best-effort

See `ENDPOINT_MAP.md` for the full breakdown. Summary:

**Verified against real captured data:** About text + Top Skills
summary, Experience (title/company/employment-type/dates/location/work-
type), Education (institution/degree/field/dates), Skills (paginated,
deduplicated), Honors & Awards (paginated), the vanityName→profileId
bootstrap, and — via the separate Voyager identity endpoint —
`location` and `member_id`.

**Best-effort, not authoritative:** Name, headline, about, and
profile_image — first recovered from an activity-feed post byline
(since no capture ever contained the profile page's initial document,
where LinkedIn's actual top card renders), then backfilled from the
same Voyager identity response as `location`/`member_id` for whatever
the activity feed didn't have. The activity-feed path only works if
the profile has recent activity; the Voyager backfill is `"verified"`
in `confidence` when it fires, but the endpoint itself is unreliable
(see §13) so it may simply not be reachable for a given request.

**Section identity confirmed, no field schema:** Certifications,
Projects, Volunteer Experience, Connected Accounts, Recommendations,
Publications, Patents, Courses, Test Scores, Languages, Organizations,
Volunteer Causes. These all have confirmed `observabilityIdentifier`
anchors proving the section exists in LinkedIn's component tree, but no
populated (non-empty) example was ever captured for the one test
profile used throughout development, so no field-level cadence could
be confirmed. They return `[]` rather than guessed structure.

**Genuinely unknown:** `profileCardsBelowActivityPart5` — every
capture's response body for this component came back empty regardless
of capture tool (Chrome HAR export or otherwise), across 7 attempts.

## 13. Known limitations

- **Name/headline are not from an authoritative source** (see §12) —
  treat as best-effort, not ground truth.
- **Certifications, Languages, and 8 other sections return empty
  arrays** for any profile, not because the code is broken, but because
  no populated example was ever captured to confirm their field
  structure against (see §12) — implementing them without a real
  example would mean guessing, which the assignment explicitly asked
  against.
- **Per-request tracking headers are regenerated, not replayed exactly**
  — their precise generation algorithm wasn't reverse engineered. Live
  testing (see below) confirms LinkedIn accepts well-formed regenerated
  values for the RSC endpoints; this was not separately confirmed for
  every header individually.
- **Single test profile.** Every confirmed field structure was verified
  against one real profile's data. Cadence-based positional parsing
  (Experience/Education) is inherently fragile to any layout variation
  LinkedIn ships for a different profile type (e.g. a profile with a
  certification between two experience entries, or a differently-
  formatted date). The defensive design means this degrades to an empty
  or partial list rather than misattributing fields, but it hasn't been
  tested against that variation.
- **Skill-category filtering** (Industry Knowledge / Tools & Tech /
  Interpersonal, seen in later captures) isn't implemented — `ALL`
  appeared to be a superset in the data available, but this wasn't
  exhaustively confirmed.
- **Voyager identity endpoint is unreliable, independent of code
  correctness.** `/voyager/api/identity/dash/profiles` (used only for
  `location`/`member_id` and to backfill `name`/`headline`/`about`/
  `profile_image` when the activity feed doesn't have them — see §5) is
  far stricter about session validation than the RSC endpoints
  everything else uses. Confirmed live: it redirects back to itself
  (a checkpoint challenge) on a large fraction of calls even with a
  valid `li_at`, and can outright invalidate the session server-side
  (`clear-site-data`) if the cookies presented don't all come from the
  same real browser session at the same moment — see
  `LINKEDIN_EXTRA_COOKIES` above. Every other endpoint (About,
  Experience, Education, Skills, Honors) has been confirmed to work
  reliably against a live account with just `li_at`/`JSESSIONID`.

## 14. Security considerations

- `LINKEDIN_LI_AT` / `LINKEDIN_JSESSIONID` are read from environment
  variables only — never hardcoded, never logged (see the logging
  discipline note at the top of `app/linkedin/client.py`), never
  returned in any API response.
- `.gitignore` excludes `.env` and `*.har` — no session data or capture
  files should ever be committed.
- The HTTP client refuses to construct requests to any path outside a
  fixed allowlist of LinkedIn's own `flagship-web` endpoints (see
  `_ALLOWED_PATHS` in `app/linkedin/client.py`) — this, plus the strict
  host allowlist in `app/utils/url.py`, is this service's SSRF guard.
- Test fixtures are synthetic, not real captured personal data (§11).

## 15. On using this beyond the assignment

This client works by replaying a real authenticated session's request
shapes against endpoints LinkedIn built for its own frontend, not for
third-party programmatic access. That's against LinkedIn's Terms of
Service regardless of how carefully the headers are replayed, and is
independent of code quality. A few concrete things worth naming rather
than leaving implicit:

- **Account risk**: the credentialed account is the one LinkedIn can
  restrict or ban if usage looks automated — this scales with request
  volume, not with how "correct" the implementation is.
- **Scope matters**: running this against the handful of profiles
  needed to demonstrate the assignment is a very different risk profile
  from using it to harvest many profiles' data at scale, even though
  the code doesn't distinguish between the two.
- **This documents mechanism, not endorsement** of any particular
  use — same as any other reverse-engineered API client for a platform
  that doesn't offer a public API for this purpose.
