"""
Client for image_gen_server.py. Launches the server (under modules/.venv,
the only Python env here with working torch/diffusers/rembg + this GPU's CUDA
build) on first use if it isn't already running, then reuses it — the model
stays loaded in that process across every call in a pipeline run instead of
reloading per-image.
"""

import atexit
import os
import re
import subprocess
import threading
import time

import requests

SERVER_PORT = 8790
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"
VENV_PYTHON = r"C:\Users\USER\AUTO 4\modules\.venv\Scripts\python.exe"
SERVER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "image_gen_server.py")
SERVER_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "image_gen_server.log")

_server_process = None


def _drain_pipe_to_file(process, log_path):
    """Continuously reads the child's stdout/stderr pipe and writes it to a
    log file. Without this, the pipe buffer fills once enough output
    accumulates and the child blocks on write() — which silently hangs
    every future request even though the process is still "running"."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8", errors="replace") as f:
        for line in iter(process.stdout.readline, ""):
            f.write(line)
            f.flush()


def _is_up() -> bool:
    try:
        r = requests.get(f"{SERVER_URL}/health", timeout=1)
        return r.status_code == 200
    except requests.RequestException:
        return False


def ensure_server_running(startup_timeout: int = 60):
    """Starts image_gen_server.py as a background process if it isn't already
    serving, and waits for it to finish loading the model."""
    global _server_process
    if _is_up():
        return

    if not os.path.exists(VENV_PYTHON):
        raise RuntimeError(f"Image-gen venv not found at {VENV_PYTHON}")

    print(f"[local_gen] starting image_gen_server.py ... (log: {SERVER_LOG})")
    _server_process = subprocess.Popen(
        [VENV_PYTHON, SERVER_SCRIPT],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    threading.Thread(
        target=_drain_pipe_to_file, args=(_server_process, SERVER_LOG), daemon=True,
    ).start()
    atexit.register(_shutdown)

    deadline = time.time() + startup_timeout
    while time.time() < deadline:
        if _is_up():
            print("[local_gen] server ready.")
            return
        if _server_process.poll() is not None:
            raise RuntimeError("image_gen_server.py exited during startup — check its output")
        time.sleep(1)
    raise RuntimeError(f"image_gen_server.py did not become ready within {startup_timeout}s")


def _shutdown():
    if _server_process is not None and _server_process.poll() is None:
        _server_process.terminate()


def generate_asset(prompt: str, out_dir: str, negative_prompt: str = None,
                    steps: int = 25, remove_bg: bool = True) -> str:
    """Generates one image for `prompt`, saves it under out_dir, and returns the
    path. Raises on failure — callers should catch and fall back to another
    source if desired, same shape as downloader.resolve_asset's contract."""
    ensure_server_running()

    payload = {"prompt": prompt, "steps": steps, "remove_bg": remove_bg}
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt

    r = requests.post(f"{SERVER_URL}/generate", json=payload, timeout=120)
    r.raise_for_status()

    os.makedirs(out_dir, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", prompt.strip().lower()).strip("-")[:60] or "gen"
    out_path = os.path.join(out_dir, f"{slug}__localgen.png")
    with open(out_path, "wb") as f:
        f.write(r.content)
    return out_path
