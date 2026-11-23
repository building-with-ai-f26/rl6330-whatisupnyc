from datetime import timedelta

import pytest

from app import db
from app.limits import LIMITS
from app.models import Score
from app.supabase_auth import AuthRefused
from tests.conftest import make_event
from tests.fakes import FakeAI
from tests.test_main import GOOD, NOW, ana_id, client_for, sign_up_ana, signed_in_ana

CHECK_EMAIL = "Check your email to confirm your account"
BANNER = "Confirm your email to come back and edit your picks."
AUTH_DOWN = "Sign-in is down right now, please try again in a little while."


def cookie_header(response, name: str) -> str:
    return next(h for h in response.headers.get_list("set-cookie") if h.startswith(f"{name}="))


def test_site_opens_without_a_password(conn, sites):
    response = client_for(conn, sites).get("/")
    assert response.status_code == 200
    assert "www-authenticate" not in response.headers
    assert 'href="/signin"' in response.text


def test_sign_up_creates_account_and_profile_and_shows_list_now(conn, sites):
    client = client_for(conn, sites)
    response = sign_up_ana(client)
    assert (response.status_code, response.headers["location"]) == (303, "/newsletter")
    first_look = cookie_header(response, "wnyc_first_look").lower()
    assert "httponly" in first_look and "secure" in first_look and "samesite=lax" in first_look
    assert db.get_profile(conn, ana_id(client)).interests == "jazz"
    page = client.get("/newsletter")
    assert page.status_code == 200 and BANNER in page.text


def test_sign_up_with_existing_email_saves_nothing_and_says_check_email(conn, sites):
    client = client_for(conn, sites)
    sign_up_ana(client)
    client.cookies.clear()
    response = sign_up_ana(client, password="another-password", interests="taking over")
    assert response.status_code == 200 and CHECK_EMAIL in response.text
    assert "wnyc_first_look" not in response.headers.get("set-cookie", "")
    assert db.get_profile(conn, ana_id(client)).interests == "jazz"


def test_sign_up_trims_email(conn, sites):
    client = client_for(conn, sites)
    sign_up_ana(client, email="  Ana@X.test ")
    assert client.supabase.signups == ["Ana@X.test"]


def test_short_password_rejected_and_form_kept(conn, sites):
    client = client_for(conn, sites)
    response = sign_up_ana(client, password="short")
    assert response.status_code == 400
    assert "at least 8 characters" in response.text
    assert 'value="Ana"' in response.text and 'value="short"' not in response.text
    assert client.supabase.signups == []


def test_weak_password_from_supabase_shows_message(conn, sites):
    client = client_for(conn, sites)

    def refuse(email, password):
        raise AuthRefused("weak_password")

    client.supabase.sign_up = refuse
    response = sign_up_ana(client)
    assert response.status_code == 400 and "password" in response.text.lower()


def test_auth_down_keeps_form_and_says_so(conn, sites):
    client = client_for(conn, sites)
    client.supabase.down = True
    response = sign_up_ana(client)
    assert response.status_code == 503 and AUTH_DOWN in response.text
    assert 'value="Ana"' in response.text and GOOD["password"] not in response.text
    signin = client.post("/signin", data={"email": GOOD["email"], "password": GOOD["password"]})
    assert signin.status_code == 503 and AUTH_DOWN in signin.text


def test_sign_in_sets_cookies_and_opens_newsletter(conn, sites):
    client = client_for(conn, sites)
    response = signed_in_ana(client)
    assert (response.status_code, response.headers["location"]) == (303, "/newsletter")
    for name in ("wnyc_access", "wnyc_refresh"):
        header = cookie_header(response, name).lower()
        assert "httponly" in header and "secure" in header and "samesite=lax" in header
    page = client.get("/newsletter")
    assert page.status_code == 200 and BANNER not in page.text
    assert "Sign out" in page.text


