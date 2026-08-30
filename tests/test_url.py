import pytest

from app.linkedin.exceptions import InvalidLinkedInUrlError
from app.utils.url import parse_profile_url


def test_valid_url_with_trailing_slash():
    parsed = parse_profile_url("https://www.linkedin.com/in/jane-doe-12345/")
    assert parsed.vanity_name == "jane-doe-12345"
    assert parsed.normalized_url == "https://www.linkedin.com/in/jane-doe-12345/"


def test_valid_url_without_trailing_slash():
    parsed = parse_profile_url("https://www.linkedin.com/in/jane-doe-12345")
    assert parsed.vanity_name == "jane-doe-12345"


def test_valid_url_without_www():
    parsed = parse_profile_url("https://linkedin.com/in/jane-doe-12345")
    assert parsed.vanity_name == "jane-doe-12345"


def test_strips_query_params():
    parsed = parse_profile_url(
        "https://www.linkedin.com/in/jane-doe-12345/?originalSubdomain=uk&trk=abc"
    )
    assert parsed.vanity_name == "jane-doe-12345"
    assert "?" not in parsed.normalized_url


def test_rejects_non_linkedin_host():
    with pytest.raises(InvalidLinkedInUrlError):
        parse_profile_url("https://evil.example.com/in/jane-doe/")


def test_rejects_non_profile_linkedin_url():
    with pytest.raises(InvalidLinkedInUrlError):
        parse_profile_url("https://www.linkedin.com/company/some-company/")


def test_rejects_non_http_scheme():
    with pytest.raises(InvalidLinkedInUrlError):
        parse_profile_url("ftp://www.linkedin.com/in/jane-doe/")


def test_rejects_empty_string():
    with pytest.raises(InvalidLinkedInUrlError):
        parse_profile_url("")


def test_rejects_missing_vanity_name():
    with pytest.raises(InvalidLinkedInUrlError):
        parse_profile_url("https://www.linkedin.com/in/")
