"""The watch page in a real browser, served by the real `benchspec watch`.

Each test records a synthetic run, starts the entry point on it, and asserts on what the
page shows. Screenshots land in `tmp/watch-screenshots/`.
"""

from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import FloatRect, Locator, Page, expect

from tests.support.browser import DESKTOP, PHONE, SCREENSHOTS, Pages, watching
from tests.support.cli import run_benchspec
from tests.support.watch import Recording, finished_run, iteration, mid_run

LOGGED_OUT = "button.bs-cell[aria-label^='logged-out on baseline']"
IMPLEMENT = "button.bs-cell[aria-label^='implement-task on baseline']"
JUDGES = "button.bs-cell[aria-label^='review-judges-findings on baseline']"


def _rect(locator: Locator) -> FloatRect:
    """A visible element's box."""
    rect = locator.bounding_box()
    assert rect is not None

    return rect


def _shoot(page: Page, name: str) -> None:
    """Save a screenshot once the fonts have loaded."""
    page.evaluate("document.fonts.ready")
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOTS / f"{name}.png"))


def test_a_live_run_shows_its_counter_matrix_and_hover_card(tmp_path: Path, pages: Pages) -> None:
    """A live run shows its counter matrix and hover card."""
    mid_run(tmp_path)

    with watching(tmp_path) as served:
        page = pages.open(served, prefers_light=True)

        expect(page.locator("html")).to_have_attribute("data-theme", "dark")
        expect(page.locator("#iteration")).to_have_text("iteration_12")
        expect(page.locator("#run-state")).to_have_text(re.compile(r"live · last event \d+s ago"))
        expect(page.locator("#headline")).to_contain_text("19 of 54 samples finished")
        expect(page.locator("#headline .bs-num")).to_have_text("35%")
        expect(page.locator("#progress")).to_have_attribute(
            "aria-valuetext", "19 of 54 samples finished"
        )
        expect(page.locator("#tally")).to_have_text(
            "12 passed6 failed1 errored4 running1 quiet30 waiting-n 4"
        )
        expect(page.locator("#caption")).to_have_text("[ 13 evals × 2 arms · live ]")
        expect(page.locator("#tab-matrix")).to_have_attribute("aria-selected", "true")

        judges = page.locator(JUDGES)
        expect(judges.locator(".bs-b")).to_have_class(
            [re.compile("bs-f1"), re.compile("bs-f2"), re.compile("bs-f3")]
        )
        expect(judges.locator(".bs-t")).to_have_text(["48%"])
        not_applicable = page.locator("[aria-label='asks-for-codex on baseline: n/a'] .bs-b")
        expect(not_applicable).to_have_class([re.compile("bs-b-na")] * 3)
        expect(page.locator(IMPLEMENT)).to_have_attribute(
            "aria-label",
            re.compile(
                r"1/3 · agent 2m \d\ds; 3 samples: fail, 7 of 9 checks; running; quiet 5\ds"
            ),
        )
        assert page.locator(".bs-cell :is(.bs-badge, .bs-pill, svg, img)").count() == 0
        expect(page.locator(".bs-grp").nth(1)).to_contain_text("5 evals · [trial-only]")
        expect(page.locator(".bs-grp").first.locator(".bs-gs").first).to_have_text("69% · 7/15")

        page.locator(LOGGED_OUT).hover()
        card = page.locator("#hovercard")
        expect(card).to_be_visible()
        expect(card.locator(".bs-hh")).to_have_text("94%17 of 18 checks passed")
        expect(card.locator(".bs-hprob")).to_have_text(
            "error · sample 2 exited 1: provider returned 429"
        )
        expect(card.locator(".bs-hr")).to_have_text(
            ["sample 1✓ 9/960s", "sample 2ERROR10s", "sample 3✗ 8/960s"]
        )
        cells_right = _rect(page.locator(".bs-mrow >> nth=2"))
        assert _rect(card)["x"] >= cells_right["x"] + cells_right["width"]
        _shoot(page, "a-live-dark")
        page.keyboard.press("Escape")
        expect(card).to_be_hidden()

        toggle = page.locator("#theme")
        expect(toggle).to_have_text("Light theme")
        toggle.click()
        expect(page.locator("html")).to_have_attribute("data-theme", "light")
        expect(toggle).to_have_text("Dark theme")
        _shoot(page, "a-live-light")

        assert page.evaluate("document.documentElement.scrollWidth") == DESKTOP["width"]
        assert all(url.startswith(served.url) for url in pages.requested)
        assert any("/fonts/jetbrains-mono-400.woff2" in url for url in pages.requested)


