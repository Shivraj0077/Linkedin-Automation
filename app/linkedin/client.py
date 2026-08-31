"""
Authenticated HTTP client for LinkedIn's flagship-web RSC/SDUI endpoints.

Authentication values are supplied through environment variables and are
never logged or returned in API responses.
"""

import asyncio
import json
import secrets
from typing import Any, Dict, Optional

import httpx

from app.config import Settings
from app.linkedin.exceptions import (
    AuthenticationExpiredError,
    LinkedInRequestFailedError,
    ProfileNotFoundError,
    RateLimitedError,
)
from app.linkedin.exceptions import TimeoutError_ as LinkedInTimeoutError


_BASE_URL = "https://www.linkedin.com"

_ALLOWED_PATHS = (
    "/flagship-web/rsc-action/actions/component",
    "/flagship-web/rsc-action/actions/pagination",
    "/flagship-web/rsc-action/actions/server-request",
    "/voyager/api/identity/dash/profiles",
)


def _random_hex(n_bytes: int = 16) -> str:
    return secrets.token_hex(n_bytes)


def _random_b64(n_bytes: int = 16) -> str:
    return secrets.token_urlsafe(n_bytes)


def _parse_extra_cookies(raw: str) -> Dict[str, str]:
    """Parse a raw `name=value; name2=value2` Cookie-header string (as
    copied verbatim from browser DevTools) into a dict.

    LinkedIn's legacy Voyager API (`/voyager/api/identity/dash/profiles`)
    applies much stricter session validation than the flagship-web RSC
    endpoints -- a request carrying only li_at/JSESSIONID gets 302'd back
    to itself (a checkpoint challenge) even though those same two cookies
    are perfectly valid for every RSC call in this client. A real browser
    session sends several more cookies alongside them (bcookie, bscookie,
    lidc, lang, ...); this lets the operator paste that full set without
    this codebase needing to hardcode or guess which specific names
    LinkedIn currently requires.
    """
    cookies: Dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        if name:
            cookies[name] = value.strip()
    return cookies


