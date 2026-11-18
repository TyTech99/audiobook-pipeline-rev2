# PDF Structure Detection Research for Audiobook Pipeline

## Executive Summary

The current pipeline (`structure.py`) does well on numbered `CHAPTER N` headings with OCR damage tolerance, but lacks:
1. **Frontmatter detection** (title page, copyright, TOC, dedication, preface) — only regex keywords in `classify_page()`
2. **Unnumbered section detection** (Introduction, Part I, unnumbered chapters)
3. **Robust backmatter boundaries** — only catches a few keywords
4. **Page-level ledger with section attribution** — `page_ledger.py` exists but `classify_page()` is naive

The best approach is a **layered cascade**: use the strongest available signal first (PDF bookmarks → TOC parsing → layout/font analysis → text heuristics), falling back gracefully. Several open-source tools validate this approach.

---

## 1. Existing Tools & Libraries

### 1.1 Docling (IBM) — Most Complete Open-Source Solution
- **Layout model**: RT-DETR based (`docling-layout-heron`), detects: Caption, Footnote, Formula, List-item, Page-footer, Page-header, Picture, **Section-header**, Table, Text, **Title**
- **Heading hierarchy inference** (3 signals, precedence order):
  1. **PDF bookmarks** (`doc.get_toc()`) — authoritative when present
  2. **Numbering patterns** — `PART I → CHAPTER 1 → 1. → 1.1 → (a) → (i)`, keyword markers (PART, TITLE, BOOK, DIVISION, CHAPTER, ARTICLE, SECTION, APPENDIX, etc.)
  3. **Visual style** — font size, weight, slant, all-caps detection
- **OCR support**: Tesseract, EasyOCR, RapidOCR, SuryaOCR
- **Relevance**: The heading-hierarchy model is directly applicable. Its numbering scheme precedence and keyword list are production-tested. The layout model runs on GPU (CUDA) — matches your hardware.
- **Limitation**: Designed for general documents, not specifically books. No frontmatter/backmatter classification per se — you'd map `Title` + early pages → frontmatter.

### 1.2 Marker (VikParuchuri) — PDF → Markdown
- **Layout detection**: Custom LayoutLMv3 model (detects tables, diagrams, titles, captions, headers, footers)
- **Section header processor**: Uses KMeans clustering on heading line heights to assign heading levels (1-4)
- **OCR**: Surya OCR (high accuracy, GPU-accelerated)
- **Relevance**: The KMeans height-clustering for heading levels is a clever, simple technique. The overall architecture (layout detection → section header classification → reading order) is a good reference.
- **Limitation**: Heavy dependencies (PyTorch, LayoutLMv3). Overkill if you only need chapter boundaries.

### 1.3 PyMuPDF (fitz) — Foundation Layer
- **`doc.get_toc()`**: Returns `[[level, title, page], ...]` from PDF bookmarks — the single most reliable signal when present
- **`page.get_text("dict")`**: Returns text with font size, name, flags (bold/italic) per span — enables font-based heading detection
- **`page.get_text("words")`**: Word-level with bounding boxes
- **`page.get_drawings()`, `page.get_images()`**: For illustration detection
- **Key insight**: For scanned PDFs (your case), the text layer is absent, so font info comes from OCR, not the PDF. PyMuPDF's value is in: (a) reading bookmarks if the scanned PDF has them, (b) rendering pages for OCR, (c) image detection.

### 1.4 Tesseract OCR — Layout Analysis
- **PSM modes relevant to books**:
  - **PSM 3** (default): Fully automatic page segmentation — best for normal book pages
  - **PSM 4**: Assume uniform column of text — good for single-column book pages
  - **PSM 6**: Uniform block of text — when you know it's one block
  - **PSM 11**: Sparse text — for title pages with little text
- **`image_to_data()`**: Returns block_num, par_num, line_num, word_num per word — gives you layout structure (blocks → paragraphs → lines → words)
- **`image_to_boxes()`**: Character-level bounding boxes
- **Confidence scores**: Per-word confidence (0-100) — useful for flagging low-quality OCR
- **Key insight**: Tesseract's block detection can help identify: (a) centered text (title pages, headings), (b) small text blocks (footnotes, copyright), (c) large text blocks (body). But it's not designed for semantic classification.

### 1.5 Audiobook-Specific Pipelines

#### `danielcorsano/reader` (audiobook-reader)
- **5-signal auto-detection of non-content**: title, EPUB structure, patterns, density, relative length
- **Tiered chapter detection**: marked chapters → heading detection → formatting-based fallback
- **Text stripping**: Interactive, with front/back boundary tuning
- **Key patterns**: Skips TOC, Bibliography, Index, "About the Author", "Acknowledgments"

