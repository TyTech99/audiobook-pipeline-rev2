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


def _range_ledger(pages):
    return {
        "source_format": "pdf",
        "source_file": "test.pdf",
        "total_pages": len(pages),
        "pages": [
            {"page": p, "action": "kept", "reason": "body text", "section": "chapter-1"}
            for p in pages
        ],
        "structure": [],
        "summary": {"total": len(pages), "kept": len(pages), "skipped": 0, "skipped_breakdown": {}},
    }


def test_validate_page_ledger_accepts_mid_book_range():
    # #55: a contiguous range that does not start at 1 is valid —
    # mid-book sampling and skip-past-junk workflows.
    assert validate_page_ledger(_range_ledger([3, 4]), 2) == []


def test_validate_page_ledger_catches_gap_inside_range():
    errors = validate_page_ledger(_range_ledger([3, 5]), 2)
    assert any("gaps" in e for e in errors)


def test_validate_page_ledger_catches_duplicates():
    errors = validate_page_ledger(_range_ledger([3, 3]), 2)
    assert any("gaps" in e for e in errors)  # duplicates break the run too


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
        assert "OEBPS/toc.ncx" in names
        assert "OEBPS/chapter-1.xhtml" in names
        assert zf.read("mimetype") == b"application/epub+zip"
        # NCX: EPUB 2 toc for Kindle compat, one navPoint per chapter.
        ncx = zf.read("OEBPS/toc.ncx").decode()
        assert '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"' in ncx
        assert ncx.count("<navPoint ") == 1
        assert "chapter-1.xhtml" in ncx
        # OPF: ncx manifest item + spine toc ref + dcterms:modified.
        opf = zf.read("OEBPS/content.opf").decode()
        assert 'id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"' in opf
        assert '<spine toc="ncx">' in opf
        assert "dcterms:modified" in opf
        # Chapter content must declare the epub: prefix it uses.
        chapter = zf.read("OEBPS/chapter-1.xhtml").decode()
        assert 'xmlns:epub="http://www.idpf.org/2007/ops"' in chapter


def test_build_epub_embeds_cover():
    pages = [{"page": 1, "text": "Body text.", "images": 0}]
    chapters = [
        {"number": 1, "title": "Chapter 1", "section": "chapter-1", "start_page": 1, "end_page": 1, "pages": [1]},
    ]
    cover = b"\xff\xd8\xff\xe0FAKEJPEGDATA"
    data = build_epub("Test Book", "Test Author", "en", pages, chapters, cover=cover)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        assert "OEBPS/cover.jpg" in names
        assert "OEBPS/cover.xhtml" in names
        assert zf.read("OEBPS/cover.jpg") == cover
        opf = zf.read("OEBPS/content.opf").decode()
        assert 'id="cover-image" href="cover.jpg" media-type="image/jpeg" properties="cover-image"' in opf
        assert '<meta name="cover" content="cover-image"/>' in opf
        # Cover is the first spine entry.
        spine_start = opf.index("<spine")
        assert opf.index('idref="cover"', spine_start) < opf.index('idref="ch-1"', spine_start)


def test_build_epub_without_cover_has_no_cover_items():
    data = build_epub(
        "Test Book", "Test Author", "en",
        [{"page": 1, "text": "Body text.", "images": 0}],
        [{"number": 1, "title": "Chapter 1", "section": "chapter-1", "start_page": 1, "end_page": 1, "pages": [1]}],
    )
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        assert "OEBPS/cover.jpg" not in names
        opf = zf.read("OEBPS/content.opf").decode()
        assert "cover-image" not in opf


