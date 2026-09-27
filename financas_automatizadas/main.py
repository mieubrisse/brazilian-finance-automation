from decouple import config

import ynab
from my_pluggy import (
    RECENT_WINDOW_MAX_DAYS,
    check_connection_health,
    get_api_key,
    get_lookback_days,
    get_transactions,
)
from schemas import (
    AccountSyncResult,
    ConnectionHealth,
    ConnectionProblem,
    ProblemSeverity,
)

import sys

NAME_ENVVAR_SUFFIX = "NAME"
PLUGGY_ID_ENVVAR_SUFFIX = "PLUGGY_ID"
YNAB_BUDGET_ID_ENVVAR_SUFFIX = "YNAB_BUDGET_ID"
YNAB_ID_ENVVAR_SUFFIX = "YNAB_ID"
ACCT_ENVVAR_SUFFIXES = [
    NAME_ENVVAR_SUFFIX,
    PLUGGY_ID_ENVVAR_SUFFIX,
    YNAB_BUDGET_ID_ENVVAR_SUFFIX,
    YNAB_ID_ENVVAR_SUFFIX,
]

def get_bank_accounts_from_env():
    """
    Parses the user's provided envvars into bank accounts, ensuring that we have
    at least one bank account, and any bank account has all the information we need
    """
    bank_accounts = []
    idx = 0
    while True:
        envvars = map(lambda suffix: f'ACCOUNT_{idx}_{suffix}', ACCT_ENVVAR_SUFFIXES)

        vars_present = 0
        envvar_to_value = {}
        suffix_to_value = {}
        for i, envvar in enumerate(envvars):
            value = config(envvar, default=None)
            if value is not None:
                vars_present += 1
            envvar_to_value[envvar] = value
            suffix_to_value[ACCT_ENVVAR_SUFFIXES[i]] = value

        if vars_present == 0:
            # If we haven't seen a signle account yet, throw an error
            if idx == 0:
                print("Error: You must define at least one bank account using the following envvars: " + ", ".join(envvars))
                sys.exit(1)

            # Otherwise, the user's done defining bank account info
            break

        # We have at least one piece of information; verify that all fields
        # are filled out and build the return object
        missing_info = False
        for envvar, value in envvar_to_value.items():
            if value is None:
                print("Error: Missing required bank information envvar: " + envvar)
                missing_info = True
        if missing_info:
            sys.exit(1)

        bank_accounts.append(suffix_to_value)
        idx += 1

    return bank_accounts

def main() -> None:
    PLUGGY_CLIENT_ID = config("PLUGGY_CLIENT_ID")
    PLUGGY_CLIENT_SECRET = config("PLUGGY_CLIENT_SECRET")
    pluggy_api_key = get_api_key(
        client_id=PLUGGY_CLIENT_ID,
        client_secret=PLUGGY_CLIENT_SECRET,
    )

    # Mapping envvar suffix -> value
    bank_accounts = get_bank_accounts_from_env()

    total_created = 0
    total_already_imported = 0
    problems_by_account_name = {}

    announce_window()

    print("Syncing...")
    print("")
    for account in bank_accounts:
        name = account[NAME_ENVVAR_SUFFIX]

        print(f'⏳ {name}')

        try:
            health, result = sync_account(
                pluggy_account_id=account[PLUGGY_ID_ENVVAR_SUFFIX],
                ynab_budget_id=account[YNAB_BUDGET_ID_ENVVAR_SUFFIX],
                ynab_account_id=account[YNAB_ID_ENVVAR_SUFFIX],
                pluggy_api_key=pluggy_api_key,
            )
        except Exception as sync_error:
            # One broken account must not hide the state of the others, so the
            # failure is recorded and reported with everything else at the end.
            print(f"❌ Sync failed: {sync_error}")
            print("")
            problems_by_account_name[name] = [
                ConnectionProblem(
                    severity=ProblemSeverity.BROKEN,
                    description=str(sync_error),
                )
            ]
            continue

        print(
            f"   Connection: status '{health.status}', "
            f"last synced {describe_timestamp(health.last_updated_at)}"
        )
        print(
            f"✅ {result.created_count} new transactions synced "
            f"({result.already_imported_count} already imported)"
        )
        print("")

        total_created += result.created_count
        total_already_imported += result.already_imported_count

        if not health.is_healthy:
            problems_by_account_name[name] = health.problems

    print(
        f"🎉 {total_created} total new transactions synced "
        f"({total_already_imported} already imported)"
    )

    if problems_by_account_name:
        report_problems_and_exit(problems_by_account_name)

