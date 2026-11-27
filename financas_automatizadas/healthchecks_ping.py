"""
Reports how a sync run ended to its healthchecks.io check, so a failed or missing
run reaches Kevin through healthchecks.io's alert rather than GitHub's
notification stream.

The workflow runs this as its last step whatever happened before it, with the
job's status in JOB_STATUS. "success" pings the check; anything else ("failure",
"cancelled") pings its /fail endpoint. A run that never gets this far sends
nothing, and healthchecks.io alarms on the silence once the check's grace has
passed.

Two rules shape everything below:

1. Reporting must never fail the sync. Every outcome, including a missing key,
   an unreachable healthchecks.io or a bug in this file, exits 0 and says what
   went wrong as a workflow warning.
2. The ping URL contains the project's ping key, which is a secret, and this
   repository's Action logs are public. Nothing here prints the URL, the key, or
   an exception text that could carry either.

It uses only the standard library and runs under the runner's own `python3`,
so it still reports when installing uv or the project's Python is what failed.
"""

import os
import sys
import time
import urllib.error
import urllib.request

PING_BASE_URL = "https://hc-ping.com"
PING_TIMEOUT_SECONDS = 10
PING_ATTEMPTS = 3
SECONDS_BETWEEN_ATTEMPTS = 1
SUCCESS_JOB_STATUS = "success"


def warn(message: str) -> None:
    # The `::warning::` prefix makes GitHub show the line on the run's summary.
    print(f"::warning::healthchecks.io: {message}")


def build_ping_url(base_url: str, ping_key: str, slug: str, job_status: str) -> str:
    url = f"{base_url}/{ping_key}/{slug}"
    if job_status != SUCCESS_JOB_STATUS:
        url += "/fail"
    return url


def build_ping_body(job_status: str) -> bytes:
    """What the alert email shows as the last ping's body: where to look."""
    run_url = "{}/{}/actions/runs/{}".format(
        os.environ.get("GITHUB_SERVER_URL", "https://github.com"),
        os.environ.get("GITHUB_REPOSITORY", "<unknown repository>"),
        os.environ.get("GITHUB_RUN_ID", "<unknown run>"),
    )
    event = os.environ.get("GITHUB_EVENT_NAME", "<unknown event>")
    return f"Job status: {job_status}\nTrigger: {event}\nRun: {run_url}\n".encode()


def describe_failure(error: Exception) -> str:
    """Names a failed ping without echoing anything that could hold the URL."""
    if isinstance(error, urllib.error.HTTPError):
        # 404: no check has this slug under this key's project.
        return f"HTTP {error.code}"
    if isinstance(error, urllib.error.URLError):
        # The reason is the transport error (refused, unreachable, DNS), not the URL.
        return f"could not connect ({type(error.reason).__name__})"
    return type(error).__name__


def send_ping(url: str, body: bytes) -> Exception | None:
    """Returns None once a ping lands, or the last error after every attempt."""
    last_error = None
    for attempt in range(PING_ATTEMPTS):
        if attempt > 0:
            time.sleep(SECONDS_BETWEEN_ATTEMPTS)
        request = urllib.request.Request(url, data=body, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=PING_TIMEOUT_SECONDS):
                return None
        except Exception as error:
            last_error = error
    return last_error


def report() -> None:
    # A key pasted with a trailing newline would otherwise become part of the URL.
    ping_key = os.environ.get("HEALTHCHECKS_PING_KEY", "").strip()
    slug = os.environ.get("HEALTHCHECKS_SLUG", "").strip()
    job_status = os.environ.get("JOB_STATUS", "").strip() or "unknown"
    base_url = os.environ.get("HEALTHCHECKS_PING_BASE_URL", PING_BASE_URL).rstrip("/")

    if not ping_key:
        warn("the HEALTHCHECKS_PING_KEY secret is not set; reported to no check.")
        return
    if not slug:
        warn("HEALTHCHECKS_SLUG is not set; reported to no check.")
        return

    url = build_ping_url(base_url, ping_key, slug, job_status)
    error = send_ping(url, build_ping_body(job_status))
    endpoint = "success" if job_status == SUCCESS_JOB_STATUS else "/fail"
    if error is None:
        print(f"healthchecks.io: reported {endpoint} to check '{slug}'.")
    else:
        warn(
            f"reporting {endpoint} to check '{slug}' failed after {PING_ATTEMPTS} "
            f"attempts: {describe_failure(error)}."
        )


def main() -> int:
    try:
        report()
    except Exception as error:
        reason = type(error).__name__
        warn(f"the reporter itself failed ({reason}); the sync is unaffected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
