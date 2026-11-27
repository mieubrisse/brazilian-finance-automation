"""
The healthchecks.io reporter, judged by the two things that matter about it:
it reports the right outcome to the right endpoint, and nothing it meets, from
a missing secret to an unreachable server, can make it fail the sync job.
"""

import subprocess
import sys
import urllib.error
from pathlib import Path

import healthchecks_ping
import pytest

FAKE_PING_KEY = "fake-ping-key-0123456789"
SLUG = "brazilian-finance-sync"
SCRIPT_PATH = Path(healthchecks_ping.__file__)


@pytest.fixture(autouse=True)
def reporter_environment(monkeypatch):
    monkeypatch.setenv("HEALTHCHECKS_PING_KEY", FAKE_PING_KEY)
    monkeypatch.setenv("HEALTHCHECKS_SLUG", SLUG)
    monkeypatch.delenv("HEALTHCHECKS_PING_BASE_URL", raising=False)
    monkeypatch.setattr(healthchecks_ping, "SECONDS_BETWEEN_ATTEMPTS", 0)


class RecordingUrlopen:
    """Stands in for urlopen: records each request, then answers or raises."""

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def install_urlopen(monkeypatch, error: Exception | None = None) -> RecordingUrlopen:
    fake = RecordingUrlopen(error)
    monkeypatch.setattr(healthchecks_ping.urllib.request, "urlopen", fake)
    return fake


def test_a_passing_job_pings_the_check(monkeypatch):
    monkeypatch.setenv("JOB_STATUS", "success")
    urlopen = install_urlopen(monkeypatch)

    assert healthchecks_ping.main() == 0

    [request] = urlopen.requests
    assert request.full_url == f"https://hc-ping.com/{FAKE_PING_KEY}/{SLUG}"
    assert request.get_method() == "POST"
    assert b"Job status: success" in request.data


@pytest.mark.parametrize("job_status", ["failure", "cancelled", ""])
def test_any_other_job_status_pings_fail(monkeypatch, job_status):
    monkeypatch.setenv("JOB_STATUS", job_status)
    urlopen = install_urlopen(monkeypatch)

    assert healthchecks_ping.main() == 0

    [request] = urlopen.requests
    assert request.full_url == f"https://hc-ping.com/{FAKE_PING_KEY}/{SLUG}/fail"


def test_a_key_pasted_with_a_newline_still_builds_a_clean_url(monkeypatch):
    monkeypatch.setenv("HEALTHCHECKS_PING_KEY", f"{FAKE_PING_KEY}\n")
    monkeypatch.setenv("JOB_STATUS", "success")
    urlopen = install_urlopen(monkeypatch)

    healthchecks_ping.main()

    assert urlopen.requests[0].full_url == f"https://hc-ping.com/{FAKE_PING_KEY}/{SLUG}"


def test_a_missing_secret_skips_the_ping_with_a_warning(monkeypatch, capsys):
    monkeypatch.setenv("HEALTHCHECKS_PING_KEY", "")
    monkeypatch.setenv("JOB_STATUS", "success")
    urlopen = install_urlopen(monkeypatch)

    assert healthchecks_ping.main() == 0

    assert urlopen.requests == []
    assert "::warning::" in capsys.readouterr().out


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
        urllib.error.HTTPError(
            f"https://hc-ping.com/{FAKE_PING_KEY}/{SLUG}", 404, "Not Found", {}, None
        ),
        TimeoutError("timed out"),
        RuntimeError(f"something unexpected mentioning {FAKE_PING_KEY}"),
    ],
    ids=["unreachable", "no-such-check", "timeout", "unexpected"],
)
def test_a_failed_ping_retries_then_exits_zero_without_leaking_the_key(
    monkeypatch, capsys, error
):
    monkeypatch.setenv("JOB_STATUS", "success")
    urlopen = install_urlopen(monkeypatch, error)

    assert healthchecks_ping.main() == 0

    assert len(urlopen.requests) == healthchecks_ping.PING_ATTEMPTS
    output = capsys.readouterr()
    assert "::warning::" in output.out
    assert FAKE_PING_KEY not in output.out + output.err


def test_a_bug_in_the_reporter_still_exits_zero(monkeypatch, capsys):
    def broken_report():
        raise ValueError(f"bug mentioning {FAKE_PING_KEY}")

    monkeypatch.setattr(healthchecks_ping, "report", broken_report)

    assert healthchecks_ping.main() == 0
    assert FAKE_PING_KEY not in capsys.readouterr().out


def test_the_script_exits_zero_against_an_unreachable_ping_url():
    """
    The workflow's own invocation, end to end in a real process with a real
    socket: nothing listens on the discard port, and under `make test-offline`
    there is no network at all. Either way the ping cannot land, and the step
    must still exit 0 without printing the key.
    """
    environment = {
        "HEALTHCHECKS_PING_KEY": FAKE_PING_KEY,
        "HEALTHCHECKS_SLUG": SLUG,
        "HEALTHCHECKS_PING_BASE_URL": "http://127.0.0.1:9",
        "JOB_STATUS": "failure",
    }
    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0
    assert "failed after 3 attempts" in completed.stdout
    assert FAKE_PING_KEY not in completed.stdout + completed.stderr