def test_wrong_password_says_so_without_saying_which(conn, sites):
    client = client_for(conn, sites)
    signed_in_ana(client)
    client.cookies.clear()
    for email, password in [(GOOD["email"], "wrong-password"), ("nobody@x.test", "wrong-password")]:
        response = client.post("/signin", data={"email": email, "password": password})
        assert response.status_code == 400 and "Wrong email or password." in response.text
        assert 'value="' + email + '"' in response.text and "wrong-password" not in response.text


def test_unconfirmed_email_asks_to_confirm_first(conn, sites):
    client = client_for(conn, sites)
    sign_up_ana(client)
    client.cookies.clear()
    response = client.post("/signin", data={"email": GOOD["email"], "password": GOOD["password"]})
    assert response.status_code == 400 and "confirm your email" in response.text.lower()


def test_expired_access_token_refreshes_and_sets_new_cookies(conn, sites):
    client = client_for(conn, sites)
    signed_in_ana(client)
    client.supabase.expire(client.cookies["wnyc_access"])
    assert client.get("/newsletter").status_code == 303  # new cookies first, then the list
    assert client.cookies["wnyc_access"] in client.supabase.access
    assert client.get("/newsletter").status_code == 200


def test_signed_out_newsletter_redirects_to_sign_in(conn, sites):
    response = client_for(conn, sites).get("/newsletter")
    assert (response.status_code, response.headers["location"]) == (303, "/signin")


def test_first_look_cookie_expires_after_24_hours(conn, sites):
    now = [NOW]
    client = client_for(conn, sites, clock=lambda: now[0])
    sign_up_ana(client)
    now[0] = NOW + timedelta(hours=24)
    response = client.get("/newsletter")
    assert (response.status_code, response.headers["location"]) == (303, "/signin")


def test_signed_in_without_profile_gets_pointed_to_start(conn, sites):
    client = client_for(conn, sites)
    signed_in_ana(client)
    with conn.connection() as c:
        c.execute("delete from profiles")
    page = client.get("/newsletter")
    assert page.status_code == 200 and 'href="/"' in page.text and "preferences" in page.text.lower()


def test_old_token_link_points_to_sign_in(conn, sites):
    page = client_for(conn, sites).get("/newsletter/abc123")
    assert page.status_code == 410
    assert 'href="/signin"' in page.text and 'href="/forgot"' in page.text


def test_sign_out_clears_cookies_and_tells_supabase(conn, sites):
    client = client_for(conn, sites)
    signed_in_ana(client)
    access = client.cookies["wnyc_access"]
    response = client.post("/signout")
    assert (response.status_code, response.headers["location"]) == (303, "/")
    assert client.supabase.signed_out == [access]
    assert "wnyc_access" not in client.cookies and "wnyc_refresh" not in client.cookies
    assert client.get("/newsletter").status_code == 303


def test_forgot_says_the_same_thing_for_any_email(conn, sites):
    client = client_for(conn, sites)
    signed_in_ana(client)
    known = client.post("/forgot", data={"email": GOOD["email"]})
    unknown = client.post("/forgot", data={"email": "nobody@x.test"})
    assert known.status_code == unknown.status_code == 200
    assert known.text.replace(GOOD["email"], "") == unknown.text.replace("nobody@x.test", "")
    assert client.supabase.resets_sent == [GOOD["email"], "nobody@x.test"]


def test_reset_link_sets_new_password_and_signs_in(conn, sites):
    client = client_for(conn, sites)
    signed_in_ana(client)
    client.cookies.clear()
    link = client.supabase.link_for(GOOD["email"], "recovery")
    form = client.get(f"/reset?token_hash={link}&type=recovery")
    assert form.status_code == 200 and f'value="{link}"' in form.text
    assert client.supabase.verified == []
    response = client.post("/reset", data={"token_hash": link, "password": "brand-new-password"})
    assert (response.status_code, response.headers["location"]) == (303, "/newsletter")
    assert client.supabase.accounts[GOOD["email"]]["password"] == "brand-new-password"
    assert client.get("/newsletter").status_code == 200