def test_analyze_chapters_summarizes_and_skips_stubs():
    from worker.prep.llm.analyzer import LLMAnalyzer
    from worker.common.llm_client import LLMClient, LLMSettings

    calls = []

    class FakeClient(LLMClient):
        def __init__(self):
            super().__init__(LLMSettings(base_url="http://x/v1", api_key="k", model="m"))

        def chat_json(self, *, system, user, temperature=None, max_tokens=None):
            calls.append(user)
            assert system.startswith("You analyze one chapter")
            return {"summary": "A summary of the chapter.", "tone_note": "measured, elegiac"}

    pages = [{"page": 1, "text": "word " * 200}]  # >500 chars: worth summarizing
    stub_pages = [{"page": 2, "text": "Only a title."}]  # frontmatter stub
    chapters = [
        {"section": "chapter-1", "title": "The Pound of Flesh", "start_page": 1, "pages": [1]},
        {"section": "frontmatter-dedication", "title": "Dedication", "start_page": 2, "pages": [2]},
    ]
    analyzer = LLMAnalyzer(FakeClient(), required=False)
    results = analyzer.analyze_chapters(
        title="Unfinished Victory", author="Arthur Bryant", language="en",
        pages=pages + stub_pages, chapters=chapters,
    )
    # The dedication stub was skipped; the real chapter was summarized.
    assert len(calls) == 1
    assert len(results) == 1
    assert results[0]["section"] == "chapter-1"
    assert results[0]["title"] == "The Pound of Flesh"
    assert results[0]["summary"] == "A summary of the chapter."
    assert results[0]["tone_note"] == "measured, elegiac"
    assert "The Pound of Flesh" in calls[0]


def test_analyze_chapters_never_raises_on_client_error():
    from worker.prep.llm.analyzer import LLMAnalyzer
    from worker.common.llm_client import LLMClient, LLMSettings

    class BrokenClient(LLMClient):
        def __init__(self):
            super().__init__(LLMSettings(base_url="http://x/v1", api_key="k", model="m"))

        def chat_json(self, *, system, user, temperature=None, max_tokens=None):
            raise RuntimeError("LLM down")

    pages = [{"page": 1, "text": "word " * 200}]
    chapters = [{"section": "chapter-1", "title": "Ch 1", "start_page": 1, "pages": [1]}]
    analyzer = LLMAnalyzer(BrokenClient(), required=False)
    assert analyzer.analyze_chapters(
        title="T", author="A", language="en", pages=pages, chapters=chapters,
    ) == []


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


def test_llm_client_chat_text_plain_completion():
    # Issue #48: the OCR cleanup path needs a plain-text completion.
    # chat_json forces response_format json_object; chat_text must not,
    # and must return the raw content string.
    from worker.common.llm_client import LLMClient, LLMSettings

    captured = {}

    class FakeHttp:
        def post(self, url, headers=None, json=None):
            captured["body"] = json
            return type("R", (), {
                "status_code": 200,
                "json": lambda self=None: {"choices": [{"message": {"content": "cleaned text"}}]},
            })()

    client = LLMClient(LLMSettings(base_url="http://llm/v1", api_key="k", model="m"), http=FakeHttp())
    out = client.chat_text(system="sys", user="usr", temperature=0.1, max_tokens=99)
    assert out == "cleaned text"
    assert captured["body"]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]
    assert captured["body"]["max_tokens"] == 99
    assert "response_format" not in captured["body"]  # plain text, not JSON mode


def test_cleanup_page_with_llm_preserves_indent_and_uses_chat_text():
    # Issue #48: the cleanup call must go through chat_text (the old code
    # awaited a nonexistent .chat and failed silently on every page), and
    # the prompt must demand preservation of leading spaces.
    import asyncio
    from worker.prep.ocr_cleanup import OcrQualitySettings, cleanup_page_with_llm

    captured = {}

    class FakeClient:
        def chat_text(self, *, system, user, temperature, max_tokens):
            captured["user"] = user
            return "   But a terrible shock was administered\nrest of the page text here."

    raw = "   But a terrible shock was administered\nrest of the page text here."
    settings = OcrQualitySettings()
    quality = {"reasons": ["mid_line_lower_restart (1 > 0)"], "confidence": 94.9, "dict_share": 0.99}
    out = asyncio.run(cleanup_page_with_llm(FakeClient(), 22, raw, quality, settings))
    assert out.startswith("   But a terrible shock")  # leading spaces survive
    assert "leading spaces" in captured["user"]
    assert "preserve each line" in captured["user"].lower()


