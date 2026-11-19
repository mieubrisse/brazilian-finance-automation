"""
Connection staleness must reflect the banks, not when GitHub started the run.

These drive the real `main()` end to end on the suite's strict HTTP mock (see
`conftest.py`), varying only when the run starts and how stale the connection
is at the scheduled time, and judge the exit code the workflow would see.
"""

from datetime import datetime, timedelta, timezone

import main
import pytest
from freezegun import freeze_time

from tests.fakes import (
    FAKE_YNAB_ACCOUNT_ID,
    FAKE_YNAB_BUDGET_ID,
    YNAB_CREATE_TRANSACTION_URL,
    FakePluggy,
    build_pluggy_transaction,
)

DAILY_CRON = "0 6 * * *"
SCHEDULED_TIME = datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc)
# A typical start, 12 minutes late; the latest GitHub has actually started this
# workflow, 3h16m (2026-08-28); and a hypothetical 10 hours, to show the
# verdict holds however late the start.
ON_TIME_START = datetime(2026, 10, 15, 6, 12, 27, tzinfo=timezone.utc)
OBSERVED_DELAYED_START = datetime(2026, 10, 15, 9, 16, 4, tzinfo=timezone.utc)
VERY_DELAYED_START = datetime(2026, 10, 15, 16, 13, 38, tzinfo=timezone.utc)
DELAYED_STARTS = [OBSERVED_DELAYED_START, VERY_DELAYED_START]
DELAYED_START_IDS = ["3h16m-late", "10h-late"]
ALL_STARTS = [ON_TIME_START, *DELAYED_STARTS]
ALL_START_IDS = ["on-time", *DELAYED_START_IDS]

HEALTHY_STALENESS = timedelta(hours=10)
# Healthy at the scheduled time, but past the 2-day threshold by either delayed
# start: the case where measuring up to the actual start raised a false alarm.
NEAR_THRESHOLD_STALENESS = timedelta(days=1.9)
JUST_PAST_THRESHOLD_STALENESS = timedelta(days=2, minutes=15)
STALE_STALENESS = timedelta(days=3)

FAR_FUTURE_CONSENT_EXPIRY = datetime(2027, 6, 1, tzinfo=timezone.utc)
ACCOUNT_ID = "fake-account-checking"

STALENESS_ALARM_TEXT = "[BROKEN] The bank connection last refreshed"
ACTION_REQUIRED_TEXT = "ACTION REQUIRED"
# Printed when an account's sync raises, which is how an unmocked request would
# surface, so no scenario here may produce it.
SYNC_FAILED_TEXT = "Sync failed"


@pytest.fixture
def run_sync(monkeypatch, capsys, requests_mock):
    monkeypatch.setenv("PLUGGY_CLIENT_ID", "fake-client-id")
    monkeypatch.setenv("PLUGGY_CLIENT_SECRET", "fake-client-secret")
    monkeypatch.setenv("ACCOUNT_0_NAME", "Fake Checking")
    monkeypatch.setenv("ACCOUNT_0_PLUGGY_ID", ACCOUNT_ID)
    monkeypatch.setenv("ACCOUNT_0_YNAB_BUDGET_ID", FAKE_YNAB_BUDGET_ID)
    monkeypatch.setenv("ACCOUNT_0_YNAB_ID", FAKE_YNAB_ACCOUNT_ID)
    requests_mock.post(YNAB_CREATE_TRANSACTION_URL, status_code=201, json={})
    pluggy = FakePluggy(requests_mock)

    def run(
        start_time,
        last_updated_at,
        scheduled_cron,
        run_attempt="1",
        consent_expires_at=FAR_FUTURE_CONSENT_EXPIRY,
    ):
        """Runs the sync and returns the exit code and log the workflow would see."""
        monkeypatch.setenv("SCHEDULED_CRON", scheduled_cron)
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", run_attempt)
        pluggy.add_account(
            ACCOUNT_ID,
            last_updated_at=last_updated_at,
            consent_expires_at=consent_expires_at,
            transactions=[
                build_pluggy_transaction(
                    transaction_id="fake-transaction-1",
                    amount=-12.5,
                    transaction_type="DEBIT",
                    description="Fake purchase|FAKE MERCHANT",
                    date=SCHEDULED_TIME - timedelta(days=1),
                )
            ],
        )
        with freeze_time(start_time):
            try:
                main.main()
                exit_code = 0
            except SystemExit as exit_request:
                exit_code = exit_request.code
        output = capsys.readouterr().out
        assert SYNC_FAILED_TEXT not in output
        return exit_code, output

    return run


@pytest.mark.parametrize("delayed_start", DELAYED_STARTS, ids=DELAYED_START_IDS)
@pytest.mark.parametrize(
    ("staleness_at_scheduled_time", "expected_exit_code"),
    [(HEALTHY_STALENESS, 0), (NEAR_THRESHOLD_STALENESS, 0), (STALE_STALENESS, 1)],
    ids=["healthy-10h", "near-threshold-1.9d", "stale-3d"],
)
def test_delayed_run_reaches_the_same_verdict_as_an_on_time_run(
    run_sync, staleness_at_scheduled_time, expected_exit_code, delayed_start
):
    last_updated_at = SCHEDULED_TIME - staleness_at_scheduled_time

    on_time_exit_code, _ = run_sync(ON_TIME_START, last_updated_at, DAILY_CRON)
    delayed_exit_code, _ = run_sync(delayed_start, last_updated_at, DAILY_CRON)

    assert on_time_exit_code == expected_exit_code
    assert delayed_exit_code == on_time_exit_code


