"""
Runtime configuration. All secrets come from environment variables —
never hardcoded, never logged. See .env.example for the full list.
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    li_at: str
    jsessionid: str
    request_timeout_seconds: float
    max_skills_pages: int

    @property
    def has_credentials(self) -> bool:
        return bool(self.li_at) and bool(self.jsessionid)


def load_settings() -> Settings:
    return Settings(
        li_at=os.environ.get("LINKEDIN_LI_AT", ""),
        jsessionid=os.environ.get("LINKEDIN_JSESSIONID", ""),
        request_timeout_seconds=float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "15")),
        max_skills_pages=int(os.environ.get("MAX_SKILLS_PAGES", "20")),
    )


settings = load_settings()
