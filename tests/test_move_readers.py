import json

import httpx

from app import db
from scripts.move_readers_to_accounts import move_readers

URL = "https://project.supabase.co"
SECRET = "sb_secret_test"


class FakeAdmin:
    """Supabase's admin API for users: lists them and creates them (adding each to local auth.users)."""

    def __init__(self, conn, existing=()):
        self.conn = conn
        self.users = []
        self.requests = []
        for email in existing:
            self._create(email)

    def _create(self, email):
        with self.conn.connection() as c:
            user_id = str(c.execute("insert into auth.users (email) values (%s) returning id", (email,)).fetchone()["id"])
        user = {"id": user_id, "email": email, "email_confirmed_at": "2026-11-22T20:00:00Z"}
        self.users.append(user)
        return user

    def handle(self, request):
        self.requests.append(request)
        if request.method == "GET":
            page = int(request.url.params["page"])
            per_page = int(request.url.params["per_page"])
            return httpx.Response(200, json={"users": self.users[(page - 1) * per_page:page * per_page]})
        return httpx.Response(200, json=self._create(json.loads(request.content)["email"]))

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def created(self):
        return [json.loads(r.content) for r in self.requests if r.method == "POST"]


def old_reader(conn, reader_id, email, name="Ana", interests="jazz", days=("Friday",), sent=()):
    with conn.connection() as c:
        c.execute(
            "insert into old_subscribers (id, name, email, interests, look_prompt, days, token)"
            " values (%s, %s, %s, %s, '', %s, %s)",
            (reader_id, name, email, interests, list(days), f"token-{reader_id}"),
        )
        for event_id in sent:
            c.execute("insert into old_sent_events (subscriber_id, event_id) values (%s, %s)", (reader_id, event_id))


def profile_for(conn, email):
    with conn.connection() as c:
        row = c.execute("select id from auth.users where email = %s", (email,)).fetchone()
    return db.get_profile(conn, str(row["id"]))


def test_each_reader_gets_a_confirmed_account_and_profile(conn):
    old_reader(conn, 1, "ana@x.test", name="Ana", interests="jazz", days=("Friday", "Sunday"), sent=(5,))
    admin = FakeAdmin(conn)
    counts = move_readers(conn, admin.client(), URL, SECRET)
    assert admin.created() == [{"email": "ana@x.test", "email_confirm": True}]
    profile = profile_for(conn, "ana@x.test")
    assert (profile.name, profile.interests, profile.days) == ("Ana", "jazz", ["Friday", "Sunday"])
    assert db.sent_event_ids(conn, profile.id) == {5}
    assert counts == {"readers": 1, "accounts_created": 1, "accounts_found": 0, "profiles": 1, "sent_events": 1}


def test_emails_differing_only_in_case_are_one_reader(conn):
    old_reader(conn, 1, "ana@x.test", interests="old interests", sent=(5, 6))
    old_reader(conn, 4, " Ana@X.test ", interests="newest interests", sent=(6, 7))
    admin = FakeAdmin(conn)
    counts = move_readers(conn, admin.client(), URL, SECRET)
    assert admin.created() == [{"email": "ana@x.test", "email_confirm": True}]
    profile = profile_for(conn, "ana@x.test")
    assert profile.interests == "newest interests"
    assert db.sent_event_ids(conn, profile.id) == {5, 6, 7}
    assert (counts["readers"], counts["profiles"], counts["sent_events"]) == (1, 1, 3)


def test_existing_account_is_reused_not_duplicated(conn):
    old_reader(conn, 1, "ana@x.test")
    admin = FakeAdmin(conn, existing=["ana@x.test"])
    counts = move_readers(conn, admin.client(), URL, SECRET)
    assert admin.created() == []
    assert profile_for(conn, "ana@x.test").name == "Ana"
    assert (counts["accounts_created"], counts["accounts_found"]) == (0, 1)


def test_lists_every_page_of_existing_accounts(conn, monkeypatch):
    monkeypatch.setattr("scripts.move_readers_to_accounts.PER_PAGE", 2)
    old_reader(conn, 1, "e@x.test")
    admin = FakeAdmin(conn, existing=["a@x.test", "b@x.test", "c@x.test", "d@x.test", "e@x.test"])
    move_readers(conn, admin.client(), URL, SECRET)
    assert admin.created() == []


def test_running_twice_changes_nothing(conn):
    old_reader(conn, 1, "ana@x.test", sent=(5,))
    old_reader(conn, 2, "bo@x.test", name="Bo", sent=(6,))
    admin = FakeAdmin(conn)
    move_readers(conn, admin.client(), URL, SECRET)
    again = move_readers(conn, admin.client(), URL, SECRET)
    assert len(admin.created()) == 2
    assert again == {"readers": 2, "accounts_created": 0, "accounts_found": 2, "profiles": 0, "sent_events": 0}
    with conn.connection() as c:
        assert c.execute("select count(*) as n from profiles").fetchone()["n"] == 2
        assert c.execute("select count(*) as n from sent_events").fetchone()["n"] == 2


def test_sent_events_for_missing_events_are_kept(conn):
    old_reader(conn, 1, "ana@x.test", sent=(999,))
    move_readers(conn, FakeAdmin(conn).client(), URL, SECRET)
    assert db.sent_event_ids(conn, profile_for(conn, "ana@x.test").id) == {999}


def test_requests_use_the_secret_key(conn):
    old_reader(conn, 1, "ana@x.test")
    admin = FakeAdmin(conn)
    move_readers(conn, admin.client(), URL, SECRET)
    assert admin.requests
    for request in admin.requests:
        assert request.headers["apikey"] == SECRET
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        assert str(request.url).startswith(f"{URL}/auth/v1/admin/users")
