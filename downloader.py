#!/usr/bin/env python3
"""
Multi-source VECTOR-ONLY clip-art asset downloader — no photos, no paintings.

Takes a list of search queries and pulls matching vector art from several free
libraries in one pass, saving everything into --out and writing a CSV
"log book" (log.csv) recording exactly where every file came from, its
license, and the source URL — so provenance is always traceable.

Sources (no login/API key required unless noted):
  - iconify      api.iconify.design — huge curated icon library, matched by precise
                 icon name (e.g. "tabler:deer"). Tried first: name-based search is
                 inherently more reliable than free-text search over noisy libraries.
  - openverse    CC-licensed sources, restricted to extension=svg. No key.
  - wikimedia    Wikimedia Commons search restricted to filetype:drawing +
                 a ".svg" title check. No key.
  - pixabay      image_type=vector search. Needs a free API key
                 (https://pixabay.com/api/docs/) via --pixabay-key or
                 PIXABAY_API_KEY env var. Skipped automatically if no key.
                 Returns a rendered raster preview (not raw SVG), so this is
                 the one source still run through --remove-bg.
  - freesvg      registered but disabled — no real public JSON search API was found.

A title/tag keyword filter blocks explicit content (Wikimedia search has no
safesearch equivalent). resolve_asset() additionally runs each openverse/
wikimedia/pixabay candidate through a VLM relevance check (vlm/vlm_client.py:
check_asset) before accepting it — this is what catches things keyword
filtering can't, like a downloaded "logo" or an unrelated map/diagram (both
happened in testing). Iconify is exempted from this check: its name-based
matches are already reliable, and the small VLM showed a systematic bias
against flat icon-style art (rejected a correct, clean deer icon at 0.98
confidence). Tries up to 4 candidates per source before moving to the next.

Usage:
    pip install requests
    pip install rembg onnxruntime pillow   # only needed for --remove-bg

    python asset_downloader.py "rocket ship" "clock icon" --out assets --per-query 5 --remove-bg

    python asset_downloader.py --queries-file queries.txt --out assets \
        --sources openverse wikimedia pixabay --pixabay-key YOURKEY --remove-bg
"""

import argparse
import csv
import os
import pathlib
import re
import sys
import time
from datetime import datetime, timezone

import requests

OPENVERSE_ENDPOINT = "https://api.openverse.org/v1/images/"
WIKIMEDIA_SEARCH_ENDPOINT = "https://commons.wikimedia.org/w/api.php"
WIKIMEDIA_IMAGEINFO_ENDPOINT = "https://commons.wikimedia.org/w/api.php"
PIXABAY_ENDPOINT = "https://pixabay.com/api/"

USER_AGENT = "asset-downloader/1.0 (personal project asset collection)"


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "asset"


def guess_ext(url: str, default: str = "png") -> str:
    m = re.search(r"\.(png|jpg|jpeg|svg|webp)(?:\?|$)", url, re.IGNORECASE)
    return m.group(1).lower() if m else default


def is_safe(title: str, tags=None) -> bool:
    return True  # Safety filters disabled by user request


# ---------------------------------------------------------------------------
# Source fetchers. Each yields dicts: {url, title, license, source, page_url}
# ---------------------------------------------------------------------------

def fetch_openverse(query, per_query, session):
    params = {
        "q": query,
        "page_size": per_query,
        "category": "illustration",
        "extension": "svg",  # vector-only — no photos/paintings
    }
    r = session.get(OPENVERSE_ENDPOINT, params=params, timeout=30)
    r.raise_for_status()
    results = r.json().get("results", [])[:per_query]
    for item in results:
        if item.get("mature") or not is_safe(item.get("title", ""), item.get("tags")):
            continue
        # Route through Openverse's own thumbnail proxy instead of hitting the
        # original source host directly (avoids provider-side rate limiting,
        # e.g. Wikimedia Commons blocking bulk full-res downloads).
        thumb_url = f"https://api.openverse.org/v1/images/{item.get('id')}/thumb/" if item.get("id") else item.get("url")
        yield {
            "url": thumb_url,
            "title": item.get("title") or query,
            "license": item.get("license", "unknown"),
            "source": f"openverse/{item.get('source', 'unknown')}",
            "page_url": item.get("foreign_landing_url", ""),
        }


