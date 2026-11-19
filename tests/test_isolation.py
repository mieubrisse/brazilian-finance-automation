"""
Negative controls for the suite's isolation. If either fails, the guarantee in
`conftest.py` has quietly stopped holding, and every other test's "no real API
was called" goes with it.
"""

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