def test_cleanup_prompt_carries_targeted_repair_instructions():
    # Issue #50: gate reasons are translated into concrete repair
    # instructions. A generic 'fix obvious OCR errors' leaves the damage
    # untouched even at 27B; naming the damage class engages the model
    # (verified live on the real p.22 damage).
    import asyncio
    from worker.prep.ocr_cleanup import OcrQualitySettings, cleanup_page_with_llm

    captured = {}

    class FakeClient:
        def chat_text(self, *, system, user, temperature, max_tokens):
            captured["user"] = user
            captured["temperature"] = temperature
            return "text"

    settings = OcrQualitySettings()
    quality = {"reasons": ["mid_line_lower_restart (1 > 0)", "digit_letter_token (2 > 0)"],
               "confidence": 94.9, "dict_share": 0.989}
    asyncio.run(cleanup_page_with_llm(FakeClient(), 22, "text", quality, settings))

    user = captured["user"]
    # The damage classes are described concretely...
    assert "clipped by the" in user and "reconstruct the missing opening word" in user
    assert "'no' read as '10'" in user
    # ...not left as bare gate reason strings
    assert "mid_line_lower_restart (1 > 0)" not in user
    assert "digit_letter_token (2 > 0)" not in user
    # Study-optimal default temperature.
    assert settings.llm_cleanup_temperature == 0.26
    assert captured["temperature"] == 0.26

    # Unknown reasons produce no bogus instructions, and the prompt still builds.
    quality2 = {"reasons": ["too_few_chars (5 < 10)"], "confidence": 0.0, "dict_share": 0.0}
    asyncio.run(cleanup_page_with_llm(FakeClient(), 7, "text", quality2, settings))
    assert "specific damage" not in captured["user"]


def test_verify_cleanup_keeps_only_defect_attributed_repairs():
    # Issue #65: default-revert. A change survives only when it repairs a
    # site with a deterministic damage signature.
    from worker.prep.ocr_cleanup import OcrQualitySettings, verify_cleanup

    settings = OcrQualitySettings()

    # digit-letter misread: '1x' (mixed digit+letter junk) -> 'no' — kept.
    # A bare all-digit token ('10') is deliberately NOT attributable: text
    # alone cannot tell it from a real numeral ('1940') — that class waits
    # for the image-verification pass (#66).
    text, record = verify_cleanup(22, "He said 1x other things.", "He said no other things.", settings)
    assert text == "He said no other things."
    assert [c["class"] for c in record["kept"]] == ["digit_letter"]
    assert record["reverted"] == []

    # clipped-line reconstruction: 'ry did not' -> 'Germany did not' — kept.
    text, record = verify_cleanup(
        22, "a victory. ry did not know", "a victory. Germany did not know", settings
    )
    assert text == "a victory. Germany did not know"
    assert [c["class"] for c in record["kept"]] == ["clipped_line"]

    # period-spelling 'fix': no damage signature — reverted whole.
    text, record = verify_cleanup(31, "He slaid down his pen.", "He laid down his pen.", settings)
    assert text == "He slaid down his pen."
    assert record["kept"] == []
    assert record["reverted"] and record["reverted"][0]["class"] == "replace"

    # invented word: sameness -> sameliness — reverted.
    text, record = verify_cleanup(28, "the sameness of it all", "the sameliness of it all", settings)
    assert text == "the sameness of it all"
    assert record["kept"] == []

    # inserted word jitter ('a her close on a million') — reverted.
    text, record = verify_cleanup(30, "her close on a million", "a her close on a million", settings)
    assert text == "her close on a million"
    assert record["kept"] == []

    # Punctuated junk keeps its attribution ('IQI4.' is digit-letter damage
    # with a sentence period glued on) — the gate's findall sees it; the
    # word-level test must strip punctuation the same way.
    text, record = verify_cleanup(41, "the year IQI4. was one", "the year 1914. was one", settings)
    assert text == "the year 1914. was one"
    assert [c["class"] for c in record["kept"]] == ["digit_letter"]

    # Comma-glued junk likewise: '1g19,' -> '1919,'.
    text, record = verify_cleanup(41, "in 1g19, the war ended", "in 1919, the war ended", settings)
    assert text == "in 1919, the war ended"
    assert [c["class"] for c in record["kept"]] == ["digit_letter"]

    # Pure digits stay unattributable even punctuated — numeral ambiguity (#66).
    text, record = verify_cleanup(41, "he said 10. other", "he said no. other", settings)
    assert text == "he said 10. other"
    assert record["kept"] == []


