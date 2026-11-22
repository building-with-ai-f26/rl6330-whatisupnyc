import pytest

from tests.conftest import require_local


def test_tests_refuse_to_wipe_a_remote_database():
    with pytest.raises(ValueError, match="local"):
        require_local("postgresql://postgres.abc:pw@aws-0-us-east-1.pooler.supabase.com:5432/postgres")


def test_tests_accept_local_postgres():
    require_local("postgresql://localhost/whatisupnyc_test")
    require_local("postgresql://127.0.0.1:5432/whatisupnyc_test")
