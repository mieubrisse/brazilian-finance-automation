"""
Moving transactions: the guards against silently under-reporting what Pluggy
holds, and against duplicating what YNAB already has.
"""

from datetime import date, datetime, timezone

import pytest
from freezegun import freeze_time
from my_pluggy import DEFAULT_LOOKBACK_DAYS, get_transactions
from schemas import Transaction, TransactionKind
from ynab import send_transactions_to_ynab

from tests.fakes import (
    FAKE_PLUGGY_API_KEY,
    FAKE_YNAB_ACCOUNT_ID,
    FAKE_YNAB_BUDGET_ID,
    YNAB_CREATE_TRANSACTION_URL,
    FakePluggy,
    ago,
    build_pluggy_transaction,
)

NOW = datetime(2026, 3, 10, 6, 15, tzinfo=timezone.utc)
ACCOUNT_ID = "fake-account-checking"


def build_transaction(
    external_id: str, kind: TransactionKind, amount: int
) -> Transaction:
    return Transaction(
        external_id=external_id,
        amount=amount,
        description="Fake description",
        date=date(2026, 3, 9),
        kind=kind,
        payee="FAKE PAYEE",
    )


def test_more_transactions_than_one_page_fails_rather_than_dropping_some(requests_mock):
    pluggy = FakePluggy(requests_mock)
    pluggy.add_account(ACCOUNT_ID, last_updated_at=ago(NOW, hours=10), total_pages=2)

    with freeze_time(NOW), pytest.raises(RuntimeError, match="2 pages of transactions"):
        get_transactions(account_id=ACCOUNT_ID, api_key=FAKE_PLUGGY_API_KEY)


def test_blank_lookback_falls_back_to_the_default_window(requests_mock, monkeypatch):
    # GitHub sets an unset optional workflow input to "" on scheduled runs, and
    # casting that straight to int would crash the daily sync.
    monkeypatch.setenv("LOOKBACK_DAYS", "")
    pluggy = FakePluggy(requests_mock)
    pluggy.add_account(
        ACCOUNT_ID,
        last_updated_at=ago(NOW, hours=10),
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

    with freeze_time(NOW):
        transactions = get_transactions(
            account_id=ACCOUNT_ID, api_key=FAKE_PLUGGY_API_KEY
        )

    transactions_request = requests_mock.last_request
    expected_start = ago(NOW, days=DEFAULT_LOOKBACK_DAYS).strftime("%Y-%m-%d")
    assert transactions_request.qs["from"] == [expected_start]
    assert [transaction.payee for transaction in transactions] == ["FAKE MERCHANT"]


def test_every_transaction_carries_its_pluggy_id_as_the_import_id(requests_mock):
    # YNAB de-duplicates on import_id. Without it, re-sending the daily window
    # would duplicate every transaction every day.
    create_mock = requests_mock.post(
        YNAB_CREATE_TRANSACTION_URL, status_code=201, json={}
    )
    transactions = [
        build_transaction("fake-transaction-debit", TransactionKind.DEBIT, 12500),
        build_transaction("fake-transaction-credit", TransactionKind.CREDIT, 100000),
    ]

    result = send_transactions_to_ynab(
        transactions=transactions,
        budget_id=FAKE_YNAB_BUDGET_ID,
        account_id=FAKE_YNAB_ACCOUNT_ID,
    )

    sent = [request.json()["transaction"] for request in create_mock.request_history]
    assert [transaction["import_id"] for transaction in sent] == [
        "fake-transaction-debit",
        "fake-transaction-credit",
    ]
    assert [transaction["amount"] for transaction in sent] == [-12500, 100000]
    assert result.created_count == 2
    assert result.already_imported_count == 0


def test_already_imported_transactions_are_counted_not_created(requests_mock):
    requests_mock.post(YNAB_CREATE_TRANSACTION_URL, status_code=409, json={})

    result = send_transactions_to_ynab(
        transactions=[
            build_transaction("fake-transaction-1", TransactionKind.DEBIT, 1000)
        ],
        budget_id=FAKE_YNAB_BUDGET_ID,
        account_id=FAKE_YNAB_ACCOUNT_ID,
    )

    assert result.created_count == 0
    assert result.already_imported_count == 1


def test_rejected_transaction_fails_loudly(requests_mock):
    requests_mock.post(YNAB_CREATE_TRANSACTION_URL, status_code=400, text="Bad request")

    with pytest.raises(RuntimeError, match="HTTP 400"):
        send_transactions_to_ynab(
            transactions=[
                build_transaction("fake-transaction-1", TransactionKind.DEBIT, 1000)
            ],
            budget_id=FAKE_YNAB_BUDGET_ID,
            account_id=FAKE_YNAB_ACCOUNT_ID,
        )
