from urllib.parse import urlparse

from fastapi import Request

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def sent_from_this_site(request: Request) -> bool:
    # The login rides in cookies, and browsers attach cookies to forms posted from any site, so
    # other sites could submit forms here as the reader. A form must say it came from this address.
    if request.method in SAFE_METHODS:
        return True
    origin = request.headers.get("origin") or request.headers.get("referer")
    return bool(origin) and urlparse(origin).netloc == request.headers.get("host")
