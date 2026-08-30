from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class ExperienceEntry(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    employment_type: Optional[str] = None
    location: Optional[str] = None
    work_type: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    duration: Optional[str] = Field(
        default=None,
        description="LinkedIn's own rendered duration string (e.g. '2 mos'), "
        "kept verbatim rather than recomputed.",
    )
    is_current: bool = False
    description: Optional[str] = None


class EducationEntry(BaseModel):
    institution: Optional[str] = None
    degree: Optional[str] = None
    field_of_study: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class SkillEntry(BaseModel):
    name: str
    context: Optional[str] = Field(
        default=None,
        description="Job/context line LinkedIn renders under the skill "
        "(e.g. 'Technical Lead at X'). Not an endorsement count — see "
        "ENDPOINT_MAP.md #12 for why endorsement counts are not "
        "attributed per-skill in this implementation.",
    )


class CertificationEntry(BaseModel):
    name: Optional[str] = None
    issuer: Optional[str] = None
    issue_date: Optional[str] = None

class HonorAwardEntry(BaseModel):
    title: Optional[str] = None
    issuer: Optional[str] = None
    issue_date: Optional[str] = None

class LanguageEntry(BaseModel):
    name: Optional[str] = None
    proficiency: Optional[str] = None


class ProfileImage(BaseModel):
    url: Optional[str] = None
    root_url: Optional[str] = None
    note: Optional[str] = Field(
        default=None,
        description="Populated when the image URL was recovered from an "
        "activity-feed actor row rather than the profile's own "
        "top-card/avatar component (see ENDPOINT_MAP.md #2).",
    )


class DataConfidence(BaseModel):
    """Per-field confidence, populated per ENDPOINT_MAP.md. `verified`
    means the field's source structure was confirmed against a real
    captured response body containing actual (non-empty) data;
    `unverified` means best-effort extraction with no confirmed schema;
    `unavailable` means no capture contained this data at all (which,
    for a real request, may also just mean this particular profile has
    nothing in that section -- see notes per field below).
    """

    name: str = "unverified"          # activity-feed byline fallback only
    headline: str = "unverified"      # activity-feed byline fallback only
    profile_image: str = "unverified"  # not observed in above-activity; activity-feed fallback only
    location: str = "verified"        # Voyager identity endpoint, structural field lookup
    about: str = "verified"           # confirmed literal text, 4th HAR capture
    experience: str = "verified"      # confirmed literal fields, 4th HAR capture
    education: str = "verified"       # confirmed literal fields, 4th HAR capture
    skills: str = "verified"
    certifications: str = "unavailable"  # section anchor confirmed; no populated example captured
    languages: str = "unavailable"       # section anchor confirmed; empty for the captured profile


class Profile(BaseModel):
    url: str
    vanity_name: str
    profile_id: Optional[str] = None
    member_id: Optional[str] = None

    name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    headline: Optional[str] = None
    location: Optional[str] = None
    about: Optional[str] = None
    top_skills_summary: List[str] = Field(
        default_factory=list,
        description="The short bullet-separated 'Top skills' line LinkedIn "
        "shows near the top card (distinct from the full paginated skills "
        "list in `skills`).",
    )
    profile_image: Optional[ProfileImage] = None

    experience: List[ExperienceEntry] = Field(default_factory=list)
    education: List[EducationEntry] = Field(default_factory=list)
    skills: List[SkillEntry] = Field(default_factory=list)
    certifications: List[CertificationEntry] = Field(default_factory=list)
    languages: List[LanguageEntry] = Field(default_factory=list)
    honors_awards: List[HonorAwardEntry] = Field(default_factory=list)

    confidence: DataConfidence = Field(default_factory=DataConfidence)


class ProfileRequest(BaseModel):
    url: str = Field(..., description="LinkedIn profile URL, e.g. https://www.linkedin.com/in/<vanity-name>/")


class Meta(BaseModel):
    source: str = "linkedin"
    retrieved_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class ProfileResponse(BaseModel):
    success: bool
    profile: Optional[Profile] = None
    meta: Meta = Field(default_factory=Meta)


class ErrorResponse(BaseModel):
    success: bool = False
    error_code: str
    message: str
