"""Per-visitor limits, so one visitor can't use up the day's AI requests or flood sign-ups.

Kept in memory, which is enough for the one server process the site runs.
"""
import ipaddress
import time
from collections import deque
from collections.abc import Callable

from fastapi import Request

# bucket: (tries allowed, per this many seconds), per visitor IP.
LIMITS: dict[str, tuple[int, int]] = {
    "sign_up": (5, 3600),
    "sign_in": (10, 900),
    "reset": (3, 3600),
    "paste": (10, 3600),
    "newsletter": (20, 3600),
}
# The most visitors tracked at once. Past it, stale ones are forgotten first, then the oldest.
MAX_TRACKED = 10_000
# The full clean-up looks at every visitor, so it runs at most this often (seconds).
SWEEP_EVERY = 60


class RateLimiter:
    def __init__(self, limits: dict[str, tuple[int, int]], clock: Callable[[], float] = time.monotonic):
        self.limits = limits
        self.clock = clock
        # Insertion order doubles as age order, so the oldest visitor is the first key.
        self.tries: dict[tuple[str, str], deque[float]] = {}
        self.last_sweep = float("-inf")

    def allow(self, bucket: str, key: str) -> bool:
        """Counts this try and says whether it is within the limit. Refused tries don't count."""
        count, seconds = self.limits[bucket]
        now = self.clock()
        if (bucket, key) not in self.tries and len(self.tries) >= MAX_TRACKED:
            self._make_room(now)
        tries = self.tries.setdefault((bucket, key), deque())
        while tries and tries[0] <= now - seconds:
            tries.popleft()
        if len(tries) >= count:
            return False
        tries.append(now)
        return True

    def _make_room(self, now: float) -> None:
        if now - self.last_sweep >= SWEEP_EVERY:
            self.last_sweep = now
            self._sweep(now)
        while len(self.tries) >= MAX_TRACKED:
            del self.tries[next(iter(self.tries))]

    def _sweep(self, now: float) -> None:
        for (bucket, key), tries in list(self.tries.items()):
            if not tries or tries[-1] <= now - self.limits[bucket][1]:
                del self.tries[(bucket, key)]


def client_ip(request: Request) -> str:
    """The visitor's address. Caddy, on this same machine, passes the real one in X-Forwarded-For.

    The header is trusted only from Caddy: anyone else could write any address into it.
    IPv6 visitors count by their /64, because one home is usually given that whole block.
    """
    host = request.client.host if request.client else ""
    forwarded = request.headers.get("x-forwarded-for")
    if host == "127.0.0.1" and forwarded:
        host = forwarded.split(",")[-1].strip()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if address.version == 6:
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return host
