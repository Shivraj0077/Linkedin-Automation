"""
Authenticated HTTP client for LinkedIn's flagship-web RSC/SDUI endpoints.

Authentication values are supplied through environment variables and are
never logged or returned in API responses.
"""

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


class LinkedInClient:
    """
    HTTP-only client for LinkedIn's flagship-web RSC/SDUI endpoints.
    """

    def __init__(self, settings: Settings):
        self._settings = settings

        self._csrf_token = settings.jsessionid.strip('"')

        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            timeout=settings.request_timeout_seconds,
            cookies={
                "li_at": settings.li_at,
                "JSESSIONID": f'"{self._csrf_token}"',
            },
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

    async def _post(
        self,
        path: str,
        query: Dict[str, str],
        body: Dict[str, Any],
        referer_vanity_name: str,
    ) -> str:

        self._guard_path(path)

        if not self._settings.has_credentials:
            raise AuthenticationExpiredError(
                "LinkedIn session credentials are not configured "
                "(LINKEDIN_LI_AT / LINKEDIN_JSESSIONID)"
            )

        try:
            response = await self._client.post(
                path,
                params=query,
                json=body,
                headers=self._headers(referer_vanity_name),
            )

        except httpx.TimeoutException as exc:
            raise LinkedInTimeoutError(
                "Timed out calling LinkedIn"
            ) from exc

        except httpx.HTTPError as exc:
            raise LinkedInRequestFailedError(
                f"Transport error calling LinkedIn: {exc}"
            ) from exc

        if response.status_code in (401, 403):
            raise AuthenticationExpiredError(
                "LinkedIn rejected the session (401/403) - "
                "li_at/JSESSIONID likely expired or invalid"
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

        return response.text

    async def _get(
        self,
        path: str,
        query: Dict[str, str],
        headers: Dict[str, str],
    ) -> str:

        self._guard_path(path)

        if not self._settings.has_credentials:
            raise AuthenticationExpiredError(
                "LinkedIn session credentials are not configured "
                "(LINKEDIN_LI_AT / LINKEDIN_JSESSIONID)"
            )

        try:
            response = await self._client.get(
                path,
                params=query,
                headers=headers,
            )

        except httpx.TimeoutException as exc:
            raise LinkedInTimeoutError(
                "Timed out calling LinkedIn"
            ) from exc

        except httpx.HTTPError as exc:
            raise LinkedInRequestFailedError(
                f"Transport error calling LinkedIn: {exc}"
            ) from exc

        if response.status_code in (401, 403):
            raise AuthenticationExpiredError(
                "LinkedIn rejected the session (401/403) - "
                "li_at/JSESSIONID likely expired or invalid"
            )

        if 300 <= response.status_code < 400:
            # Voyager redirects a GET (typically to a login/checkpoint
            # page) when the session is invalid/challenged, rather than
            # returning 401/403 the way the RSC endpoints do -- treat it
            # the same way rather than silently returning an empty body.
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

        return response.text

    async def get_profile_identity(self, vanity_name: str) -> str:
        """
        Fetch the Voyager identity-profile response for a vanity name.

        Used only as a fallback source for fields the RSC/SDUI
        profileCardsAboveActivity response does not reliably expose
        (currently: profile-level location) -- see
        app.linkedin.parser.extract_profile_location. Does not replace
        or alter any existing RSC-based fetch.
        """

        query = {
            "q": "memberIdentity",
            "memberIdentity": vanity_name,
            "decorationId": (
                "com.linkedin.voyager.dash.deco.identity.profile."
                "FullProfileWithEntities-101"
            ),
        }

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