#### `pwdel/pdftoaudio`
- **Chapter plan**: Creates editable `chapters.json` with chapter types and inclusion flags
- **Front matter handling**: `--exclude-front-matter` flag; testimonials, copyright, contents, prefaces, introductions become named chapters by default (narratable but separable)
- **Key insight**: Treating frontmatter as *named chapters that can be included or excluded* is better than hard-skipping — some prefaces/introductions should be narrated.

#### `StevenJPx2/audiobook-studio`
- **Two-pass approach**: (1) Deterministic cleanup (running heads/footers by frequency, footnotes, hyphenation), (2) LLM polish pass (deletion-only, verified by length tolerance)
- **Chapter detection**: PDF outline + text scan, TOC-deduped, reviewed by local LLM (Ollama)
- **Key insight**: The LLM polish pass for frontmatter boilerplate is a good fallback for edge cases.

#### `exemartinez/bookcast-chapter-forge`
- **Adaptive cascade**: `regex → layout → llm`
- **Signal types**: regex chapter headings, TOC-driven (printed page numbers, clickable destinations, bookmarks), layout-aware (typography), semantic (unstructured title blocks), heuristic (deterministic signal weights)
- **Key insight**: The cascade approach with explicit fallbacks is the right architecture.

---

## 2. Techniques for Each Detection Problem

### 2.1 Frontmatter Detection

**Signal hierarchy (strongest to weakest):**

1. **PDF bookmarks/TOC** (`doc.get_toc()`): If present, entries before the first "Chapter" or numbered section are frontmatter. Most reliable.

2. **Page position + content heuristics** (for scanned PDFs without bookmarks):
   - **Title page**: First 1-3 pages, centered text, book title + author, minimal text (<200 chars), no page number or Roman numeral
   - **Copyright page**: Contains "copyright", "all rights reserved", "ISBN", "printed in", publication year, Library of Congress data. Usually page 2-4. Short text, small font.
   - **TOC page**: Contains "contents" or "table of contents" heading, followed by lines with dot leaders (`......`) or right-aligned page numbers. Multiple lines with this pattern.
   - **Dedication**: Short (1-3 lines), often centered, starts with "For", "To", or a name. Usually 1 page.
   - **Preface/Foreword/Introduction**: Starts with the keyword, has substantial text (paragraphs), may be numbered with Roman numerals.

3. **Page numbering pattern**: Frontmatter typically uses Roman numerals (i, ii, iii) or no numbers; body starts with Arabic 1. The transition point is a strong signal for body start.

4. **Text density**: Frontmatter pages have lower text density than body pages. Title pages have very low density; copyright pages have medium-low; TOC pages have medium.

**Recommended implementation:**
```python
FRONTMATTER_PATTERNS = {
    "title_page": {
        "position": "first 3 pages",
        "signals": ["centered text", "low char count (<300)", "no page number", "title + author present"],
        "action": "skip"
    },
    "copyright": {
        "position": "pages 1-5",
        "signals": ["copyright", "all rights reserved", "isbn", "printed in", "library of congress"],
        "action": "skip"
    },
    "table_of_contents": {
        "position": "pages 1-10",
        "signals": ["contents" heading", "dot leaders (3+ dots)", "right-aligned numbers", "multiple entries"],
        "action": "skip"  # or "kept" if you want to narrate the TOC
    },
    "dedication": {
        "position": "pages 1-5",
        "signals": ["short (1-5 lines)", "centered", "starts with For/To", "no body text"],
        "action": "skip"  # or "kept" — some narrators read dedications
    },
    "preface": {
        "position": "after TOC, before Chapter 1",
        "signals": ["Preface/Foreword/Introduction heading", "substantial text", "Roman numeral pages"],
        "action": "kept"  # usually narrated
    },
}
```

### 2.2 Chapter/Section Heading Detection

**For scanned/OCR'd books (your case), the cascade should be:**

1. **PDF bookmarks** (if the scanned PDF has them — rare but possible)
2. **TOC parsing**: Extract chapter titles + page numbers from the printed TOC, then find those titles in the OCR'd text. This is powerful because it gives you both the chapter list AND the expected page locations.
3. **Text pattern matching** (your current `structure.py` does this well):
   - `CHAPTER N`, `Chapter N`, `CHAPTER N` (OCR-damaged variants)
   - `PART I`, `BOOK II`, `SECTION 3`
   - `Introduction`, `Prologue`, `Epilogue` (unnumbered)
   - Roman numerals alone on a line (`I.`, `II.`, `III.`) — but only if they appear in sequence
   - Numbered headings (`1.`, `2.`, `3.`) — only in non-fiction
