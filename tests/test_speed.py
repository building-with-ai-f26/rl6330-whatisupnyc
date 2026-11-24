"""The server sits far from the database (about 0.26 s a trip), so pages are kept to a few trips."""
import psycopg
import pytest

from app import db
from tests.test_dashboard import ready
from tests.test_main import ana_id

FETCH = {"X-Requested-With": "fetch"}


@pytest.fixture
def trips(monkeypatch):
    """Counts trips to the database: each statement sent, plus BEGIN and COMMIT of a transaction block."""
    count = {"n": 0}
    real_execute, real_transaction = psycopg.Cursor.execute, psycopg.Connection.transaction

    def execute(self, *args, **kwargs):
        count["n"] += 1
        return real_execute(self, *args, **kwargs)

    def transaction(self, *args, **kwargs):
        count["n"] += 2
        return real_transaction(self, *args, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, "execute", execute)
    monkeypatch.setattr(psycopg.Connection, "transaction", transaction)
    return count


def test_reloading_the_dashboard_takes_few_trips(conn, sites, trips):
    client, ai, ids = ready(conn, sites)
    client.post(f"/events/{ids['Jazz Night']}/save", headers=FETCH)
    trips["n"] = 0
    assert client.get("/newsletter").status_code == 200
    assert trips["n"] <= 11, trips["n"]  # was 21


def test_saving_takes_few_trips_and_no_reload(conn, sites, trips):
    client, ai, ids = ready(conn, sites)
    trips["n"] = 0
    response = client.post(f"/events/{ids['Jazz Night']}/save", headers=FETCH, follow_redirects=False)
    assert response.status_code == 200 and response.json() == {"saved": True}
    assert trips["n"] <= 7, trips["n"]  # was 15, plus 21 for the reload


def test_a_connection_used_moments_ago_is_not_checked_again(conn, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(db.time, "monotonic", lambda: clock["t"])
    checks = []
    monkeypatch.setattr(db.ConnectionPool, "check_connection", staticmethod(lambda c: checks.append(c)))
    with psycopg.connect(conn.conninfo, autocommit=True) as c:
        db._check_if_idle(c)
        assert len(checks) == 1  # never lent before: checked
        db._note_returned(c)
        clock["t"] += db.CHECK_AFTER_IDLE_SECONDS - 1
        db._check_if_idle(c)
        assert len(checks) == 1  # used moments ago: lent straight away
        clock["t"] += 2
        db._check_if_idle(c)
        assert len(checks) == 2  # idle for a while: checked


def test_an_error_inside_a_reader_block_undoes_it_and_drops_the_reader_role(conn, sites):
    client, ai, ids = ready(conn, sites)
    user = ana_id(client)
    with pytest.raises(RuntimeError):
        with db.as_reader(conn, user) as c:
            c.execute("delete from saved_for_later where user_id = %s", (user,))
            raise RuntimeError("boom")
    with conn.connection() as c:
        assert c.execute("select current_user = session_user as owner").fetchone()["owner"]
        assert c.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