def test_declare_page_words_flags_non_dictionary_words(tmp_path, monkeypatch):
    # Issue #68: page-gate-blind misreads ('almos') and the author's rare
    # words ('slaid') are DECLARED — counted in the fidelity report, never
    # silently shipped, never repaired here (#66 adjudicates).
    from worker.prep.ocr_cleanup import declare_page_words

    dictdir = tmp_path / "dicts"
    dictdir.mkdir()
    (dictdir / "american-english-large").write_text(
        "there\nare\nsome\nwho\nfaced\nby\nsecond\nworld\nwar\nin\n"
        "generation\nfeel\nthat\nit\nmight\nhave\nbeen\nbetter\nthe\nof\n"
        "author\naccount\nwas\nalmost\ncomplete\n"
    )
    monkeypatch.setenv("AUDIOBOOK_DICT_DIR", str(dictdir))

    text = (
        "There are some who, faced by a second world war in a generation, almos feel "
        "that it might have been better. The author's account of 1940 was almost complete."
    )
    sites = declare_page_words(8, text, "eng")
    words = {s["word"]: s for s in sites}
    assert "almos" in words
    assert words["almos"]["page"] == 8
    assert words["almos"]["count"] == 1
    assert "almos" in words["almos"]["excerpt"]
    # Dictionary words, the apostrophe word ('author's' via its stem), and
    # pure-number tokens ('1940' — dicts.py's WORD_RE requires letters) are
    # never declared.
    assert "author's" not in words
    assert "1940" not in words
    assert "almost" not in words
    # Most-frequent first: a word appearing twice sorts ahead.
    (dictdir / "american-english-large").write_text(
        "the\nof\nwar\npeace\nworld\nsecond\ngeneration\nsome\nwho\nfaced\n"
        "almos\nfeel\nthat\nit\nmight\nhave\nbeen\nbetter\nthere\nare\n"
        "author\naccount\nwas\nalmost\ncomplete\n"
    )
    text2 = "Slaid aside, slaid again: the slaid sword of 1940." \
            " There are some who, faced by a second world war in a generation, almos feel peace."
    sites2 = declare_page_words(8, text2, "eng")
    assert sites2[0]["word"] == "slaid" and sites2[0]["count"] == 3
    assert any(s["word"] == "almos" for s in sites2)


def test_declare_page_words_degrades_gracefully_without_word_list(tmp_path, monkeypatch):
    from worker.prep.ocr_cleanup import declare_page_words

    empty = tmp_path / "no-dicts"
    empty.mkdir()
    monkeypatch.setenv("AUDIOBOOK_DICT_DIR", str(empty))
    assert declare_page_words(8, "any text at all", "eng") == []


def test_verify_cleanup_reverts_structural_damage():
    # Issue #65: the line sequence is the paragraph witness.
    from worker.prep.ocr_cleanup import OcrQualitySettings, verify_cleanup

    settings = OcrQualitySettings()

    # com-modities rupture: a line inserted at the hyphen — whole page reverts.
    orig = "trade in com-\nmodities and goods"
    ruptured = "trade in com-\nmodities and goods\nextra line appeared"
    text, record = verify_cleanup(35, orig, ruptured, settings)
    assert text == orig
    assert record["structural_revert"] is True

    # indent-only edit: paragraph structure changed — whole page reverts.
    orig = "   first line\nsecond line"
    indented = "       first line\nsecond line"
    text, record = verify_cleanup(35, orig, indented, settings)
    assert text == orig
    assert record["structural_revert"] is True

    # hyphen merge (2 lines -> 1): owned downstream by join_hyphen — reverted.
    text, record = verify_cleanup(
        35, "trade in com-\nmodities and goods", "trade in commodities and goods", settings
    )
    assert text == "trade in com-\nmodities and goods"
    assert record["kept"] == []

    # whole-line deletion (header removal): no damage signature — line kept.
    orig = "UNFINISHED VICTORY\nthe body text of the page"
    trimmed = "the body text of the page"
    text, record = verify_cleanup(35, orig, trimmed, settings)
    assert text == orig
    assert any(c["class"] == "line_deleted" for c in record["reverted"])


