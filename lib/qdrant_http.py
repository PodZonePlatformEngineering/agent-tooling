"""
qdrant_http.py — canonical Qdrant access primitive for agent-tooling.

PROJ-033/T-016 (CC-304). The single place where agent-tooling resolves the
Qdrant API key and makes HTTP calls. Two design rules, both load-bearing for
the PROJ-039 substrate write path:

  1. **No third-party dependency.** Uses only the Python stdlib (``urllib``).
     The system ``python3`` does not ship ``requests``; every tool that did
     ``import requests`` silently degraded (``requests = None`` →
     best-effort no-op) or hard-failed with ``requests not available``. This
     module removes that failure mode permanently — it works in any
     interpreter, in cron, and in CI with no ``pip install`` step. (Mirrors
     the curl-based shell primitives in ``primitives/qdrant/``.)

  2. **Loud on a missing key — never a silent zero-write.** ``resolve_api_key``
     raises :class:`QdrantAuthError` when ``PODZONE_QDRANT_APIKEY`` is empty.
     There is no code path here that proceeds to a request with no auth header.

Credential path (verified 2026-06-10, PROJ-033/T-016):
  The only non-interactive bootstrap available to a script is the
  ``PODZONE_QDRANT_APIKEY`` env var (injected by the Claude Code harness env
  block, and visible to Bash-tool subprocesses in the current harness). It
  cannot be self-recovered when absent: ``getSecret.sh`` needs that same key
  to reach the agentsonly secrets store, and the ``secretctl`` CLI requires an
  interactive TTY. When the key is genuinely absent (cron without the env,
  a stripped shell), the only recovery is to wrap the invocation in
  ``mcp__secrets__secret_run -k podzone_qdrant_apikey`` (or ``secretctl run``
  with a TTY), which injects the key into the child env. So: callers that must
  not crash (best-effort hooks) catch :class:`QdrantAuthError`; tools let it
  propagate to a non-zero exit so ``/consolidate-tasks`` sees a real failure
  rather than a green no-op.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from typing import Optional

# Single source of truth for the cloud cluster URL. Overridable via env for
# tests and for the (separate) T-019 agentsonly→cloud migration.
CLOUD_QDRANT_URL = os.environ.get(
    "PODZONE_QDRANT_URL",
    "https://2dd1f0b8-5cf1-4caf-bc96-2b4811251f4c.eu-west-2-0.aws.cloud.qdrant.io",
)
AGENTSONLY_QDRANT_URL = os.environ.get(
    "AGENTSONLY_QDRANT_URL", "http://qdrant.agenticflows.co.uk:8080"
)

API_KEY_ENV = "PODZONE_QDRANT_APIKEY"

DEFAULT_TIMEOUT = 8.0

# python.org macOS framework builds ship without a CA bundle and `requests`
# (which would bring `certifi`) is deliberately not a dependency — so urllib's
# default SSL context can verify nothing and every https call would fail with
# CERTIFICATE_VERIFY_FAILED. Fall back to a system bundle. (curl already works
# because it uses the system bundle.) Override with PODZONE_CA_BUNDLE.
_CA_BUNDLE_CANDIDATES = (
    "/etc/ssl/cert.pem",
    "/opt/homebrew/etc/ca-certificates/cert.pem",
    "/opt/homebrew/etc/openssl@3/cert.pem",
    "/usr/local/etc/openssl/cert.pem",
)

_SSL_CONTEXT: Optional[ssl.SSLContext] = None


def _ssl_context() -> ssl.SSLContext:
    """A verifying SSL context with CA certs loaded from the best source.

    Order: the interpreter's default bundle (if it actually has certs) →
    PODZONE_CA_BUNDLE → certifi (if installed) → a known system bundle. The
    context is built once and cached.
    """
    global _SSL_CONTEXT
    if _SSL_CONTEXT is not None:
        return _SSL_CONTEXT
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs():
        candidates = []
        override = os.environ.get("PODZONE_CA_BUNDLE")
        if override:
            candidates.append(override)
        try:
            import certifi  # type: ignore
            candidates.append(certifi.where())
        except Exception:
            pass
        candidates.extend(_CA_BUNDLE_CANDIDATES)
        for cafile in candidates:
            if cafile and os.path.exists(cafile):
                try:
                    ctx.load_verify_locations(cafile)
                    break
                except Exception:
                    continue
    _SSL_CONTEXT = ctx
    return ctx


class QdrantError(RuntimeError):
    """Base class for Qdrant access errors raised by this module."""


class QdrantAuthError(QdrantError):
    """Raised when no API key is available. A configuration error, always
    loud — never silently downgraded to an unauthenticated request."""


class QdrantHTTPError(QdrantError):
    """Raised when Qdrant returns a non-2xx response. Carries the status code
    and (truncated) body for diagnosis."""

    def __init__(self, status: int, url: str, body: str = "") -> None:
        self.status = status
        self.url = url
        self.body = body[:500]
        super().__init__(f"Qdrant {status} for {url}: {self.body}")


def resolve_api_key(api_key: Optional[str] = None) -> str:
    """Return the Qdrant API key, or raise :class:`QdrantAuthError`.

    Resolution order: explicit ``api_key`` arg → ``PODZONE_QDRANT_APIKEY`` env.
    There is deliberately no ``getSecret.sh`` fallback: that primitive needs
    this same key to authenticate to the agentsonly secrets store, so it
    cannot bootstrap an absent key (see module docstring).
    """
    if api_key:
        return api_key
    env_key = os.environ.get(API_KEY_ENV, "")
    if env_key:
        return env_key
    raise QdrantAuthError(
        f"{API_KEY_ENV} not set (and no api_key= override). This key cannot be "
        "self-recovered from a bare subprocess — run inside the Claude Code "
        f"harness, or wrap the call in `secretctl run -k podzone_qdrant_apikey` "
        "/ `mcp__secrets__secret_run` to inject it. Refusing to make an "
        "unauthenticated Qdrant request (would silently zero-write or 403)."
    )


def request_json(
    method: str,
    url: str,
    *,
    payload: Optional[dict] = None,
    api_key: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Make an authenticated Qdrant request and return the parsed JSON body.

    Raises :class:`QdrantAuthError` if no key is available, and
    :class:`QdrantHTTPError` on any non-2xx response. Returns ``{}`` for an
    empty (2xx) body.
    """
    resolved_key = resolve_api_key(api_key)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url=url, data=data, method=method.upper())
    req.add_header("api-key", resolved_key)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    context = _ssl_context() if url.lower().startswith("https") else None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            pass
        raise QdrantHTTPError(exc.code, url, body) from exc
    except urllib.error.URLError as exc:
        raise QdrantError(f"could not reach Qdrant at {url}: {exc.reason}") from exc
    if not raw.strip():
        return {}
    return json.loads(raw)


