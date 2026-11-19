"""Stage 3, structure: find chapter boundaries from the text layer. Audible style file list is made by the script stage."""
import difflib
import re

from worker.prep import pageparts
from worker.prep.bookio_old import title_case as _title_case
# Backmatter exclusion keywords are defined once in page_types.py next to
# the label table (issue #42); this is the "narration stops here" table.
from worker.prep.page_types import BACKMATTER_EXCLUSION_RE as BACK_MATTER_RE

WORDS = "ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE TEN ELEVEN TWELVE THIRTEEN FOURTEEN FIFTEEN SIXTEEN SEVENTEEN EIGHTEEN NINETEEN TWENTY".split()
ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
HEADING_RE = re.compile(r"^\s*([A-Z]{6,9}|CHAPTER)\b[\s.:]*(.*?)[\s.,:;]*$")
# 'Chapter IV' / 'Chapter 4' / 'Chapter Four' set in mixed case (typeset books with a real text layer). Stricter than the
# upper-case form: the word must be exactly 'Chapter' and the rest of the line exactly one numeral, so no guessing.
MIXED_HEADING_RE = re.compile(r"^\s*Chapter\s+([IVXLC]+|\d{1,3}|[A-Z][a-z]+)\s*[.:]?\s*$")
# Lessons instead of chapters (Ramacharaka/Atkinson course books, YOGeBooks editions): 'The First Lesson.' alone on its line,
# then the lesson title. Also 'Lesson IV' / 'LESSON 4' / 'LESSON NUMBER 4'. The line must be only the heading: a contents line
# ('The First Lesson.......The Coming of the Master.') or a sentence ('Thus endeth our First Lesson.') is never one.
ORDINALS = ("FIRST SECOND THIRD FOURTH FIFTH SIXTH SEVENTH EIGHTH NINTH TENTH ELEVENTH TWELFTH THIRTEENTH FOURTEENTH FIFTEENTH "
            "SIXTEENTH SEVENTEENTH EIGHTEENTH NINETEENTH TWENTIETH").split()
ORDINAL_LESSON_RE = re.compile(r"^\s*(The|THE)\s+([A-Z][a-z]+|[A-Z]+)\s+(Lesson|LESSON)\s*[.:]?\s*$")
NUMBERED_LESSON_RE = re.compile(r"^\s*(Lesson|LESSON)\s+(?:(?:Number|NUMBER|No\.?|NO\.?)\s*)?([IVXLC]+|\d{1,3}|[A-Z][a-z]+|[A-Z]+)\s*[.:]?\s*$")
# A contents line for a lesson: the heading, dot leaders, then the title.
TOC_LESSON_RE = re.compile(r"^\s*(.*?\b(?:Lesson|LESSON)\b(?:\s+\S+)?\.?)\s*(?:[.\u2026]\s*){3,}(\S.*?)\s*$")

# Unnumbered section headings: Introduction, Preface, Part I, Prologue, Epilogue, etc.
# These are standalone lines (not merged with body text) that mark section boundaries.
UNNUMBERED_SECTION_RE = re.compile(
    r"^\s*(Introduction|Introductory Note|Note to the Reader|Preface|Author's Note|Author's Foreword|"
    r"Foreword|Prologue|Epilogue|Afterword|Coda|"
    r"Part\s+[IVXLC]+|Part\s+\d+|Part\s+[A-Z][a-z]*|"
    r"Book\s+[IVXLC]+|Book\s+\d+|Book\s+[A-Z][a-z]*|"
    r"Appendix\s+[A-Z0-9]+|Appendix\s+[A-Z][a-z]*|"
    r"Interlude|Intermezzo|Prelude|Overture|Curtain|Raise|Drop|"
    r"Act\s+[IVXLC]+|Act\s+\d+|Act\s+[A-Z][a-z]*|"
    r"Scene\s+[IVXLC]+|Scene\s+\d+|Scene\s+[A-Z][a-z]*)\s*[.:]?\s*$",
    re.IGNORECASE,
)


