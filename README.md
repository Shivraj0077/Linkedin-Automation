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

For one `POST /v1/linkedin/profile` call, the service makes up to 4–5
outbound calls to LinkedIn:

1. `profileCardsActivity` component → best-effort name/headline (see
   Limitations — this is the weakest field)
2. `profileCardsAboveActivity` component → About text, Top Skills
   summary, and bootstraps the internal `profileId` from embedded
   `componentKey` references (no separate identity-lookup call needed)
3. `profileCardsBelowActivityPart1WithoutExp` component → Education
4. `profileCardsExperienceOnly` component (needs the `profileId` from
   step 2) → Experience
5. `pagination` (skills pager), repeated with `start += 10` until a page
   returns fewer than 10 items → full Skills list, deduplicated

Every request carries the exact header set confirmed from the HAR
captures (`csrf-token`, `x-li-*` tracking headers, `x-restli-protocol-
version`, etc.) — see `app/linkedin/client.py` for which values are
static (copied from the capture) vs. randomly regenerated per-request
(tracking IDs whose exact generation algorithm wasn't reverse
engineered, and which were not verified against a live account to
confirm LinkedIn accepts arbitrary well-formed values here).

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
    "name": "...",
    "first_name": "...",
    "last_name": "...",
    "headline": "...",
    "about": "...",
    "top_skills_summary": ["...", "..."],
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
    "confidence": {
      "name": "unverified", "headline": "unverified",
      "about": "verified", "experience": "verified",
      "education": "verified", "skills": "verified",
      "certifications": "unavailable", "languages": "unavailable"
    }
  },
  "meta": { "source": "linkedin", "retrieved_at": "2026-08-30T12:00:00Z" }
}
```

The **`confidence`** block is not cosmetic — read it. `"verified"`
means the field's extraction logic was checked against a real decoded
LinkedIn response containing actual data. `"unverified"` means
best-effort with no confirmed authoritative source (currently:
name/headline, since no capture contained the profile page's initial
document — see Limitations). `"unavailable"` means nothing was found
for this profile/session at request time (may mean the section is
genuinely empty, or that its schema was never captured with real data
— `ENDPOINT_MAP.md` says which, per section).

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
44 tests, all passing at time of writing. Tests use **synthetic
fixtures** matching the exact byte-level shape confirmed from real
captured LinkedIn responses (see `tests/fixtures/*_synthetic.txt`) —
deliberately not the real captured third party's actual profile data,
even though that data was available during development, out of respect
for that person's privacy (see §16). Coverage: URL validation, RSC
decoding (including malformed input and reference-cycle safety),
date normalization, Experience/Education/About/Skills extraction,
skills pagination and dedup, and the profile-service orchestration
layer's degradation behavior (a mocked client is used here — there is
no live LinkedIn session in this test suite or in CI).

## 12. What's genuinely verified vs. best-effort

See `ENDPOINT_MAP.md` for the full breakdown. Summary:

**Verified against real captured data:** About text + Top Skills
summary, Experience (title/company/employment-type/dates/location/work-
type), Education (institution/degree/field/dates), Skills (paginated,
deduplicated), and the vanityName→profileId bootstrap.

**Best-effort, not authoritative:** Name and headline — recovered from
an activity-feed post byline, since no capture (across 7 HAR files and
one incomplete mitmproxy attempt) contained the profile page's initial
document, where LinkedIn's actual top card renders. This only works if
the profile has recent activity, and its accuracy relative to the real
top card was never independently confirmed.

**Section identity confirmed, no field schema:** Certifications,
Projects, Volunteer Experience, Connected Accounts, Recommendations,
Publications, Patents, Courses, Honors & Awards, Test Scores,
Languages, Organizations, Volunteer Causes. These all have confirmed
`observabilityIdentifier` anchors proving the section exists in
LinkedIn's component tree, but no populated (non-empty) example was
ever captured for the one test profile used throughout development, so
no field-level cadence could be confirmed. They return `[]` rather than
guessed structure.

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
  — their precise generation algorithm wasn't reverse engineered, and
  whether LinkedIn's backend validates them beyond "well-formed" was
  never tested against a live account (no credentials were available
  during development).
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
- **No live end-to-end test.** This was built without LinkedIn
  credentials at any point; every extractor was validated against
  captured historical response bytes, not a live request/response
  cycle against the real API today. LinkedIn's frontend can and does
  change; the first live run should be treated as a validation step,
  not an assumption.

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
# Linkedin-Automation
