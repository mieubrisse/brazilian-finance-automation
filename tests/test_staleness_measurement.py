"""
Connection staleness must reflect the banks, not when GitHub started the run.

These drive the real `main()` end to end. Every HTTP call goes through
`requests_mock`, which raises `NoMockAddress` on any URL not registered here,
so no test can reach the real Pluggy or YNAB API.
"""

import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests_mock
from freezegun import freeze_time

# The application modules import each other by bare name (`from my_pluggy
# import ...`), exactly as `make run` executes them.
APP_DIRPATH = Path(__file__).resolve().parent.parent / "financas_automatizadas"
sys.path.insert(0, str(APP_DIRPATH))

PLUGGY_URL = "https://api.pluggy.ai/"
YNAB_URL = "https://api.youneedabudget.com/v1"
MOCKED_HOSTNAMES = {"api.pluggy.ai", "api.youneedabudget.com"}

PLUGGY_ACCOUNT_ID = "fake-pluggy-account"
PLUGGY_ITEM_ID = "fake-pluggy-item"
YNAB_BUDGET_ID = "fake-ynab-budget"

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

STALENESS_ALARM_TEXT = "The bank connection last refreshed"
ACTION_REQUIRED_TEXT = "ACTION REQUIRED"


@pytest.fixture
def run_sync(monkeypatch, capsys):
    monkeypatch.setenv("YNAB_TOKEN", "fake-ynab-token")
    monkeypatch.setenv("PLUGGY_CLIENT_ID", "fake-client-id")
    monkeypatch.setenv("PLUGGY_CLIENT_SECRET", "fake-client-secret")
    monkeypatch.setenv("ACCOUNT_0_NAME", "Itau Checking")
    monkeypatch.setenv("ACCOUNT_0_PLUGGY_ID", PLUGGY_ACCOUNT_ID)
    monkeypatch.setenv("ACCOUNT_0_YNAB_BUDGET_ID", YNAB_BUDGET_ID)
    monkeypatch.setenv("ACCOUNT_0_YNAB_ID", "fake-ynab-account")
    monkeypatch.setenv("LOOKBACK_DAYS", "")
    # Imported only once the environment is in place: ynab.py reads its token
    # at import time.
    main_module = importlib.import_module("main")

    def run(start_time, last_updated_at, scheduled_cron, run_attempt="1"):
        monkeypatch.setenv("SCHEDULED_CRON", scheduled_cron)
        # Set explicitly so a test run inside GitHub Actions is not steered by
        # the runner's own value.
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", run_attempt)
        with requests_mock.Mocker() as http_mock, freeze_time(start_time):
            _register_fake_apis(http_mock, last_updated_at)
            try:
                main_module.main()
                exit_code = 0
            except SystemExit as system_exit:
                exit_code = system_exit.code
            requested_hostnames = {
                request.hostname for request in http_mock.request_history
            }
        assert requested_hostnames <= MOCKED_HOSTNAMES
        return exit_code, capsys.readouterr().out

    return run


@pytest.mark.parametrize("delayed_start", DELAYED_STARTS, ids=DELAYED_START_IDS)
@pytest.mark.parametrize(
    "staleness_at_scheduled_time",
    [HEALTHY_STALENESS, NEAR_THRESHOLD_STALENESS, STALE_STALENESS],
    ids=["healthy-10h", "near-threshold-1.9d", "stale-3d"],
)
def test_delayed_run_reaches_the_same_verdict_as_an_on_time_run(
    run_sync, staleness_at_scheduled_time, delayed_start
):
    last_updated_at = SCHEDULED_TIME - staleness_at_scheduled_time

    on_time_exit_code, _ = run_sync(ON_TIME_START, last_updated_at, DAILY_CRON)
    delayed_exit_code, _ = run_sync(delayed_start, last_updated_at, DAILY_CRON)

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
    assert "[BROKEN]" in output


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


def _register_fake_apis(http_mock, last_updated_at):
    http_mock.post(f"{PLUGGY_URL}auth", json={"apiKey": "fake-api-key"})
    http_mock.get(
        f"{PLUGGY_URL}accounts/{PLUGGY_ACCOUNT_ID}",
        json={"id": PLUGGY_ACCOUNT_ID, "itemId": PLUGGY_ITEM_ID},
    )
    http_mock.get(
        f"{PLUGGY_URL}items/{PLUGGY_ITEM_ID}",
        json={
            "id": PLUGGY_ITEM_ID,
            "status": "UPDATED",
            "executionStatus": "SUCCESS",
            "error": None,
            "lastUpdatedAt": last_updated_at.isoformat().replace("+00:00", "Z"),
            "consentExpiresAt": "2027-06-01T00:00:00.000Z",
        },
    )
    http_mock.get(
        f"{PLUGGY_URL}transactions",
        json={
            "total": 1,
            "totalPages": 1,
            "page": 1,
            "results": [
                {
                    "id": "fake-transaction",
                    "description": "Transferência enviada|FULANO",
                    "amount": -42.5,
                    "date": "2026-10-14T12:00:00.000Z",
                    "type": "DEBIT",
                }
            ],
        },
    )
    http_mock.post(
        f"{YNAB_URL}/budgets/{YNAB_BUDGET_ID}/transactions",
        status_code=201,
        json={},
    )
