import requests
from decouple import config

from schemas import AccountSyncResult, Transaction, TransactionKind

auth_token = config("YNAB_TOKEN")

headers = {"Authorization": "Bearer " + auth_token, "Content-Type": "application/json"}

# YNAB answers a create with this when the account already holds a transaction
# carrying the same import_id. That is the expected outcome for most of what we
# send, because this sync re-sends its whole lookback window every day.
# Spec: https://api.ynab.com/papi/open_api_spec.yaml
ALREADY_IMPORTED_STATUS_CODE = requests.codes.conflict


def get_amount(transaction: Transaction) -> int:
    if transaction.kind == TransactionKind.DEBIT:
        return -abs(transaction.amount)
    else:  # TransactionKind is CREDIT
        return +abs(transaction.amount)


def send_transactions_to_ynab(
    transactions: list[Transaction],
    budget_id: str,
    account_id: str,
) -> AccountSyncResult:
    base_url = "https://api.youneedabudget.com/v1"
    url = f"{base_url}/budgets/{budget_id}/transactions"

    created_count = 0
    already_imported_count = 0

    for transaction in transactions:
        amount = get_amount(transaction)

        payload = {
            "transaction": {
                "account_id": account_id,
                "date": transaction.date.strftime("%Y-%m-%d"),
                "amount": amount,
                "payee_id": None,
                "payee_name": transaction.payee,
                "category_id": None,
                "memo": transaction.description,
                "cleared": "cleared",
                "approved": False,
                "import_id": transaction.external_id,
            }
        }

        response = requests.post(url, json=payload, headers=headers)

        if response.status_code == ALREADY_IMPORTED_STATUS_CODE:
            already_imported_count += 1
            continue

        if not response.ok:
            raise RuntimeError(
                f"YNAB returned HTTP {response.status_code} when creating the "
                f"transaction dated '{transaction.date}' for payee "
                f"'{transaction.payee}' in account '{account_id}'. "
                f"Response body: '{response.text}'"
            )

        created_count += 1

    return AccountSyncResult(
        created_count=created_count,
        already_imported_count=already_imported_count,
    )
