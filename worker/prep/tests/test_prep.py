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

        def exists(self, name):
            return 0

        def delete(self, name):
            return 1

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


def test_job_cancelled_exception():
    from worker.prep.worker import JobCancelled

    exc = JobCancelled("test-job-123")
    assert exc.job_id == "test-job-123"
    assert "test-job-123" in str(exc)


def test_check_cancelled_raises_when_flag_set():
    import redis
    from unittest.mock import MagicMock
    from worker.prep.worker import PrepWorker, JobCancelled

    mock_redis = MagicMock()
    mock_redis.exists.return_value = 1
    mock_redis.delete.return_value = 1

    worker = PrepWorker.__new__(PrepWorker)
    worker.redis = mock_redis

    with pytest.raises(JobCancelled):
        worker.check_cancelled("test-job")

    mock_redis.exists.assert_called_once_with("job:cancel:test-job")
    mock_redis.delete.assert_called_once_with("job:cancel:test-job")


def test_check_cancelled_noop_when_flag_not_set():
    import redis
    from unittest.mock import MagicMock
    from worker.prep.worker import PrepWorker, JobCancelled

    mock_redis = MagicMock()
    mock_redis.exists.return_value = 0

    worker = PrepWorker.__new__(PrepWorker)
    worker.redis = mock_redis

    # Should not raise
    worker.check_cancelled("test-job")

    mock_redis.exists.assert_called_once_with("job:cancel:test-job")
    mock_redis.delete.assert_not_called()


# --- Frontmatter detector tests ---

def test_frontmatter_title_page():
    from worker.prep.frontmatter import classify_frontmatter

    pages = [
        {"page": 1, "text": "THE GREAT ADVENTURE\n\nby\n\nJOHN SMITH\n\nLONDON\nPUBLISHER"},
        {"page": 2, "text": "Chapter 1\n\nThis is the first chapter with a lot of body text that makes it clearly not a title page. " * 5},
    ]
    result = classify_frontmatter(pages)
    assert result.get(1) == "frontmatter-title"
    assert 2 not in result


def test_frontmatter_copyright_page():
    from worker.prep.frontmatter import classify_frontmatter

    pages = [
        {"page": 1, "text": "THE GREAT ADVENTURE\n\nby\n\nJOHN SMITH"},
        {"page": 2, "text": "Copyright 2026 by John Smith.\nAll rights reserved.\nISBN 978-0-306-40615-7.\nPrinted in Great Britain."},
    ]
    result = classify_frontmatter(pages)
    assert result.get(1) == "frontmatter-title"
    assert result.get(2) == "frontmatter-copyright"


def test_frontmatter_toc_page():
    from worker.prep.frontmatter import classify_frontmatter

    pages = [
        {"page": 1, "text": "THE GREAT ADVENTURE\n\nby\n\nJOHN SMITH"},
        {"page": 2, "text": "CONTENTS\n\nIntroduction ............ 5\nChapter One ........... 10\nChapter Two ........... 25\nChapter Three ......... 40"},
    ]
    result = classify_frontmatter(pages)
    assert result.get(2) == "frontmatter-toc"


def test_frontmatter_dedication():
    from worker.prep.frontmatter import classify_frontmatter

    pages = [
        {"page": 1, "text": "THE GREAT ADVENTURE\n\nby\n\nJOHN SMITH"},
        {"page": 2, "text": "For my wife, who understood"},
    ]
    result = classify_frontmatter(pages)
    assert result.get(2) == "frontmatter-dedication"


def test_frontmatter_preface():
    # Preface pages are section boundaries now, not a frontmatter page type:
    # structure.py's unnumbered-section detection spans the whole preface,
    # so classify_frontmatter must not label only the heading page.
    from worker.prep.frontmatter import classify_frontmatter

    pages = [
        {"page": 1, "text": "THE GREAT ADVENTURE\n\nby\n\nJOHN SMITH"},
        {"page": 2, "text": "PREFACE\n\nI wrote this book because I wanted to share my story with the world. " * 5},
    ]
    result = classify_frontmatter(pages)
    assert result.get(1) == "frontmatter-title"
    assert 2 not in result


