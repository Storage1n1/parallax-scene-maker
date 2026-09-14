"""
One shared headless Chromium instance for the whole process.

Both capture.py (renders the 3D scene) and downloader.py (rasterizes SVGs) need
Playwright. Playwright's sync API does not support two independent
sync_playwright() instances coexisting in one process — the second call raises
"It looks like you are using Playwright Sync API inside the asyncio loop."
This module exists so both callers share one browser and just open their own
pages against it.
"""

LAUNCH_ARGS = [
    "--allow-file-access-from-files",
    "--disable-web-security",
    "--use-gl=angle",
    "--use-angle=swiftshader",
]

_state = {"pw": None, "browser": None}


def get_browser(headless: bool = True):
    """headless only takes effect on the first call in a process — once the shared
    browser is launched it stays that way for every subsequent caller."""
    if _state["browser"] is None:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=headless, args=LAUNCH_ARGS)
        _state.update(pw=pw, browser=browser)
    return _state["browser"]


def shutdown():
    """Call once at the very end of a process/run — not after each use, since the
    browser is meant to be reused across many capture/rasterize calls."""
    if _state["browser"] is not None:
        _state["browser"].close()
    if _state["pw"] is not None:
        _state["pw"].stop()
    _state.update(pw=None, browser=None)