def chapter_number(token):
    if token.isdigit():
        return int(token)
    if token in WORDS:
        return WORDS.index(token) + 1
    if set(token) <= set(ROMAN):
        total = 0
        for a, b in zip(token, token[1:] + " "):
            total += -ROMAN[a] if b in ROMAN and ROMAN[b] > ROMAN[a] else ROMAN[a]
        return total
    return None


def is_chapter_word(word):
    """CHAPTER, or what OCR makes of it (CUAPTER, CIEAPTER, OUAPTER): upper-case letters, 6 to 9 of them, 70% like CHAPTER."""
    return word == "CHAPTER" or (re.fullmatch(r"[A-Z]{6,9}", word) is not None and difflib.SequenceMatcher(None, word, "CHAPTER").ratio() >= 0.7
                                 and word not in ("CHAPTERS", "CAPTAIN", "CHARTER", "CHAPLET"))


def lesson_number(line):
    """Number of a lesson heading line ('The First Lesson.', 'LESSON IV', 'Lesson 12'), else None."""
    m = ORDINAL_LESSON_RE.match(line)
    if m:
        word = m.group(2)
        if (m.group(1).isupper() != word.isupper()) or (m.group(1).isupper() != m.group(3).isupper()):
            return None                                   # 'The FIRST lesson': one case for the whole line
        return ORDINALS.index(word.upper()) + 1 if word.upper() in ORDINALS else None
    m = NUMBERED_LESSON_RE.match(line)
    if m:
        tok = m.group(2)
        if tok[1:].islower() and tok.upper() not in WORDS:    # 'Lesson Summary': not a numeral
            return None
        return chapter_number(tok.upper()) or None
    return None


def heading_number(line, expected):
    """(number, guessed) for a 'CHAPTER <number>' line, else None. The number may be a roman numeral, digits or a word.
    OCR often damages the numeral ('I I r', 'X V', 'X f I'): a short unreadable token after an upper-case CHAPTER is
    taken to be the next chapter in sequence (guessed=True, and a warning is written by run()).
    A lesson heading (see lesson_number) counts the same way as a chapter heading."""
    n = lesson_number(line)
    if n:
        return n, False
    m = MIXED_HEADING_RE.match(line)
    if m:
        tok = m.group(1)
        if tok[1:].islower() and tok.upper() not in WORDS:      # 'Chapter Summary', 'Chapter Ill': not a numeral
            return None
        n = chapter_number(tok.upper())
        return (n, False) if n else None
    m = HEADING_RE.match(line)
    if not m:
        return None
    if not is_chapter_word(m.group(1)):
        return None
    rest = m.group(2).strip()
    if re.fullmatch(r"[A-Z]+|\d+", rest):
        n = chapter_number(rest)
        if n is not None:
            return n, False
    collapsed = re.sub(r"\s+", "", rest)
    if 0 < len(rest) <= 8 and re.fullmatch(r"[A-Za-z0-9|!.,]+", collapsed):
        n = chapter_number(collapsed.upper().strip(".,"))
        if n is not None and not (" " in rest and n != expected):     # 'I I' is more likely III than II when III is next
            return n, False
        return expected, True
    return None


def body_start(lines, title, running=()):
    """Index of the first line of a unit that is not blank and not part of its running header (or len(lines))."""
    z = pageparts.header_zone(lines, title, running)
    i = z[1] if z else 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    return i


def back_matter_unit(units, after_unit, title, running=()):
    """First unit after `after_unit` whose first line below the running header is a back-matter heading
    (Bibliography, Index, Publishers' Announcement, ...): (unit index, heading text), or None."""
    for ui in range(after_unit + 1, len(units)):
        lines = units[ui]["text"].split("\n")
        i = body_start(lines, title, running)
        if i < len(lines) and BACK_MATTER_RE.match(lines[i]):
            return ui, lines[i].strip()
    return None


