"""Cover art extraction, search, and placeholder generation."""

from __future__ import annotations

import io
import re
import zipfile

import httpx
from PIL import Image, ImageDraw, ImageFont

OPEN_LIBRARY_SEARCH = "https://openlibrary.org/search.json"
OPEN_LIBRARY_COVER = "https://covers.openlibrary.org/b/id/{id}-{size}.jpg"


def extract_cover_from_pdf(data: bytes) -> bytes | None:
    import fitz

    doc = None
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        page = doc[0]
        images = page.get_images(full=True)
        if images:
            xref = images[0][0]
            # Decode the image stream itself and let PIL handle whatever
            # colorspace it actually is (grayscale, RGB, RGBA). The old
            # Pixmap/frombytes path guessed RGB and crashed on grayscale
            # scans (#79): pix.n == 1 fell through the n > 4 guard and
            # Image.frombytes raised "not enough image data", failing the
            # whole job over cover art.
            info = doc.extract_image(xref)
            img = Image.open(io.BytesIO(info["image"]))
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
        pix = page.get_pixmap(dpi=150)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        # Cover extraction must never fail a job (#79): returning None
        # hands control to get_cover's fallback chain (OpenLibrary search,
        # then placeholder). This includes unopenable input — the open
        # itself lives inside the guard.
        return None
    finally:
        if doc is not None:
            doc.close()


def extract_cover_from_epub(data: bytes) -> bytes | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            container_xml = zf.read("META-INF/container.xml")
            import xml.etree.ElementTree as ET

            root = ET.fromstring(container_xml)
            ns = {"container": "urn:oasis:names:tc:opendocument:xmlns:container"}
            rootfile = root.find(".//container:rootfile", ns)
            if rootfile is None:
                return None
            opf_path = rootfile.get("full-path")
            opf = ET.fromstring(zf.read(opf_path))
            opf_ns = {"opf": "http://www.idpf.org/2007/opf"}

            cover_id = None
            meta = opf.find("opf:metadata", opf_ns)
            if meta is not None:
                for item in meta.findall("opf:meta", opf_ns):
                    if item.get("name") == "cover":
                        cover_id = item.get("content")
                        break

            manifest = opf.find("opf:manifest", opf_ns)
            if manifest is None:
                return None
            for item in manifest.findall("opf:item", opf_ns):
                href = item.get("href")
                if not href:
                    continue
                if cover_id and item.get("id") == cover_id:
                    return zf.read(href)
                media_type = (item.get("media-type") or "").lower()
                if media_type.startswith("image/"):
                    return zf.read(href)
    except Exception:
        return None
    return None


def search_cover(title: str, author: str, http: httpx.Client | None = None) -> bytes | None:
    client = http or httpx.Client(timeout=15)
    try:
        response = client.get(
            OPEN_LIBRARY_SEARCH,
            params={"title": title, "author": author, "limit": 1},
        )
        response.raise_for_status()
        docs = response.json().get("docs", [])
        if not docs:
            return None
        cover_id = docs[0].get("cover_i")
        if not cover_id:
            return None
        cover_url = OPEN_LIBRARY_COVER.format(id=cover_id, size="L")
        cover_response = client.get(cover_url)
        cover_response.raise_for_status()
        return cover_response.content
    except Exception:
        return None


def generate_placeholder(title: str, author: str) -> bytes:
    width, height = 600, 900
    img = Image.new("RGB", (width, height), color=(44, 62, 80))
    draw = ImageDraw.Draw(img)
    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_author = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
    except OSError:
        font_title = ImageFont.load_default()
        font_author = ImageFont.load_default()

    title_lines = _wrap_text(title, font_title, draw, width - 80)
    y = 120
    for line in title_lines:
        draw.text((40, y), line, fill=(255, 255, 255), font=font_title)
        y += 45

    y = height - 160
    author_lines = _wrap_text(author, font_author, draw, width - 80)
    for line in author_lines:
        draw.text((40, y), line, fill=(189, 195, 199), font=font_author)
        y += 32

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _wrap_text(text: str, font, draw: ImageDraw.ImageDraw, max_width: int) -> list[str]:
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def get_cover(
    data: bytes,
    input_format: str,
    title: str,
    author: str,
    http: httpx.Client | None = None,
) -> tuple[bytes, str]:
    """Return (jpeg_bytes, source) where source is extracted|searched|placeholder."""
    if input_format == "pdf":
        cover = extract_cover_from_pdf(data)
    elif input_format == "epub":
        cover = extract_cover_from_epub(data)
    else:
        cover = None

    if cover:
        return cover, "extracted"

    cover = search_cover(title, author, http=http)
    if cover:
        return cover, "searched"

    return generate_placeholder(title, author), "placeholder"
