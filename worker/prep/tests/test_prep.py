"""Tests for prep worker pure functions."""

import io
import json
import struct
import zipfile

import pytest

from worker.prep.epub_builder import build_epub
from worker.prep.epub_parser import classify_epub_item, parse_epub
from worker.prep.narrator_script import build_narrator_script, validate_script
from worker.prep.page_ledger import build_page_ledger, validate_page_ledger
from worker.prep.pdf_parser import classify_page, detect_chapters, parse_pdf


def make_test_pdf() -> bytes:
    """Create a minimal valid PDF with 3 pages using PyMuPDF."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(50, 50, 500, 700), "Chapter 1\n\nThis is the first page of the book. It has some content to read.", fontsize=12)
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(50, 50, 500, 700), "This is the second page. It continues the story with more text.", fontsize=12)
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(50, 50, 500, 700), "Chapter 2\n\nThis is the second chapter. It starts here.", fontsize=12)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def make_test_epub() -> bytes:
    """Create a minimal valid EPUB with 2 chapters."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        zf.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="id">test</dc:identifier>
    <dc:title>Test Book</dc:title>
    <dc:creator>Test Author</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="ch2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="ch1"/><itemref idref="ch2"/></spine>
</package>""",
        )
        zf.writestr(
            "OEBPS/nav.xhtml",
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>Nav</title></head><body><nav><h1>TOC</h1><ol><li><a href="ch1.xhtml">Ch 1</a></li><li><a href="ch2.xhtml">Ch 2</a></li></ol></nav></body></html>',
        )
        zf.writestr(
            "OEBPS/ch1.xhtml",
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>Ch 1</title></head><body><h1>Chapter 1</h1><p>This is the first chapter. It has content.</p></body></html>',
        )
        zf.writestr(
            "OEBPS/ch2.xhtml",
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>Ch 2</title></head><body><h1>Chapter 2</h1><p>This is the second chapter. More content here.</p></body></html>',
        )
    return buf.getvalue()


def test_parse_pdf():
    data = make_test_pdf()
    doc = parse_pdf(data)
    assert doc["format"] == "pdf"
    assert doc["total_pages"] == 3
    assert len(doc["pages"]) == 3
    assert doc["pages"][0]["page"] == 1
    assert "Chapter 1" in doc["pages"][0]["text"]


def test_classify_page():
    blank = {"page": 1, "text": "", "images": 0}
    assert classify_page(blank) == ("skipped", "blank")

    illustration = {"page": 2, "text": "", "images": 3}
    assert classify_page(illustration) == ("skipped", "illustration only")

    body = {"page": 3, "text": "This is a normal paragraph of body text in the book.", "images": 0}
    assert classify_page(body) == ("kept", "body text")

    copyright_page = {"page": 4, "text": "Copyright 2026. All rights reserved. ISBN 123-456.", "images": 0}
    assert classify_page(copyright_page) == ("kept", "copyright")


def test_detect_chapters():
    pages = [
        {"page": 1, "text": "Chapter 1\n\nFirst page content.", "images": 0},
        {"page": 2, "text": "More content on page two.", "images": 0},
        {"page": 3, "text": "Chapter 2\n\nSecond chapter starts.", "images": 0},
    ]
    chapters = detect_chapters(pages)
    assert len(chapters) == 2
    assert chapters[0]["title"] == "Chapter 1"
    assert chapters[0]["pages"] == [1, 2]
    assert chapters[1]["title"] == "Chapter 2"
    assert chapters[1]["pages"] == [3]


def test_build_page_ledger_pdf():
    pages = [
        {"page": 1, "text": "Chapter 1\n\nContent.", "images": 0, "action": "kept", "reason": "chapter heading"},
        {"page": 2, "text": "More content.", "images": 0, "action": "kept", "reason": "body text"},
        {"page": 3, "text": "", "images": 0, "action": "skipped", "reason": "blank"},
    ]
    chapters = [
        {"number": 1, "title": "Chapter 1", "section": "chapter-1", "start_page": 1, "end_page": 3, "pages": [1, 2, 3]},
    ]
    ledger = build_page_ledger("pdf", "test.pdf", pages, chapters)
    assert ledger["source_format"] == "pdf"
    assert ledger["total_pages"] == 3
    assert len(ledger["pages"]) == 3
    assert ledger["summary"]["total"] == 3
    assert ledger["summary"]["kept"] == 2
    assert ledger["summary"]["skipped"] == 1

    errors = validate_page_ledger(ledger, 3)
    assert errors == []


def test_validate_page_ledger_catches_gaps():
    ledger = {
        "source_format": "pdf",
        "source_file": "test.pdf",
        "total_pages": 3,
        "pages": [
            {"page": 1, "action": "kept", "reason": "body", "section": "chapter-1"},
            {"page": 3, "action": "kept", "reason": "body", "section": "chapter-1"},
        ],
        "structure": [],
        "summary": {"total": 2, "kept": 2, "skipped": 0, "skipped_breakdown": {}},
    }
    errors = validate_page_ledger(ledger, 3)
    assert any("gaps" in e for e in errors)


def test_build_narrator_script():
    pages = [
        {"page": 1, "text": "Chapter 1\n\nThis is the first sentence. This is the second sentence.", "images": 0, "action": "kept", "reason": "body text"},
        {"page": 2, "text": "This continues on page two. Another sentence here.", "images": 0, "action": "kept", "reason": "body text"},
    ]
    chapters = [
        {"number": 1, "title": "Chapter 1", "section": "chapter-1", "start_page": 1, "end_page": 2, "pages": [1, 2]},
    ]
    script = build_narrator_script("job-1", "pdf", "test.pdf", "Test", "Author", "en", pages, chapters)
    assert script["title"] == "Test"
    assert script["author"] == "Author"
    assert len(script["chapters"]) == 1
    assert len(script["chapters"][0]["segments"]) >= 2
    assert script["metadata"]["total_segments"] == len(script["chapters"][0]["segments"])

    ledger = build_page_ledger("pdf", "test.pdf", pages, chapters)
    errors = validate_script(script, ledger)
    assert errors == []


def test_build_epub():
    pages = [
        {"page": 1, "text": "Chapter 1\n\nFirst page content.", "images": 0},
        {"page": 2, "text": "More content.", "images": 0},
    ]
    chapters = [
        {"number": 1, "title": "Chapter 1", "section": "chapter-1", "start_page": 1, "end_page": 2, "pages": [1, 2]},
    ]
    data = build_epub("Test Book", "Test Author", "en", pages, chapters)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        assert "mimetype" in names
        assert "META-INF/container.xml" in names
        assert "OEBPS/content.opf" in names
        assert "OEBPS/nav.xhtml" in names
        assert "OEBPS/chapter-1.xhtml" in names
        assert zf.read("mimetype") == b"application/epub+zip"


def test_parse_epub():
    data = make_test_epub()
    doc = parse_epub(data)
    assert doc["format"] == "epub"
    assert doc["total_pages"] == 2
    assert doc["metadata"]["title"] == "Test Book"
    assert doc["metadata"]["author"] == "Test Author"


def test_classify_epub_item():
    blank = {"page": 1, "item": "blank.xhtml", "text": "", "images": 0, "media_type": "application/xhtml+xml"}
    assert classify_epub_item(blank) == ("skipped", "blank")

    body = {"page": 2, "item": "ch1.xhtml", "text": "This is a normal paragraph of body text.", "images": 0, "media_type": "application/xhtml+xml"}
    assert classify_epub_item(body) == ("kept", "body text")
