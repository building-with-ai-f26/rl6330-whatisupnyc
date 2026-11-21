import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import psycopg
import pytest

from app import db, sites as sites_module
from app.models import CandidateEvent

NY = ZoneInfo("America/New_York")
# A local Postgres, never a Supabase project. See AGENTS.md for the one-time setup.
TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://localhost/whatisupnyc_test")
MIGRATIONS = Path(__file__).parent.parent / "supabase" / "migrations"


def make_event(**overrides) -> CandidateEvent:
    fields = dict(
        name="Show",
        start=datetime(2026, 11, 7, 20, tzinfo=NY),
        venue="Beacon Theatre",
        city="New York",
        url="https://reliable.example/e/1",
        source="test",
        source_type="web",
    )
    fields.update(overrides)
    return CandidateEvent(**fields)


def require_local(url: str) -> None:
    # The setup below wipes the whole database, so it must never reach Supabase.
    if urlparse(url).hostname not in ("localhost", "127.0.0.1", None):
        raise ValueError(f"TEST_DATABASE_URL must be a local Postgres, not {urlparse(url).hostname}")


@pytest.fixture(scope="session")
def fresh_schema():
    # Rebuild the tables from the migration files once per test run.
    try:
        require_local(TEST_URL)
    except ValueError as refusal:
        pytest.exit(str(refusal), returncode=2)
    try:
        admin = psycopg.connect(TEST_URL, autocommit=True)
    except psycopg.OperationalError:
        pytest.fail("Local Postgres isn't running. Start it with: brew services start postgresql@17 (see AGENTS.md)")
    with admin:
        admin.execute("drop schema public cascade")
        admin.execute("create schema public")
        for migration in sorted(MIGRATIONS.glob("*.sql")):
            admin.execute(migration.read_text())


@pytest.fixture
def conn(fresh_schema):
    pool = db.connect(TEST_URL)
    yield pool
    with pool.connection() as c:
        c.execute(f"truncate {', '.join(db.TABLES)} restart identity")
    pool.close()


@pytest.fixture
def sites(tmp_path):
    entries = [
        {"domain": "reliable.example", "automated_access": "allowed", "reliable": True},
        {"domain": "listing.example", "automated_access": "allowed", "reliable": False},
        {"domain": "blocked.example", "automated_access": "prohibited", "reliable": True},
        {"domain": "unclear.example", "automated_access": "unclear", "reliable": False},
    ]
    path = tmp_path / "sites.json"
    path.write_text(json.dumps(entries))
    return sites_module.load(str(path))
