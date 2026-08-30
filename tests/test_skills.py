import os

from app.linkedin.parser import extract_skills, safe_parse_component
from app.linkedin.sdui import extract_skills_page

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _read(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def test_extract_skills_page_full_page():
    raw = _read("skills_page_full_synthetic.txt")
    doc = safe_parse_component(raw, "skills-page")
    items = extract_skills_page(doc)
    assert len(items) == 10
    assert items[0] == {"name": "Python", "context": None}
    assert items[1] == {"name": "FastAPI", "context": "Backend Engineer at Example Corp"}
    # two bold nodes back-to-back with no context in between -> first is
    # context=None, matching the real capture's behaviour
    assert items[2] == {"name": "Docker", "context": None}
    assert items[3] == {"name": "Kubernetes", "context": "Backend Engineer at Example Corp"}


def test_extract_skills_page_short_page_signals_last_page():
    raw = _read("skills_page_last_synthetic.txt")
    doc = safe_parse_component(raw, "skills-page")
    items = extract_skills_page(doc)
    assert len(items) == 2  # fewer than a full page of 10 -> caller stops paginating


def test_extract_skills_dedupes_across_pages():
    raw = _read("skills_page_full_synthetic.txt")
    # Same page "fetched" twice, e.g. a retried request -- must not double-count
    skills = extract_skills([raw, raw])
    names = [s.name for s in skills]
    assert len(names) == len(set(names)) == 10


def test_extract_skills_handles_undecodable_page_gracefully():
    skills = extract_skills(["", None, "not a valid rsc stream"])
    assert skills == []
