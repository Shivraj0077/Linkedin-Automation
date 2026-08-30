"""
Explicit exception hierarchy for the LinkedIn integration.

Every exception carries a stable `code` string used for the API error
response and a `status_code` used for the HTTP mapping in
app/api/routes.py. Never include cookies, tokens, or headers in the
exception message — see app.linkedin.client for the logging discipline
this depends on.
"""


class LinkedInApiError(Exception):
    code = "LINKEDIN_REQUEST_FAILED"
    status_code = 502

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidLinkedInUrlError(LinkedInApiError):
    code = "INVALID_LINKEDIN_URL"
    status_code = 400


class ProfileNotFoundError(LinkedInApiError):
    code = "PROFILE_NOT_FOUND"
    status_code = 404


class AuthenticationExpiredError(LinkedInApiError):
    code = "AUTHENTICATION_EXPIRED"
    status_code = 401


class RateLimitedError(LinkedInApiError):
    code = "RATE_LIMITED"
    status_code = 429


class LinkedInRequestFailedError(LinkedInApiError):
    code = "LINKEDIN_REQUEST_FAILED"
    status_code = 502


class RscDecodeError(LinkedInApiError):
    code = "RSC_DECODE_FAILED"
    status_code = 502


class ProfileParseError(LinkedInApiError):
    code = "PROFILE_PARSE_FAILED"
    status_code = 502


class TimeoutError_(LinkedInApiError):  # noqa: N801 (avoid shadowing builtin)
    code = "TIMEOUT"
    status_code = 504


class MissingProfileDataError(LinkedInApiError):
    code = "MISSING_PROFILE_DATA"
    status_code = 200  # not fatal — surfaced in the response, not as an error