def fetch_wikimedia(query, per_query, session):
    search_params = {
        "action": "query",
        "list": "search",
        "srsearch": f"{query} filetype:drawing",  # vector/drawing only — excludes photos/paintings
        "srnamespace": 6,  # File namespace
        "format": "json",
        "srlimit": per_query,
    }
    r = session.get(WIKIMEDIA_SEARCH_ENDPOINT, params=search_params, timeout=30)
    r.raise_for_status()
    hits = r.json().get("query", {}).get("search", [])[:per_query]
    for hit in hits:
        title = hit["title"]  # e.g. "File:Foo.svg"
        if not title.lower().endswith(".svg") or not is_safe(title):
            continue
        info_params = {
            "action": "query",
            "titles": title,
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": 800,  # request a thumbnail (thumb.php), not the full original —
            "format": "json",   # Wikimedia hard rate-limits bulk full-res downloads
        }
        ir = session.get(WIKIMEDIA_IMAGEINFO_ENDPOINT, params=info_params, timeout=30)
        ir.raise_for_status()
        pages = ir.json().get("query", {}).get("pages", {})
        for page in pages.values():
            infos = page.get("imageinfo", [])
            if not infos:
                continue
            info = infos[0]
            meta = info.get("extmetadata", {})
            license_name = meta.get("LicenseShortName", {}).get("value", "unknown")
            yield {
                "url": info.get("thumburl") or info.get("url"),
                "title": title,
                "license": license_name,
                "source": "wikimedia",
                "page_url": f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}",
            }
        time.sleep(0.3)


def fetch_pixabay(query, per_query, session, api_key):
    if not api_key:
        return
    params = {
        "key": api_key,
        "q": query,
        "image_type": "vector",
        "per_page": max(per_query, 3),
        "safesearch": "false",    }
    r = session.get(PIXABAY_ENDPOINT, params=params, timeout=30)
    r.raise_for_status()
    hits = r.json().get("hits", [])[:per_query]
    for hit in hits:
        yield {
            "url": hit.get("largeImageURL") or hit.get("webformatURL"),
            "title": hit.get("tags", query),
            "license": "Pixabay License",
            "source": "pixabay",
            "page_url": hit.get("pageURL", ""),
        }


def fetch_freesvg(query, per_query, session):
    # NOTE: freesvg.org/api/v1/search is not a real endpoint (returns the site's HTML
    # homepage, confirmed by hand) — no public JSON search API was found for freesvg.org.
    # Left disabled in SOURCE_FETCHERS/defaults below rather than deleted, in case a real
    # endpoint is found later.
    try:
        r = session.get(f"https://freesvg.org/api/v1/search", params={"query": query}, timeout=30)
        r.raise_for_status()
        hits = r.json()[:per_query]
        for hit in hits:
            # Assuming hit contains 'url' or 'id'
            yield {
                "url": hit.get("url") or f"https://freesvg.org/storage/img/thumb/{hit.get('id')}.png",
                "title": hit.get("title") or query,
                "license": "Public Domain",
                "source": "freesvg",
                "page_url": f"https://freesvg.org/{hit.get('id')}",
            }
    except Exception:
        pass


def fetch_iconify(query, per_query, session):
    try:
        r = session.get("https://api.iconify.design/search", params={"query": query, "limit": per_query}, timeout=30)
        r.raise_for_status()
        icons = r.json().get("icons", [])
        for icon in icons:
            prefix, name = icon.split(":")
            yield {
                "url": f"https://api.iconify.design/{prefix}/{name}.svg",
                "title": icon,
                "license": "Open Source",
                "source": "iconify",
                "page_url": f"https://icon-sets.iconify.design/{prefix}/{name}/",
            }
    except Exception:
        pass


