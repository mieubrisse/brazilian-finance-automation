from datetime import date, datetime, timedelta, timezone

import requests

from schemas import ConnectionHealth, ConnectionProblem, ProblemSeverity, Transaction

PLUGGY_URL = "https://api.pluggy.ai/"

# Using 6 days instead of 7 because of OpenFinance rate limits:
# https://docs.pluggy.ai/docs/rate-limits-of
TRANSACTION_LOOKBACK_DAYS = 6

# DISABLED, deliberately. This was meant to catch a connection that had quietly
# stopped feeding us data. It is not safe to fail on, because Pluggy's
# `lastUpdatedAt` appears to track when the item record was last edited rather
# than when its bank data last refreshed -- Kevin's reading, and consistent with
# two of his items reporting timestamps months old while the sync was working.
# Failing on it would have produced a daily false alarm, and an alarm that cries
# wolf is worse than no alarm at all: it trains you to ignore the real one.
#
# The value is still printed on every run, so the signal is not lost -- only the
# failure. Re-enable by restoring it to _find_connection_problems once Pluggy
# confirms what the field actually measures.

# How many transactions a single request asks Pluggy for. This sync only ever
# reads the first page, so anything beyond this in one window would be dropped.
TRANSACTION_PAGE_SIZE = 50

# Open Finance consents expire, and when one does the connection goes dark
# silently. Warn while there is still time to renew it, but keep the window
# short so the warning stays urgent instead of becoming background noise.
CONSENT_EXPIRY_WARNING_DAYS = 14


def get_api_key(client_id: str, client_secret: str) -> str:
    response = requests.post(
        f"{PLUGGY_URL}auth",
        json={
            "clientId": client_id,
            "clientSecret": client_secret,
        },
        headers={
            "accept": "application/json",
            "content-type": "application/json",
        },
    )
    data = _get_json_or_raise(response, "authenticating with Pluggy")

    api_key = data.get("apiKey")
    if api_key is None:
        raise RuntimeError(
            "Pluggy's authentication response did not contain an API key. "
            f"Response body: '{response.text}'"
        )

    return api_key


def get_transactions(account_id: str, api_key: str) -> list[Transaction]:
    today = date.today()
    lookback_start_date = today - timedelta(days=TRANSACTION_LOOKBACK_DAYS)

    response = requests.get(
        url=f"{PLUGGY_URL}transactions",
        params={
            "accountId": account_id,
            "from": lookback_start_date.strftime("%Y-%m-%d"),
            "to": today.strftime("%Y-%m-%d"),
            "page": 1,
            "pageSize": TRANSACTION_PAGE_SIZE,
        },
        headers={
            "accept": "application/json",
            "X-API-KEY": api_key,
        },
    )
    data = _get_json_or_raise(
        response,
        f"fetching transactions for account '{account_id}'",
    )

    # Silently dropping the overflow is precisely the kind of quiet
    # under-reporting this sync exists to stop doing, so refuse the run instead.
    # This matters most during a wide-window backfill, where a single window can
    # hold far more than one page.
    total_pages = data.get("totalPages", 1)
    if total_pages > 1:
        raise RuntimeError(
            f"Pluggy has {total_pages} pages of transactions in this window, but "
            f"this sync only reads the first {TRANSACTION_PAGE_SIZE}. Some "
            "transactions would be missed, so the run is being failed instead of "
            "silently under-reporting. Narrow the date window, or add pagination."
        )

    return normalize_transactions(data["results"])


def check_connection_health(account_id: str, api_key: str) -> ConnectionHealth:
    """
    Inspects the Pluggy connection behind an account.

    Asking Pluggy for transactions succeeds even when the underlying bank
    connection has died -- it just returns an empty list, which is
    indistinguishable from a quiet week. This looks at the connection itself so
    that a dead connection can be reported as the failure it is.
    """
    account = _fetch_account(account_id, api_key)
    item = _fetch_item(account["itemId"], api_key)

    now = datetime.now(timezone.utc)
    last_updated_at = _parse_optional_timestamp(item.get("lastUpdatedAt"))
    consent_expires_at = _parse_optional_timestamp(item.get("consentExpiresAt"))

    problems = []
    problems.extend(_find_reported_error_problems(item))
    problems.extend(_find_consent_problems(consent_expires_at, now))

    return ConnectionHealth(
        status=item.get("status", "UNKNOWN"),
        execution_status=item.get("executionStatus", "UNKNOWN"),
        last_updated_at=last_updated_at,
        consent_expires_at=consent_expires_at,
        problems=problems,
    )