def heading_title(lines):
    """An upper-case title is put in title case; a title set in mixed case is kept as printed."""
    text = re.sub(r"\s+", " ", " ".join(lines)).strip()
    if not text:
        return ""
    return _title_case(text) if pageparts._mostly_upper(text) else text


def unnumbered_section_number(line, expected):
    """(number, label) for an unnumbered section heading line, else None.
    Returns a sequential number and the section label (e.g., 'Introduction', 'Part I')."""
    m = UNNUMBERED_SECTION_RE.match(line)
    if not m:
        return None
    label = re.sub(r"\s+", " ", m.group(1)).strip()
    # Title-case the label for display, but preserve Roman numerals
    if pageparts._mostly_upper(label):
        # Preserve Roman numerals after Part/Book/Act/Scene/Appendix
        mm = re.match(r"^(Part|Book|Act|Scene|Appendix)\s+([IVXLC]+)$", label, re.IGNORECASE)
        if mm:
            label = f"{mm.group(1).title()} {mm.group(2)}"
        else:
            label = _title_case(label)
    return expected, label


def find_headings(units):
    """'CHAPTER ONE' then the title line(s) up to a blank line, within the first 12 lines of a unit.
    Also detects unnumbered sections (Introduction, Part I, Prologue, etc.).
    A heading whose number does not rise (a table-of-contents entry that came before the real chapter) is replaced by the
    later one. With no heading at all the whole body is one chapter ('synthetic')."""
    found = []
    for ui, u in enumerate(units):
        lines = u["text"].split("\n")
        shown = 0
        for li, ln in enumerate(lines):
            if ln.strip():
                shown += 1
            if shown > 12:
                break
            got = heading_number(ln, (found[-1]["number"] + 1) if found else 1)
            if got is None:
                # Check for unnumbered section headings (Introduction, Part I, etc.)
                unum = unnumbered_section_number(ln, (found[-1]["number"] + 1) if found else 1)
                if unum is not None:
                    n, label = unum
                    k = li + 1
                    while k < len(lines) and not lines[k].strip():
                        k += 1
                    # Title lines after the heading: upper-case lines only
                    # (the title of 'INTRODUCTION' is 'HISTORIAN'S TESTAMENT',
                    # set in caps; the first mixed-case line is body text).
                    title = []
                    while k < len(lines) and lines[k].strip() and len(title) < 3:
                        line_text = lines[k].strip()
                        if not pageparts._mostly_upper(line_text):
                            break
                        if len(line_text) > 50:
                            break
                        title.append(line_text)
                        k += 1
                    h = {"unit": ui, "line": li, "title_end": k, "number": n, "title": heading_title(title)}
                    if not h["title"]:
                        h["title"] = label
                    h["label"] = label
                    h["unnumbered"] = True
                    while found and found[-1]["number"] >= n and not found[-1].get("unnumbered"):
                        found.pop()
                        h["replaced_earlier"] = True
                    found.append(h)
                    break
                continue
            n, guessed = got
            k = li + 1
            while k < len(lines) and not lines[k].strip():
                k += 1
            title = []
            geom = u.get("geom") or []
            tall = None                        # height of the first title line, when line positions are known
            while k < len(lines) and lines[k].strip():
                g = geom[k] if k < len(geom) else None
                hgt = (g["y2"] - g["y"]) if g and "y2" in g else None
                if tall and hgt and hgt < 0.8 * tall:
                    break                      # a much smaller line right under the title is a subheading or the text
                tall = tall or hgt
                # Stop at the first line that looks like body text (long, mixed-case sentence)
                line_text = lines[k].strip()
                # Stop at an epigraph/poem line: chapter opening quotes open with a
                # quotation mark, chapter titles never do (Unfinished Victory ch.2,
                # 'THE POUND OF FLESH' followed by '"When we've wound up the watch...').
                if line_text and line_text[0] in "\u201c\u201d\u2018\u2019\"'":
                    break
                if len(line_text) > 40 and not pageparts._mostly_upper(line_text) and "." in line_text:
                    break
                # Stop if the line looks like a full sentence (has punctuation, not a title)
                if len(line_text) > 10 and any(c in line_text for c in ".!?") and not pageparts._mostly_upper(line_text):
                    break
                # Stop if the line is very long (likely body text, not a title)
                if len(line_text) > 50:
                    break
                title.append(line_text)
                k += 1
            if len(" ".join(title)) > 100:        # no blank line after the title: keep the leading upper-case lines only
                keep = 1
                while keep < len(title) and keep < 3 and pageparts._mostly_upper(title[keep]):
                    keep += 1
                k = k - (len(title) - keep)
                title = title[:keep]
            h = {"unit": ui, "line": li, "title_end": k, "number": n, "title": heading_title(title)}
            if not h["title"]:
                h["title"] = f"Chapter {n}"
            if lesson_number(ln):                 # spoken as printed ('The First Lesson'), not as 'Chapter One'
                h["label"] = re.sub(r"\s+", " ", ln.strip()).rstrip(".:")
                h["title"] = re.sub(r"(?<!\.)\.$", "", h["title"])     # 'The Coming of the Master.': the heading's own full stop
            if guessed:
                h["guessed"] = True
            while found and found[-1]["number"] >= n and not found[-1].get("unnumbered"):      # the earlier one was a contents entry or a misread
                found.pop()
                h["replaced_earlier"] = True
            found.append(h)
            break
    if not found:
        sizes = [len(re.sub(r"\s", "", u["text"])) for u in units]
        filled = sorted(n for n in sizes if n >= 20)
        floor = max(40, 0.4 * filled[len(filled) // 2]) if filled else 0      # a page with 40% of the median text or more is body
        for ui, n in enumerate(sizes):
            if filled and n >= floor:
                return [{"unit": ui, "line": -1, "title_end": 0, "number": 1, "title": "The Complete Text", "synthetic": True}]
    return found


def toc_chapters(units, before_unit):
    """Chapter lines of the table of contents, read from the units before the first chapter: [(roman, title, page)]."""
    lines = []
    for u in units[:before_unit]:
        lines += u["text"].split("\n")
    out = []
    for i, ln in enumerate(lines):
        m = re.match(r"^\s*([IVX]+)\.\s+(.+?)\s*\.{3,}\s*(\d+)?\s*$", ln)
        if not m:
            continue
        page = m.group(3)
        if page is None and i + 1 < len(lines) and re.match(r"^\s*\d+\s*$", lines[i + 1]):
            page = lines[i + 1].strip()
        out.append((chapter_number(m.group(1)), _title_case(m.group(2)), int(page) if page else None))
    return out


def toc_lesson_lines(units, before_unit):
    """Contents lines of a lessons book, 'The First Lesson.……… The Coming of the Master.': {number: (title, None)}.
    These contents carry no page numbers."""
    out = {}
    for u in units[:before_unit]:
        for ln in u["text"].split("\n"):
            m = TOC_LESSON_RE.match(ln)
            if not m:
                continue
            n = lesson_number(m.group(1))
            title = re.sub(r"(?<!\.)\.$", "", re.sub(r"\s+", " ", m.group(2)).strip())
            if n and n not in out and len(re.sub(r"[^A-Za-z]", "", title)) >= 4:
                out[n] = (title, None)
    return out


def unknown_words(text):
    """How many words of a title the spelling dictionary does not know (used to choose between two readings of one title)."""
    from pipeline.text import cleanup
    return sum(1 for w in re.findall(r"[A-Za-z]+", text) if len(w) > 2 and not cleanup.known(w))


def toc_chapter_lines(units, before_unit):
    """Contents in the form 'CHAPTER V.' on one line and 'Title ........ 20' on the next: {number: (title, page)}."""
    lines = []
    for u in units[:before_unit]:
        lines += [ln for ln in u["text"].split("\n") if ln.strip()]
    out, last = {}, 0
    for i, ln in enumerate(lines[:-1]):
        got = heading_number(ln, last + 1)
        if got is None or not re.search(r"\.{3,}", lines[i + 1]):
            continue
        title = re.split(r"\s*\.{3,}", lines[i + 1].strip())[0].strip(" .,")
        if len(re.sub(r"[^A-Za-z]", "", title)) >= 4 and got[0] not in out:
            out[got[0]] = (_title_case(re.sub(r"\s+", " ", title)), None)
            last = got[0]
    return out


def book_pages(units, title, running=()):
    """Printed page number of each unit (None if unknown). Printed numbers are read from headers/footers;
    a unit without one takes its neighbor's number plus or minus its distance."""
    printed = []
    for u in units:
        lines = u["text"].split("\n")
        z = pageparts.header_zone(lines, title, running)
        num = z[2] if z else None
        if num is None:
            f = pageparts.footer_number(lines)
            num = f[1] if f else None
        printed.append(num)
    anchors = [i for i, n in enumerate(printed) if n is not None]
    pages = []
    for i in range(len(units)):
        if printed[i] is not None:
            pages.append(printed[i])
            continue
        near = min(anchors, key=lambda a: (abs(a - i), a > i)) if anchors else None
        guess = printed[near] + (i - near) if near is not None else None
        pages.append(guess if guess and guess >= 1 else None)
    return pages, printed


# Guard: one chapter longer than this many pdf pages means the heading form was not recognised (Mystic Christianity, Actions
# run 200: 206 pages read as 'The Complete Text'). Measured on the books done so far: the longest real chapter is 10 pages
# (Science of Breath ch. 15), the longest lesson 22 pages (Mystic Christianity lessons 1 and 2); Clark is 34 pages in all
# with 6 chapters. 40 is about twice the longest chapter seen and leaves a short pamphlet free to be one piece.
SINGLE_CHAPTER_MAX_PAGES = 40


def single_chapter_limit(cfg):
    value = ((cfg or {}).get("structure") or {}).get("single_chapter_max_pages", SINGLE_CHAPTER_MAX_PAGES)
    return int(value)


def check_single_chapter(chapters, limit):
    """Raise SystemExit when the whole book came out as one chapter longer than `limit` pdf pages (0 = check off)."""
    if limit and len(chapters) == 1:
        first, last = chapters[0]["pdf_pages"]
        span = last - first + 1
        if span > limit:
            raise SystemExit(
                "only one chapter found ('%s', pdf pages %d-%d, %d pages), which is more than %d pages. The book's chapter "
                "headings were most likely not recognised (known forms: 'CHAPTER I', 'Chapter IV', 'The First Lesson', "
                "'LESSON 4'), and narrating it as one file would be wrong. Nothing was narrated. Fix the heading rule in "
                "_pipeline/pipeline/text/structure.py, or, if this book really is one long piece, set structure.single_chapter_max_pages "
                "(env AUDIOBOOK_SINGLE_CHAPTER_MAX_PAGES; 0 turns the check off) and run it again."
                % (chapters[0]["title"], first, last, span, limit))


def run(book_dir, book, pdf, cfg=None):
    """Fills book['chapters']. Returns warnings. Fails (SystemExit) on a long book that yields only one chapter."""
    units = pdftext.units(pdf)
    heads = find_headings(units)
    if book.get("type") == "treatise" or (len(heads) == 1 and heads[0].get("synthetic")):
        from pipeline.text import treatise
        t_chapters, t_warnings = treatise.build_chapters(units, book)
        if t_chapters is not None:
            book["type"] = "treatise"
            book["chapters"] = t_chapters
            return t_warnings
    warnings = []
    if not heads:
        raise SystemExit("no chapter headings found and no page with a body of text to treat as a single chapter")
    for h in heads:
        if h.get("synthetic"):
            warnings.append(("no-chapter-headings", "book", "no 'CHAPTER <number>' headings found; the text from pdf page %d on is treated as one chapter" % units[h["unit"]]["pdf_page"]))
        if h.get("guessed"):
            warnings.append(("chapter-number-guessed", "chapter %d" % h["number"], "the number after CHAPTER on pdf page %d was unreadable (OCR); taken as the next in sequence" % units[h["unit"]]["pdf_page"]))
        if h.get("replaced_earlier"):
            warnings.append(("heading-replaced", "chapter %d" % h["number"], "an earlier CHAPTER line with the same or a higher number (a contents entry or misread) was dropped in favour of the heading on pdf page %d" % units[h["unit"]]["pdf_page"]))
    running = pageparts.running_lines(units)
    pages, printed = book_pages(units, book["title"], running)
    toc = {n: (t, p) for n, t, p in toc_chapters(units, heads[0]["unit"])}
    for n, tp in toc_chapter_lines(units, heads[0]["unit"]).items():
        toc.setdefault(n, tp)
    if any(h.get("label") for h in heads):
        for n, tp in toc_lesson_lines(units, heads[0]["unit"]).items():
            toc.setdefault(n, tp)
    old = {c["number"]: c for c in book.get("chapters", [])}
    chapters = []
    for k, h in enumerate(heads):
        if k + 1 < len(heads):
            nh = heads[k + 1]
            last_unit = nh["unit"] - 1
            lines = units[nh["unit"]]["text"].split("\n")
            z = pageparts.header_zone(lines, book["title"], running)
            stray = [l for i, l in enumerate(lines[:nh["line"]]) if l.strip() and not (z and z[0] <= i < z[1])]
            if stray:
                warnings.append(("chapter-starts-mid-page", "chapter %d" % nh["number"],
                                 "text sits above the heading on its page; it is not assigned to the previous chapter"))
        else:
            last_unit = len(units) - 1
            back = back_matter_unit(units, h["unit"], book["title"], running)
            if back:
                last_unit = back[0] - 1
                warnings.append(("back-matter-excluded", "chapter %d" % h["number"],
                                 "the last chapter ends before pdf page %d, which opens with '%s' (back matter, not read)" % (units[back[0]]["pdf_page"], back[1])))
        ch = {
            "number": h["number"],
            "title": h["title"],
            "first_unit": h["unit"],
            "last_unit": last_unit,
            "pdf_pages": [units[h["unit"]]["pdf_page"], units[last_unit]["pdf_page"]],
            "book_pages": [pages[h["unit"]], pages[last_unit]],
            "switches": old.get(h["number"], {}).get("switches", {}),
        }
        if h.get("label"):
            ch["label"] = h["label"]
        if h.get("synthetic"):
            ch["synthetic"] = True             # no chapter headings: spoken under the book title alone (script.py)
        if h["number"] in toc:
            t_title, t_page = toc[h["number"]]
            ch["toc_page"] = t_page
            if t_page is not None and t_page != ch["book_pages"][0]:
                warnings.append(("toc-mismatch", "chapter %d" % h["number"],
                                 "table of contents says page %s, structure found page %s" % (t_page, ch["book_pages"][0])))
            sim = pageparts.similar(t_title, h["title"])
            if 0.5 <= sim < 1 and t_page is None and not h.get("synthetic") and unknown_words(t_title) < unknown_words(h["title"]):
                warnings.append(("title-from-contents", "chapter %d" % h["number"], "heading read '%s' (OCR damage?); the contents page says '%s' with fewer unknown words, which is used" % (h["title"], t_title)))
                ch["title"] = t_title
            elif sim < 0.9:
                warnings.append(("toc-mismatch", "chapter %d" % h["number"],
                                 "table of contents title '%s' differs from heading '%s'" % (t_title, h["title"])))
        chapters.append(ch)
    check_single_chapter(chapters, single_chapter_limit(cfg))
    if not any(w[0] == "back-matter-excluded" for w in warnings):
        warnings.append(("last-chapter-end", "chapter %d" % chapters[-1]["number"],
                         "no later heading, so the chapter is taken to the end of the book (back matter, if any, is included)"))
    book["chapters"] = chapters
    return warnings