def test_frontmatter_title_page_with_year():
    # The publication year on a title page ("1940") is not a page number.
    from worker.prep.frontmatter import classify_frontmatter

    pages = [
        {"page": 1, "text": "UNFINISHED\nVICTORY\nb\nARTHUR BRYANT\nLONDON\nMACMILLAN & CO. LTD\n1940"},
        {"page": 2, "text": "Chapter 1\n\nBody text. " * 20},
    ]
    result = classify_frontmatter(pages)
    assert result.get(1) == "frontmatter-title"
    assert 2 not in result


def test_frontmatter_epigraph():
    from worker.prep.frontmatter import classify_frontmatter

    pages = [
        {"page": 1, "text": "THE GREAT ADVENTURE\n\nby\n\nJOHN SMITH"},
        {"page": 2, "text": "For my wife, who understood"},
        {"page": 3, "text": "\u201c History is philosophy learned from examples.\u201d\nTHUCYDIDES"},
    ]
    result = classify_frontmatter(pages)
    assert result.get(2) == "frontmatter-dedication"
    assert result.get(3) == "frontmatter-epigraph"


def test_frontmatter_not_detected_in_body():
    from worker.prep.frontmatter import classify_frontmatter

    pages = [
        {"page": 1, "text": "THE GREAT ADVENTURE\n\nby\n\nJOHN SMITH"},
        {"page": 2, "text": "Chapter 1\n\nThis is body text. " * 20},
        {"page": 3, "text": "Chapter 2\n\nMore body text. " * 20},
    ]
    result = classify_frontmatter(pages)
    assert result.get(1) == "frontmatter-title"
    assert 2 not in result
    assert 3 not in result


def test_backmatter_index():
    from worker.prep.frontmatter import classify_frontmatter

    pages = [
        {"page": 1, "text": "Chapter 1\n\nBody text. " * 20},
        {"page": 2, "text": "Chapter 2\n\nMore body text. " * 20},
        {"page": 3, "text": "INDEX\n\nSmith, John ......... 1\nAdventure, The .... 5"},
    ]
    result = classify_frontmatter(pages)
    assert result.get(3) == "backmatter-index"


def test_backmatter_bibliography():
    from worker.prep.frontmatter import classify_frontmatter

    pages = [
        {"page": 1, "text": "Chapter 1\n\nBody text. " * 20},
        {"page": 2, "text": "BIBLIOGRAPHY\n\nSmith, J. (2020). The Great Adventure. Publisher."},
    ]
    result = classify_frontmatter(pages)
    assert result.get(2) == "backmatter-bibliography"


def test_page_ledger_with_frontmatter():
    from worker.prep.page_ledger import build_page_ledger

    pages = [
        {"page": 1, "text": "THE GREAT ADVENTURE\n\nby\n\nJOHN SMITH", "action": "kept", "reason": "body text"},
        {"page": 2, "text": "Copyright 2026. All rights reserved.", "action": "kept", "reason": "body text"},
        {"page": 3, "text": "Chapter 1\n\nBody text.", "action": "kept", "reason": "body text"},
    ]
    chapters = [
        {"number": 1, "title": "Chapter 1", "section": "chapter-1", "start_page": 1, "end_page": 3, "pages": [1, 2, 3]},
    ]
    ledger = build_page_ledger("pdf", "test.pdf", pages, chapters)

    # Page 1 should be title page, not chapter-1
    assert ledger["pages"][0]["section"] == "frontmatter-title"
    assert ledger["pages"][0]["reason"] == "title page"

    # Page 2 should be copyright, not chapter-1
    assert ledger["pages"][1]["section"] == "frontmatter-copyright"
    assert ledger["pages"][1]["reason"] == "copyright"

    # Page 3 should be chapter-1
    assert ledger["pages"][2]["section"] == "chapter-1"

    # Structure should include frontmatter sections
    sections = [s["section"] for s in ledger["structure"]]
    assert "frontmatter-title" in sections
    assert "frontmatter-copyright" in sections
    assert "chapter-1" in sections


