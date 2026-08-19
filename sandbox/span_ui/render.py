"""Render each mockup to a PNG. Chromium via Playwright; nothing here touches the tool."""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOCKS = ["mock_today", "mock_a_on_the_graph", "mock_b_derive_exits", "mock_c_candidates"]
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"


def main() -> None:
    from playwright.sync_api import sync_playwright

    pages = []
    for name in MOCKS:
        html = HERE / f"{name}.html"
        subprocess.run([sys.executable, str(HERE / f"{name}.py"), str(html)],
                       cwd=HERE, check=True)
        pages.append((name, html))

    with sync_playwright() as play:
        browser = play.chromium.launch(executable_path=CHROME)
        page = browser.new_page(viewport={"width": 1120, "height": 900},
                                device_scale_factor=2)
        for name, html in pages:
            page.goto(html.as_uri())
            page.wait_for_timeout(150)
            page.screenshot(path=str(HERE / f"{name}.png"), full_page=True)
            print(f"wrote {name}.png")
        browser.close()


if __name__ == "__main__":
    main()
