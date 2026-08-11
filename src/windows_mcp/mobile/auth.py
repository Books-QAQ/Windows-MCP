"""Simple token-based authentication for the mobile gateway."""

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
import os

AUTH_TOKEN_ENV = "WINDOWS_MCP_MOBILE_TOKEN"

# In-memory session store: token → username
_sessions: dict[str, str] = {}


def get_auth_token() -> str | None:
    """Get the configured auth token from env or return None (no auth)."""
    return os.getenv(AUTH_TOKEN_ENV)


def verify_token(token: str) -> bool:
    """Verify a token against the configured auth token."""
    expected = get_auth_token()
    if expected is None:
        return True  # No auth configured → allow all
    # Support comma-separated tokens
    valid_tokens = [t.strip() for t in expected.split(",") if t.strip()]
    return token in valid_tokens


def require_auth(request: Request):
    """FastAPI dependency: require valid token in Authorization header or ?token= query param."""
    token = _extract_token(request)
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid or missing token")
    return token


def optional_auth(request: Request) -> str | None:
    """FastAPI dependency: extract token if present, None otherwise."""
    token = _extract_token(request)
    if verify_token(token):
        return token
    return None


def _extract_token(request: Request) -> str:
    """Extract token from Authorization header or query param."""
    # Authorization: Bearer <token>
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()

    # ?token=<token>
    token = request.query_params.get("token", "")
    if token:
        return token.strip()

    # Cookie
    token = request.cookies.get("auth_token", "")
    return token.strip()


def create_session(token: str) -> str:
    """Create a session and return a session ID."""
    import uuid
    sid = uuid.uuid4().hex[:16]
    _sessions[sid] = token
    return sid


def get_session(sid: str) -> str | None:
    """Get token for a session ID."""
    return _sessions.get(sid)
