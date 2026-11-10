import base64
import binascii
import secrets
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import PlainTextResponse

# Shorter passwords close the site instead of protecting it weakly.
MIN_PASSWORD_LENGTH = 16
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def password_matches(header: str | None, password: str) -> bool:
    # Browsers send "Basic base64(name:password)". Any name works; only the password counts.
    if not header or header[:6].lower() != "basic ":
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    _, colon, given = decoded.partition(":")
    # compare_digest takes the same time whether or not the guess is close.
    return bool(colon) and secrets.compare_digest(given.encode(), password.encode())


def sent_from_this_site(request: Request) -> bool:
    # Browsers attach the saved password to forms posted from any site, so other sites
    # could submit forms here. A form must say it came from this same address.
    if request.method in SAFE_METHODS:
        return True
    origin = request.headers.get("origin") or request.headers.get("referer")
    return bool(origin) and urlparse(origin).netloc == request.headers.get("host")


def check(request: Request, password: str) -> PlainTextResponse | None:
    """Returns the refusal to send, or None when the request may go through."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return PlainTextResponse("The site is closed until SITE_PASSWORD is set to 16 or more characters.", 503)
    if not password_matches(request.headers.get("authorization"), password):
        return PlainTextResponse(
            "Password needed.", 401, headers={"WWW-Authenticate": 'Basic realm="whatisupnyc", charset="UTF-8"'}
        )
    if not sent_from_this_site(request):
        return PlainTextResponse("Forms must be sent from this site.", 403)
    return None
