#!/usr/bin/env python3
"""Build data/slides.json from the pictures in images/slideshow/.

Drop pictures into images/slideshow/ on GitHub. This script (run by the
"Update slideshow" workflow) does four things:

1. Keeps every picture at full quality. A picture is only re-saved when it is
   wider than 3840 px (4K) or larger than 8 MB.
2. Makes smaller copies (800, 1600 and 2560 px wide) in images/slideshow-sizes/
   so phones and laptops download a small file while a 4K monitor gets the
   full-size one. The browser chooses the right copy by itself.
3. Reads the file name to make the description (alt text), for example
   "03-drone-view-of-soybean-plots.jpg" becomes "Drone view of soybean plots".
4. Writes data/slides.json, which the web page reads to build the slideshow.

Optional: images/slideshow/captions.json can set an exact description, a
caption, or "contain": true for a picture that must not be cropped, e.g.
{"03-soybean.jpg": {"alt": "...", "caption": "...", "contain": false}}.
A file name containing "figure", "fig" or "diagram" as a whole word is shown
uncropped automatically.
"""

import json
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import quote

from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = None  # large field and drone photographs are expected

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "images" / "slideshow"
SIZES = ROOT / "images" / "slideshow-sizes"
OUTPUT = ROOT / "data" / "slides.json"
CAPTIONS = FOLDER / "captions.json"
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_WIDTH = 3840
MAX_BYTES = 8 * 1024 * 1024
MASTER_QUALITY = 92
SMALL_QUALITY = 84
SMALL_WIDTHS = (800, 1600, 2560)
UNCROPPED = re.compile(r"(^|[-_ ])(figure|fig|diagram)([-_. ]|$)", re.IGNORECASE)


def natural_key(name):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]


def alt_from_name(path):
    stem = path.stem
    stem = re.sub(r"^\d+[-_. ]*", "", stem)
    stem = re.sub(r"^(figure|fig|diagram)[-_ ]+", "", stem, flags=re.IGNORECASE)
    text = re.sub(r"[-_]+", " ", stem).strip()
    if not text:
        return "Research photograph"
    return text[0].upper() + text[1:]


def save(image, path, quality):
    suffix = path.suffix.lower()

    if suffix in (".jpg", ".jpeg"):
        image.convert("RGB").save(
            path, "JPEG", quality=quality, optimize=True, progressive=True, subsampling=0
        )
    elif suffix == ".png":
        image.save(path, "PNG", optimize=True)
    else:
        image.save(path, "WEBP", quality=quality, method=6)


def prepare_master(path):
    """Keep the original unless it is above 4K width or 8 MB. Returns (width, height)."""
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened)
        image.load()
        width, height = image.size
        too_wide = width > MAX_WIDTH
        too_big = path.stat().st_size > MAX_BYTES

        if not (too_wide or too_big):
            return width, height

        if too_wide:
            height = round(height * MAX_WIDTH / width)
            width = MAX_WIDTH
            image = image.resize((width, height), Image.LANCZOS)

        save(image, path, MASTER_QUALITY)

    return width, height


def make_copies(path, width):
    """Write smaller copies. Returns the list of (relative url, width)."""
    entries = []

    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened)
        image.load()

        for target in SMALL_WIDTHS:
            if target >= width * 0.9:  # not worth a copy that is almost full size
                continue

            height = round(image.size[1] * target / image.size[0])
            copy_path = SIZES / f"{path.stem}-{target}w{path.suffix.lower()}"
            save(image.resize((target, height), Image.LANCZOS), copy_path, SMALL_QUALITY)
            entries.append(("images/slideshow-sizes/" + quote(copy_path.name), target))

    entries.append(("images/slideshow/" + quote(path.name), width))
    return entries


def read_captions():
    if not CAPTIONS.exists():
        return {}

    try:
        data = json.loads(CAPTIONS.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"::warning::captions.json could not be read: {error}")
        return {}

    return data if isinstance(data, dict) else {}


def main():
    FOLDER.mkdir(parents=True, exist_ok=True)

    if SIZES.exists():  # rebuilt every time, so deleted pictures disappear too
        shutil.rmtree(SIZES)

    SIZES.mkdir(parents=True, exist_ok=True)
    captions = read_captions()
    files = sorted(
        (p for p in FOLDER.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS),
        key=lambda p: natural_key(p.name),
    )
    slides = []

    for path in files:
        try:
            width, height = prepare_master(path)
            sources = make_copies(path, width)
        except Exception as error:  # a damaged picture must not stop the others
            print(f"::warning::Skipped {path.name}: {error}")
            continue

        extra = captions.get(path.name, {})
        extra = extra if isinstance(extra, dict) else {}
        slides.append({
            "src": "images/slideshow/" + quote(path.name),
            "sources": [{"src": url, "w": w} for url, w in sources],
            "alt": str(extra.get("alt") or alt_from_name(path)),
            "caption": str(extra.get("caption") or ""),
            "contain": bool(extra.get("contain", UNCROPPED.search(path.stem))),
            "width": width,
            "height": height,
        })
        print(f"Added {path.name} ({width} x {height}, {len(sources)} sizes)")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({"slides": slides}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(slides)} slides to {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
