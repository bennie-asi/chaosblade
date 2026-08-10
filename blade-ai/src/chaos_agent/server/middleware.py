"""Request middleware: Request-ID, logging, timing, auth, and protocol version."""

import hmac
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


# Bump this whenever the SSE event schema, the JSON envelope, or any
# request/response shape changes in a backwards-incompatible way. The
# TUI compares its own constant against the header on first contact and
# warns the user if they drift — without this, a stale npm-installed
# TUI silently mis-parses new server events and the failure mode looks
# like "the server is broken" rather than "your TUI is out of date".
#
# Compatible additions (new optional fields, new event types the TUI
# can ignore) do NOT require a bump. Removing or repurposing existing
# fields, or changing their semantics, does.
PROTOCOL_VERSION = "1"


# Process-level auth bypass for the TS TUI's embedded server. The TUI
# spawns that server itself (loopback host, OS-allocated port, same
# user), so the token gate would add no protection there — but the TUI's
# HTTP client sends no Authorization header, so a server_token set in
# config.json would 401-block the entire TUI. The public ``blade-ai
# server`` / ``blade-ai-server`` entry points NEVER set this flag.
_TOKEN_AUTH_BYPASS = False


def set_token_auth_bypass(enabled: bool) -> None:
    """Enable/disable the embedded-server auth bypass (see flag docs)."""
    global _TOKEN_AUTH_BYPASS
    _TOKEN_AUTH_BYPASS = enabled


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Add a unique request_id to every request and response."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class TimingMiddleware(BaseHTTPMiddleware):
    """Log request duration for observability."""

    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000

        logger.info(
            f"{request.method} {request.url.path} - "
            f"{response.status_code} - {duration_ms:.1f}ms"
        )
        response.headers["X-Duration-Ms"] = f"{duration_ms:.1f}"
        return response


class ProtocolVersionMiddleware(BaseHTTPMiddleware):
    """Stamp every response with ``X-Blade-Protocol-Version``.

    Sticking it on every response (rather than just /health or /version)
    means the TUI can pick it up on its very first hit — typically
    ``POST /api/v1/sessions`` during boot — so we surface a mismatch
    before the user has typed anything.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Blade-Protocol-Version"] = PROTOCOL_VERSION
        return response


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Bearer-token gate, active only when ``server_token`` is configured.

    The API exposes fault-injection endpoints while binding ``0.0.0.0`` by
    default — without a gate, anyone on the network could inject faults.
    Opt-in semantics keep every existing deployment working:

    * empty ``server_token`` (the default) → auth disabled; loopback and
      the TS TUI's embedded server flows are untouched;
    * any configured token → EVERY request must carry a matching
      ``Authorization: Bearer <token>`` or gets a 401.

    The token is read live from settings on each request, so a config
    reload takes effect without a restart. Comparison is constant-time.

    Exemption: the TS TUI's embedded server enables
    :func:`set_token_auth_bypass` — it is loopback-only and spawned by
    the TUI itself, so the gate would only break the TUI when a token
    is configured, without adding any protection.
    """

    async def dispatch(self, request: Request, call_next):
        from chaos_agent.config.settings import settings

        if _TOKEN_AUTH_BYPASS:
            return await call_next(request)

        token = (settings.server_token or "").strip()
        if not token:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        scheme, _, presented = header.partition(" ")
        # Compare as UTF-8 bytes: compare_digest() rejects non-ASCII
        # str operands with a TypeError, so a token containing any
        # non-ASCII character would 500 every request instead of
        # gating cleanly.
        if scheme.lower() == "bearer" and hmac.compare_digest(
            presented.strip().encode("utf-8"), token.encode("utf-8")
        ):
            return await call_next(request)

        logger.warning(
            "Rejected unauthenticated request %s %s",
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=401,
            content={
                "code": 4011,
                "message": "Unauthorized: missing or invalid bearer token "
                           "(the server requires server_token)",
            },
        )