@pytest.mark.parametrize("start_time", ALL_STARTS, ids=ALL_START_IDS)
@pytest.mark.parametrize(
    "staleness_at_scheduled_time",
    [HEALTHY_STALENESS, NEAR_THRESHOLD_STALENESS],
    ids=["healthy-10h", "near-threshold-1.9d"],
)
def test_healthy_connection_passes(run_sync, start_time, staleness_at_scheduled_time):
    last_updated_at = SCHEDULED_TIME - staleness_at_scheduled_time

    exit_code, output = run_sync(start_time, last_updated_at, DAILY_CRON)

    assert exit_code == 0
    assert ACTION_REQUIRED_TEXT not in output
    assert "1 total new transactions synced" in output


@pytest.mark.parametrize("start_time", ALL_STARTS, ids=ALL_START_IDS)
@pytest.mark.parametrize(
    "staleness_at_scheduled_time",
    [JUST_PAST_THRESHOLD_STALENESS, STALE_STALENESS],
    ids=["just-past-threshold-2d15m", "stale-3d"],
)
def test_stale_connection_fails(run_sync, start_time, staleness_at_scheduled_time):
    last_updated_at = SCHEDULED_TIME - staleness_at_scheduled_time

    exit_code, output = run_sync(start_time, last_updated_at, DAILY_CRON)

    assert exit_code == 1
    assert STALENESS_ALARM_TEXT in output


def test_late_start_is_reported_in_the_log(run_sync):
    last_updated_at = SCHEDULED_TIME - HEALTHY_STALENESS

    _, output = run_sync(OBSERVED_DELAYED_START, last_updated_at, DAILY_CRON)

    assert "measured up to '2026-10-15T06:00:00+00:00'" in output
    assert "started 3.3 hours after it" in output


@pytest.mark.parametrize(
    ("staleness_at_start", "expected_exit_code"),
    [(timedelta(days=1.9), 0), (timedelta(days=2.1), 1)],
    ids=["1.9d-passes", "2.1d-fails"],
)
def test_manual_dispatch_measures_staleness_up_to_its_start(
    run_sync, staleness_at_start, expected_exit_code
):
    manual_start = VERY_DELAYED_START
    last_updated_at = manual_start - staleness_at_start

    exit_code, _ = run_sync(manual_start, last_updated_at, scheduled_cron="")

    assert exit_code == expected_exit_code


# A re-run of a scheduled run keeps the schedule in its event payload, but it is
# someone checking the connection now -- the re-run the alarm itself asks for.
@pytest.mark.parametrize(
    ("staleness_at_start", "expected_exit_code"),
    [(timedelta(days=1.9), 0), (timedelta(days=2.1), 1)],
    ids=["1.9d-passes", "2.1d-fails"],
)
def test_rerun_measures_staleness_up_to_its_start(
    run_sync, staleness_at_start, expected_exit_code
):
    rerun_start = VERY_DELAYED_START
    last_updated_at = rerun_start - staleness_at_start

    exit_code, _ = run_sync(
        rerun_start, last_updated_at, DAILY_CRON, run_attempt="2"
    )

    assert exit_code == expected_exit_code


def test_consent_expiry_is_judged_against_the_real_clock(run_sync):
    # Expired by the time the delayed run starts, though not at 06:00Z: unlike
    # staleness, a deadline that has passed has passed.
    consent_expires_at = SCHEDULED_TIME + timedelta(hours=1)
    last_updated_at = SCHEDULED_TIME - HEALTHY_STALENESS

    exit_code, output = run_sync(
        OBSERVED_DELAYED_START,
        last_updated_at,
        DAILY_CRON,
        consent_expires_at=consent_expires_at,
    )

    assert exit_code == 1
    assert "The Open Finance consent expired" in output


def test_unrecognised_schedule_falls_back_to_now_and_still_syncs(run_sync):
    # 1.9 days stale at 06:00 but 2.3 days at the start: fails only if the
    # fallback measures up to the start, the stricter choice.
    last_updated_at = SCHEDULED_TIME - NEAR_THRESHOLD_STALENESS

    exit_code, output = run_sync(
        VERY_DELAYED_START, last_updated_at, scheduled_cron="0 */6 * * *"
    )

    assert exit_code == 1
    assert "not the daily" in output
    assert STALENESS_ALARM_TEXT in output
    assert "1 total new transactions synced" in output


@pytest.mark.parametrize(
    ("now", "expected_fire_time"),
    [
        (ON_TIME_START, SCHEDULED_TIME),
        (SCHEDULED_TIME, SCHEDULED_TIME),
        # Before today's fire time: the run belongs to yesterday's.
        (
            datetime(2026, 10, 15, 5, 59, tzinfo=timezone.utc),
            SCHEDULED_TIME - timedelta(days=1),
        ),
        # Delayed past midnight: still belongs to the previous day's schedule.
        (
            datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc),
            SCHEDULED_TIME,
        ),
    ],
    ids=["after-fire", "at-fire", "before-fire", "past-midnight"],
)
def test_latest_daily_cron_fire_time(now, expected_fire_time):
    from my_pluggy import _find_latest_daily_cron_fire_time

    assert _find_latest_daily_cron_fire_time(DAILY_CRON, now) == expected_fire_time


@pytest.mark.parametrize(
    "unrecognised_cron", ["0 */6 * * *", "0 6,18 * * *", "0 6 * * 1", "0 6 * *"]
)
def test_non_daily_cron_is_not_guessed_at(unrecognised_cron):
    from my_pluggy import _find_latest_daily_cron_fire_time

    assert _find_latest_daily_cron_fire_time(unrecognised_cron, ON_TIME_START) is None