def announce_window() -> None:
    """
    States the window being requested, and whether it costs a rationed request.

    Worth shouting about: a window reaching past the recent boundary spends one
    of only four non-recent Open Finance requests allowed per product per month,
    and exhausting them stops the bank syncing until the next calendar month. A
    run that quietly did that would be a nasty surprise.
    """
    lookback_days = get_lookback_days()
    print(f"Looking back {lookback_days} days.")

    if lookback_days <= RECENT_WINDOW_MAX_DAYS:
        return

    print("")
    print(
        f"⚠️  BACKFILL MODE: {lookback_days} days reaches past the {RECENT_WINDOW_MAX_DAYS}-day "
        "recent window, so this run spends ONE of the four non-recent Open "
        "Finance requests allowed per product per calendar month. Exhaust them "
        "and the bank stops syncing until next month. Do not re-run this "
        "casually."
    )
    print("")


def sync_account(
    pluggy_account_id: str,
    ynab_budget_id: str,
    ynab_account_id: str,
    pluggy_api_key: str,
) -> tuple[ConnectionHealth, AccountSyncResult]:
    # The health of the bank connection is checked separately from the
    # transactions themselves, because Pluggy happily returns an empty
    # transaction list for a connection that has been dead for months.
    health = check_connection_health(
        account_id=pluggy_account_id,
        api_key=pluggy_api_key,
    )

    transactions = get_transactions(
        account_id=pluggy_account_id,
        api_key=pluggy_api_key,
    )

    result = ynab.send_transactions_to_ynab(
        transactions=transactions,
        budget_id=ynab_budget_id,
        account_id=ynab_account_id,
    )

    return health, result

def describe_timestamp(timestamp) -> str:
    if timestamp is None:
        return "never"
    return f"'{timestamp.isoformat()}'"

def report_problems_and_exit(problems_by_account_name: dict) -> None:
    """
    Prints every problem found and exits non-zero.

    Exiting non-zero is the whole point: it turns the scheduled run red, which
    is the only thing that produces a notification. A run that prints a warning
    and exits 0 is a run nobody ever finds out about.
    """
    all_problems = [
        problem
        for problems in problems_by_account_name.values()
        for problem in problems
    ]
    is_anything_broken = any(
        problem.severity == ProblemSeverity.BROKEN for problem in all_problems
    )

    print("")
    if is_anything_broken:
        print("🚨🚨🚨 ACTION REQUIRED - THE BRAZILIAN SYNC IS BROKEN 🚨🚨🚨")
    else:
        print("⚠️  ACTION REQUIRED SOON - THE BRAZILIAN SYNC IS ABOUT TO BREAK ⚠️")
    print("")

    for account_name, problems in problems_by_account_name.items():
        print(f"{account_name}:")
        for problem in problems:
            print(f"  - [{problem.severity.value}] {problem.description}")
        print("")

    print(
        "Fix the problems above (usually by reconnecting the bank at "
        "https://dashboard.pluggy.ai), then re-run this workflow to confirm the "
        "sync is healthy again."
    )

    if is_anything_broken:
        print("")
        print(
            f"NOTE: this sync only looks back {get_lookback_days()} days, so "
            "anything missed while it was broken will NOT arrive on its own and "
            "needs a deliberate backfill -- re-run with LOOKBACK_DAYS set wide "
            "enough to cover the gap."
        )

    sys.exit(1)

if __name__ == "__main__":
    main()