def test_reset_with_short_password_keeps_the_link_usable(conn, sites):
    client = client_for(conn, sites)
    signed_in_ana(client)
    link = client.supabase.link_for(GOOD["email"], "recovery")
    response = client.post("/reset", data={"token_hash": link, "password": "short"})
    assert response.status_code == 400 and f'value="{link}"' in response.text
    assert client.supabase.verified == []


def test_expired_reset_link_offers_a_new_one(conn, sites):
    response = client_for(conn, sites).post("/reset", data={"token_hash": "used-up", "password": "long-enough-2"})
    assert response.status_code == 400 and 'href="/forgot"' in response.text


def test_confirm_link_get_does_not_spend_the_token(conn, sites):
    client = client_for(conn, sites)
    sign_up_ana(client)
    link = next(h for h, (_, kind) in client.supabase.links.items() if kind == "email")
    page = client.get(f"/confirm?token_hash={link}&type=email")
    assert page.status_code == 200 and 'method="post"' in page.text and f'value="{link}"' in page.text
    assert client.supabase.verified == []


def test_confirm_post_signs_in_and_opens_newsletter(conn, sites):
    client = client_for(conn, sites)
    sign_up_ana(client)
    link = next(h for h, (_, kind) in client.supabase.links.items() if kind == "email")
    client.cookies.clear()
    response = client.post("/confirm", data={"token_hash": link, "password": GOOD["password"]})
    assert (response.status_code, response.headers["location"]) == (303, "/newsletter")
    page = client.get("/newsletter")
    assert page.status_code == 200 and BANNER not in page.text


def test_confirming_sets_the_password_and_ends_other_logins(conn, sites):
    # Someone signs up with Ana's email and a password they chose. When Ana confirms from her inbox,
    # her password replaces theirs and every other login on the account ends.
    stranger = client_for(conn, sites)
    sign_up_ana(stranger, password="strangers-password")
    link = next(h for h, (_, kind) in stranger.supabase.links.items() if kind == "email")
    ana = client_for(conn, sites, auth=stranger.supabase)
    page = ana.get(f"/confirm?token_hash={link}&type=email")
    assert 'type="password"' in page.text
    assert ana.post("/confirm", data={"token_hash": link, "password": "anas-own-password"}).status_code == 303
    assert stranger.supabase.accounts[GOOD["email"]]["password"] == "anas-own-password"
    assert stranger.supabase.others_signed_out == [ana.cookies["wnyc_access"]]
    refused = stranger.post("/signin", data={"email": GOOD["email"], "password": "strangers-password"})
    assert refused.status_code == 400


def test_confirm_with_short_password_keeps_the_link_usable(conn, sites):
    client = client_for(conn, sites)
    sign_up_ana(client)
    link = next(h for h, (_, kind) in client.supabase.links.items() if kind == "email")
    response = client.post("/confirm", data={"token_hash": link, "password": "short"})
    assert response.status_code == 400 and f'value="{link}"' in response.text
    assert client.supabase.verified == []


def test_reset_ends_other_logins(conn, sites):
    client = client_for(conn, sites)
    signed_in_ana(client)
    link = client.supabase.link_for(GOOD["email"], "recovery")
    client.post("/reset", data={"token_hash": link, "password": "brand-new-password"})
    assert client.supabase.others_signed_out == [client.cookies["wnyc_access"]]


def test_used_confirm_link_points_to_sign_in(conn, sites):
    response = client_for(conn, sites).post("/confirm", data={"token_hash": "used-up", "password": "long-enough-2"})
    assert response.status_code == 400 and 'href="/signin"' in response.text