4. **Layout/font signals** (from OCR data or PyMuPDF text layer):
   - **Font size**: Headings are typically 1.3x-2x body text size
   - **Centering**: Chapter titles are often centered (x-position analysis)
   - **All-caps**: Chapter headings are often all-caps
   - **Isolation**: Heading is alone on its line or with only a title below it
   - **Page position**: Chapter headings often appear in the top third of a page
5. **Structural signals**:
   - Chapter headings often start on a new page (or after a large vertical gap)
   - Sequential numbering (1, 2, 3... or I, II, III...)
   - TOC cross-reference (title matches a TOC entry)

**Your current `structure.py` strengths to preserve:**
- OCR damage tolerance (`is_chapter_word()` with difflib)
- Roman numeral + word number parsing
- Lesson heading support
- TOC cross-referencing for title correction
- Single-chapter guard (40-page limit)

**Gaps to fill:**
- Unnumbered sections (Introduction, Part I without "Chapter")
- Multi-level structure (Part → Chapter → Section)
- Frontmatter sections as first-class citizens (not just "before Chapter 1")

### 2.3 Backmatter Detection

**Keywords (expand your current `BACK_MATTER_RE`):**
```python
BACKMATTER_KEYWORDS = [
    "index", "general index", "subject index", "name index",
    "bibliography", "references", "works cited", "further reading",
    "appendix", "appendices", "glossary", "notes", "endnotes",
    "acknowledgments", "acknowledgements", "about the author",
    "colophon", "advertisements", "publishers' announcements",
    "other books by the same author", "also by this author",
    "about the publisher", "credits", "dedication",  # if at end
]
```

**Detection strategy:**
- Backmatter starts at the first page (after the last chapter) that opens with a backmatter keyword
- Everything from that point to the end of the book is backmatter
- Your current `back_matter_unit()` does this — just expand the keyword list
- **Edge case**: Some books have "Notes" or "Appendix" as part of the body (e.g., academic books). Use position: if it appears after the last numbered chapter, it's backmatter.

### 2.4 Page Ledger / Page Parity

Your `page_ledger.py` is well-designed. The key improvements:

1. **Section attribution must come from structure detection, not just chapter ranges**:
   - Currently `_find_section()` only checks chapter start/end pages
   - Pages before Chapter 1 need explicit section labels: `frontmatter-title`, `frontmatter-copyright`, `frontmatter-toc`, `frontmatter-preface`
   - Pages after the last chapter: `backmatter-index`, `backmatter-bibliography`, etc.

2. **Action semantics**:
   - `kept` = will be narrated
   - `skipped` = will not be narrated
   - Consider a third state: `kept-optional` = can be narrated but user may want to skip (e.g., preface, acknowledgments)

3. **Reason taxonomy** (standardize):
   ```
   body-text, chapter-heading, section-heading,
   frontmatter-title, frontmatter-copyright, frontmatter-toc,
   frontmatter-dedication, frontmatter-preface, frontmatter-acknowledgments,
   backmatter-index, backmatter-bibliography, backmatter-glossary,
   backmatter-appendix, backmatter-about-author, backmatter-ads,
   illustration-only, blank, low-ocr-quality
   ```

4. **Validation**: Your `validate_page_ledger()` is good. Add:
   - Check that every page is assigned to exactly one section
   - Check that sections are contiguous (no gaps in page ranges)
   - Check that frontmatter sections appear before body, backmatter after

---

## 3. Recommended Architecture

```
PDF Input
    │
    ▼
[1] PyMuPDF: Open PDF, check for bookmarks (doc.get_toc())
    │
    ├── Bookmarks present? → Use as primary structure signal
    │
    ▼
[2] Page-by-page extraction (PyMuPDF text layer or Tesseract OCR)
    │   - For each page: text, word positions, font info (if text layer),
    │     confidence scores (if OCR), image count
    │
    ▼
[3] Page classification (per-page, lightweight)
    │   - blank / illustration-only / low-quality
    │   - frontmatter candidate (position + content)
    │   - backmatter candidate (position + keywords)
    │
    ▼
[4] TOC parsing (if no bookmarks)
    │   - Find TOC pages (keywords + dot leaders + page numbers)
    │   - Extract chapter titles + expected pages
    │
    ▼
[5] Chapter/section detection (cascade)
    │   a. Match TOC entries to OCR'd text (fuzzy match)
    │   b. Text pattern matching (CHAPTER N, Part I, Introduction, etc.)
    │   c. Layout signals (font size, centering, isolation) — if available
    │   d. Structural signals (new page, sequential numbering)
    │
    ▼
[6] Structure assembly
    │   - Frontmatter sections (title, copyright, TOC, dedication, preface)
    │   - Body chapters/sections (numbered + unnumbered)
    │   - Backmatter sections (index, bibliography, etc.)
    │   - Validate: contiguous, no gaps, sequential numbering
    │
    ▼
[7] Page ledger generation
    │   - Every page → {page, action, reason, section}
    │   - Validate: all pages accounted for, sections contiguous
    │
    ▼
Output: book.json (structure) + page_ledger.json
```