def test_verify_cleanup_flag_off_restores_verbatim_acceptance():
    from worker.prep.ocr_cleanup import OcrQualitySettings, verify_cleanup

    settings = OcrQualitySettings(llm_cleanup_verify=False)
    cleaned = "He laid down his pen."
    text, record = verify_cleanup(31, "He slaid down his pen.", cleaned, settings)
    assert text == cleaned
    assert record.get("verify") is False


def test_verify_cleanup_mixed_changes_keep_only_the_attributed_one():
    # A span mixing a genuine junk-token repair with an unattributed verb
    # substitution reverts as a unit — no partial trust. The refused unit is
    # recorded; the image-verification pass (#66) can recover such sites.
    from worker.prep.ocr_cleanup import OcrQualitySettings, verify_cleanup

    settings = OcrQualitySettings()
    orig = "He said 1x other things."
    cleaned = "He stated no other things."
    text, record = verify_cleanup(22, orig, cleaned, settings)
    assert text == "He said 1x other things."
    assert record["kept"] == []
    assert record["reverted"] and record["reverted"][0]["class"] == "replace"

    # A pure junk span repairs cleanly even with neighbors untouched.
    text, record = verify_cleanup(22, "He said 1x and 2y tokens.", "He said no and 2y tokens.", settings)
    assert text == "He said no and 2y tokens."
    assert [c["class"] for c in record["kept"]] == ["digit_letter"]


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

        def analyze_chapters(self, **kwargs):
            return []

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


def test_pdf_and_epub_classifiers_agree():
    # Issue #42: classify_page and classify_epub_item are wrappers over
    # one classifier. Every input must classify identically on both
    # paths — including the cases where the old twins had drifted
    # (acknowledgments and backmatter rules existed only on the PDF side).
    from worker.prep.epub_parser import classify_epub_item
    from worker.prep.pdf_parser import classify_page

    cases = [
        # (text, images)
        ("", 3),                                   # illustration only
        ("", 0),                                   # blank
        ("Copyright 2026 by A. Author. All rights reserved.", 0),
        ("CONTENTS\n\nCHAPTER 1 ......... 9\nCHAPTER 2 ......... 20", 0),  # case-insensitive heading
        ("DEDICATION\n\nFor my wife, who understood", 0),
        ("Body text. For events outstripped me. And more words here to fill it out.", 0),  # mid-page 'For ...'
        ("ACKNOWLEDGMENTS\n\nThanks to everyone who helped with this book over the years.", 0),
        ("INDEX\n\nSmith, John ......... 1\nAdventure, The .... 5", 0),
        ("Chapter 1\n\nThis is the first chapter with plenty of body text to read.", 0),
        ("This is plain body text of an ordinary chapter, long enough to classify.", 0),
    ]
    for text, images in cases:
        page = {"page": 1, "text": text, "images": images}
        item = {"page": 1, "item": "x.xhtml", "text": text, "images": images, "media_type": "application/xhtml+xml"}
        assert classify_page(page) == classify_epub_item(item), (
            f"classifiers disagree on {text[:40]!r}: {classify_page(page)} vs {classify_epub_item(item)}"
        )


def test_classification_keyword_tables_defined_once():
    # Issue #42 guard: the keyword tables must come from page_types, not
    # be re-declared in the consuming files.
    import worker.prep.epub_parser as epub_parser
    import worker.prep.frontmatter as fm_mod
    import worker.prep.page_types as page_types
    import worker.prep.pdf_parser as pdf_parser
    import worker.prep.structure as structure_mod

    # No classifier body left in the wrappers (no 'body text' returns inline)
    src_pdf = open(pdf_parser.__file__).read()
    src_epub = open(epub_parser.__file__).read()
    assert 'return "kept", "body text"' not in src_pdf.split("def classify_page")[1].split("def ")[0]
    assert 'return "kept", "body text"' not in src_epub.split("def classify_epub_item")[1].split("def ")[0]
    # Backmatter tables are the page_types objects, not copies
    assert structure_mod.BACK_MATTER_RE is page_types.BACKMATTER_EXCLUSION_RE
    assert fm_mod.BACKMATTER_RE is page_types.BACKMATTER_LABEL_RES


