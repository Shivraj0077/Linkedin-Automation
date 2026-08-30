"""
FastAPI entrypoint.

Run locally with: uvicorn app.main:app --reload
"""
import logging

from fastapi import FastAPI

from app.api.routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(
    title="LinkedIn Profile API",
    description=(
        "Reverse-engineered, browserless HTTP client for LinkedIn's "
        "flagship-web SDUI endpoints. See ENDPOINT_MAP.md at the repo "
        "root and the README's Limitations section before relying on "
        "any field this returns."
    ),
    version="0.1.0",
)

app.include_router(router)
