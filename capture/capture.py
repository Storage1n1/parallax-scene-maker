"""
SceneDriver — Python wrapper around the Three.js parallax stage (scene/index.html),
driven headlessly via Playwright. Mirrors window.ParallaxScene 1:1 so pipeline code
never touches JS directly.

Screenshot note: page.screenshot() can return a blank frame for WebGL canvases in
this headless Chromium config, so snapshot() reads pixels via canvas.toDataURL()
instead (confirmed reliable in testing).
"""

import base64
import io
import json
import pathlib
import sys

from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import browser_singleton  # noqa: E402 — shared Playwright browser (see that module for why)

SCENE_DIR = pathlib.Path(__file__).resolve().parent.parent / "scene"
SCENE_URL = (SCENE_DIR / "index.html").as_uri()


def to_file_uri(path) -> str:
    return pathlib.Path(path).resolve().as_uri()


class SceneDriver:
    def __init__(self, headless: bool = True, width: int = 1280, height: int = 720):
        self.browser = browser_singleton.get_browser(headless=headless)
        self.page = self.browser.new_page(viewport={"width": width, "height": height})
        self.console_errors = []
        self.page.on("console", lambda m: self.console_errors.append(m.text) if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self.console_errors.append(str(e)))
        self.page.goto(SCENE_URL)
        self.page.wait_for_function("window.__sceneReady === true", timeout=10000)

    # -- element ops ---------------------------------------------------

    def add_element(self, element_id: str, layer: str, image_path: str, **opts) -> dict:
        img_url = to_file_uri(image_path)
        opts_json = json.dumps(opts)
        return self.page.evaluate(
            f"""(async () => window.ParallaxScene.addElement(
                {json.dumps(element_id)}, {json.dumps(layer)}, {json.dumps(img_url)}, {opts_json}
            ))()"""
        )

    def move_element(self, element_id: str, **delta) -> dict:
        return self.page.evaluate(
            f"window.ParallaxScene.moveElement({json.dumps(element_id)}, {json.dumps(delta)})"
        )

    def set_element(self, element_id: str, **abs_vals) -> dict:
        return self.page.evaluate(
            f"window.ParallaxScene.setElement({json.dumps(element_id)}, {json.dumps(abs_vals)})"
        )

    def remove_element(self, element_id: str) -> bool:
        return self.page.evaluate(f"window.ParallaxScene.removeElement({json.dumps(element_id)})")

    def list_elements(self) -> list:
        return self.page.evaluate("window.ParallaxScene.listElements()")

    # -- camera ----------------------------------------------------------

    def move_camera(self, **delta) -> dict:
        return self.page.evaluate(f"window.ParallaxScene.moveCamera({json.dumps(delta)})")

    def set_camera(self, **abs_vals) -> dict:
        return self.page.evaluate(f"window.ParallaxScene.setCamera({json.dumps(abs_vals)})")

    # -- atmosphere / text ------------------------------------------------

    def set_atmosphere(self, **opts) -> None:
        self.page.evaluate(f"window.ParallaxScene.setAtmosphere({json.dumps(opts)})")

    def set_text(self, text_id: str, **opts) -> None:
        self.page.evaluate(f"window.ParallaxScene.setText({json.dumps(text_id)}, {json.dumps(opts)})")

    # -- state / capture ---------------------------------------------------

    def get_state(self) -> dict:
        return self.page.evaluate("window.ParallaxScene.getState()")

    def snapshot(self, out_path: str) -> str:
        """Composites the WebGL canvas (via toDataURL — page.screenshot() can return a
        blank frame for this WebGL context) with the DOM text-caption layer (via a
        transparent page.screenshot(), since toDataURL only sees the canvas)."""
        # Force a fresh paint synchronously before reading the buffer — toDataURL can
        # otherwise race the last render() call and return a stale/blank frame.
        self.page.evaluate(
            "(async () => { window.ParallaxScene.render();"
            " await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))); })()"
        )
        data_url = self.page.evaluate("document.getElementById('stage').toDataURL('image/png')")
        canvas_bytes = base64.b64decode(data_url.split(",", 1)[1])
        base_img = Image.open(io.BytesIO(canvas_bytes)).convert("RGBA")

        self.page.evaluate(
            "document.getElementById('stage').style.visibility = 'hidden';"
            "document.documentElement.style.background = 'transparent';"
            "document.body.style.background = 'transparent';"
            "document.getElementById('root').style.background = 'transparent';"
        )
        text_bytes = self.page.screenshot(omit_background=True)
        self.page.evaluate(
            "document.getElementById('stage').style.visibility = 'visible';"
            "document.documentElement.style.background = '';"
            "document.body.style.background = '';"
            "document.getElementById('root').style.background = '';"
        )
        text_img = Image.open(io.BytesIO(text_bytes)).convert("RGBA")

        composited = Image.alpha_composite(base_img, text_img)
        out_path = str(out_path)
        composited.save(out_path)
        return out_path

    def close(self):
        # Only close this driver's own page — self.browser is the shared
        # browser_singleton instance, reused across the whole process (including
        # downloader.py's SVG rasterization), not owned by this SceneDriver.
        self.page.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