def test_build_page_ledger_uses_passed_fm_sections():
    # fm_sections passed in must be used verbatim, not recomputed.
    from worker.prep.page_ledger import build_page_ledger

    pages = [
        # Text that would NOT classify as frontmatter on its own
        {"page": 1, "text": "Just some plain body text here, long enough to count as text.", "action": "kept", "reason": "body text"},
        {"page": 2, "text": "More plain body text here, also long enough to count.", "action": "kept", "reason": "body text"},
    ]
    chapters = [
        {"number": 1, "title": "Chapter 1", "section": "chapter-1", "start_page": 1, "end_page": 2, "pages": [1, 2]},
    ]
    fm_sections = {1: "frontmatter-title"}  # externally supplied
    ledger = build_page_ledger("pdf", "test.pdf", pages, chapters, fm_sections=fm_sections)
    assert ledger["pages"][0]["section"] == "frontmatter-title"
    assert ledger["pages"][0]["reason"] == "title page"
    assert ledger["pages"][1]["section"] == "chapter-1"


def test_detect_chapters_uses_passed_fm_sections():
    # A page marked as TOC via passed-in fm_sections must be excluded
    # from the heading scan even when its text lacks TOC heuristics.
    from worker.prep.pdf_parser import detect_chapters

    pages = [
        {"page": 1, "text": "CHAPTER 1\n\nTHE TREATY\n\nBody text of the first chapter follows here.", "images": 0},
        {"page": 2, "text": "CHAPTER 1\n\nTHE REAL ONE\n\nBody text that goes on and on here for a while.", "images": 0},
    ]
    fm_sections = {1: "frontmatter-toc"}
    chapters = detect_chapters(pages, fm_sections=fm_sections)
    assert len(chapters) == 1
    assert chapters[0]["start_page"] == 2


def test_chapter_title_stops_at_epigraph_quote():
    # Issue #44: Unfinished Victory ch.2 — the title collection must stop
    # at the epigraph line that opens with a quotation mark. Chapter 1
    # previously escaped only via the >100-char fallback; ch.2's short
    # epigraph was swallowed into its title.
    from worker.prep.structure import find_headings

    units = [
        {"pdf_page": 33, "half": None, "text": "CHAPTER I\nFAMINE OVER EUROPE\n\u2018\u201cT saw in vision the worm in the wheat\nAnd in the shops nothing for people to eat ;\nRALPH HODGSON\nIN the middle of the war famine came to central Europe.", "geom": None, "normalized": {}},
        {"pdf_page": 60, "half": None, "text": "CHAPTER 2\nTHE POUND OF FLESH\n\u201cWhen we\u2019ve wound up the watch on the Rhine,\nO Keir Hardie no doubt will repine !\n\u2018Hurrah !\u2019 we\u2019ll cry,", "geom": None, "normalized": {}},
    ]
    heads = find_headings(units)
    assert len(heads) == 2
    assert heads[0]["title"] == "Famine Over Europe"
    assert heads[1]["title"] == "The Pound of Flesh"


