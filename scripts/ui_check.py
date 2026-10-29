"""Headless browser check of the dashboard (developer tool; needs `pip install playwright` and
`python -m playwright install chromium`). Clicks the main action on every page, fails on any
JavaScript error or error toast, and saves screenshots to docs/screenshots/.

Usage: .venv\\Scripts\\python scripts\\ui_check.py   (server must be running)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("HANF_URL", "http://127.0.0.1:8000")
OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

problems: list[str] = []


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        page.on("console", lambda m: problems.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)

        def nav(view):
            page.click(f"#nav button[data-view='{view}']")
            page.wait_for_timeout(700)

        def click_and_wait(sel, wait_ms=1500):
            page.click(sel)
            page.wait_for_function(f"!document.querySelector(\"{sel}\").disabled", timeout=180_000)
            page.wait_for_timeout(wait_ms)
            toast = page.locator("#toast.err.show")
            if toast.count():
                problems.append(f"error toast after {sel}: {toast.inner_text()}")

        def shot(name):
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
            print("screenshot", name)

        page.goto(BASE + "/")
        page.wait_for_timeout(1500)
        click_and_wait("#btn-seed", 2500)
        shot("01_dashboard")

        nav("playground")
        page.click("#presets .btn >> text=KV read")
        click_and_wait("#btn-exec")
        assert "shard-" in page.inner_text("#pg-shard"), "shard panel empty"
        shot("02_playground_kv")
        page.click("#presets .btn >> text=Document write")
        click_and_wait("#btn-exec")
        page.click("#presets .btn >> text=Wide-column read")
        click_and_wait("#btn-exec")
        shot("03_playground_widecolumn")
        page.click("#presets .btn >> text=Injection attempt")
        click_and_wait("#btn-exec")
        assert "rejected" in page.inner_text("#pg-status"), "injection not rejected"

        nav("engines")
        click_and_wait("#btn-deep-health")
        shot("04_engines")

        nav("security")
        click_and_wait("#btn-traffic", 2000)
        click_and_wait("#btn-evaluate", 1500)
        assert "Isolation Forest" in page.inner_text("#sec-eval")
        shot("05_security")

        nav("shards")
        page.wait_for_timeout(1000)
        shot("06_shards_before")
        click_and_wait("#btn-add-shard", 2000)
        assert "completed" in page.inner_text("#migration-out"), "migration report missing"
        shot("07_shards_after_migration")
        click_and_wait("#btn-locate")

        nav("consistency")
        page.fill("#cong", "6")
        page.dispatch_event("#cong", "input")
        click_and_wait("#btn-cong")
        click_and_wait("#btn-sim", 2500)
        shot("08_consistency")

        nav("benchmarks")
        page.select_option("#bm-exp", "e4")
        click_and_wait("#btn-bench", 500)
        page.wait_for_function("document.querySelector('#bm-status').innerText.includes('done') || "
                               "document.querySelector('#bm-status').innerText.includes('failed')", timeout=180_000)
        page.wait_for_timeout(2500)
        shot("09_benchmarks")

        nav("experiments")
        page.wait_for_timeout(800)
        page.locator("[data-run]").first.click()
        page.wait_for_timeout(1500)
        shot("10_experiments")

        nav("shards")
        click_and_wait("#btn-reset-shards", 2000)
        nav("dashboard")
        page.wait_for_timeout(3500)
        shot("11_dashboard_after_demo")

        page.set_viewport_size({"width": 390, "height": 900})
        page.wait_for_timeout(800)
        width = page.evaluate("document.documentElement.scrollWidth")
        if width > 400:
            problems.append(f"horizontal scroll at phone width: {width}px")
        shot("12_mobile")
        browser.close()

    if problems:
        print("\nPROBLEMS:")
        for p_ in problems:
            print(" -", p_)
        return 1
    print("\nUI check passed: no JavaScript errors, all actions completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