SOURCE_FETCHERS = {
    "freesvg": fetch_freesvg,  # disabled by default — see note above, no real API found
    "iconify": fetch_iconify,
    "openverse": fetch_openverse,
    "wikimedia": fetch_wikimedia,
    "pixabay": fetch_pixabay,
}
ACTIVE_SOURCES = ("local_gen", "iconify", "openverse", "wikimedia")
# local_gen (see local_gen.py): Stable Diffusion v1.5 + rembg, running locally on this
# machine's GPU. Tried first — originally free-text web search was the only option and
# repeatedly returned irrelevant or inappropriate results (a corporate logo, an
# unrelated map, a coat-of-arms flag — all confirmed in testing) because vector-art
# libraries are sparse and Wikimedia's SVG collection is mostly logos/flags/diagrams,
# not clip-art. Local generation sidesteps both the relevance problem (content is
# generated to match the query, not searched for) and any copyright/ToS concern
# (originally-generated, not sourced from someone else's work).


CONTENT_TYPE_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/svg+xml": "svg",
    "image/webp": "webp",
}


def download(url: str, dest_path_no_ext: str, session: requests.Session, retries: int = 4) -> str:
    """Downloads url, picks the real extension from Content-Type, returns final path."""
    delay = 2.0
    for attempt in range(retries):
        r = session.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
        # 424 = Openverse's thumb proxy failing to reach its upstream (usually
        # Wikimedia) host, typically transient under the same rate limiting as 429.
        if r.status_code in (429, 424) and attempt < retries - 1:
            time.sleep(delay)
            delay *= 2
            continue
        r.raise_for_status()
        content_type = r.headers.get("Content-Type", "").split(";")[0].strip()
        ext = CONTENT_TYPE_EXT.get(content_type, guess_ext(url))
        dest_path = f"{dest_path_no_ext}.{ext}"
        with open(dest_path, "wb") as f:
            f.write(r.content)
        return dest_path
    r.raise_for_status()


def remove_background(src_path: str, transparent_dir: str):
    """Runs src_path through rembg, saves a transparent PNG in transparent_dir.
    Returns the output path, or None if the source isn't a raster image (e.g. SVG)
    or rembg isn't installed."""
    ext = os.path.splitext(src_path)[1].lower().lstrip(".")
    if ext not in ("png", "jpg", "jpeg", "webp"):
        return None  # rembg needs a raster input; SVGs are already vector/editable

    try:
        from rembg import remove
        from PIL import Image
    except ImportError:
        return "MISSING_DEPS"

    os.makedirs(transparent_dir, exist_ok=True)
    out_name = os.path.splitext(os.path.basename(src_path))[0] + ".png"
    out_path = os.path.join(transparent_dir, out_name)

    with open(src_path, "rb") as f:
        input_bytes = f.read()
    output_bytes = remove(input_bytes)
    with open(out_path, "wb") as f:
        f.write(output_bytes)
    return out_path


_svg_render_page = {"page": None}


def _get_svg_render_page():
    """Lazily opens one persistent page on the shared browser_singleton, reused
    across calls. cairosvg was tried first but needs a native Cairo library not
    present on Windows; Chromium is already a proven, working SVG renderer in this
    project (see capture.py) — and browser_singleton is what lets this share a
    process with capture.py's own Playwright usage without conflict (Playwright's
    sync API errors if two independent sync_playwright() instances coexist)."""
    if _svg_render_page["page"] is None:
        root_dir = os.path.dirname(os.path.abspath(__file__))
        if root_dir not in sys.path:
            sys.path.insert(0, root_dir)
        import browser_singleton
        browser = browser_singleton.get_browser()
        _svg_render_page["page"] = browser.new_page(viewport={"width": 800, "height": 800})
    return _svg_render_page["page"]


