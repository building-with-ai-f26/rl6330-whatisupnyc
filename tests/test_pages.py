import httpx

from app.pages import fetch_page


def recording_client(routes):
    requested = []

    def handler(request):
        requested.append(str(request.url))
        for suffix, response in routes.items():
            if request.url.path.endswith(suffix):
                return response
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler)), requested


def test_fetch_page_never_requests_disallowed_site(sites):
    client, requested = recording_client({})
    page = fetch_page(client, "https://blocked.example/e/1", sites)
    assert page.blocked
    assert requested == []


def test_robots_disallow_blocks_without_fetching_page(sites):
    client, requested = recording_client(
        {"robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /e/\n")}
    )
    page = fetch_page(client, "https://reliable.example/e/1", sites)
    assert page.blocked
    assert requested == ["https://reliable.example/robots.txt"]


def test_page_text_excludes_scripts(sites):
    html = "<html><script>var x='hidden';</script><body><p>Nov 7 at Beacon</p></body></html>"
    client, _ = recording_client(
        {"robots.txt": httpx.Response(404), "/e/1": httpx.Response(200, text=html)}
    )
    page = fetch_page(client, "https://reliable.example/e/1", sites)
    assert page.status == 200
    assert "Nov 7 at Beacon" in page.text
    assert "hidden" not in page.text


def test_network_failure_is_not_a_crash(sites):
    def fail(request):
        raise httpx.ConnectError("down")

    client = httpx.Client(transport=httpx.MockTransport(fail))
    page = fetch_page(client, "https://reliable.example/e/1", sites)
    assert page.status is None
    assert not page.blocked


def test_403_counts_as_blocked(sites):
    client, _ = recording_client(
        {"robots.txt": httpx.Response(404), "/e/1": httpx.Response(403)}
    )
    assert fetch_page(client, "https://reliable.example/e/1", sites).blocked