def test_the_drawer_opens_on_the_first_sample_that_did_not_pass(
    tmp_path: Path, pages: Pages
) -> None:
    """The drawer opens on the first sample that did not pass."""
    mid_run(tmp_path)

    with watching(tmp_path) as served:
        page = pages.open(served)
        cell = page.locator(LOGGED_OUT)
        cell.click()
        drawer = page.locator("#drawer")

        expect(drawer.locator(".bs-x")).to_be_focused()
        expect(cell).to_have_attribute("aria-expanded", "true")
        expect(drawer.locator(".bs-st")).to_have_text("ERROR · agent · agent")
        expect(drawer.locator(".bs-msg")).to_have_text("exited 1: provider returned 429")
        expect(drawer.locator(".bs-sub").first).to_contain_text("baseline · sample 2 of 3")

        drawer.locator(".bs-prev").click()
        expect(drawer.locator(".bs-prev")).to_be_disabled()
        expect(drawer.locator(".bs-st")).to_have_text("PASS · 9/9 assertions")
        drawer.locator(".bs-next").click()
        drawer.locator(".bs-next").click()
        expect(drawer.locator(".bs-next")).to_be_disabled()
        expect(drawer.locator(".bs-st")).to_have_text("FAIL · 8/9 assertions")
        expect(drawer.locator(".bs-checks .bs-section-label")).to_have_text(
            "[ assertions · 1 failed · 8 passed ]"
        )
        expect(drawer.locator(".bs-alst li").first).to_have_text(
            "✗line 9 holdsjudge · seen in the workspace"
        )
        expect(drawer.locator(".bs-phase")).to_have_attribute(
            "aria-label", "bind 1.0s, boot 2.0s, setup 1.0s, agent 51.0s, grade 5.0s"
        )
        expect(drawer.locator(".bs-path")).to_have_text(
            "tmp/evals/iteration_12/skills/plan/eval-logged-out/baseline/sample-2/"
        )
        counter = _rect(page.locator("#counter"))
        assert _rect(drawer)["y"] >= counter["y"] + counter["height"]
        _shoot(page, "c-drawer")

        page.keyboard.press("Escape")
        expect(drawer).to_be_hidden()
        expect(cell).to_be_focused()

        page.locator("button.bs-cell[aria-label^='second-opinion on trial']").click()
        expect(drawer.locator(".bs-out li")).to_have_count(5)
        expect(drawer.locator(".bs-out li").last).to_contain_text("step 7")


def test_new_events_update_the_page_without_a_reload(tmp_path: Path, pages: Pages) -> None:
    """New events update the page without a reload."""
    run = mid_run(tmp_path)

    with watching(tmp_path) as served:
        page = pages.open(served)
        quiet = page.locator(f"{IMPLEMENT} .bs-b").nth(2)
        expect(quiet).to_have_class(re.compile("bs-b-quiet"))

        run.delta("plan/implement-task/baseline/2", 402)
        expect(quiet).to_have_class(re.compile("bs-b-running"))

        run.emit("cell.phase", 403, cell="plan/implement-task/baseline/2", phase="grade")
        run.emit(
            "cell.done",
            404,
            cell="plan/implement-task/baseline/2",
            passed=9,
            total=9,
            pass_rate=1.0,
            wall_ms=64000,
            stopped=False,
        )
        expect(quiet).to_have_class(re.compile("bs-b-pass"))
        expect(page.locator("#headline")).to_contain_text("20 of 54 samples finished")
        expect(page.locator("#tally li").first).to_have_text("13 passed")