@pytest.mark.parametrize("method, path, data, bucket", [
    ("post", "/subscribers", GOOD, "sign_up"),
    ("post", "/signin", {"email": GOOD["email"], "password": "wrong-password"}, "sign_in"),
    ("post", "/forgot", {"email": GOOD["email"]}, "reset"),
    ("post", "/paste", {"link": "not a link", "caption": "x"}, "paste"),
    ("get", "/newsletter", None, "newsletter"),
])
def test_rate_limits_return_429(conn, sites, method, path, data, bucket):
    client = client_for(conn, sites)
    send = getattr(client, method)

    def attempt(n):
        # A different account each time, so only the per-address limit is being measured.
        form = {**data, "email": f"visitor{n}@x.test"} if data and "email" in data else data
        return send(path, data=form) if form else send(path)

    for n in range(LIMITS[bucket][0]):
        assert attempt(n).status_code != 429
    over = attempt(LIMITS[bucket][0])
    assert over.status_code == 429 and "try again" in over.text.lower()


def test_no_page_mentions_a_secret_key(conn, sites):
    client = client_for(conn, sites)
    sign_up_ana(client)
    for path in ["/", "/newsletter", "/signin", "/forgot", "/reset?token_hash=x&type=recovery", "/confirm?token_hash=x&type=email"]:
        text = client.get(path).text
        assert "sb_secret_" not in text and "SUPABASE" not in text, path


def test_analytics_never_sees_email_link_tokens_or_records_private_pages(conn, sites):
    html = client_for(conn, sites, posthog_key="phc_test").get("/reset?token_hash=secret-hash&type=recovery").text
    assert "token_hash=:token" in html  # hidden from every event before it leaves the browser
    pages = html[html.index("var privatePages"):html.index("posthog.init(")]
    for private in ['"/newsletter"', '"/reset"', '"/confirm"']:
        assert private in pages


def test_confirming_ends_the_strangers_first_look(conn, sites):
    stranger = client_for(conn, sites)
    sign_up_ana(stranger, password="strangers-password")
    assert stranger.get("/newsletter").status_code == 200
    link = next(h for h, (_, kind) in stranger.supabase.links.items() if kind == "email")
    ana = client_for(conn, sites, auth=stranger.supabase)
    ana.post("/confirm", data={"token_hash": link, "password": "anas-own-password"})
    assert stranger.get("/newsletter").status_code == 303


def test_password_that_cannot_be_set_ends_every_login(conn, sites):
    stranger = client_for(conn, sites)
    sign_up_ana(stranger, password="strangers-password")
    link = next(h for h, (_, kind) in stranger.supabase.links.items() if kind == "email")
    ana = client_for(conn, sites, auth=stranger.supabase)

    set_password = stranger.supabase.set_password

    def refuse_hers(access_token, password):
        # Her choice is refused (in practice: Supabase timed out); a random one still goes through.
        if password == "anas-own-password":
            raise AuthRefused("weak_password")
        set_password(access_token, password)

    stranger.supabase.set_password = refuse_hers
    response = ana.post("/confirm", data={"token_hash": link, "password": "anas-own-password"})
    assert response.status_code == 400 and 'href="/forgot"' in response.text
    assert "wnyc_access" not in ana.cookies
    assert stranger.supabase.access == {}  # every login on the account has ended
    # And the stranger's own password no longer works: the account got one nobody knows.
    assert stranger.post("/signin", data={"email": GOOD["email"], "password": "strangers-password"}).status_code == 400


def test_renewed_login_is_saved_before_the_slow_ai_call(conn, sites):
    # Supabase refresh tokens work once. If the new ones only reached the browser after the AI
    # (about 40 seconds), a closed tab or an error would leave the reader signed out.
    ai = FakeAI(error=RuntimeError("unexpected"))
    client = client_for(conn, sites, ai)
    signed_in_ana(client)
    client.supabase.expire(client.cookies["wnyc_access"])
    response = client.get("/newsletter")
    assert (response.status_code, response.headers["location"]) == (303, "/newsletter")
    assert ai.calls == 0
    assert client.cookies["wnyc_access"] in client.supabase.access
    assert client.cookies["wnyc_refresh"] in client.supabase.refreshes