def rasterize_svg(svg_path: str, out_dir: str, size: int = 800):
    """Renders a raw .svg file to a transparent PNG. Needed because iconify serves raw
    SVG (unlike openverse/wikimedia, which proxy through a thumb renderer that already
    returns PNG) — and raw SVG can't be fed to the vision-model check or loaded as a
    Three.js texture, both of which need a raster image."""
    try:
        page = _get_svg_render_page()
        os.makedirs(out_dir, exist_ok=True)
        out_name = os.path.splitext(os.path.basename(svg_path))[0] + ".png"
        out_path = os.path.join(out_dir, out_name)

        svg_uri = pathlib.Path(svg_path).resolve().as_uri()
        page.goto(svg_uri)
        page.wait_for_timeout(50)
        # Chromium's built-in SVG viewer renders the element at its intrinsic CSS size
        # (often 1em / 16px for icon-style SVGs with no explicit width/height) — force
        # it up to a real resolution before screenshotting, or the raster comes out
        # as a 16x16 thumbnail.
        # Most iconify icon sets are single-color and paint via fill/stroke
        # "currentColor", which resolves from the CSS `color` property — Chromium's
        # default text color is black, so on this project's dark scene background
        # (near-black) these icons rendered essentially invisible. Setting `color`
        # to white only affects elements actually using currentColor; icon sets
        # with their own explicit fill colors (multicolor/emoji-style sets) are
        # unaffected and keep their real colors.
        page.evaluate(
            f"""
            document.documentElement.style.background = 'transparent';
            document.documentElement.style.color = '#ffffff';
            if (document.body) {{
                document.body.style.background = 'transparent';
                document.body.style.margin = '0';
            }}
            const svg = document.querySelector('svg');
            if (svg) {{
                svg.style.width = '{size}px';
                svg.style.height = '{size}px';
            }}
            """
        )
        svg_el = page.query_selector("svg")
        if svg_el:
            svg_el.screenshot(path=out_path, omit_background=True)
        else:
            page.screenshot(path=out_path, omit_background=True)
        return out_path
    except Exception:
        return None


_VECTOR_SOURCES = ("iconify", "openverse", "wikimedia")  # already-transparent SVG-based — skip rembg


def _vlm_check_asset(image_path: str, query: str, intent: str = None):
    """Lazy-imports vlm_client so downloader.py has no hard dependency on Ollama/vlm/
    being available (e.g. the standalone CLI use case). Returns None if unavailable —
    callers should treat that as 'no opinion, accept'."""
    try:
        vlm_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vlm")
        if vlm_dir not in sys.path:
            sys.path.insert(0, vlm_dir)
        import vlm_client
        return vlm_client.check_asset(image_path, query, intent)
    except Exception as e:
        return {"accept": True, "confidence": 0.0, "reason": f"check unavailable: {e}"}