---

## 4. Specific Recommendations for Your Pipeline

### 4.1 Immediate (Low Effort, High Impact)

1. **Use `doc.get_toc()` first** in `pdf_parser.py`. If bookmarks exist, they're the ground truth. Most scanned PDFs won't have them, but it's a free check.

2. **Expand `classify_page()`** with proper frontmatter/backmatter detection:
   - Position-aware: page 1-3 = title page candidate, page 2-5 = copyright candidate
   - TOC detection: look for dot leaders (`\.{3,}`) + right-aligned numbers
   - Expand backmatter keywords

3. **Parse the printed TOC** to get chapter titles + page numbers, then cross-reference with OCR'd text. Your `structure.py` already has `toc_chapters()` — integrate it earlier in the pipeline as a primary signal, not just a title-correction fallback.

4. **Add unnumbered section detection**: `Introduction`, `Prologue`, `Epilogue`, `Part I`, `Part II` as standalone headings.

5. **Improve page ledger section labels**: Distinguish `frontmatter-title` from `frontmatter-copyright` from `frontmatter-toc`, etc.

### 4.2 Medium Effort

6. **Font size analysis from OCR**: Tesseract's `image_to_data()` gives you word positions. You can estimate relative font size from character height. Headings will be taller. Cluster heights (like Marker's KMeans approach) to find heading vs. body text.

7. **Page numbering transition detection**: Find where Roman numerals end and Arabic numerals begin — that's your body start.

8. **Text density analysis**: Compute chars-per-page for each page. Frontmatter pages will have anomalously low density. Use this to confirm frontmatter boundaries.

### 4.3 Higher Effort (If Needed)

9. **Layout model (Docling or Marker)**: If text heuristics aren't enough for your book corpus, add Docling's layout model. It runs on GPU (you have it) and detects section headers, titles, and page furniture. But this adds significant complexity and dependencies.

10. **LLM-assisted structure review**: For books where heuristics are uncertain, use a local LLM to review the detected structure. The `audiobook-studio` project does this with Ollama. Keep it as a fallback, not the primary path.

### 4.4 What NOT to Do

- **Don't replace your existing `structure.py`** — its OCR damage tolerance and TOC cross-referencing are battle-tested. Extend it, don't rewrite it.
- **Don't use a heavy ML model as the primary path** — text heuristics + TOC parsing will handle 90%+ of books. ML is a fallback for edge cases.
- **Don't hard-skip all frontmatter** — prefaces and introductions are often worth narrating. Make it configurable.

---

## 5. Key References

| Tool/Project | URL | Key Takeaway |
|---|---|---|
| Docling | https://github.com/docling-project/docling | Heading hierarchy: bookmarks > numbering > style. RT-DETR layout model. |
| Marker | https://github.com/VikParuchuri/marker | KMeans height clustering for heading levels. LayoutLMv3 layout detection. |
| PyMuPDF | https://pymupdf.readthedocs.io | `doc.get_toc()` for bookmarks. `page.get_text("dict")` for font info. |
| Tesseract PSM | https://tesseract-ocr.github.io | PSM 3 for book pages. `image_to_data()` for layout structure. |
| audiobook-reader | https://github.com/danielcorsano/reader | 5-signal non-content detection. Tiered chapter detection. |
| pdftoaudio | https://github.com/pwdel/pdftoaudio | Frontmatter as named, optionally-included chapters. |
| audiobook-studio | https://github.com/StevenJPx2/audiobook-studio | Two-pass: deterministic cleanup + LLM polish. |
| bookcast-chapter-forge | https://github.com/exemartinez/bookcast-chapter-forge | Adaptive cascade: regex → layout → LLM. |
| pdfstructx | https://pypi.org/project/pdfstructx/ | Font analysis + geometric reasoning for structure. |
| DocLayout-YOLO | https://github.com/opendatalab/DocLayout-YOLO | Real-time layout detection (YOLO-v10 based). |
