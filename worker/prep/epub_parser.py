"""Pure functions for EPUB parsing. No I/O, no Redis, no Docker."""

from __future__ import annotations

import re
import zipfile
from xml.etree import ElementTree as ET


NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "xhtml": "http://www.w3.org/1999/xhtml",
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
}


def parse_epub(data: bytes) -> dict:
    """Parse EPUB bytes into a structured document representation."""
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as zf:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        rootfile = container.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
        if rootfile is None:
            rootfile = container.find(".//rootfile")
        if rootfile is None:
            raise ValueError("No rootfile in container.xml")
        opf_path = rootfile.get("full-path")

        opf = ET.fromstring(zf.read(opf_path))
        metadata = _extract_metadata(opf)

        manifest = opf.find("opf:manifest", NS)
        items = {}
        if manifest is not None:
            for item in manifest.findall("opf:item", NS):
                items[item.get("id")] = {
                    "href": item.get("href"),
                    "media_type": item.get("media-type"),
                    "properties": item.get("properties", ""),
                }

        spine = opf.find("opf:spine", NS)
        spine_ids = []
        if spine is not None:
            for itemref in spine.findall("opf:itemref", NS):
                spine_ids.append(itemref.get("idref"))

        pages = []
        page_num = 1
        for item_id in spine_ids:
            item = items.get(item_id)
            if item is None:
                continue
            href = item["href"]
            media_type = item["media_type"] or ""
            if "html" not in media_type and "xhtml" not in media_type:
                pages.append(
                    {
                        "page": page_num,
                        "item": href,
                        "text": "",
                        "images": 0,
                        "media_type": media_type,
                    }
                )
                page_num += 1
                continue

            try:
                content = zf.read(href)
                root = ET.fromstring(content)
                text = _extract_text(root).strip()
                images = len(root.findall(".//xhtml:img", NS))
                pages.append(
                    {
                        "page": page_num,
                        "item": href,
                        "text": text,
                        "images": images,
                        "media_type": media_type,
                    }
                )
            except Exception:
                pages.append(
                    {
                        "page": page_num,
                        "item": href,
                        "text": "",
                        "images": 0,
                        "media_type": media_type,
                    }
                )
            page_num += 1

    return {
        "format": "epub",
        "pages": pages,
        "total_pages": len(pages),
        "metadata": metadata,
    }


def _extract_metadata(opf: ET.Element) -> dict:
    metadata_el = opf.find("opf:metadata", NS)
    if metadata_el is None:
        return {"title": "", "author": "", "subject": ""}

    dc_ns = "{http://purl.org/dc/elements/1.1/}"

    def _text(tag):
        el = metadata_el.find(f"{dc_ns}{tag}")
        if el is not None and el.text:
            return el.text.strip()
        return ""

    return {
        "title": _text("title"),
        "author": _text("creator"),
        "subject": _text("subject"),
    }


def _extract_text(root: ET.Element) -> str:
    parts = []
    for el in root.iter():
        if el.tag.endswith("}img") or el.tag == "img":
            continue
        if el.text:
            parts.append(el.text)
        if el.tail:
            parts.append(el.tail)
    text = " ".join(parts)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def classify_epub_item(page: dict) -> tuple[str, str]:
    """Classify a single EPUB item. Returns (action, reason).

    Thin wrapper over page_types.classify_text_page — the canonical
    classifier shared with the PDF path (issue #42). Before the
    consolidation this twin had silently drifted from classify_page
    (missing the acknowledgments and backmatter rules, and the
    'For <word>' dedication bug fixed twice).
    """
    from worker.prep.page_types import classify_text_page

    return classify_text_page(page["text"], page.get("images", 0))