def resolve_asset(query: str, out_dir: str, sources=None,
                   remove_bg: bool = True, pixabay_key: str = None, session: requests.Session = None,
                   verify: bool = True, intent: str = None, max_candidates_per_source: int = 4):
    """Programmatic single-query API for callers like the parallax pipeline.

    Tries each source in order, and within each source tries up to
    max_candidates_per_source results, downloading and (if verify=True) running
    each through vlm_client.check_asset() — a small-model caption/relevance check —
    before accepting it. This exists because keyword/category filtering alone let a
    corporate logo and an unrelated map through in testing; the VLM check catches
    some (not all — it's an 0.8B model) of what keyword filtering misses.

    Returns the first ACCEPTED download as:
        {"path": <original file>, "transparent_path": <PNG or None>,
         "license": str, "source": str, "page_url": str, "check": <check_asset result or None>}
    Returns None if every source/candidate failed or was rejected.
    """
    sources = sources or ACTIVE_SOURCES
    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    os.makedirs(out_dir, exist_ok=True)
    transparent_dir = os.path.join(out_dir, "transparent")
    query_slug = slugify(query)

    for source_name in sources:
        if source_name == "local_gen":
            # Locally generated (Stable Diffusion + rembg, see local_gen.py) — no
            # search/licensing involved, content is deterministically derived from
            # the query itself rather than found, so it's exempted from the VLM
            # relevance gate the same way iconify is (see below), and there's only
            # ever one "candidate" per call, not a list to page through.
            try:
                gen_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)))
                if gen_dir not in sys.path:
                    sys.path.insert(0, gen_dir)
                import local_gen
                gen_path = local_gen.generate_asset(query, out_dir)
            except Exception as e:
                print(f"  local_gen failed for {query!r}: {e}")
                continue
            return {
                "path": gen_path,
                "transparent_path": gen_path,  # server already ran rembg
                "license": "Generated (Stable Diffusion v1.5, local)",
                "source": "local_gen",
                "page_url": "",
                "query": query,
                "check": None,
            }
        if source_name == "pixabay":
            if not pixabay_key:
                continue
            items = list(fetch_pixabay(query, max_candidates_per_source, session, pixabay_key))
        else:
            try:
                items = list(SOURCE_FETCHERS[source_name](query, max_candidates_per_source, session))
            except requests.RequestException:
                continue

        for i, item in enumerate(items[:max_candidates_per_source], start=1):
            url = item.get("url")
            if not url:
                continue
            base_name = f"{query_slug}__{source_name}-{i}"
            dest_path_no_ext = os.path.join(out_dir, base_name)
            try:
                saved_path = download(url, dest_path_no_ext, session)
            except requests.RequestException:
                continue

            transparent_path = None
            ext = os.path.splitext(saved_path)[1].lower().lstrip(".")
            if ext == "svg":
                # Raw SVG (iconify) — rasterize so it can be VLM-checked and later
                # loaded as a Three.js texture. Already transparent, no rembg needed.
                transparent_path = rasterize_svg(saved_path, transparent_dir)
            elif remove_bg and source_name not in _VECTOR_SOURCES:
                # openverse/wikimedia proxy through a thumb renderer that already
                # returns transparent PNG — rembg would only degrade clean vector
                # edges. Only raster sources (pixabay's rendered vector previews)
                # need background removal.
                result = remove_background(saved_path, transparent_dir)
                if result and result != "MISSING_DEPS":
                    transparent_path = result

            check_result = None
            # Iconify results are excluded from the VLM gate: its search matches on
            # precise, curated icon names (e.g. "tabler:deer" for query "deer"), which
            # is already strong evidence of relevance — and in testing the small VLM
            # showed a systematic bias against flat monochrome icon-style art, rejecting
            # a clean, correct deer icon as "no subject" at 0.98 confidence. The check
            # earns its keep on the noisy free-text sources (openverse/wikimedia/pixabay),
            # where it caught real problems (a brand logo, an unrelated venue map).
            if verify and source_name not in ("iconify",):
                check_path = transparent_path or saved_path
                check_result = _vlm_check_asset(check_path, query, intent)
                if not check_result.get("accept", True):
                    continue  # rejected — try the next candidate

            return {
                "path": saved_path,
                "transparent_path": transparent_path,
                "license": item.get("license", ""),
                "source": source_name,
                "page_url": item.get("page_url", ""),
                "query": query,
                "check": check_result,
            }
    return None


