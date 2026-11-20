"""
The connection-health checks: what stands between a broken bank connection and
a green run over missing data.

Every connection here reports `status: UPDATED` and `executionStatus: SUCCESS`,
the values Pluggy showed throughout the ten-week outage, so a check only passes
these tests by looking at the signals that actually discriminate.
"""

from datetime import datetime, timezone

import pytest
from freezegun import freeze_time
from my_pluggy import check_connection_health
from schemas import ProblemSeverity

from tests.fakes import FAKE_PLUGGY_API_KEY, FakePluggy, ago, from_now

# Both the clock and the time staleness is measured up to; these tests exercise
# the threshold, not where the reference time comes from.
NOW = datetime(2026, 3, 10, 6, 15, tzinfo=timezone.utc)
ACCOUNT_ID = "fake-account-checking"
FAR_FUTURE_CONSENT = from_now(NOW, days=300)


@pytest.fixture
def pluggy(requests_mock) -> FakePluggy:
    return FakePluggy(requests_mock)


def check_health():
    with freeze_time(NOW):
        return check_connection_health(
            account_id=ACCOUNT_ID,
            api_key=FAKE_PLUGGY_API_KEY,
            staleness_reference_time=NOW,
        )


def get_severities(health) -> list[ProblemSeverity]:
    return [problem.severity for problem in health.problems]


def test_connection_refreshed_this_morning_is_healthy(pluggy):
    pluggy.add_account(
        ACCOUNT_ID,
        last_updated_at=ago(NOW, hours=10),
        consent_expires_at=FAR_FUTURE_CONSENT,
    )

    health = check_health()

    assert health.is_healthy
    assert health.problems == []


def test_connection_frozen_for_days_is_broken(pluggy):
    pluggy.add_account(
        ACCOUNT_ID,
        last_updated_at=ago(NOW, days=3, hours=1),
        consent_expires_at=FAR_FUTURE_CONSENT,
    )

    health = check_health()

    assert get_severities(health) == [ProblemSeverity.BROKEN]
    assert "last refreshed 3.0 days ago" in health.problems[0].description


def test_staleness_alarm_states_the_staleness_it_tripped_on(pluggy):
    # KEV-87: the alarm used to print `timedelta.days`, which truncates, so a
    # connection 2.2 days stale read "2 days ago" beside a 2-day limit -- an
    # alarm that appears to contradict its own threshold invites doubting it.
    last_updated_at = ago(NOW, days=2.2)
    pluggy.add_account(
        ACCOUNT_ID,
        last_updated_at=last_updated_at,
        consent_expires_at=FAR_FUTURE_CONSENT,
    )

    health = check_health()

    description = health.problems[0].description
    assert get_severities(health) == [ProblemSeverity.BROKEN]
    assert (
        "last refreshed 2.2 days ago (52.8 hours, past the 48-hour limit) "
        f"at '{last_updated_at.isoformat()}'"
    ) in description
    # Everything else the alarm carries stays.
    assert "status 'UPDATED' and execution status 'SUCCESS'" in description
    assert "not a contradiction" in description


def test_staleness_alarm_just_past_the_limit_reads_as_past_it(pluggy):
    # Twenty minutes over is still over; the stated figure must show it.
    pluggy.add_account(
        ACCOUNT_ID,
        last_updated_at=ago(NOW, days=2, minutes=20),
        consent_expires_at=FAR_FUTURE_CONSENT,
    )

    health = check_health()

    assert "(48.3 hours, past the 48-hour limit)" in health.problems[0].description


def test_connection_just_past_two_days_is_broken(pluggy):
    # The 2-day threshold was chosen deliberately (KEV-75): every day of
    # detection latency eats the free-backfill runway. Loosening it should fail.
    pluggy.add_account(
        ACCOUNT_ID,
        last_updated_at=ago(NOW, days=2, hours=1),
        consent_expires_at=FAR_FUTURE_CONSENT,
    )

    health = check_health()

    assert get_severities(health) == [ProblemSeverity.BROKEN]


def test_connection_one_skipped_collection_is_still_healthy(pluggy):
    # A healthy connection that missed one daily collection must not alarm: a
    # check that cries wolf gets disabled, which happened here once already.
    pluggy.add_account(
        ACCOUNT_ID,
        last_updated_at=ago(NOW, days=1, hours=23),
        consent_expires_at=FAR_FUTURE_CONSENT,
    )

    health = check_health()

    assert health.is_healthy


def test_connection_that_never_synced_is_broken(pluggy):
    pluggy.add_account(
        ACCOUNT_ID,
        last_updated_at=None,
        consent_expires_at=FAR_FUTURE_CONSENT,
    )

    health = check_health()

    assert get_severities(health) == [ProblemSeverity.BROKEN]
    assert "never completed a sync" in health.problems[0].description


def test_error_reported_by_pluggy_is_broken_even_when_fresh(pluggy):
    pluggy.add_account(
        ACCOUNT_ID,
        last_updated_at=ago(NOW, hours=10),
        consent_expires_at=FAR_FUTURE_CONSENT,
        error={"code": "FAKE_ERROR_CODE", "message": "Fake connector failure"},
    )

    health = check_health()

    assert get_severities(health) == [ProblemSeverity.BROKEN]
    assert "Pluggy reports an error" in health.problems[0].description


def test_expired_consent_is_broken(pluggy):
    pluggy.add_account(
        ACCOUNT_ID,
        last_updated_at=ago(NOW, hours=10),
        consent_expires_at=ago(NOW, hours=1),
    )

    health = check_health()

    assert get_severities(health) == [ProblemSeverity.BROKEN]
    assert "consent expired" in health.problems[0].description


def test_consent_expiring_within_two_weeks_is_a_warning(pluggy):
    pluggy.add_account(
        ACCOUNT_ID,
        last_updated_at=ago(NOW, hours=10),
        consent_expires_at=from_now(NOW, days=10),
    )

    health = check_health()

    assert get_severities(health) == [ProblemSeverity.WARNING]
    assert not health.is_healthy


def test_connection_without_consent_is_healthy(pluggy):
    # Connectors outside Open Finance have no consent to expire.
    pluggy.add_account(
        ACCOUNT_ID,
        last_updated_at=ago(NOW, hours=10),
        consent_expires_at=None,
    )

    health = check_health()

    assert health.is_healthy


def test_account_id_that_no_longer_resolves_fails_loudly(pluggy):
    # The failure behind the ten-week outage: transactions for a dead account
    # id come back as an empty list with HTTP 200, so only this lookup catches it.
    pluggy.add_deleted_account(ACCOUNT_ID)

    with pytest.raises(RuntimeError, match="ACCOUNT_<n>_PLUGGY_ID"):
        check_health()


def test_connection_lookup_failure_fails_loudly(pluggy, requests_mock):
    pluggy.add_account(ACCOUNT_ID, last_updated_at=ago(NOW, hours=10))
    requests_mock.get(
        f"https://api.pluggy.ai/items/fake-item-for-{ACCOUNT_ID}",
        status_code=500,
        text="Internal Server Error",
    )

    with pytest.raises(RuntimeError, match="HTTP 500"):
        check_health()
