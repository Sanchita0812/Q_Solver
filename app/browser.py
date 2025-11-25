from playwright.sync_api import sync_playwright


def fetch_rendered_html(url: str) -> str:
    """
    Use Playwright to fully render a JS-heavy page and return the final HTML.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")
        html = page.content()
        browser.close()
    return html
