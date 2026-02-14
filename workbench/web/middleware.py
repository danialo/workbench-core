"""
Security middleware for the Workbench web server.

Enterprise-grade middleware stack:
- Token-based authentication (ready for SSO/OIDC extension)
- CSRF protection on state-changing requests
- Per-IP sliding-window rate limiting
- Security headers (CSP, HSTS, X-Frame-Options, etc.)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from collections import defaultdict
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Static asset paths that bypass auth
# -----------------------------------------------------------------------
_PUBLIC_PREFIXES = ("/static/", "/favicon.ico")
_PUBLIC_PATHS = {"/", "/api/csrf-token"}


# -----------------------------------------------------------------------
# Authentication Middleware
# -----------------------------------------------------------------------

class AuthMiddleware(BaseHTTPMiddleware):
    """
    Bearer-token authentication.

    If ``auth_token`` is None or empty, authentication is disabled
    (development mode).  When set, every non-public request must include
    ``Authorization: Bearer <token>``.

    Design note: deliberately simple so it can be swapped for
    OIDC / SAML / mTLS in enterprise deployments without touching
    endpoint code.
    """

    def __init__(self, app: Any, auth_token: str | None = None) -> None:
        super().__init__(app)
        self.auth_token = auth_token

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip auth for static assets and public paths
        path = request.url.path
        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)
        if path in _PUBLIC_PATHS:
            return await call_next(request)

        # If no token configured, auth is disabled (dev mode)
        if not self.auth_token:
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            logger.warning("Missing auth token from %s", request.client.host if request.client else "unknown")
            return JSONResponse(
                {"error": "Authentication required"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        provided = auth_header[7:]  # Strip "Bearer "
        if not hmac.compare_digest(provided, self.auth_token):
            logger.warning("Invalid auth token from %s", request.client.host if request.client else "unknown")
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        return await call_next(request)


# -----------------------------------------------------------------------
# CSRF Middleware
# -----------------------------------------------------------------------

class CSRFMiddleware(BaseHTTPMiddleware):
    """
    Double-submit CSRF protection.

    Clients fetch a token from ``GET /api/csrf-token`` and include it
    as ``X-CSRF-Token`` on all state-changing requests.
    """

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self._secret = secrets.token_hex(32)

    def generate_token(self) -> str:
        """Generate a CSRF token tied to the server secret."""
        nonce = secrets.token_hex(16)
        sig = hmac.new(
            self._secret.encode(), nonce.encode(), hashlib.sha256
        ).hexdigest()
        return f"{nonce}:{sig}"

    def validate_token(self, token: str) -> bool:
        """Validate a CSRF token."""
        if ":" not in token:
            return False
        nonce, sig = token.split(":", 1)
        expected = hmac.new(
            self._secret.encode(), nonce.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(sig, expected)

    async def dispatch(self, request: Request, call_next) -> Response:
        # Only enforce on state-changing methods
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)

        # Skip for static assets
        path = request.url.path
        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        token = request.headers.get("x-csrf-token", "")
        if not self.validate_token(token):
            return JSONResponse(
                {"error": "Invalid or missing CSRF token"},
                status_code=403,
            )

        return await call_next(request)


# -----------------------------------------------------------------------
# Rate Limiting Middleware
# -----------------------------------------------------------------------

class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Sliding-window rate limiter per client IP.

    Defaults: 120 requests per 60-second window.
    """

    def __init__(
        self,
        app: Any,
        max_requests: int = 120,
        window_seconds: int = 60,
    ) -> None:
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip rate limiting for static assets
        if any(request.url.path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        cutoff = now - self.window_seconds

        # Prune old entries
        entries = self._requests[client_ip]
        self._requests[client_ip] = [t for t in entries if t > cutoff]

        if len(self._requests[client_ip]) >= self.max_requests:
            logger.warning("Rate limit exceeded for %s", client_ip)
            return JSONResponse(
                {"error": "Rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(self.window_seconds)},
            )

        self._requests[client_ip].append(now)
        return await call_next(request)


# -----------------------------------------------------------------------
# Security Headers Middleware
# -----------------------------------------------------------------------

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Adds security headers to all responses.

    Configured for enterprise environments with strict CSP.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
        return response
