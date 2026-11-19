"""
Suite-wide isolation. Everything here runs before any test module imports the
application, which is what lets it take effect.

Two guarantees, both load-bearing because this pipeline touches real money:

1. No test can reach a real API. Every test runs inside a strict
   `requests_mock`, which raises on any URL a test has not explicitly mocked.
   "No real call happened" is therefore something the suite proves, not
   something each test author promises. That matters most for Pluggy, whose
   connections can be knocked offline for a month by overuse, and for YNAB,
   whose writes land in a live budget.

2. No test reads a developer's real configuration. The application reads
   settings through python-decouple, which falls back to a `.env` file. On a
   machine with a real `.env`, that would quietly feed real account ids and
   tokens into the tests. Rebinding decouple's `config` to an empty repository
   leaves `os.environ` as the only source, and the tests control that.
"""

import os

import decouple
import pytest

decouple.config = decouple.Config(decouple.RepositoryEmpty())

# `ynab.py` reads its token at import time, so it has to exist before any test
# module imports the application.
os.environ["YNAB_TOKEN"] = "fake-ynab-token"

PIPELINE_SETTING_NAMES = ("PLUGGY_CLIENT_ID", "PLUGGY_CLIENT_SECRET", "LOOKBACK_DAYS")


@pytest.fixture(autouse=True)
def block_unmocked_http(requests_mock):
    """
    Turns on strict HTTP mocking for every test, whether or not it asks for it.
    """
    return requests_mock


@pytest.fixture(autouse=True)
def clear_pipeline_environment(monkeypatch):
    """
    Removes any pipeline setting inherited from the shell, so a test sees only
    the configuration it sets itself.
    """
    for name in list(os.environ):
        is_account_setting = name.startswith("ACCOUNT_")
        if is_account_setting or name in PIPELINE_SETTING_NAMES:
            monkeypatch.delenv(name)