def normalize_transactions(pluggy_transactions) -> list[Transaction]:
    normalized_transactions = []

    for transaction in pluggy_transactions:

        description = transaction["description"].strip()
        description_parts = description.split("|")
        payee = None
        if len(description_parts) >= 2:
            # The majority of transaction descriptions are like this:
            # Transferência enviada|JUAN CARLOS
            payee = description_parts[1]
        else:
            # Other times (maybe with autopay?) we get descriptions like this:
            # VIVO (MÓVEL + COMBOS)
            payee = description

        new_transaction = Transaction(
            external_id=transaction["id"],
            amount=int(
                transaction["amount"] * 1000
            ),  # ynab data format ref: https://api.ynab.com/#response-format
            description=transaction["description"].strip(),
            date=datetime.fromisoformat(
                transaction["date"].replace("Z", "+00:00")
            ).date(),
            kind=transaction["type"],
            payee=payee,
        )

        normalized_transactions.append(new_transaction)

    return normalized_transactions


def _find_reported_error_problems(item: dict) -> list[ConnectionProblem]:
    """
    Surfaces an error Pluggy is already reporting about the connection.

    Deliberately keyed off the `error` field rather than off `status`. A healthy
    status proves nothing -- an item serving no data at all still reports
    `UPDATED` / `SUCCESS` -- but a populated error means Pluggy itself is saying
    something went wrong, which does not need interpreting.
    """
    reported_error = item.get("error")
    if not reported_error:
        return []

    return [
        ConnectionProblem(
            severity=ProblemSeverity.BROKEN,
            description=(
                f"Pluggy reports an error on this bank connection: "
                f"'{reported_error}'. Connection status is "
                f"'{item.get('status')}' and execution status is "
                f"'{item.get('executionStatus')}'."
            ),
        )
    ]


def _find_consent_problems(
    consent_expires_at: datetime | None,
    now: datetime,
) -> list[ConnectionProblem]:
    # Connectors that don't go through Open Finance have no consent to expire.
    if consent_expires_at is None:
        return []

    time_until_expiry = consent_expires_at - now

    if time_until_expiry <= timedelta(0):
        return [
            ConnectionProblem(
                severity=ProblemSeverity.BROKEN,
                description=(
                    "The Open Finance consent expired at "
                    f"'{consent_expires_at.isoformat()}'. The bank must be "
                    "reconnected in the Pluggy dashboard before any "
                    "transactions can sync again."
                ),
            )
        ]

    if time_until_expiry <= timedelta(days=CONSENT_EXPIRY_WARNING_DAYS):
        return [
            ConnectionProblem(
                severity=ProblemSeverity.WARNING,
                description=(
                    f"The Open Finance consent expires in "
                    f"{time_until_expiry.days} days (at "
                    f"'{consent_expires_at.isoformat()}'). Renew it in the "
                    "Pluggy dashboard before it lapses, or the sync will go "
                    "silent."
                ),
            )
        ]

    return []


def _fetch_account(account_id: str, api_key: str) -> dict:
    response = requests.get(
        url=f"{PLUGGY_URL}accounts/{account_id}",
        headers={
            "accept": "application/json",
            "X-API-KEY": api_key,
        },
    )

    # Worth calling out specifically, because asking for TRANSACTIONS belonging
    # to an account ID that no longer exists returns an empty list and HTTP 200,
    # which is how this failure stayed invisible for months.
    if response.status_code == requests.codes.not_found:
        raise RuntimeError(
            "Pluggy has no account with the configured ID. The bank connection "
            "behind it was deleted or recreated, and recreating a connection "
            "mints brand new account IDs. Reconnect the bank at "
            "https://dashboard.pluggy.ai AND THEN update the matching "
            "ACCOUNT_<n>_PLUGGY_ID repository secret -- reconnecting on its own "
            "will NOT fix this."
        )

    return _get_json_or_raise(response, f"fetching account '{account_id}'")


def _fetch_item(item_id: str, api_key: str) -> dict:
    response = requests.get(
        url=f"{PLUGGY_URL}items/{item_id}",
        headers={
            "accept": "application/json",
            "X-API-KEY": api_key,
        },
    )
    return _get_json_or_raise(response, f"fetching bank connection '{item_id}'")


def _parse_optional_timestamp(raw_timestamp: str | None) -> datetime | None:
    if raw_timestamp is None:
        return None
    return datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))


def _get_json_or_raise(response: requests.Response, description: str) -> dict:
    if not response.ok:
        raise RuntimeError(
            f"Pluggy returned HTTP {response.status_code} when {description}. "
            f"Response body: '{response.text}'"
        )
    return response.json()