def test_ocr_artifact_and_indent_helpers():
    # Issue #46: pipe glyphs are page scratches, not prose; paragraph
    # indentation survives OCR as leading spaces.
    from worker.prep.ocr import _drop_artifact_words, _indent_spaces, _page_margin_and_char_px

    words = [
        {"text": "complete", "left": 100, "width": 200},
        {"text": "|", "left": 400, "width": 8},
        {"text": "confidence", "left": 500, "width": 240},
    ]
    kept = _drop_artifact_words(words)
    assert [w["text"] for w in kept] == ["complete", "confidence"]

    # p.22 numbers: 'But a terrible shock' first word at left=470,
    # page margin ~357, char width ~23px -> 4 leading spaces.
    assert _indent_spaces(470, 357, 23.0) == 4
    # Letter-bearing jitter (T vs I) rounds to nothing.
    assert _indent_spaces(373, 357, 23.0) == 0
    # Centered display lines are capped.
    assert _indent_spaces(2000, 357, 23.0) == 12
    # Left of the margin and missing margin are safe.
    assert _indent_spaces(300, 357, 23.0) == 0
    assert _indent_spaces(470, None, 23.0) == 0

    lines = [
        {"words": [{"left": 354, "text": "a", "width": 46}]},
        {"words": [{"left": 355, "text": "bb", "width": 46}, {"left": 600, "text": "ccc", "width": 69}]},
        {"words": [{"left": 470, "text": "dd", "width": 46}]},
        {"words": [{"left": 357, "text": "eee", "width": 69}]},
    ]
    margin, char_px = _page_margin_and_char_px(lines)
    assert margin == 357          # median of first-word lefts [354,355,357,470]
    assert char_px == 23.0        # median of word width / char count

    # Too few lines: no margin estimate, no indentation applied.
    assert _page_margin_and_char_px(lines[:2]) == (None, 0.0)


def test_ocr_quality_gate_structural_triggers(tmp_path, monkeypatch):
    # Issue #48: the gate must flag pages carrying junk that the old stub
    # dictionary check (always 1.0) and the mean confidence could not see.
    import os

    from worker.prep.ocr_cleanup import OcrQualitySettings, judge_ocr_quality, _dictionary_share

    # Fixture dictionary so the tests do not depend on host word lists.
    dict_dir = tmp_path / "dict"
    dict_dir.mkdir()
    (dict_dir / "american-english-large").write_text(
        "book\nwar\nbritish\npeople\nprepared\nfight\nthe\nof\nand\nwas\npublished\nin\nby\n"
    )
    monkeypatch.setenv("AUDIOBOOK_DICT_DIR", str(dict_dir))

    settings = OcrQualitySettings(min_confidence=0.0)

    # Clean page: passes.
    clean = {"text": "the book of war and the people was prepared", "confidence": 96.0}
    result = judge_ocr_quality(clean, settings)
    assert result["passed"], result["reasons"]
    assert result["dict_share"] == 1.0

    # Real dictionary measure: junk words lower the share (the old stub
    # returned 1.0 for anything alphabetic).
    assert _dictionary_share("the book of gobbleyx") == 0.75
    junky = {"text": "the book of gobbleyx and frumious bandersnatch", "confidence": 96.0}
    result = judge_ocr_quality(junky, settings)
    assert not result["passed"]
    assert any(r.startswith("low_dict_share") for r in result["reasons"])

    # Scan-edge clipped line: lowercase fragment after terminal punctuation
    # mid-line ('fight. ry did not' — Unfinished Victory p.22).
    clipped = {"text": "the British people were prepared to fight. ry did not", "confidence": 96.0}
    result = judge_ocr_quality(clipped, settings)
    assert not result["passed"]
    assert any(r.startswith("mid_line_lower_restart") for r in result["reasons"]), result["reasons"]

    # Digit+letter junk token ('1x' — misread roman-numeral footer).
    mixed = {"text": "the book of war 1x and the people", "confidence": 96.0}
    result = judge_ocr_quality(mixed, settings)
    assert any(r.startswith("digit_letter_token") for r in result["reasons"]), result["reasons"]

    # Real numbers ('1940') carry no letters and do not trip the trigger.
    dated = {"text": "the book of war published in 1940 by the people", "confidence": 96.0}
    result = judge_ocr_quality(dated, settings)
    assert result["passed"], result["reasons"]

    # Missing word list: the share check disables itself, structural
    # triggers still work.
    monkeypatch.setenv("AUDIOBOOK_DICT_DIR", str(tmp_path / "nowhere"))
    clipped2 = {"text": "the British people were prepared to fight. ry did not", "confidence": 96.0}
    result = judge_ocr_quality(clipped2, settings)
    assert any(r.startswith("mid_line_lower_restart") for r in result["reasons"]), result["reasons"]
