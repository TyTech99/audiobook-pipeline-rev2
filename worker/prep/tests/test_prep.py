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


def test_parse_pdf_page_range():
    data = make_test_pdf()
    doc = parse_pdf(data, start_page=2, end_page=3)
    assert doc["total_pages"] == 2
    assert len(doc["pages"]) == 2
    assert doc["pages"][0]["page"] == 2
    assert doc["pages"][1]["page"] == 3


def test_parse_pdf_page_range_single_page():
    data = make_test_pdf()
    doc = parse_pdf(data, start_page=1, end_page=1)
    assert doc["total_pages"] == 1
    assert len(doc["pages"]) == 1
    assert doc["pages"][0]["page"] == 1


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


def test_llm_analyzer_normalizes_response():
    from worker.common.llm_client import LLMClient, LLMSettings
    from worker.prep.llm.analyzer import LLMAnalyzer

    class FakeHttp:
        def post(self, url, headers=None, json=None):
            body = {
                "choices": [
                    {
                        "message": {
                            "content": """{
                              "subject_summary": "A castle mystery.",
                              "author_tone": "Dark and slow.",
                              "narration_style_recommendations": [
                                {"style": "noir-mystery", "reason": "Dark tone", "confidence": "high"},
                                {"style": "literary-fiction", "reason": "Prose", "confidence": "medium"},
                                {"style": "neutral", "reason": "Broad", "confidence": "medium"}
                              ],
                              "suggested_voice_recipes": [
                                {"recipe": "narrator-deep-male-v2", "style": "noir-mystery", "reason": "Fits"}
                              ]
                            }"""
                        }
                    }
                ]
            }
            return type(
                "R",
                (),
                {
                    "status_code": 200,
                    "text": "",
                    "json": lambda self=None: body,
                },
            )()

    analyzer = LLMAnalyzer(
        LLMClient(
            LLMSettings(base_url="http://llm/v1", api_key="k", model="m"),
            http=FakeHttp(),
        )
    )
    result = analyzer.analyze(
        title="The Castle",
        author="Jane",
        language="en",
        pages=[{"text": "Chapter 1. The night was long and cold."}],
    )
    assert result["subject_summary"].startswith("A castle")
    assert len(result["narration_style_recommendations"]) == 3
    assert result["suggested_voice_recipes"][0]["recipe"] == "narrator-deep-male-v2"


def test_llm_required_without_endpoint_raises():
    from worker.common.llm_client import LLMError
    from worker.prep.llm.analyzer import analyzer_from_settings

    analyzer = analyzer_from_settings({"llm": {"base_url": "", "required": True}})
    try:
        analyzer.analyze(title="t", author="a", language="en", pages=[{"text": "hi"}])
        assert False, "expected LLMError"
    except LLMError as exc:
        assert "base_url" in str(exc)


def test_prep_worker_uses_analyzer(tmp_path):
    from worker.prep.worker import PrepWorker

    class StubAnalyzer:
        def analyze(self, **kwargs):
            return {
                "subject_summary": "summary",
                "author_tone": "tone",
                "narration_style_recommendations": [
                    {"style": "neutral", "reason": "r1", "confidence": "high"},
                    {"style": "noir-mystery", "reason": "r2", "confidence": "medium"},
                    {"style": "literary-fiction", "reason": "r3", "confidence": "low"},
                ],
                "suggested_voice_recipes": [],
            }

    class FakeRedis:
        def blmove(self, src, dst, timeout=30, src_end=None, dst_end=None):
            return None

        def hset(self, name, key, value):
            return 1

        def hdel(self, name, key):
            return 1

        def lrem(self, name, count, value):
            return 0

    class FakeHttp:
        def post(self, url, headers=None, json=None, timeout=None):
            return type("R", (), {"status_code": 200, "raise_for_status": lambda self=None: None})()

        def delete(self, url, headers=None):
            return type("R", (), {"status_code": 200, "raise_for_status": lambda self=None: None})()

        def get(self, url, headers=None, timeout=None):
            return type("R", (), {
                "status_code": 200,
                "content": b"",
                "raise_for_status": lambda self=None: None,
                "json": lambda self=None: {"status": "submitted"},
            })()

    pdf = make_test_pdf()
    pdf_path = tmp_path / "book.pdf"
    pdf_path.write_bytes(pdf)
    out = tmp_path / "out"
    out.mkdir()
    worker = PrepWorker(
        {
            "node_id": "n",
            "coordinator_url": "http://c/api/v1",
            "api_key": "k",
            "redis_host": "localhost",
            "redis_port": 6379,
            "output_prefix": str(out) + "/",
        },
        http=FakeHttp(),
        redis_client=FakeRedis(),
        analyzer=StubAnalyzer(),
    )
    package = worker.process_job(
        {
            "job_id": "j1",
            "input_format": "pdf",
            "input_url": f"file://{pdf_path}",
            "metadata": {"title": "T", "author": "A"},
        }
    )
    assert package["llm_analysis"]["subject_summary"] == "summary"
    assert (out / "llm-analysis.json").exists()
    assert (out / "cover.jpg").exists()
    cover_data = (out / "cover.jpg").read_bytes()
    assert cover_data[:2] == b"\xff\xd8"


def test_storage_download_upload_roundtrip(tmp_path):
    from worker.common.storage import download_bytes, upload_package_dir, download_dir, upload_dir

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hello")
    (src / "b.bin").write_bytes(b"\x00\x01\x02")

    dest = tmp_path / "dest"
    download_dir(str(src), dest)
    assert (dest / "a.txt").read_text() == "hello"
    assert (dest / "b.bin").read_bytes() == b"\x00\x01\x02"

    out_prefix = tmp_path / "out" / "pkg"
    upload_package_dir(str(out_prefix), {"x.json": '{"k": 1}', "y.bin": b"raw"})
    assert (out_prefix / "x.json").read_text() == '{"k": 1}'
    assert (out_prefix / "y.bin").read_bytes() == b"raw"

    data = download_bytes(f"file://{src}/a.txt")
    assert data == b"hello"

    up_out = tmp_path / "up_out"
    result = upload_dir(src, str(up_out))
    assert result.endswith("/")
    assert (up_out / "a.txt").read_text() == "hello"


def test_storage_s3_raises():
    from worker.common.storage import download_bytes

    with pytest.raises(ValueError, match="s3://"):
        download_bytes("s3://bucket/key")


def test_cover_placeholder_produces_jpeg():
    from worker.prep.cover import generate_placeholder

    data = generate_placeholder("Test Book Title", "Test Author")
    assert data[:2] == b"\xff\xd8"
    assert data[-2:] == b"\xff\xd9"


def test_isbn_regex_finds_isbn():
    from worker.prep.metadata_enrich import find_isbn_in_text

    text = "Published by Test Press. ISBN 978-0-306-40615-7. Copyright 2026."
    assert find_isbn_in_text(text) == "9780306406157"

    text2 = "ISBN-10: 0-306-40615-2"
    assert find_isbn_in_text(text2) == "0306406152"

    text3 = "No isbn here at all."
    assert find_isbn_in_text(text3) is None


def test_scan_pages_for_isbn():
    from worker.prep.metadata_enrich import scan_pages_for_isbn

    pages = [
        {"text": "Chapter 1"},
        {"text": "ISBN 978-0-306-40615-7"},
    ]
    assert scan_pages_for_isbn(pages) == "9780306406157"