class LinkedInClient:
    """
    HTTP-only client for LinkedIn's flagship-web RSC/SDUI endpoints.
    """

    def __init__(self, settings: Settings):
        self._settings = settings

        self._csrf_token = settings.jsessionid.strip('"')

        cookies = _parse_extra_cookies(settings.extra_cookies)
        # li_at/JSESSIONID are the primary session credentials and must
        # always reflect the configured values, even if the pasted extra
        # cookie string happens to also contain stale copies of them.
        cookies["li_at"] = settings.li_at
        cookies["JSESSIONID"] = f'"{self._csrf_token}"'

        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            timeout=settings.request_timeout_seconds,
            cookies=cookies,
        )

    async def aclose(self):
        await self._client.aclose()

    def _headers(self, referer_vanity_name: str) -> Dict[str, str]:
        """
        Build headers used by LinkedIn flagship-web RSC requests.

        Per-request tracking identifiers are regenerated because their exact
        generation algorithm was not reverse engineered from the HAR.
        """

        page_instance_tracking_id = _random_b64(16)
        trace_id = _random_hex(8)
        span_id = _random_hex(8)

        x_li_track = json.dumps(
            {
                "clientVersion": "0.2.7003",
                "mpVersion": "0.2.7003",
                "osName": "web",
                "timezoneOffset": 5.5,
                "timezone": "Asia/Calcutta",
                "deviceFormFactor": "DESKTOP",
                "mpName": "web",
                "displayDensity": 1,
                "displayWidth": 1680,
                "displayHeight": 1050,
            },
            separators=(",", ":"),
        )

        return {
            "accept": "*/*",
            "content-type": "application/json",
            "csrf-token": self._csrf_token,
            "origin": _BASE_URL,
            "referer": f"{_BASE_URL}/in/{referer_vanity_name}/",

            "x-li-anchor-page-key": "d_flagship3_profile_view_base",
            "x-li-application-instance": _random_b64(16),
            "x-li-application-version": "0.2.7003",

            "x-li-page-instance": (
                "urn:li:page:d_flagship3_profile_view_base;"
                f"{page_instance_tracking_id}"
            ),

            "x-li-page-instance-tracking-id": page_instance_tracking_id,
            "x-li-pageforestid": _random_hex(16),
            "x-li-rsc-stream": "true",

            "x-li-traceparent": (
                f"00-{_random_hex(16)}-{trace_id}-00"
            ),

            "x-li-tracestate": f"LinkedIn={span_id}",

            "x-li-track": x_li_track,
            "x-restli-protocol-version": "2.0.0",

            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
        }

    def _voyager_headers(self, referer_vanity_name: str) -> Dict[str, str]:
        """
        Headers for LinkedIn's Voyager (legacy identity) API, reusing the
        same session cookies/CSRF token as the RSC client but with the
        Accept/protocol headers Voyager itself expects -- distinct from
        `_headers()`, which is RSC-specific (x-li-rsc-stream, "accept: */*").
        """

        return {
            "accept": "application/vnd.linkedin.normalized+json+2.1",
            "csrf-token": self._csrf_token,
            "x-restli-protocol-version": "2.0.0",
            "referer": f"{_BASE_URL}/in/{referer_vanity_name}/",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
        }

    def _guard_path(self, path: str):
        """
        Prevent requests from being made to arbitrary paths.
        """

        if not any(path.startswith(p) for p in _ALLOWED_PATHS):
            raise LinkedInRequestFailedError(
                f"Refusing to call disallowed path: {path}"
            )

    def _raise_for_status(self, response: httpx.Response, check_redirect: bool) -> None:
        """Map a LinkedIn HTTP response to the matching LinkedInApiError
        subclass. `check_redirect` is only set for GET: Voyager redirects
        a GET (typically to a login/checkpoint page) when the session is
        invalid/challenged, instead of returning 401/403 the way the RSC
        endpoints do -- treated the same way rather than silently
        returning an empty body.
        """
        if response.status_code in (401, 403):
            raise AuthenticationExpiredError(
                "LinkedIn rejected the session (401/403) - "
                "li_at/JSESSIONID likely expired or invalid"
            )
        if check_redirect and 300 <= response.status_code < 400:
            raise AuthenticationExpiredError(
                f"LinkedIn redirected this request (HTTP {response.status_code}) - "
                "session likely expired or requires a checkpoint"
            )
        if response.status_code == 404:
            raise ProfileNotFoundError(
                "LinkedIn returned 404 for this profile/component"
            )
        if response.status_code == 429:
            raise RateLimitedError(
                "LinkedIn rate-limited this session (429)"
            )
        if response.status_code >= 400:
            raise LinkedInRequestFailedError(
                f"LinkedIn returned HTTP {response.status_code}"
            )

    async def _request(
        self,
        method: str,
        path: str,
        query: Dict[str, str],
        headers: Dict[str, str],
        json_body: Optional[Dict[str, Any]] = None,
        check_redirect: bool = False,
    ) -> str:
        self._guard_path(path)

        if not self._settings.has_credentials:
            raise AuthenticationExpiredError(
                "LinkedIn session credentials are not configured "
                "(LINKEDIN_LI_AT / LINKEDIN_JSESSIONID)"
            )

        try:
            response = await self._client.request(
                method, path, params=query, json=json_body, headers=headers
            )
        except httpx.TimeoutException as exc:
            raise LinkedInTimeoutError("Timed out calling LinkedIn") from exc
        except httpx.HTTPError as exc:
            raise LinkedInRequestFailedError(
                f"Transport error calling LinkedIn: {exc}"
            ) from exc

        self._raise_for_status(response, check_redirect)
        return response.text

    async def _post(
        self,
        path: str,
        query: Dict[str, str],
        body: Dict[str, Any],
        referer_vanity_name: str,
    ) -> str:
        return await self._request(
            "POST", path, query, self._headers(referer_vanity_name), json_body=body
        )

    async def _get(
        self,
        path: str,
        query: Dict[str, str],
        headers: Dict[str, str],
    ) -> str:
        return await self._request("GET", path, query, headers, check_redirect=True)

    async def get_profile_identity(self, vanity_name: str) -> str:
        """
        Fetch the Voyager identity-profile response for a vanity name.

        Used as a fallback source for fields the RSC/SDUI
        profileCardsAboveActivity/profileCardsActivity responses don't
        reliably expose (location always; name/headline/about/photo/
        member_id when the activity feed has no content to render) --
        see app.linkedin.parser.extract_profile_location and
        extract_voyager_identity_fields. Does not replace or alter any
        existing RSC-based fetch.

        Retries once on a redirect (observed, repeatedly, to flip from
        failing to succeeding again within seconds for the same
        vanity_name/session -- a transient checkpoint challenge on this
        specific endpoint, not a hard session failure) before giving up
        and letting the caller degrade gracefully.
        """

        query = {
            "q": "memberIdentity",
            "memberIdentity": vanity_name,
            "decorationId": (
                "com.linkedin.voyager.dash.deco.identity.profile."
                "FullProfileWithEntities-101"
            ),
        }

        try:
            return await self._get(
                "/voyager/api/identity/dash/profiles",
                query,
                self._voyager_headers(vanity_name),
            )
        except AuthenticationExpiredError:
            await asyncio.sleep(1.5)
            return await self._get(
                "/voyager/api/identity/dash/profiles",
                query,
                self._voyager_headers(vanity_name),
            )

    async def get_component(
        self,
        component_id: str,
        vanity_name: str,
        is_self_view: bool = False,
        extra_payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Fetch a LinkedIn RSC component.
        """

        payload = {
            "vanityName": vanity_name,
            "isSelfView": is_self_view,
        }

        if extra_payload:
            payload.update(extra_payload)

        body = {
            "clientArguments": {
                "payload": payload,
                "states": [],
                "requestMetadata": {
                    "$type": "proto.sdui.common.RequestMetadata"
                },
                "screenId": (
                    "com.linkedin.sdui.flagshipnav.home.Home"
                ),
                "knownTemplateIds": [],
            }
        }

        query = {
            "componentId": component_id,
            "sduiid": component_id,
        }

        return await self._post(
            "/flagship-web/rsc-action/actions/component",
            query,
            body,
            vanity_name,
        )

    async def get_experience(
        self,
        vanity_name: str,
        viewee_profile_id: str,
    ) -> str:
        """
        Fetch the profile experience component.
        """

        return await self.get_component(
            (
                "com.linkedin.sdui.generated.profile.dsl.impl."
                "profileCardsExperienceOnly"
            ),
            vanity_name,
            extra_payload={
                "replaceableSectionArgs": {
                    "vanityName": vanity_name,
                    "vieweeProfileId": viewee_profile_id,
                }
            },
        )

    async def get_skills_page(
        self,
        vanity_name: str,
        profile_id: str,
        start: int,
        count: int = 10,
    ) -> str:
        """
        Fetch one page of LinkedIn profile skills.

        Request shape follows the captured LinkedIn pagination request.
        """

        payload = {
            "vanityName": vanity_name,
            "profileId": profile_id,
            "start": start,
            "count": count,
            "filter": "ProfileSkillCategory_ALL",
        }

        body = {
            "pagerId": (
                "com.linkedin.sdui.pagers.profile.details.skills"
            ),

            "clientArguments": {
                "$type": (
                    "proto.sdui.actions.requests.RequestedArguments"
                ),

                "requestedStateKeys": [],

                "payload": payload,

                "requestMetadata": {
                    "$type": "proto.sdui.common.RequestMetadata"
                },

                "states": [],

                "screenId": (
                    "com.linkedin.sdui.flagshipnav.profile."
                    "ProfileSkillDetails"
                ),

                "knownTemplateIds": [],
            },

            "paginationRequest": {
                "$type": (
                    "proto.sdui.actions.requests.PaginationRequest"
                ),

                "pagerId": (
                    "com.linkedin.sdui.pagers.profile.details.skills"
                ),

                "trigger": {
                    "$case": "itemDistanceTrigger",

                    "itemDistanceTrigger": {
                        "$type": (
                            "proto.sdui.actions.requests."
                            "ItemDistanceTrigger"
                        ),
                        "preloadDistance": 3,
                        "preloadLength": 250,
                    },
                },

                "retryCount": 2,

                "requestedArguments": {
                    "$type": (
                        "proto.sdui.actions.requests."
                        "RequestedArguments"
                    ),

                    "requestedStateKeys": [],

                    "payload": payload,

                    "requestMetadata": {
                        "$type": (
                            "proto.sdui.common.RequestMetadata"
                        )
                    },
                },
            },
        }

        return await self._post(
            "/flagship-web/rsc-action/actions/pagination",
            {},
            body,
            vanity_name,
        )

    async def get_honors_page(
        self,
        vanity_name: str,
        profile_id: str,
        start: int = 0,
        count: int = 10,
    ) -> str:
        """Fetch one page of LinkedIn Honors & Awards."""

        payload = {
            "vanityName": vanity_name,
            "start": start,
            "count": count,
            "profileId": profile_id,
        }

        body = {
            "pagerId": "com.linkedin.sdui.pagers.profile.details.honors",
            "clientArguments": {
                "$type": "proto.sdui.actions.requests.RequestedArguments",
                "requestedStateKeys": [],
                "payload": payload,
                "requestMetadata": {
                    "$type": "proto.sdui.common.RequestMetadata"
                },
                "states": [],
                "screenId": (
                    "com.linkedin.sdui.flagshipnav.profile."
                    "ProfileHonorDetails"
                ),
                "knownTemplateIds": [],
            },
            "paginationRequest": {
                "$type": (
                    "proto.sdui.actions.requests.PaginationRequest"
                ),
                "pagerId": (
                    "com.linkedin.sdui.pagers.profile.details.honors"
                ),
                "trigger": {
                    "$case": "itemDistanceTrigger",
                    "itemDistanceTrigger": {
                        "$type": (
                            "proto.sdui.actions.requests."
                            "ItemDistanceTrigger"
                        ),
                        "preloadDistance": 3,
                        "preloadLength": 250,
                    },
                },
                "retryCount": 2,
                "requestedArguments": {
                    "$type": (
                        "proto.sdui.actions.requests."
                        "RequestedArguments"
                    ),
                    "requestedStateKeys": [],
                    "payload": payload,
                    "requestMetadata": {
                        "$type": "proto.sdui.common.RequestMetadata"
                    },
                },
            },
        }

        return await self._post(
            "/flagship-web/rsc-action/actions/pagination",
            {},
            body,
            vanity_name,
        )