import logging
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from weakref import WeakKeyDictionary

import httpx
from bs4 import BeautifulSoup

from app.sites import Sites

log = logging.getLogger(__name__)

USER_AGENT = "whatisupnyc-bot/0.1 (+https://whatisupnyc.com)"
BLOCKED_STATUSES = {401, 403, 429, 451}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 5

_robots_cache: WeakKeyDictionary = WeakKeyDictionary()


@dataclass
class PageResult:
    status: int | None
    text: str
    html: str
    blocked: bool


def fetch_page(client: httpx.Client, url: str, sites: Sites) -> PageResult:
    for _ in range(MAX_REDIRECTS + 1):
        # Every hop is re-checked, so a redirect can't lead to a site we ruled out.
        if not _may_fetch(client, url, sites):
            return PageResult(None, "", "", True)
        try:
            response = client.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        except httpx.HTTPError as exc:
            log.info("Could not fetch page: %s", type(exc).__name__)
            return PageResult(None, "", "", False)
        if response.status_code not in REDIRECT_STATUSES:
            break
        url = urljoin(url, response.headers.get("Location", ""))
    else:
        return PageResult(None, "", "", False)
    if response.status_code in BLOCKED_STATUSES:
        return PageResult(response.status_code, "", "", True)
    return PageResult(response.status_code, _page_text(response.text), response.text, False)


def _may_fetch(client: httpx.Client, url: str, sites: Sites) -> bool:
    if urlparse(url).scheme not in ("http", "https"):
        return False
    return sites.is_allowed(url) and _robots_allow(client, url)


def _page_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())


def _robots_allow(client: httpx.Client, url: str) -> bool:
    parser = robots_rules(client, url)
    return parser is None or parser.can_fetch(USER_AGENT, url)


def robots_rules(client: httpx.Client, url: str) -> RobotFileParser | None:
    """The site's robots rules, loaded once per client. None means the site has no readable robots.txt."""
    parts = urlparse(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    per_client = _robots_cache.setdefault(client, {})
    if origin not in per_client:
        per_client[origin] = _load_robots(client, origin)
    return per_client[origin]


def _load_robots(client: httpx.Client, origin: str) -> RobotFileParser | None:
    try:
        response = client.get(f"{origin}/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=20)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    return parser


def clean_image_url(url, base: str | None = None) -> str | None:
    """An image address safe to show: made absolute against base, and https only."""
    if not isinstance(url, str) or not url.strip():
        return None
    url = urljoin(base, url.strip()) if base else url.strip()
    parts = urlparse(url)
    return url if parts.scheme == "https" and parts.netloc else None


def page_image(html: str, page_url: str) -> str | None:
    """The picture a page offers for link previews (og:image, then twitter:image)."""
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for attrs in ({"property": "og:image"}, {"name": "og:image"}, {"name": "twitter:image"}, {"property": "twitter:image"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and (url := clean_image_url(tag.get("content"), page_url)):
            return url
    return None