def test_a_finished_run_opens_on_the_leaderboard(tmp_path: Path, pages: Pages) -> None:
    """A finished run opens on the leaderboard."""
    finished_run(tmp_path)

    with watching(tmp_path) as served:
        page = pages.open(served)

        expect(page.locator("#tab-leaderboard")).to_have_attribute("aria-selected", "true")
        expect(page.locator("#headline .bs-ok")).to_have_text("✓ Finished")
        expect(page.locator("#headline .bs-num")).to_have_text("100%")
        expect(page.locator("#headline")).to_contain_text("117 of 117")
        expect(page.locator("#headline .bs-el")).to_have_text("took 14m 29s")
        expect(page.locator("#run-state")).to_have_text("ended · exit 1")
        expect(page.locator("#reopen")).to_have_text("benchspec watch --iteration iteration_12")
        table = page.locator("table.bs-lb")
        expect(table.locator("thead th b")).to_have_text(
            ["bare", "claude-code", "codex", "opencode"]
        )
        expect(table.locator("tbody tr").first.locator("td")).to_have_text(
            ["60%", "90%+30pp vs bare", "◆ 100% best+40pp vs bare", "88%+28pp vs bare"]
        )
        expect(table.locator("tbody tr").first.locator(".bs-best")).to_have_count(1)
        expect(table.locator("tbody tr").nth(2).locator("td")).to_have_text(
            ["27 of 27", "30 of 30", "27 of 30+3 errored", "30 of 30"]
        )
        expect(table.locator("tbody tr").nth(3).locator("td").nth(1)).to_have_text(
            "55.0s1.28× bare"
        )
        expect(table.locator("tbody tr").last.locator("td").last).to_have_text("not reported")
        assert table.locator("[aria-sort]").count() == 0
        _shoot(page, "b-finished-leaderboard")

        page.locator("#tab-matrix").click()
        expect(page.locator("#caption")).to_have_text("[ 10 evals × 4 arms · finished ]")
        expect(page.locator("div.bs-k-notrun")).to_have_text("not run")
        errored = page.locator("button.bs-cell[aria-label^='cites-lines on codex']")
        expect(errored.locator(".bs-t")).to_have_text("—")
        expect(page.locator(".bs-grp").first).to_contain_text("[1 check skips bare]")
        errored.hover()
        expect(page.locator("#hovercard .bs-hprob")).to_have_text(
            "error · sample 1 sandbox failed to boot"
        )
        _shoot(page, "e-four-arms-hover")


def test_the_page_moves_to_the_leaderboard_at_run_end_unless_a_tab_was_picked(
    tmp_path: Path, pages: Pages
) -> None:
    """The page moves to the leaderboard at run end unless a tab was picked."""
    run = mid_run(tmp_path)

    with watching(tmp_path) as served:
        untouched = pages.open(served)
        picked = pages.open(served)
        picked.locator("#tab-leaderboard").click()
        picked.locator("#tab-matrix").click()

        run.end(410, exit_status=1)

        expect(untouched.locator("#tab-leaderboard")).to_have_attribute("aria-selected", "true")
        expect(untouched.locator("#headline")).to_contain_text("Run stopped · 19 of 54 finished")
        expect(untouched.locator("#status")).to_have_text("Run stopped · 19 of 54 samples")
        expect(picked.locator("#reopen-line")).to_be_visible()
        expect(picked.locator("#tab-matrix")).to_have_attribute("aria-selected", "true")
        expect(picked.locator("#caption")).to_have_text("[ 13 evals × 2 arms · stopped ]")


