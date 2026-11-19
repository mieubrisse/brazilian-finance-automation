"""
Negative controls for the suite's isolation. If either fails, the guarantee in
`conftest.py` has quietly stopped holding, and every other test's "no real API
was called" goes with it.
"""

import os

import decouple
import my_pluggy
import pytest
import requests
import requests_mock


@pytest.mark.parametrize(
    "real_url",
    [
        f"{my_pluggy.PLUGGY_URL}accounts/any-account",
        "https://api.youneedabudget.com/v1/budgets",
    ],
)
def test_unmocked_request_is_refused(real_url):
    with pytest.raises(requests_mock.exceptions.NoMockAddress):
        requests.get(real_url)


def test_configuration_comes_only_from_the_environment():
    # Every module that reads settings must hold the rebound config, or a
    # developer's real `.env` would leak real account ids into the tests.
    import main
    import ynab

    for module in (main, my_pluggy, ynab):
        assert module.config is decouple.config
    assert isinstance(decouple.config.repository, decouple.RepositoryEmpty)


# Every setting the application reads besides accounts, listed here rather
# than imported from conftest so that dropping one there is caught.
SETTINGS_THE_APPLICATION_READS = (
    "PLUGGY_CLIENT_ID",
    "PLUGGY_CLIENT_SECRET",
    "LOOKBACK_DAYS",
    "SCHEDULED_CRON",
    "GITHUB_RUN_ATTEMPT",
)


def test_no_pipeline_setting_is_inherited_from_the_shell():
    # GitHub sets GITHUB_RUN_ATTEMPT on every Actions run, so in CI this fails
    # if that name ever drops out of the list conftest clears.
    inherited = [
        name for name in SETTINGS_THE_APPLICATION_READS if name in os.environ
    ]
    assert inherited == []
