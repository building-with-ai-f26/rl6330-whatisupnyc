def test_prohibited_site_not_allowed(sites):
    assert not sites.is_allowed("https://blocked.example/e/1")


def test_unclear_site_not_allowed(sites):
    assert not sites.is_allowed("https://unclear.example/e/1")


def test_unknown_site_not_allowed(sites):
    assert not sites.is_allowed("https://never-reviewed.example/")


def test_listing_site_allowed_but_not_reliable(sites):
    assert sites.is_allowed("https://listing.example/x")
    assert not sites.is_reliable("https://listing.example/x")


def test_subdomain_uses_parent_rule(sites):
    assert sites.is_allowed("https://www.reliable.example/e/1")
    assert not sites.is_allowed("https://www.blocked.example/e/1")


def test_lookalike_domain_not_allowed(sites):
    assert not sites.is_allowed("https://notreliable.example/e/1")


def test_allowed_domains_lists_only_allowed(sites):
    assert sorted(sites.allowed_domains()) == ["listing.example", "reliable.example"]


def test_calendar_pages_lists_only_allowed_sites(tmp_path):
    import json

    from app.sites import load

    path = tmp_path / "s.json"
    path.write_text(json.dumps([
        {"domain": "a.example", "automated_access": "allowed", "reliable": True, "calendar_url": "https://a.example/today"},
        {"domain": "b.example", "automated_access": "prohibited", "reliable": True, "calendar_url": "https://b.example/today"},
        {"domain": "c.example", "automated_access": "allowed", "reliable": True},
        {"domain": "d.example", "automated_access": "allowed", "reliable": False, "calendar_url": "https://d.example/today"},
    ]))
    assert load(str(path)).calendar_pages() == ["https://a.example/today"]


def test_sites_whose_terms_ban_collection_stay_off():
    # The Build 1 feedback asked to drop these, because their terms ban automated collection.
    from app.sites import load

    real = load()
    for url in ["https://secretnyc.co/x", "https://donyc.com/x", "https://www.eventbrite.com/e/1",
                # NYC Parks blocks our requests, so its events come from NYC Open Data instead.
                "https://www.nycgovparks.org/events/x"]:
        assert not real.is_allowed(url)
