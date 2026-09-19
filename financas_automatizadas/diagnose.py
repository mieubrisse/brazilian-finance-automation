"""
Reports what the configured Pluggy credentials can actually see.

Pluggy exposes no endpoint that lists items, so an application's contents cannot
be discovered -- item ids have to be read off the dashboard and handed in. This
script takes them and answers the questions the sync itself cannot: do these
credentials see this item at all, is it healthy, and what are its accounts?

Account ids are printed abbreviated. This runs in a public repository, its logs
are world-readable, and the full ids live in repository secrets.
"""

import os
import sys

import requests
from decouple import config

from my_pluggy import PLUGGY_URL, get_api_key

ITEM_IDS_ENVVAR = "ITEM_IDS"

# Enough of an id to match a row against the dashboard with confidence, without
# publishing a usable identifier to a public log.
ID_PREFIX_LENGTH = 8
ID_SUFFIX_LENGTH = 4


def main() -> None:
    raw_item_ids = os.environ.get(ITEM_IDS_ENVVAR, "").strip()
    if not raw_item_ids:
        print(
            f"Error: set {ITEM_IDS_ENVVAR} to a comma-separated list of Pluggy "
            "item ids, read from the Items list of your application at "
            "https://dashboard.pluggy.ai"
        )
        sys.exit(1)

    item_ids = [
        item_id.strip() for item_id in raw_item_ids.split(",") if item_id.strip()
    ]

    print("Authenticating with Pluggy...")
    api_key = get_api_key(
        client_id=config("PLUGGY_CLIENT_ID"),
        client_secret=config("PLUGGY_CLIENT_SECRET"),
    )
    print("✅ Authentication succeeded -- the client id and secret are valid.")
    print("")

    for item_id in item_ids:
        describe_item(item_id, api_key)


def describe_item(item_id: str, api_key: str) -> None:
    print(f"=== Item {abbreviate(item_id)} ===")

    item_response = requests.get(
        url=f"{PLUGGY_URL}items/{item_id}",
        headers={"accept": "application/json", "X-API-KEY": api_key},
    )
    if not item_response.ok:
        print(
            f"❌ These credentials CANNOT see this item: HTTP "
            f"{item_response.status_code} -- {item_response.text}"
        )
        print("")
        return

    item = item_response.json()
    connector = item.get("connector") or {}
    print(f"  Bank:             {connector.get('name')}")
    print(f"  Status:           {item.get('status')}")
    print(f"  Execution status: {item.get('executionStatus')}")
    print(f"  Created at:       {item.get('createdAt')}")
    print(f"  Last synced:      {item.get('lastUpdatedAt')}")
    print(f"  Consent expires:  {item.get('consentExpiresAt')}")
    print(f"  Next auto-sync:   {item.get('nextAutoSyncAt')}")
    print(f"  Products:         {item.get('products')}")
    print(f"  Error:            {item.get('error')}")

    accounts_response = requests.get(
        url=f"{PLUGGY_URL}accounts",
        params={"itemId": item_id},
        headers={"accept": "application/json", "X-API-KEY": api_key},
    )
    if not accounts_response.ok:
        print(
            f"  ❌ Could not list accounts: HTTP "
            f"{accounts_response.status_code} -- {accounts_response.text}"
        )
        print("")
        return

    accounts = accounts_response.json().get("results", [])
    print(f"  Accounts ({len(accounts)}):")
    for account in accounts:
        print(
            f"    - {account.get('name')} [{account.get('type')}"
            f"/{account.get('subtype')}] id={abbreviate(account.get('id', ''))} "
            f"balance={account.get('balance')} {account.get('currencyCode')}"
        )
    print("")


def abbreviate(identifier: str) -> str:
    if len(identifier) <= ID_PREFIX_LENGTH + ID_SUFFIX_LENGTH:
        return identifier
    return f"{identifier[:ID_PREFIX_LENGTH]}...{identifier[-ID_SUFFIX_LENGTH:]}"


if __name__ == "__main__":
    main()
