"""Pictures for well-known events, found with an image search, and spotting an event Muse and a feed both list.

The search never opens the event's page: Tavily returns pictures from the trusted sites in data/sites.json,
each with a description of what it shows."""
from urllib.parse import urlparse

from app.checks import NY, name_words
from app.intake import SOCIAL_IMAGE_HOSTS, _on
from app.models import CandidateEvent
from app.pages import clean_image_url
from app.sites import Sites


def _site(url: str) -> str:
    """The site a link belongs to, so www1.nyc.gov and www.nyc.gov count as one."""
    host = (urlparse(url).hostname or "").lower()
    return ".".join(host.split(".")[-2:])


def _words(text: str) -> set[str]:
    # "lights" and "light" are the same word here.
    return {word.removesuffix("s") for word in name_words(text)}


def find_picture(event: CandidateEvent, tavily, sites: Sites) -> str | None:
    """A picture of the event, only when another trusted site writes about it (so it's well known) and the
    picture's description names it. None otherwise; the card then shows its plain flyer."""
    wanted = _words(event.name)
    if not wanted:
        return None
    hits, images = tavily.search_images(f'"{event.name}" New York', sites.allowed_domains())
    known = any(
        sites.is_reliable(hit.url) and _site(hit.url) != _site(event.url)
        and wanted <= _words(f"{hit.title} {hit.content}")
        for hit in hits
    )
    if not known:
        return None
    needed = min(2, len(wanted))
    for image in images:
        url = clean_image_url(image.url)
        if not url or _on(urlparse(url).hostname or "", SOCIAL_IMAGE_HOSTS):
            continue
        shared = wanted & _words(image.description)
        if len(shared) >= needed and len(shared) * 2 >= len(wanted):
            return url
    return None


def is_copy(a: CandidateEvent, b: CandidateEvent) -> bool:
    """The same event under two names, like "Macy's Parade Balloon Inflation" and "Macy's Thanksgiving Day Parade
    Balloon Inflation": the same day, and every word of the shorter name (at least three) is in the longer one.
    Venues are not compared, because Muse names the street and a feed names the building."""
    if a.start.astimezone(NY).date() != b.start.astimezone(NY).date():
        return False
    shorter, longer = sorted((_words(a.name), _words(b.name)), key=len)
    return len(shorter) >= 3 and shorter <= longer
