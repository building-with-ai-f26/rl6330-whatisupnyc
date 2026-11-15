import json
from urllib.parse import urlparse


class Sites:
    def __init__(self, entries: list[dict]):
        self._entries = {entry["domain"]: entry for entry in entries}

    def _entry(self, url: str) -> dict | None:
        host = (urlparse(url).hostname or "").lower()
        parts = host.split(".")
        for i in range(len(parts) - 1):
            entry = self._entries.get(".".join(parts[i:]))
            if entry:
                return entry
        return None

    def is_allowed(self, url: str) -> bool:
        entry = self._entry(url)
        return bool(entry) and entry["automated_access"] == "allowed"

    def is_reliable(self, url: str) -> bool:
        entry = self._entry(url)
        return bool(entry) and entry["reliable"]

    def allowed_domains(self) -> list[str]:
        return [d for d, e in self._entries.items() if e["automated_access"] == "allowed"]

    def events_feeds(self) -> list[tuple[str, dict]]:
        """(domain, feed details) for each allowed site that publishes an events feed."""
        return [
            (d, e["events_api"])
            for d, e in self._entries.items()
            if e["automated_access"] == "allowed" and e.get("events_api")
        ]

    def calendar_pages(self) -> list[str]:
        return [
            e["calendar_url"]
            for e in self._entries.values()
            if e["automated_access"] == "allowed" and e["reliable"] and e.get("calendar_url")
        ]


def load(path: str = "data/sites.json") -> Sites:
    with open(path) as f:
        return Sites(json.load(f))
