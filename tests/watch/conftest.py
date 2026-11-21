"""Fixtures for the watch browser tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser

from tests.support.browser import Pages, chromium


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    """One headless Chromium for the session."""
    with chromium() as launched:
        yield launched


@pytest.fixture
def pages(browser: Browser) -> Iterator[Pages]:
    """Pages for one test, closed after it."""
    opened = Pages(browser)

    yield opened

    opened.close()