def test_unnumbered_section_detection():
    # Real-book shape (Unfinished Victory p.5): the section heading, an
    # ALL-CAPS subtitle, then mixed-case body text. The subtitle is the
    # title; the body line must NOT be swallowed into it.
    from worker.prep.structure import find_headings

    units = [
        {"pdf_page": 1, "half": None, "text": "INTRODUCTION\n\nHISTORIAN\u2019S TESTAMENT\n\nTHIS book, which is based upon a larger work now slaid aside, was written before the war. I wrote it as an historian\u2019s attempt to retell the story.", "geom": None, "normalized": {}},
        {"pdf_page": 2, "half": None, "text": "More introduction text here.", "geom": None, "normalized": {}},
        {"pdf_page": 3, "half": None, "text": "CHAPTER ONE\n\nTHE TREATY OF VERSAILLES\n\nThe peace which ended the Great War.", "geom": None, "normalized": {}},
        {"pdf_page": 4, "half": None, "text": "PART II\n\nTHE WAR YEARS\n\nThe war began in 1939.", "geom": None, "normalized": {}},
        {"pdf_page": 5, "half": None, "text": "More war text.", "geom": None, "normalized": {}},
        {"pdf_page": 6, "half": None, "text": "EPILOGUE\n\nTHE AFTERMATH\n\nThe war ended in 1945.", "geom": None, "normalized": {}},
    ]
    heads = find_headings(units)
    assert len(heads) == 4
    assert heads[0]["label"] == "Introduction"
    assert heads[0]["title"] == "Historian\u2019s Testament"
    assert heads[1]["title"] == "The Treaty of Versailles"
    assert heads[2]["label"] == "Part II"
    assert heads[2]["title"] == "The War Years"
    assert heads[3]["label"] == "Epilogue"
    assert heads[3]["title"] == "The Aftermath"


def test_unnumbered_section_with_chapters():
    from worker.prep.structure import find_headings

    units = [
        {"pdf_page": 1, "half": None, "text": "PROLOGUE\n\nTHE BEGINNING\n\nIt started on a cold winter night.", "geom": None, "normalized": {}},
        {"pdf_page": 2, "half": None, "text": "CHAPTER 1\n\nTHE FIRST DAY\n\nThe sun rose over the hills.", "geom": None, "normalized": {}},
        {"pdf_page": 3, "half": None, "text": "CHAPTER 2\n\nTHE SECOND DAY\n\nThe journey continued.", "geom": None, "normalized": {}},
        {"pdf_page": 4, "half": None, "text": "AFTERWORD\n\nREFLECTIONS\n\nLooking back on the journey.", "geom": None, "normalized": {}},
    ]
    heads = find_headings(units)
    assert len(heads) == 4
    assert heads[0]["label"] == "Prologue"
    assert heads[0]["title"] == "The Beginning"
    assert heads[1]["title"] == "The First Day"
    assert heads[2]["title"] == "The Second Day"
    assert heads[3]["label"] == "Afterword"
    assert heads[3]["title"] == "Reflections"


def test_detect_chapters_excludes_toc_page():
    # The CONTENTS page's 'CHAPTER 1' entry lines are not real headings;
    # detect_chapters must skip frontmatter-toc pages when scanning.
    from worker.prep.pdf_parser import detect_chapters

    pages = [
        {"page": 1, "text": "THE GREAT ADVENTURE\n\nby\n\nJOHN SMITH", "images": 0},
        {"page": 2, "text": "CONTENTS\n\nCHAPTER 1 ......... 9\nCHAPTER 2 ......... 20", "images": 0},
        {"page": 3, "text": "CHAPTER 1\n\nTHE TREATY\n\nBody text of the first chapter follows here.", "images": 0},
        {"page": 4, "text": "More body text of chapter one.", "images": 0},
    ]
    chapters = detect_chapters(pages)
    assert len(chapters) == 1
    assert chapters[0]["section"] == "chapter-1"
    assert chapters[0]["start_page"] == 3
    assert chapters[0]["pages"] == [3, 4]


def test_detect_chapters_unnumbered_section_slug():
    from worker.prep.pdf_parser import detect_chapters

    pages = [
        {"page": 1, "text": "INTRODUCTION\n\nHISTORIAN\u2019S TESTAMENT\n\nTHIS book, which is based upon a larger work now slaid aside, was written before the war.", "images": 0},
        {"page": 2, "text": "More introduction text here.", "images": 0},
    ]
    chapters = detect_chapters(pages)
    assert len(chapters) == 1
    assert chapters[0]["section"] == "section-introduction"
    assert chapters[0]["label"] == "Introduction"
    assert chapters[0]["title"] == "Historian\u2019s Testament"
    assert chapters[0]["pages"] == [1, 2]
