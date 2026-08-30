"""
FastAPI routes. Thin -- all real logic lives in
app.services.profile_service and app.linkedin.*.
"""
import logging

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.linkedin.client import LinkedInClient
from app.linkedin.exceptions import LinkedInApiError, MissingProfileDataError
from app.models.profile import ErrorResponse, ProfileRequest, ProfileResponse
from app.services.profile_service import build_profile
from app.utils.url import parse_profile_url

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "linkedin_session_configured": settings.has_credentials}


@router.post(
    "/v1/linkedin/profile",
    response_model=ProfileResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse},
               404: {"model": ErrorResponse}, 429: {"model": ErrorResponse},
               502: {"model": ErrorResponse}, 504: {"model": ErrorResponse}},
)
async def get_linkedin_profile(request: ProfileRequest):
    client = LinkedInClient(settings)
    try:
        parsed = parse_profile_url(request.url)
        profile = await build_profile(client, parsed.vanity_name, parsed.normalized_url)
    except MissingProfileDataError:
        # Not fatal by design (see exceptions.py) -- return a normal 200
        # with success=True and an (almost) empty profile, rather than an
        # HTTP error, since the URL itself was valid and the request
        # succeeded; there was just nothing recoverable for this profile.
        from app.models.profile import Profile

        empty_profile = Profile(url=parsed.normalized_url, vanity_name=parsed.vanity_name)
        return ProfileResponse(success=True, profile=empty_profile)
    except LinkedInApiError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"success": False, "error_code": exc.code, "message": exc.message},
        )
    finally:
        await client.aclose()

    return ProfileResponse(success=True, profile=profile)
