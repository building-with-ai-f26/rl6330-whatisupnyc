from starlette.requests import Request

from app.limits import LIMITS, RateLimiter, client_ip


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def request_from(host: str, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers, "client": (host, 50000)})


def test_allows_up_to_the_limit_then_refuses():
    limiter = RateLimiter({"sign_up": (5, 3600)}, Clock())
    assert [limiter.allow("sign_up", "1.2.3.4") for _ in range(6)] == [True] * 5 + [False]
    assert limiter.allow("sign_up", "5.6.7.8")


def test_window_slides():
    clock = Clock()
    limiter = RateLimiter({"sign_up": (2, 3600)}, clock)
    limiter.allow("sign_up", "ip")
    clock.now += 1800
    limiter.allow("sign_up", "ip")
    assert not limiter.allow("sign_up", "ip")
    clock.now += 1801  # the first try is now more than an hour old
    assert limiter.allow("sign_up", "ip")
    assert not limiter.allow("sign_up", "ip")


def test_refused_tries_do_not_count():
    clock = Clock()
    limiter = RateLimiter({"paste": (1, 60)}, clock)
    limiter.allow("paste", "ip")
    for _ in range(10):
        limiter.allow("paste", "ip")
    clock.now += 61
    assert limiter.allow("paste", "ip")


def test_buckets_are_separate():
    limiter = RateLimiter({"sign_in": (1, 900), "reset": (1, 3600)}, Clock())
    assert limiter.allow("sign_in", "ip")
    assert limiter.allow("reset", "ip")
    assert not limiter.allow("sign_in", "ip")


def test_the_numbers():
    assert LIMITS == {
        "sign_up": (5, 3600), "sign_in": (10, 900), "reset": (3, 3600), "paste": (10, 3600), "newsletter": (20, 3600)
    }


def test_forwarded_header_trusted_only_from_caddy():
    assert client_ip(request_from("127.0.0.1", "1.2.3.4, 5.6.7.8")) == "5.6.7.8"
    assert client_ip(request_from("9.9.9.9", "1.2.3.4")) == "9.9.9.9"
    assert client_ip(request_from("127.0.0.1")) == "127.0.0.1"
