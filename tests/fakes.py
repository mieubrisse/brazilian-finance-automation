"""
Stand-ins for Pluggy and YNAB, registered on the suite's strict HTTP mock.

The repository is public, so every id, token and transaction here is
synthetic. Never paste a real one in, not even one copied from a run log.
"""

from datetime import datetime, timedelta

import my_pluggy

FAKE_PLUGGY_API_KEY = "fake-pluggy-api-key"
FAKE_YNAB_BUDGET_ID = "fake-ynab-budget"
FAKE_YNAB_ACCOUNT_ID = "fake-ynab-account"

YNAB_CREATE_TRANSACTION_URL = (
    f"https://api.youneedabudget.com/v1/budgets/{FAKE_YNAB_BUDGET_ID}/transactions"
)


class FakePluggy:
    """
    Describes bank accounts as Pluggy would report them: the account record,
    the connection ("item") behind it, and its transactions.
    """

    def __init__(self, requests_mock):
        self._requests_mock = requests_mock
        requests_mock.post(
            f"{my_pluggy.PLUGGY_URL}auth",
            json={"apiKey": FAKE_PLUGGY_API_KEY},
        )

    def add_account(
        self,
        account_id: str,
        last_updated_at: datetime | None,
        error: dict | None = None,
        consent_expires_at: datetime | None = None,
        transactions: list[dict] | None = None,
        total_pages: int = 1,
    ) -> None:
        item_id = f"fake-item-for-{account_id}"
        self._requests_mock.get(
            f"{my_pluggy.PLUGGY_URL}accounts/{account_id}",
            json={"id": account_id, "itemId": item_id},
        )
        self._requests_mock.get(
            f"{my_pluggy.PLUGGY_URL}items/{item_id}",
            json={
                "id": item_id,
                # The reassuring values Pluggy reports even for a dead
                # connection, so no test can pass by trusting them.
                "status": "UPDATED",
                "executionStatus": "SUCCESS",
                "lastUpdatedAt": format_pluggy_timestamp(last_updated_at),
                "consentExpiresAt": format_pluggy_timestamp(consent_expires_at),
                "error": error,
            },
        )
        results = transactions or []
        self._requests_mock.get(
            f"{my_pluggy.PLUGGY_URL}transactions?accountId={account_id}",
            json={
                "total": len(results),
                "totalPages": total_pages,
                "page": 1,
                "results": results,
            },
        )

    def add_deleted_account(self, account_id: str) -> None:
        """An account id that no longer resolves, as after a reconnect."""
        self._requests_mock.get(
            f"{my_pluggy.PLUGGY_URL}accounts/{account_id}",
            status_code=404,
            json={"message": "Account not found"},
        )


def format_pluggy_timestamp(timestamp: datetime | None) -> str | None:
    """Pluggy's wire format: ISO 8601 in UTC with a trailing Z."""
    if timestamp is None:
        return None
    return timestamp.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def build_pluggy_transaction(
    transaction_id: str,
    amount: float,
    transaction_type: str,
    description: str,
    date: datetime,
) -> dict:
    return {
        "id": transaction_id,
        "description": description,
        "amount": amount,
        "date": format_pluggy_timestamp(date),
        "type": transaction_type,
    }


def ago(now: datetime, **elapsed) -> datetime:
    return now - timedelta(**elapsed)


def from_now(now: datetime, **remaining) -> datetime:
    return now + timedelta(**remaining)
