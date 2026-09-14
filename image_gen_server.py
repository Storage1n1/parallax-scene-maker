"""
Local Stable Diffusion HTTP server — runs under modules/.venv (the only Python
env on this machine with a working torch/diffusers/rembg + this RTX 5060's
CUDA build). Loads the model once, stays warm, serves generate requests over
plain HTTP so the main project (running under a different Python env without
torch) can call it without a subprocess-per-image cost.

Run directly with modules/.venv's python — see local_gen.py for the client
that launches this automatically.
"""

import io
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The model is already fully cached locally (confirmed: ~5.2GB in
# ~/.cache/huggingface/hub). from_pretrained() otherwise makes a network call to
# check for updates before falling back to cache, and on this network that call
# can hang for a long time (same root cause as the Wikimedia rate-limit issues
# seen elsewhere in this project) — skip it entirely.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import torch
from diffusers import StableDiffusionPipeline
from rembg import remove

PORT = 8790
MODEL_ID = "runwayml/stable-diffusion-v1-5"

STYLE_SUFFIX = (
    ", flat vector illustration, simple shapes, solid colors, "
    "no gradients, no shading, plain white background, "
    "single centered object, isolated on background"
)
BG_STYLE_SUFFIX = (
    ", flat vector illustration, clip art style, simple shapes, solid colors, "
    "no gradients, no shading, minimalist scene design, wide panoramic "
    "landscape, single continuous scene, seamless composition"
)
DEFAULT_NEGATIVE = (
    "photo, photorealistic, realistic, 3d render, blurry, text, watermark, "
    "stock photo watermark, dreamstime, shutterstock, getty images, alamy, "
    "istockphoto, gradient, shadow, signature, logo, deformed, distorted, "
    "warped, glitch, low quality, grid, collage, multiple panels, tiled, "
    "comic panels, split screen, borders, frame, contact sheet, diptych, "
    "triptych, multiple images, thumbnail grid, icon, badge, app icon, "
    "sticker, circle background, circular frame, vignette, emblem, coin, "
    "button, seal, stamp, tilted angle, distorted perspective, wide angle "
    "lens, fisheye, worm's eye view, low angle shot, cropped, close-up, "
    "zoomed in, partial view, cut off"
)
BG_DEFAULT_NEGATIVE = (
    "photo, realistic, 3d render, blurry, text, watermark, gradient, shadow, "
    "signature, logo, deformed, low quality, grid, collage, multiple panels, "
    "tiled, comic panels, split screen, borders, frame, contact sheet, "
    "diptych, triptych, multiple images, thumbnail grid, people, characters, "
    "white background, cropped, aerial view, top-down, top down view, "
    "isometric, birds eye view, bird's eye view, map view, overhead view"
)

_pipe = None


def load_pipeline():
    global _pipe
    print(f"[image_gen_server] loading {MODEL_ID} ...", flush=True)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    _pipe = StableDiffusionPipeline.from_pretrained(
        MODEL_ID, torch_dtype=dtype, safety_checker=None, requires_safety_checker=False,
    ).to(device)
    print(f"[image_gen_server] ready on {device}", flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep stdout clean — errors still surface via print() in do_POST

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/generate":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
            prompt = body["prompt"]
            mode = body.get("mode", "subject")  # "subject" (isolated cutout) or "bg" (full scene)
            is_bg = mode == "bg"
            default_style = BG_STYLE_SUFFIX if is_bg else STYLE_SUFFIX
            default_negative = BG_DEFAULT_NEGATIVE if is_bg else DEFAULT_NEGATIVE

            negative_prompt = body.get("negative_prompt") or default_negative
            steps = int(body.get("steps", 25))
            remove_bg = bool(body.get("remove_bg", not is_bg))
            apply_style = bool(body.get("apply_style", True))
            width = int(body.get("width", 512))
            height = int(body.get("height", 512))

            full_prompt = prompt + (default_style if apply_style else "")
            t0 = time.time()
            image = _pipe(prompt=full_prompt, negative_prompt=negative_prompt,
                           num_inference_steps=steps, width=width, height=height).images[0]
            elapsed = time.time() - t0
            print(f"[image_gen_server] generated {prompt!r} in {elapsed:.2f}s", flush=True)

            if remove_bg:
                image = remove(image)

            buf = io.BytesIO()
            image.save(buf, format="PNG")
            data = buf.getvalue()

            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            print(f"[image_gen_server] ERROR: {e}", flush=True)
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode())


def main():
    load_pipeline()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[image_gen_server] listening on http://127.0.0.1:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
