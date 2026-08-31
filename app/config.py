"""
Runtime configuration. All secrets come from environment variables —
never hardcoded, never logged. See .env.example for the full list.
"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load .env into the process environment before anything below reads
# os.environ -- nothing else in this codebase does this, and uvicorn
# does not do it either, so without this call every LINKEDIN_* var is
# silently empty unless the shell happens to export them itself.
load_dotenv()


@dataclass(frozen=True)
class Settings:
    li_at: str
    jsessionid: str
    extra_cookies: str
    request_timeout_seconds: float
    max_skills_pages: int

    @property
    def has_credentials(self) -> bool:
        return bool(self.li_at) and bool(self.jsessionid)


def load_settings() -> Settings:
    return Settings(
        li_at=os.environ.get("LINKEDIN_LI_AT", ""),
        jsessionid=os.environ.get("LINKEDIN_JSESSIONID", ""),
        extra_cookies=os.environ.get("LINKEDIN_EXTRA_COOKIES", ""),
        request_timeout_seconds=float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "15")),
        max_skills_pages=int(os.environ.get("MAX_SKILLS_PAGES", "20")),
    )


settings = load_settings()