def main():
    parser = argparse.ArgumentParser(description="Download clip-art / vector assets from multiple free libraries.")
    parser.add_argument("queries", nargs="*", help='Search terms, e.g. "rocket ship" "clock icon"')
    parser.add_argument("--queries-file", help="Path to a text file with one query per line")
    parser.add_argument("--out", default="assets", help="Output directory (default: assets)")
    parser.add_argument("--per-query", type=int, default=5, help="Max images per query per source (default: 5)")
    parser.add_argument(
        "--sources",
        nargs="+",
        default=list(ACTIVE_SOURCES) + ["pixabay"],
        choices=list(SOURCE_FETCHERS.keys()),
        help="Which sources to try (default: all working sources — freesvg is registered but disabled, no real API found)",
    )
    parser.add_argument("--pixabay-key", default=os.environ.get("PIXABAY_API_KEY"), help="Pixabay API key (optional)")
    parser.add_argument(
        "--remove-bg",
        action="store_true",
        help="Run rembg on every downloaded raster image and save a transparent PNG in <out>/transparent/",
    )
    args = parser.parse_args()

    queries = list(args.queries)
    if args.queries_file:
        with open(args.queries_file, "r", encoding="utf-8") as f:
            queries.extend(line.strip() for line in f if line.strip())

    if not queries:
        parser.error("No queries given. Pass them as arguments or via --queries-file.")

    os.makedirs(args.out, exist_ok=True)
    log_path = os.path.join(args.out, "log.csv")
    log_exists = os.path.exists(log_path)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    total_ok, total_fail = 0, 0

    transparent_dir = os.path.join(args.out, "transparent")
    warned_missing_deps = False

    with open(log_path, "a", newline="", encoding="utf-8") as logfile:
        writer = csv.writer(logfile)
        if not log_exists:
            writer.writerow(
                ["timestamp_utc", "query", "source", "filename", "status", "license", "source_url", "page_url", "transparent_png"]
            )

        for query in queries:
            query_slug = slugify(query)
            print(f"\n=== Query: {query!r} ===")
            for source_name in args.sources:
                fetcher = SOURCE_FETCHERS[source_name]
                print(f"  [{source_name}] searching...")
                try:
                    if source_name == "pixabay":
                        if not args.pixabay_key:
                            print("  [pixabay] skipped (no API key provided)")
                            continue
                        items = list(fetcher(query, args.per_query, session, args.pixabay_key))
                    else:
                        items = list(fetcher(query, args.per_query, session))
                except requests.HTTPError as e:
                    print(f"  [{source_name}] ERROR searching: {e}")
                    continue
                except requests.RequestException as e:
                    print(f"  [{source_name}] ERROR: {e}")
                    continue

                if not items:
                    print(f"  [{source_name}] no results")
                    continue

                for i, item in enumerate(items, start=1):
                    url = item.get("url")
                    if not url:
                        continue
                    base_name = f"{query_slug}__{source_name}-{i}"
                    dest_path_no_ext = os.path.join(args.out, base_name)
                    filename = base_name  # fallback label if download fails before extension is known
                    timestamp = datetime.now(timezone.utc).isoformat()
                    transparent_result = ""
                    try:
                        saved_path = download(url, dest_path_no_ext, session)
                        filename = os.path.basename(saved_path)
                        print(f"  [{source_name}] saved {filename}")

                        if args.remove_bg:
                            result = remove_background(saved_path, transparent_dir)
                            if result == "MISSING_DEPS":
                                if not warned_missing_deps:
                                    print("  --remove-bg requested but rembg/pillow not installed. "
                                          "Run: pip install rembg onnxruntime pillow")
                                    warned_missing_deps = True
                                transparent_result = "MISSING_DEPS"
                            elif result:
                                transparent_result = result
                                print(f"    -> transparent PNG: {os.path.basename(result)}")

                        writer.writerow(
                            [timestamp, query, source_name, filename, "ok", item.get("license", ""), url, item.get("page_url", ""), transparent_result]
                        )
                        total_ok += 1
                    except requests.RequestException as e:
                        print(f"  [{source_name}] FAILED {url}: {e}")
                        writer.writerow(
                            [timestamp, query, source_name, filename, f"failed: {e}", item.get("license", ""), url, item.get("page_url", ""), ""]
                        )
                        total_fail += 1
                    logfile.flush()
                    time.sleep(1.0)

    print(f"\nDone. {total_ok} saved, {total_fail} failed.")
    print(f"Assets:  {os.path.abspath(args.out)}")
    print(f"Logbook: {os.path.abspath(log_path)}")
    print("Note: transparency is NOT guaranteed on any source. Run a background remover")
    print("      (e.g. rembg) on anything you need alpha-channel PNGs for.")


if __name__ == "__main__":
    main()
