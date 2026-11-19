"""
The whole sync, end to end, judged by the one thing that reaches Kevin: the
exit code. A non-zero exit turns the scheduled run red, which is the only thing
that produces a notification. A problem printed under a zero exit is never seen.
"""

from datetime import datetime, timezone

import main
import pytest
from freezegun import freeze_time

from tests.fakes import (
    FAKE_YNAB_ACCOUNT_ID,
    FAKE_YNAB_BUDGET_ID,
    YNAB_CREATE_TRANSACTION_URL,
    FakePluggy,
    ago,
    build_pluggy_transaction,
    from_now,
)

NOW = datetime(2026, 3, 10, 6, 15, tzinfo=timezone.utc)
FAR_FUTURE_CONSENT = from_now(NOW, days=300)

CHECKING_ACCOUNT_ID = "fake-account-checking"
CARD_ACCOUNT_ID = "fake-account-card"


@pytest.fixture
def pluggy(requests_mock) -> FakePluggy:
    return FakePluggy(requests_mock)


@pytest.fixture
def ynab_create_transaction(requests_mock):
    return requests_mock.post(YNAB_CREATE_TRANSACTION_URL, status_code=201, json={})


@pytest.fixture(autouse=True)
def pluggy_credentials(monkeypatch):
    monkeypatch.setenv("PLUGGY_CLIENT_ID", "fake-client-id")
    monkeypatch.setenv("PLUGGY_CLIENT_SECRET", "fake-client-secret")


def configure_account(
    monkeypatch, index: int, name: str, pluggy_account_id: str
) -> None:
    monkeypatch.setenv(f"ACCOUNT_{index}_NAME", name)
    monkeypatch.setenv(f"ACCOUNT_{index}_PLUGGY_ID", pluggy_account_id)
    monkeypatch.setenv(f"ACCOUNT_{index}_YNAB_BUDGET_ID", FAKE_YNAB_BUDGET_ID)
    monkeypatch.setenv(f"ACCOUNT_{index}_YNAB_ID", FAKE_YNAB_ACCOUNT_ID)


def run_sync() -> int:
    """Runs the sync and returns the exit code the workflow would see."""
    with freeze_time(NOW):
        try:
            main.main()
        except SystemExit as exit_request:
            return exit_request.code
    return 0


def test_healthy_connection_syncs_and_exits_zero(
    monkeypatch, capsys, pluggy, ynab_create_transaction
):
    configure_account(monkeypatch, 0, "Fake Checking", CHECKING_ACCOUNT_ID)
    pluggy.add_account(
        CHECKING_ACCOUNT_ID,
        last_updated_at=ago(NOW, hours=10),
        consent_expires_at=FAR_FUTURE_CONSENT,
        transactions=[
            build_pluggy_transaction(
                transaction_id="fake-transaction-1",
                amount=-12.5,
                transaction_type="DEBIT",
                description="Fake purchase|FAKE MERCHANT",
                date=ago(NOW, days=1),
            )
        ],
    )

    exit_code = run_sync()

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Connection: status 'UPDATED'" in output
    assert "ACTION REQUIRED" not in output
    assert ynab_create_transaction.call_count == 1


def test_stale_connection_fails_the_run(
    monkeypatch, capsys, pluggy, ynab_create_transaction
):
    configure_account(monkeypatch, 0, "Fake Checking", CHECKING_ACCOUNT_ID)
    pluggy.add_account(
        CHECKING_ACCOUNT_ID,
        last_updated_at=ago(NOW, days=3),
        consent_expires_at=FAR_FUTURE_CONSENT,
    )

    exit_code = run_sync()

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "THE BRAZILIAN SYNC IS BROKEN" in output
    # Name the cause: main reports any crash as BROKEN, so the banner alone
    # would pass even with the staleness check disarmed.
    assert "[BROKEN] The bank connection last refreshed" in output


def test_reported_error_fails_the_run(
    monkeypatch, capsys, pluggy, ynab_create_transaction
):
    configure_account(monkeypatch, 0, "Fake Checking", CHECKING_ACCOUNT_ID)
    pluggy.add_account(
        CHECKING_ACCOUNT_ID,
        last_updated_at=ago(NOW, hours=10),
        consent_expires_at=FAR_FUTURE_CONSENT,
        error={"code": "FAKE_ERROR_CODE", "message": "Fake connector failure"},
    )

    exit_code = run_sync()

    assert exit_code == 1
    assert "[BROKEN] Pluggy reports an error" in capsys.readouterr().out


def test_consent_about_to_expire_fails_the_run(
    monkeypatch, capsys, pluggy, ynab_create_transaction
):
    # Still red: a warning inside a green run is a warning nobody reads.
    configure_account(monkeypatch, 0, "Fake Checking", CHECKING_ACCOUNT_ID)
    pluggy.add_account(
        CHECKING_ACCOUNT_ID,
        last_updated_at=ago(NOW, hours=10),
        consent_expires_at=from_now(NOW, days=10),
    )

    exit_code = run_sync()

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "ABOUT TO BREAK" in output
    assert "[WARNING]" in output


def test_deleted_account_fails_the_run_without_hiding_the_others(
    monkeypatch, capsys, pluggy, ynab_create_transaction
):
    configure_account(monkeypatch, 0, "Fake Card", CARD_ACCOUNT_ID)
    configure_account(monkeypatch, 1, "Fake Checking", CHECKING_ACCOUNT_ID)
    pluggy.add_deleted_account(CARD_ACCOUNT_ID)
    pluggy.add_account(
        CHECKING_ACCOUNT_ID,
        last_updated_at=ago(NOW, hours=10),
        consent_expires_at=FAR_FUTURE_CONSENT,
        transactions=[
            build_pluggy_transaction(
                transaction_id="fake-transaction-2",
                amount=100.0,
                transaction_type="CREDIT",
                description="Fake transfer received|FAKE SENDER",
                date=ago(NOW, days=1),
            )
        ],
    )

    exit_code = run_sync()

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "Pluggy has no account with the configured ID" in output
    # The broken card must not stop the healthy checking account syncing.
    assert ynab_create_transaction.call_count == 1
