"""
LinkedIn profile URL validation and vanity-name extraction.

Only accepts linkedin.com/in/<vanity-name> URLs. Everything else is
rejected before any outbound request is made, so this module doubles
as the SSRF guard for the service (see app.linkedin.client, which only
ever talks to a fixed allowlist of linkedin.com hosts/paths).
"""
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.linkedin.exceptions import InvalidLinkedInUrlError

_ALLOWED_HOSTS = {"linkedin.com", "www.linkedin.com"}

# LinkedIn vanity names: letters, digits, hyphens. Observed examples in the
# HAR: "parthparmar7-gec-ldce-it-dte", "shashank-v-4b7a9536b".
_VANITY_RE = re.compile(r"^[A-Za-z0-9\-]{3,100}$")


@dataclass(frozen=True)
class ParsedProfileUrl:
    vanity_name: str
    normalized_url: str


def parse_profile_url(raw_url: str) -> ParsedProfileUrl:
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise InvalidLinkedInUrlError("URL must be a non-empty string")

    raw_url = raw_url.strip()

    try:
        parsed = urlparse(raw_url)
    except ValueError as exc:
        raise InvalidLinkedInUrlError(f"Could not parse URL: {exc}") from exc

    if parsed.scheme not in ("https", "http"):
        raise InvalidLinkedInUrlError("URL must use http or https")

    host = (parsed.hostname or "").lower()
    if host not in _ALLOWED_HOSTS:
        raise InvalidLinkedInUrlError(
            f"Unsupported host '{host}'. Only linkedin.com profile URLs are accepted."
        )

    # Strip tracking params / fragments entirely — we never need them and
    # they must not leak into any outbound request or log line.
    path_parts = [p for p in parsed.path.split("/") if p]

    if len(path_parts) < 2 or path_parts[0] != "in":
        raise InvalidLinkedInUrlError(
            "URL must be a profile URL of the form https://www.linkedin.com/in/<vanity-name>/"
        )

    vanity_name = path_parts[1]

    if not _VANITY_RE.match(vanity_name):
        raise InvalidLinkedInUrlError(f"Invalid vanity name: '{vanity_name}'")

    normalized = f"https://www.linkedin.com/in/{vanity_name}/"
    return ParsedProfileUrl(vanity_name=vanity_name, normalized_url=normalized)
