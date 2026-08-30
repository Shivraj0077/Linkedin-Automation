import os

import pytest

from app.linkedin.parser import extract_education, extract_experience_entries, extract_about
from app.linkedin.sdui import extract_skills_for_entities

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _read(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def test_extract_experience_from_synthetic_fixture():
    raw = _read("experience_synthetic.txt")
    entries = extract_experience_entries(raw)
    assert len(entries) == 2

    current = entries[0]
    assert current.title == "Senior Widget Engineer"
    assert current.company == "Example Corp"
    assert current.employment_type == "Full-time"
    assert current.start_date == "2023-01"
    assert current.end_date is None
    assert current.is_current is True
    assert current.location == "Metropolis, Example State"
    assert current.work_type == "Hybrid"


def test_extract_education_from_synthetic_fixture():
    raw = _read("education_synthetic.txt")
    entries = extract_education(None, raw_text=raw)
    assert len(entries) == 1
    e = entries[0]
    assert e.institution == "Example State University"
    assert e.degree == "Bachelor of Science"
    assert e.field_of_study == "Computer Science"
    assert e.start_date == "2019-08"
    assert e.end_date == "2023-05"


def test_extract_about_from_synthetic_fixture():
    raw = _read("about_synthetic.txt")
    about_text, top_skills = extract_about(raw)
    assert about_text == (
        "I build backend systems and enjoy mentoring junior engineers on "
        "clean architecture."
    )
    assert top_skills == ["Python", "FastAPI", "Distributed Systems"]


def test_extract_education_returns_empty_list_on_no_anchor_and_no_raw_text():
    assert extract_education(None, raw_text=None) == []


def test_extract_experience_returns_empty_list_on_empty_input():
    assert extract_experience_entries(None) == []
    assert extract_experience_entries("") == []


def test_extract_experience_stops_at_first_non_date_shaped_block():
    # A malformed/changed-schema response should degrade to [] (or a
    # partial list) rather than raise or fabricate fields.
    raw = '0:["$","div",null,{"textProps":{"children":["Experience"],"fontWeight":"bold"}}]\n'
    assert extract_experience_entries(raw) == []


def test_extract_skills_for_entities_experience_shape():
    raw = '{"title":"Skills for Senior Widget Engineer at Example Corp"}'
    out = extract_skills_for_entities(raw)
    assert out == [
        {"title": "Senior Widget Engineer", "company": "Example Corp", "institution": None}
    ]


def test_extract_skills_for_entities_education_shape():
    raw = '{"title":"Skills for Example State University"}'
    out = extract_skills_for_entities(raw)
    assert out == [{"title": None, "company": None, "institution": "Example State University"}]


def test_extract_skills_for_entities_empty_input():
    assert extract_skills_for_entities(None) == []
    assert extract_skills_for_entities("") == []
