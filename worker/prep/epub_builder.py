"""EPUB builder. Produces a minimal valid EPUB 3 from parsed content."""

from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET


NS = "http://www.w3.org/1999/xhtml"


def build_epub(
    title: str,
    author: str,
    language: str,
    pages: list[dict],
    chapters: list[dict],
) -> bytes:
    """Build a minimal valid EPUB 3 from parsed pages and chapters."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", _container_xml())
        zf.writestr("OEBPS/content.opf", _content_opf(title, author, language, chapters))
        zf.writestr("OEBPS/nav.xhtml", _nav_xml(title, chapters))
        for ch in chapters:
            content = _chapter_xml(ch, pages)
            zf.writestr(f"OEBPS/{ch['section']}.xhtml", content)
    return buf.getvalue()


def _container_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _content_opf(title: str, author: str, language: str, chapters: list[dict]) -> str:
    items = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>']
    spine = []
    for i, ch in enumerate(chapters, 1):
        item_id = f"ch-{i}"
        items.append(f'<item id="{item_id}" href="{ch["section"]}.xhtml" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="{item_id}"/>')

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id" xml:lang="{language}">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">urn:uuid:{title.replace(' ', '-')}-{author.replace(' ', '-')}</dc:identifier>
    <dc:title>{_esc(title)}</dc:title>
    <dc:creator>{_esc(author)}</dc:creator>
    <dc:language>{language}</dc:language>
  </metadata>
  <manifest>
    {''.join(items)}
  </manifest>
  <spine>
    {''.join(spine)}
  </spine>
</package>
"""


def _nav_xml(title: str, chapters: list[dict]) -> str:
    entries = []
    for ch in chapters:
        entries.append(f'<li><a href="{ch["section"]}.xhtml">{_esc(ch["title"])}</a></li>')
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="{NS}" xmlns:epub="http://www.idpf.org/2007/ops">
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
<html xmlns="{NS}">
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
