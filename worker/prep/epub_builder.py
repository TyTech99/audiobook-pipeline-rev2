"""EPUB builder. Produces a valid EPUB 3 from parsed content.

Includes the EPUB 2 NCX table of contents alongside the EPUB 3 nav
document (Kindle compatibility), embeds the cover image with proper
manifest metadata, and declares the epub: namespace on content that
uses epub:type.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET


NS = "http://www.w3.org/1999/xhtml"
EPUB_NS = "http://www.idpf.org/2007/ops"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"


def build_epub(
    title: str,
    author: str,
    language: str,
    pages: list[dict],
    chapters: list[dict],
    cover: bytes | None = None,
) -> bytes:
    """Build a valid EPUB 3 from parsed pages and chapters."""
    identifier = _identifier(title, author)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # mimetype must be the first, uncompressed entry.
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", _container_xml())
        zf.writestr("OEBPS/content.opf", _content_opf(identifier, title, author, language, chapters, cover))
        zf.writestr("OEBPS/nav.xhtml", _nav_xml(title, chapters))
        zf.writestr("OEBPS/toc.ncx", _ncx_xml(identifier, title, chapters))
        if cover:
            zf.writestr("OEBPS/cover.jpg", cover)
            zf.writestr("OEBPS/cover.xhtml", _cover_xhtml(title))
        for ch in chapters:
            content = _chapter_xml(ch, pages)
            zf.writestr(f"OEBPS/{ch['section']}.xhtml", content)
    return buf.getvalue()


def _identifier(title: str, author: str) -> str:
    return f"urn:uuid:{title.replace(' ', '-')}-{author.replace(' ', '-')}"


def _container_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _content_opf(identifier: str, title: str, author: str, language: str, chapters: list[dict], cover: bytes | None) -> str:
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
    ]
    spine = []
    if cover:
        items.append('<item id="cover-image" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>')
        items.append('<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>')
        spine.append('<itemref idref="cover"/>')
    for i, ch in enumerate(chapters, 1):
        item_id = f"ch-{i}"
        items.append(f'<item id="{item_id}" href="{ch["section"]}.xhtml" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="{item_id}"/>')

    cover_meta = '<meta name="cover" content="cover-image"/>' if cover else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id" xml:lang="{language}">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">{_esc(identifier)}</dc:identifier>
    <dc:title>{_esc(title)}</dc:title>
    <dc:creator>{_esc(author)}</dc:creator>
    <dc:language>{language}</dc:language>
    <meta property="dcterms:modified">{modified}</meta>
    {cover_meta}
  </metadata>
  <manifest>
    {''.join(items)}
  </manifest>
  <spine toc="ncx">
    {''.join(spine)}
  </spine>
</package>
"""


def _nav_xml(title: str, chapters: list[dict]) -> str:
    entries = []
    for ch in chapters:
        entries.append(f'<li><a href="{ch["section"]}.xhtml">{_esc(ch["title"])}</a></li>')
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="{NS}" xmlns:epub="{EPUB_NS}">
<head><title>{_esc(title)}</title></head>
<body>
<nav epub:type="toc" id="toc">
<h1>Table of Contents</h1>
<ol>
{''.join(entries)}
</ol>
</nav>
</body>
</html>
"""


def _ncx_xml(identifier: str, title: str, chapters: list[dict]) -> str:
    """EPUB 2 table of contents for Kindle compatibility."""
    points = []
    for i, ch in enumerate(chapters, 1):
        points.append(
            f'<navPoint id="navpoint-{i}" playOrder="{i}">'
            f"<navLabel><text>{_esc(ch['title'])}</text></navLabel>"
            f'<content src="{ch["section"]}.xhtml"/>'
            f"</navPoint>"
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="{NCX_NS}" version="2005-1">
  <head>
    <meta name="dtb:uid" content="{_esc(identifier)}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{_esc(title)}</text></docTitle>
  <navMap>
    {''.join(points)}
  </navMap>
</ncx>
"""


def _cover_xhtml(title: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="{NS}" xmlns:epub="{EPUB_NS}">
<head><title>{_esc(title)}</title></head>
<body>
<figure epub:type="cover" style="text-align: center; margin: 0;">
<img src="cover.jpg" alt="{_esc(title)}" style="max-width: 100%; max-height: 100%;"/>
</figure>
</body>
</html>
"""


def _chapter_xml(chapter: dict, pages: list[dict]) -> str:
    paragraphs = []
    for page_num in chapter["pages"]:
        page = next((p for p in pages if p["page"] == page_num), None)
        if page is None or not page["text"]:
            continue
        for para in page["text"].split("\n\n"):
            para = para.strip()
            if para:
                paragraphs.append(f"<p>{_esc(para)}</p>")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="{NS}" xmlns:epub="{EPUB_NS}">
<head><title>{_esc(chapter["title"])}</title></head>
<body>
<section epub:type="chapter">
<h1>{_esc(chapter["title"])}</h1>
{''.join(paragraphs)}
</section>
</body>
</html>
"""


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
