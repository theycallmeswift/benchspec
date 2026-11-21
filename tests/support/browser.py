"""Drive the real `benchspec watch` in headless Chromium."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Error, Page, ViewportSize, expect, sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[2]
SCREENSHOTS = REPO_ROOT / "tmp" / "watch-screenshots"
DESKTOP: ViewportSize = {"width": 1308, "height": 1100}
WIDE: ViewportSize = {"width": 1440, "height": 900}
PHONE: ViewportSize = {"width": 390, "height": 844}


@contextmanager
def chromium() -> Generator[Browser]:
    """Launch headless Chromium, skipping the test where it is not installed (never in CI)."""
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Error as error:
            if os.environ.get("CI"):
                raise

            pytest.skip(f"Chromium is not installed (uv run playwright install chromium): {error}")

        yield browser

        browser.close()


@dataclass
class Watching:
    """A running `benchspec watch` process and the page it serves."""

    process: subprocess.Popen[str]
    url: str

    def stop(self) -> int:
        """Press Ctrl-C and return the exit code."""
        self.process.send_signal(signal.SIGINT)

        return self.process.wait(timeout=10)


def _take_ctrl_c() -> None:
    """Undo an inherited ignore of SIGINT, as a backgrounded test run leaves one."""
    signal.signal(signal.SIGINT, signal.SIG_DFL)


@contextmanager
def watching(root: Path) -> Generator[Watching]:
    """Start the real entry point on `root` and stop it with Ctrl-C."""
    process = subprocess.Popen(
        [sys.executable, "-m", "benchspec", "watch", str(root)],
        stdout=subprocess.PIPE,
        text=True,
        cwd=root,
        preexec_fn=_take_ctrl_c,
    )
    assert process.stdout is not None
    address = re.search(r"http://127\.0\.0\.1:\d+/", process.stdout.readline())
    assert address is not None
    served = Watching(process, address.group())
    try:
        yield served
    finally:
        if process.poll() is None:
            assert served.stop() == 0


class Pages:
    """Opens watch pages and records every request they make."""

    def __init__(self, browser: Browser) -> None:
        """Start with no page open."""
        self._browser = browser
        self._pages: list[Page] = []
        self.requested: list[str] = []

    def open(
        self,
        served: Watching,
        viewport: ViewportSize = DESKTOP,
        *,
        prefers_light: bool = False,
        wait_for_run: bool = True,
    ) -> Page:
        """Load the served page; a phone viewport also gets touch."""
        page = self._browser.new_page(
            viewport=viewport,
            device_scale_factor=2,
            has_touch=viewport == PHONE,
            color_scheme="light" if prefers_light else "dark",
        )
        page.on("request", lambda request: self.requested.append(request.url))
        page.goto(served.url)
        if wait_for_run:
            expect(page.locator("#counter")).to_be_visible()

        self._pages.append(page)

        return page

    def close(self) -> None:
        """Close every page opened."""
        for page in self._pages:
            page.close()
