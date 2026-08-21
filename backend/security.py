"""
security.py
------------
Applies to EVERY request, but only actually restricts anything when it
arrives via the public internet address. LAN access (direct IP) is
completely unaffected — no password, full access, exactly as before.

Relies on the Cloudflare Tunnel already restricting WHICH paths can ever
reach this app via the public hostname (only /dashboard, /assets,
/api/dashboard*, /api/targets* are forwarded — everything else 404s at
Cloudflare's edge, before ever reaching this server). So this file only
needs to check identity and block writes on that already-narrowed set.

Credentials live in ~/.solrate_auth, outside this git repo.
"""
import os
import secrets
import base64
from starlette.responses import JSONResponse

PUBLIC_HOSTNAME = "solrate.solreibehavioralhealth.com"
CREDENTIALS_FILE = os.path.expanduser("~/.solrate_auth")


def _load_credentials():
    if not os.path.exists(CREDENTIALS_FILE):
        return None, None
    with open(CREDENTIALS_FILE) as f:
        lines = [line.strip() for line in f if line.strip()]
    if len(lines) < 2:
        return None, None
    return lines[0], lines[1]


async def public_access_control(request, call_next):
    host = request.headers.get("host", "")

    # LAN / direct access — unchanged, no password, full access.
    if not host.startswith(PUBLIC_HOSTNAME):
        return await call_next(request)

    # Public access — require a valid login.
    expected_user, expected_pass = _load_credentials()
    if not expected_user or not expected_pass:
        return JSONResponse({"detail": "Access control not configured"}, status_code=503)

    auth = request.headers.get("authorization", "")
    valid = False
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode()
            user, _, pw = decoded.partition(":")
            valid = secrets.compare_digest(user, expected_user) and secrets.compare_digest(pw, expected_pass)
        except Exception:
            valid = False

    if not valid:
        return JSONResponse(
            {"detail": "Authentication required"},
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="SolRate"'},
        )

    # Logged in via the public address — view-only. Block any write.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        return JSONResponse({"detail": "Remote access is view-only"}, status_code=403)

    return await call_next(request)
