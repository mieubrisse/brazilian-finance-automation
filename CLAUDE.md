Brazilian finance automation
============================

This repo carries Brazilian bank transactions into YNAB. A scheduled GitHub
Action runs `financas_automatizadas/main.py` once a day, which pulls a short
trailing window of transactions from Pluggy and posts them to the YNAB API.

Setup instructions live in `README.md`. This file covers how the pieces relate
and how to debug them, which is the part that is not obvious from the code.

The chain, and where it breaks
------------------------------

```
your banks  ->  MyPluggy  ->  an Item in your Pluggy Application  ->  this Action  ->  YNAB
               (consumer)      (developer dashboard)
```

**MyPluggy and the developer Dashboard are two different systems**, and
conflating them will cost you an afternoon:

- **MyPluggy** (<https://meu.pluggy.ai>) is the consumer app where you connect
  your banks. Your transactions live here.
- **The developer Dashboard** (<https://dashboard.pluggy.ai>) holds the
  *Application* that owns the `PLUGGY_CLIENT_ID` and `PLUGGY_CLIENT_SECRET` this
  Action authenticates with. **This is the only thing the Action can see.**

The bridge between them is a MeuPluggy OAuth authorization performed from inside
the Application, **once per connected bank**. Bank connections can be perfectly
healthy and up to date in MyPluggy while the Application has no access to them
at all — so "MyPluggy looks fine" is not evidence the sync works.

**An Item is one bank connection, not one account.** One Item can hold several
accounts: a Nubank Item covers both the checking account and the credit card.
Two Items serving three configured accounts is normal, not a symptom.

Each account is configured by an `ACCOUNT_<n>_PLUGGY_ID` repository secret
holding that account's Pluggy account id.

Two things that lie to you
--------------------------

Both of these produced a ten-week outage (2026-07-10 to 2026-09-19) in which no
transaction reached YNAB and nothing anywhere reported a problem.

**1. A successful Action run does not mean transactions were imported.** Asking
Pluggy for transactions belonging to an account id that no longer exists returns
an empty list and HTTP 200, which is indistinguishable from a quiet week. The
run exits zero and goes green.

The sync now checks the health of the connection itself and exits non-zero when
an account id no longer resolves, so a green run means more than it used to —
but only for runs that carried that check. **Read the log, not the conclusion:**
a checked run prints a per-account `Connection:` line. A green run without that
line ran older code and proves nothing.

**2. A Pluggy Item reports itself healthy while holding nothing.** An Item can
return `status: UPDATED`, `executionStatus: SUCCESS` and `error: None` while
containing zero accounts and serving no data. Do not treat those fields as
evidence that data is flowing. `lastUpdatedAt` also appears to track when the
Item record was last modified rather than when its bank data last refreshed, so
it is not a freshness signal either.

The question that actually discriminates: **can the configured account ids still
be fetched?**

Diagnosing
----------

Start with what the runs actually printed, not whether they passed:

```
gh run list -R mieubrisse/brazilian-finance-automation --limit 10
gh run view <run-id> -R mieubrisse/brazilian-finance-automation --log
```

Then ask what the credentials can see. Pluggy deliberately exposes **no endpoint
that lists Items** — so the ids have to be read off the Items list of your
Application in the dashboard and handed in:

```
gh workflow run diagnose-pluggy.yml \
  -R mieubrisse/brazilian-finance-automation \
  -f item_ids=<item-id>,<item-id>
```

That reports, per Item, whether these credentials can see it, its status and
timestamps, and every account under it. Account ids are printed abbreviated
because this repository is public and its Action logs are world-readable.

Recovery does not backfill
--------------------------

The sync only ever asks for a short trailing window (`TRANSACTION_LOOKBACK_DAYS`
in `financas_automatizadas/my_pluggy.py`). Anything that fell outside that window
while the pipeline was broken **will never arrive on its own**. Fixing the
pipeline fixes the future, not the gap; closing a gap takes a deliberate
wide-window run.

Duplicates, and when protection disappears
------------------------------------------

Re-sending the same window every day is safe because each transaction is posted
with its Pluggy transaction id as YNAB's `import_id`, and YNAB answers a repeat
with `409 Conflict` rather than creating it. The sync treats 409 as
"already imported" and counts it separately from genuinely new transactions.

**That protection is tied to the Pluggy connection.** A remade connection
re-issues every transaction under a new id, so after reconnecting, re-importing
dates already present in YNAB will genuinely duplicate them. Bound any backfill
to start after the last transaction already in the budget and reconcile the
boundary by date and amount rather than trusting `import_id`.

Deleting a transaction in YNAB is irreversible — there is no undelete, and
recreating it severs its link to the imported bank record permanently. Diagnose
duplicates; do not delete your way out of them.

Gotchas
-------

- The default branch is `kevin-main`, not `main`. There is no `main` branch;
  anything pinned to it gets a 404.
- **The test suite does not run.** All four modules under `tests/` fail at
  import, referencing functions that no longer exist, and there is no CI. Do not
  read that directory as coverage.
- The `push` trigger in `sync-to-ynab.yml` points at a `trunk` branch that does
  not exist, inherited from upstream.

For the repo owner
------------------

Kevin's own routing, conventions, and escalation context for this pipeline live
in his private Claude skill `/brazilian-finance-automation`, alongside `/ynab`
for what happens to the transactions after they land. Invoke those when working
on this repo from one of his machines; everything needed to work on the code
itself is in this file and the README.
