#!/usr/bin/env python3
"""Build data/slides.json from the pictures in images/slideshow/.

Drop pictures into images/slideshow/ on GitHub. This script (run by the
"Update slideshow" workflow) does three things:

1. Shrinks any picture that is wider than 1900 px or larger than 600 KB.
2. Reads the file name to make the description (alt text), for example
   "03-drone-view-of-soybean-plots.jpg" becomes "Drone view of soybean plots".
3. Writes data/slides.json, which the web page reads to build the slideshow.

Optional: images/slideshow/captions.json can set an exact description, a
caption, or "contain": true for a picture that must not be cropped, e.g.
{"03-soybean.jpg": {"alt": "...", "caption": "...", "contain": false}}.
A file name containing "figure", "fig" or "diagram" as a whole word is shown
uncropped automatically.
"""

import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "images" / "slideshow"
OUTPUT = ROOT / "data" / "slides.json"
CAPTIONS = FOLDER / "captions.json"
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_WIDTH = 1900
MAX_BYTES = 600 * 1024
QUALITY = 72
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


def shrink(path):
    """Resize in place when the picture is too big. Returns (width, height)."""
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        width, height = image.size
        too_wide = width > MAX_WIDTH
        too_big = path.stat().st_size > MAX_BYTES

        if not (too_wide or too_big):
            return width, height

        if too_wide:
            height = round(height * MAX_WIDTH / width)
            width = MAX_WIDTH
            image = image.resize((width, height), Image.LANCZOS)

        suffix = path.suffix.lower()

        if suffix in (".jpg", ".jpeg"):
            image.convert("RGB").save(path, "JPEG", quality=QUALITY, optimize=True, progressive=True)
        elif suffix == ".png":
            image.save(path, "PNG", optimize=True)
        else:
            image.save(path, "WEBP", quality=QUALITY)

    return width, height


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
    captions = read_captions()
    files = sorted(
        (p for p in FOLDER.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS),
        key=lambda p: natural_key(p.name),
    )
    slides = []

    for path in files:
        try:
            width, height = shrink(path)
        except Exception as error:  # a damaged picture must not stop the others
            print(f"::warning::Skipped {path.name}: {error}")
            continue

        extra = captions.get(path.name, {})
        extra = extra if isinstance(extra, dict) else {}
        slides.append({
            "src": "images/slideshow/" + quote(path.name),
            "alt": str(extra.get("alt") or alt_from_name(path)),
            "caption": str(extra.get("caption") or ""),
            "contain": bool(extra.get("contain", UNCROPPED.search(path.stem))),
            "width": width,
            "height": height,
        })
        print(f"Added {path.name} ({width} x {height})")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({"slides": slides}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(slides)} slides to {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
