from app.utils.dates import is_present, normalize_date, split_date_range


def test_normalize_month_year():
    assert normalize_date("Jan 2024") == "2024-01"
    assert normalize_date("January 2024") == "2024-01"


def test_normalize_year_only():
    assert normalize_date("2024") == "2024"


def test_normalize_present_returns_none():
    assert normalize_date("Present") is None
    assert normalize_date("present") is None


def test_normalize_none_and_empty():
    assert normalize_date(None) is None
    assert normalize_date("") is None


def test_normalize_unrecognized_format_passthrough():
    # Doesn't crash, doesn't fabricate a date -- returns as-is
    assert normalize_date("Sometime in the past") == "Sometime in the past"


def test_is_present():
    assert is_present("Present") is True
    assert is_present("present") is True
    assert is_present("Jan 2024") is False
    assert is_present(None) is False


def test_split_date_range_month_year_to_present():
    start, end = split_date_range("Jan 2023 - Present")
    assert start == "2023-01"
    assert end is None


def test_split_date_range_month_year_to_month_year():
    start, end = split_date_range("Jul 2024 \u2013 Jul 2028")
    assert start == "2024-07"
    assert end == "2028-07"


def test_split_date_range_year_only():
    start, end = split_date_range("2022 - 2024")
    assert start == "2022"
    assert end == "2024"


def test_split_date_range_rejects_non_date_text():
    # Must not misinterpret an arbitrary hyphenated phrase as a date range
    start, end = split_date_range("Full-Stack Development")
    assert start is None
    assert end is None


def test_split_date_range_none_input():
    assert split_date_range(None) == (None, None)
