import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import db, sites as sites_module
from app.models import CandidateEvent

NY = ZoneInfo("America/New_York")


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


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    yield connection
    connection.close()


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