def test_a_class_on_one_wifi_can_all_sign_up(conn, sites):
    client = client_for(conn, sites)
    for n in range(25):
        assert sign_up_ana(client, email=f"classmate{n}@nyu.test").status_code == 303


def test_incomplete_sign_up_forms_do_not_count_toward_the_limit(conn, sites):
    client = client_for(conn, sites)
    for _ in range(LIMITS["sign_up"][0] + 5):
        assert sign_up_ana(client, email="").status_code == 400
    assert sign_up_ana(client).status_code == 303


def test_password_guessing_is_limited_per_account_too(conn, sites):
    client = client_for(conn, sites)
    signed_in_ana(client)
    client.cookies.clear()
    for n in range(LIMITS["failed_sign_in"][0]):
        client.post("/signin", data={"email": "Ana@x.test " if n % 2 else "ana@x.test", "password": "guess"})
    assert client.post("/signin", data={"email": GOOD["email"], "password": GOOD["password"]}).status_code == 429
    assert client.post("/signin", data={"email": "bo@x.test", "password": "guess"}).status_code == 400


def test_a_strangers_wrong_guesses_do_not_lock_out_the_owner(conn, sites):
    stranger = client_for(conn, sites)
    signed_in_ana(stranger)
    stranger.cookies.clear()
    for _ in range(LIMITS["failed_sign_in"][0] + 5):
        stranger.post("/signin", data={"email": GOOD["email"], "password": "guess"},
                      headers={"X-Forwarded-For": "6.6.6.6"})
    # Ana, from her own address, still signs in.
    ana = stranger.post("/signin", data={"email": GOOD["email"], "password": GOOD["password"]},
                        headers={"X-Forwarded-For": "1.2.3.4"})
    assert ana.status_code == 303


def test_successful_sign_ins_do_not_count(conn, sites):
    client = client_for(conn, sites)
    signed_in_ana(client)
    for _ in range(LIMITS["failed_sign_in"][0] + 2):
        client.cookies.clear()
        assert client.post("/signin", data={"email": GOOD["email"], "password": GOOD["password"]}).status_code == 303


def test_too_many_reset_requests_look_the_same_but_send_nothing(conn, sites):
    client = client_for(conn, sites)
    pages = [client.post("/forgot", data={"email": GOOD["email"]}) for _ in range(LIMITS["reset_account"][0] + 2)]
    assert {page.status_code for page in pages} == {200}
    assert len({page.text for page in pages}) == 1
    assert client.supabase.resets_sent == [GOOD["email"]] * LIMITS["reset_account"][0]


def test_new_lists_are_limited_per_reader_too(conn, sites):
    from datetime import timedelta

    client = client_for(conn, sites, FakeAI())
    signed_in_ana(client)
    for n in range(LIMITS["newsletter_account"][0]):
        # Each preferences change means the next load makes a new list.
        db.update_profile(conn, ana_id(client), "Ana", f"jazz {n}", "", ["Friday"], NOW + timedelta(minutes=n + 1))
        assert client.get("/newsletter").status_code == 200
    db.update_profile(conn, ana_id(client), "Ana", "jazz again", "", ["Friday"], NOW + timedelta(hours=1))
    assert client.get("/newsletter").status_code == 429


def test_guesses_sent_at_the_same_moment_are_all_counted(conn, sites):
    import threading

    client = client_for(conn, sites)
    signed_in_ana(client)
    client.cookies.clear()
    inside = threading.Barrier(20, timeout=1)  # only the allowed guesses get here now
    sign_in = client.supabase.sign_in

    def slow_sign_in(email, password):
        # Every guess reaches Supabase before any of them has been answered.
        try:
            inside.wait()
        except threading.BrokenBarrierError:
            pass
        return sign_in(email, password)

    client.supabase.sign_in = slow_sign_in
    statuses = []

    def guess():
        statuses.append(client.post("/signin", data={"email": GOOD["email"], "password": "guess"}).status_code)

    threads = [threading.Thread(target=guess) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert statuses.count(400) <= LIMITS["failed_sign_in"][0]