def test_a_phone_shows_boxes_short_rates_and_a_bottom_sheet(tmp_path: Path, pages: Pages) -> None:
    """A phone shows boxes short rates and a bottom sheet."""
    mid_run(tmp_path)

    with watching(tmp_path) as served:
        page = pages.open(served, PHONE)

        assert page.evaluate("document.documentElement.scrollWidth") == PHONE["width"]
        expect(page.locator("#run-state")).to_have_text("live", use_inner_text=True)
        expect(page.locator(JUDGES).locator(".bs-t")).to_be_visible()
        expect(page.locator(f"{IMPLEMENT} .bs-t")).to_be_hidden()
        expect(page.locator("#arm-hint")).to_be_hidden()
        _shoot(page, "d-phone-matrix")

        page.locator(JUDGES).tap()
        sheet = page.locator("#drawer.bs-sheet")
        expect(sheet).to_be_visible()
        expect(page.locator("#hovercard")).to_be_hidden()
        assert _rect(sheet.locator(".bs-next"))["width"] == 44
        expect(sheet.locator(".bs-alst li")).to_have_count(9)
        _shoot(page, "d-phone-sheet")

        page.locator("#scrim").tap(position={"x": 20, "y": 20})
        expect(sheet).to_be_hidden()


def test_a_phone_scrolls_four_arms_under_a_pinned_eval_column(tmp_path: Path, pages: Pages) -> None:
    """A phone scrolls four arms under a pinned eval column."""
    finished_run(tmp_path)

    with watching(tmp_path) as served:
        page = pages.open(served, PHONE)

        expect(page.locator(".bs-lbc")).to_be_visible()
        expect(page.locator(".bs-lbc .bs-dl").first).to_have_text("codex is best · +40pp vs bare")
        _shoot(page, "d2-phone-leaderboard")

        page.locator("#tab-matrix").tap()
        hint = page.locator("#arm-hint")
        expect(hint).to_have_text("arms 1–2 of 4 →")
        page.locator(".bs-mx-scroll").evaluate("scroller => scroller.scrollTo(240, 0)")
        expect(hint).to_have_text("← arms 3–4 of 4")
        expect(page.locator(".bs-mx")).to_have_class(re.compile("bs-scrolled"))
        pinned = page.locator(".bs-mx-scroll .bs-eval").first
        assert _rect(pinned)["x"] == 16
        assert (
            pinned.evaluate("node => getComputedStyle(node).backgroundColor") != "rgba(0, 0, 0, 0)"
        )
        assert page.evaluate("document.documentElement.scrollWidth") == PHONE["width"]
        _shoot(page, "d3-phone-four-arms")


def test_the_page_says_when_it_is_loading_and_when_the_connection_is_lost(
    tmp_path: Path, pages: Pages
) -> None:
    """The page says when it is loading and when the connection is lost."""
    run = Recording(iteration(tmp_path))
    run.plan("hello", "greets")
    run.play("hello/greets/trial/0", 0)

    with watching(tmp_path) as served:
        page = pages.open(served, wait_for_run=False)

        expect(page.locator("#empty-line")).to_have_text("Loading run…")
        expect(page.locator("#counter")).to_be_hidden()

        run.begin()
        expect(page.locator("#headline")).to_contain_text("0 of 2 samples finished")
        expect(page.locator("#run-state")).to_have_class("bs-live")

        assert served.stop() == 0
        expect(page.locator("#banner")).to_have_text(
            "Lost connection to benchspec run · retrying in 5s"
        )
        expect(page.locator("#run-state")).not_to_have_class("bs-live")
        expect(page.locator("#headline")).to_contain_text("0 of 2 samples finished")


def test_watch_refuses_a_root_without_an_event_stream(tmp_path: Path) -> None:
    """Watch refuses a root without an event stream."""
    result = run_benchspec("watch", str(tmp_path), "--iteration", "iteration_05", cwd=tmp_path)

    assert result.returncode == 2
    assert result.stderr.strip() == (
        f"error: {tmp_path}/tmp/evals/iteration_05 has no event stream"
    )


def test_watch_refuses_events_in_another_format(tmp_path: Path) -> None:
    """Watch refuses events in another format."""
    events = iteration(tmp_path) / "events-main.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text('{"format_version": 2, "type": "run.start"}\n', encoding="utf-8")

    result = run_benchspec("watch", str(tmp_path), cwd=tmp_path)

    assert result.returncode == 2
    assert result.stderr.strip() == (
        "error: events format_version 2 is not supported by this benchspec"
    )
