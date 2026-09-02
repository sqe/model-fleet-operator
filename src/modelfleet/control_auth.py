"""Authentication dependency for public control-plane endpoints."""

import hmac
import os

from fastapi import Header, HTTPException


def require_control_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.getenv("CONTROL_PLANE_API_KEY")
    if not expected:
        return
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if (
        not authorization
        or not authorization.startswith("Bearer ")
        or not hmac.compare_digest(supplied, expected)
    ):
        raise HTTPException(status_code=401, detail="invalid bearer token")