def upsert_points(
    points: list[dict],
    *,
    collection: str,
    qdrant_url: str = CLOUD_QDRANT_URL,
    api_key: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """PUT one or more points into ``collection``."""
    url = f"{qdrant_url}/collections/{collection}/points"
    return request_json(
        "PUT", url, payload={"points": points}, api_key=api_key, timeout=timeout
    )


def get_point(
    point_id: str,
    *,
    collection: str,
    qdrant_url: str = CLOUD_QDRANT_URL,
    api_key: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Optional[dict]:
    """GET a single point's payload, or ``None`` if it does not exist (404)."""
    url = f"{qdrant_url}/collections/{collection}/points/{point_id}"
    try:
        body = request_json("GET", url, api_key=api_key, timeout=timeout)
    except QdrantHTTPError as exc:
        if exc.status == 404:
            return None
        raise
    result = body.get("result")
    if isinstance(result, dict):
        return result.get("payload") or {}
    return None


def scroll(
    *,
    collection: str,
    body: Optional[dict] = None,
    qdrant_url: str = CLOUD_QDRANT_URL,
    api_key: Optional[str] = None,
    timeout: float = 30.0,
) -> dict:
    """POST a scroll request; returns the raw parsed response body."""
    url = f"{qdrant_url}/collections/{collection}/points/scroll"
    return request_json(
        "POST", url, payload=body or {}, api_key=api_key, timeout=timeout
    )


def delete_points(
    point_ids: list,
    *,
    collection: str,
    qdrant_url: str = CLOUD_QDRANT_URL,
    api_key: Optional[str] = None,
    timeout: float = 15.0,
) -> dict:
    """POST a points/delete request for the given ids."""
    url = f"{qdrant_url}/collections/{collection}/points/delete"
    return request_json(
        "POST", url, payload={"points": point_ids}, api_key=api_key, timeout=timeout
    )
