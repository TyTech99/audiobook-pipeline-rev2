"""Treatise structure (TREATISE-TOC-RESEARCH.md): books whose structure is a topical CONTENTS list rather than
numbered CHAPTER or LESSON headings.

This module runs ONLY when structure.find_headings() finds no CHAPTER or LESSON sequence (or when book.json sets
"type": "treatise"). Standard chapter and lesson books never enter this code.

How it works:
  1. Find the printed CONTENTS page(s) near the front of the book and parse each (title, print_page) entry.
  2. Map printed page numbers onto PDF units using the numbers printed in running headers and footers.
  3. Match each TOC entry to its heading line(s) on the mapped PDF unit (or its immediate neighbors), requiring
     strictly increasing (unit, line) order so two sections on the same page (e.g. print pages 14 and 69 in Byrum's
     Power to Heal) split in vertical order.
  4. Slice chapters at exact (unit, line) boundaries so mid-page and same-page sections neither lose nor duplicate
     any lines on shared pages."""
from difflib import SequenceMatcher
import re

from worker.prep.bookio_old import title_case as _title_case
from worker.prep import pageparts
from worker.prep import structure

MIN_TOC_ENTRIES = 5
CONTENTS_HEADER_RE = re.compile(
    r"^\s*[|#*\s]*(?:TABLE\s+OF\s+)?C\s*O\s*N\s*T\s*E\s*N\s*T\s*S\.?(?:\s*[|.]*\s*(?:Page\.?)?\s*[|]*)?\s*$", re.I
)
TABLE_RULE_RE = re.compile(r"^\s*\|[\s:\-|]+\|\s*$")
APPENDIX_RE = re.compile(r"^\s*appendix\b", re.I)


def _clean_toc_title(raw):
    """Strip markdown bullets/emphasis, dot leaders, trailing dashes/pipes and OCR leader noise from a TOC title cell."""
    s = re.sub(r"^\s*(?:[-*•]+|\d+[.)])\s*", "", raw.strip())
    s = re.sub(r"[*_`]+", "", s)
    # If dot leaders ('...' or '. . .') sit inside the title before OCR noise, cut at the leaders.
    s = re.split(r"\s*(?:\.\s*){2,}|\s*\u2026+\s*", s)[0]
    s = re.sub(r"[\s.\u2026\u00b7\-_*+:=<>|]+$", "", s).strip()
    s = re.sub(r"\s+\d+\s*$", "", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def parse_toc_line(line, allow_missing_page=False):
    """(title, print_page_or_None) for one line of a CONTENTS page, or None.
    Accepts dot-leader lines ('Title ........ 14' or 'Title........ 14' with no space before the dots),
    markdown table rows from the vision model ('| Title | 14 |' or 'Title | 14'),
    and plain title + trailing page number lines on a CONTENTS page."""
    s = line.strip()
    if not s or TABLE_RULE_RE.match(s):
        return None
    page = None
    if "|" in s:
        cells = [c.strip() for c in s.strip("|").split("|") if c.strip()]
        if len(cells) < 2:
            return None
        m_page = re.search(r"(\d{1,3})\s*[.:*_]*\s*$", cells[-1])
        if not m_page:
            return None
        title = _clean_toc_title(" ".join(cells[:-1]))
        page = int(m_page.group(1))
    else:
        m = re.match(r"^(.+?)(?:\s*[.\u2026\u00b7\-_*+:=<>]{2,}.*?\s*|\s+)(\d{1,3})\s*[.:*_]*\s*$", s)
        if m:
            title = _clean_toc_title(m.group(1))
            page = int(m.group(2))
        elif allow_missing_page and re.search(r"(?:\.\s*){3,}|\u2026{2,}", s):
            title = _clean_toc_title(s)
            page = None
        else:
            return None
    letters = [c for c in title if c.isalpha()]
    if len(letters) < 3 or (page is not None and page <= 0):
        return None
    if title.lower() in ("page", "pages", "contents", "table of contents", "chapter", "chapters"):
        return None
    if structure.heading_number(title, 1) is not None:
        return None
    return title, page


def find_toc(units, max_front_units=20):
    """(toc_unit_indices, [(title, print_page, raw_page)]) if a treatise CONTENTS page is present in the front units,
    else ([], []). Single-entry OCR digit glitches in the page column (e.g. '82' between 31 and 34) are repaired when
    bounded by monotonic neighbors."""
    limit = min(len(units), max_front_units)
    for ui in range(limit):
        lines = units[ui]["text"].split("\n")
        start = None
        for li, ln in enumerate(lines):
            if CONTENTS_HEADER_RE.match(ln.strip()):
                start = li + 1
                break
        if start is None:
            non_empty = [ln for ln in lines if ln.strip()]
            hits = [parse_toc_line(ln) for ln in non_empty]
            valid = [x for x in hits if x is not None]
            if len(valid) >= 8 and len(valid) >= len(non_empty) // 2:
                start = 0
            else:
                continue
        toc_units = [ui]
        raw_entries = []
        for ln in lines[start:]:
            got = parse_toc_line(ln, allow_missing_page=True)
            if got:
                raw_entries.append(got)
        # A multi-page CONTENTS continues on the next unit when that unit also consists of TOC lines.
        nxt = ui + 1
        while nxt < limit:
            nxt_lines = [ln for ln in units[nxt]["text"].split("\n") if ln.strip()]
            nxt_parsed = [parse_toc_line(ln) for ln in nxt_lines]
            nxt_hits = [x for x in nxt_parsed if x is not None]
            if len(nxt_hits) >= 3 and len(nxt_hits) >= len(nxt_lines) // 2:
                toc_units.append(nxt)
                raw_entries.extend(nxt_hits)
                nxt += 1
            else:
                break
        if sum(1 for _, p in raw_entries if p is not None) < MIN_TOC_ENTRIES:
            continue
        entries = _repair_page_glitches(raw_entries)
        monotonic = sum(1 for a, b in zip(entries, entries[1:]) if b[1] >= a[1])
        if monotonic >= max(1, int((len(entries) - 1) * 0.85)):
            return toc_units, entries
    return [], []


def _repair_page_glitches(raw_entries):
    """[(title, page, raw_page)] with missing or single-entry OCR digit misreads clamped between monotonic neighbors."""
    out = [[t, p, p] for t, p in raw_entries]
    for i in range(len(out)):
        if out[i][1] is None:
            prev_p = next((out[j][1] for j in range(i - 1, -1, -1) if out[j][1] is not None), None)
            next_p = next((out[j][1] for j in range(i + 1, len(out)) if out[j][1] is not None), None)
            if prev_p is not None and next_p is not None:
                out[i][1] = (prev_p + next_p) // 2
            elif next_p is not None:
                out[i][1] = max(1, next_p - 1)
            elif prev_p is not None:
                out[i][1] = prev_p + 1
            else:
                out[i][1] = 1
            out[i][2] = out[i][1]
    for i in range(1, len(out) - 1):
        prev_p, cur_p, next_p = out[i - 1][1], out[i][1], out[i + 1][1]
        if prev_p <= next_p and not (prev_p <= cur_p <= next_p):
            out[i][1] = (prev_p + next_p) // 2
    return [(t, p, rp) for t, p, rp in out]


def _word_sim(a, b):
    wa = re.findall(r"[a-z]+", a.lower())
    wb = re.findall(r"[a-z]+", b.lower())
    if not wa or not wb:
        return 0.0
    return SequenceMatcher(None, " ".join(wa), " ".join(wb)).ratio()


def _heading_score(cand_text, toc_title):
    """Similarity in [0, 1] between a candidate body line span and a TOC title, or 0.0 if the span is too long or
    too short to be that heading."""
    cn = pageparts.norm(cand_text)
    tn = pageparts.norm(toc_title)
    if len(cn) < 3 or not tn:
        return 0.0
    max_len = max(len(tn) + 12, int(len(tn) * 1.40))
    min_len = min(max(3, len(tn) - 10), int(len(tn) * 0.60))
    if not (min_len <= len(cn) <= max_len):
        return 0.0
    char_sim = SequenceMatcher(None, cn, tn).ratio()
    word_sim = _word_sim(cand_text, toc_title)
    return max(char_sim, (char_sim + word_sim) / 2.0)


def _unit_offset(units, printed, toc_units, entries):
    """Median (unit_index - print_page) offset across body pages, falling back to a first-heading scan if no page
    numbers were read from headers/footers."""
    after = max(toc_units) + 1 if toc_units else 0
    diffs = [ui - p for ui, p in enumerate(printed) if ui >= after and p is not None and 1 <= p <= len(units) + 20]
    if diffs:
        diffs.sort()
        return diffs[len(diffs) // 2]
    # Fallback when no printed page numbers were extracted: find the unit where the first TOC title appears.
    if entries:
        first_title, first_page, _ = entries[0]
        best_ui, best_s = None, 0.0
        for ui in range(after, min(len(units), after + 15)):
            for ln in units[ui]["text"].split("\n"):
                s = _heading_score(ln, first_title)
                if s > best_s:
                    best_s, best_ui = s, ui
        if best_ui is not None and best_s >= 0.72:
            return best_ui - first_page
    return after - (entries[0][1] if entries else 1)


def _expected_unit(print_page, offset, printed, after_unit, num_units):
    exact = [ui for ui in range(after_unit, num_units) if printed[ui] == print_page]
    if exact:
        guess = max(after_unit, min(num_units - 1, print_page + offset))
        return min(exact, key=lambda ui: abs(ui - guess))
    anchors = [ui for ui, p in enumerate(printed) if p is not None and 1 <= p <= num_units + 50]
    if anchors:
        near = min(anchors, key=lambda a: abs(printed[a] - print_page))
        return max(after_unit, min(num_units - 1, near + (print_page - printed[near])))
    return max(after_unit, min(num_units - 1, print_page + offset))


def _candidates_on_unit(unit, ui, min_line, max_line, book_title, running):
    """[(start_line, end_line_exclusive, text, is_isolated)] for 1-line and 2-line spans on `unit`."""
    lines = unit["text"].split("\n")
    z = pageparts.header_zone(lines, book_title, running)
    f = pageparts.footer_number(lines)
    r = pageparts.footer_roman(lines)
    header_end = z[1] if z else 0
    footer_idx = f[0] if f else (r[0] if r else len(lines))
    lo = max(min_line, header_end)
    hi = min(max_line if max_line is not None else len(lines), footer_idx)
    spans = []
    for li in range(lo, hi):
        first = lines[li].strip()
        if not first or pageparts.ORNAMENT_RE.match(first):
            continue
        clean1 = re.sub(r"^\s*#{1,6}\s+", "", first).strip()
        prev_blank = li == header_end or not lines[li - 1].strip()
        next_blank = li + 1 >= hi or not lines[li + 1].strip()
        spans.append((li, li + 1, clean1, prev_blank or next_blank or pageparts._mostly_upper(clean1)))
        # Also consider a 2-line or 3-line wrapped heading (allowing at most one blank line between wrapped lines).
        nxt = li + 1
        if nxt < hi and not lines[nxt].strip():
            nxt += 1
        if nxt < hi and lines[nxt].strip() and not pageparts.ORNAMENT_RE.match(lines[nxt]):
            second = re.sub(r"^\s*#{1,6}\s+", "", lines[nxt].strip()).strip()
            if len(clean1.split()) <= 10 and len(second.split()) <= 10:
                joined2 = clean1 + " " + second
                spans.append((li, nxt + 1, joined2, prev_blank or pageparts._mostly_upper(clean1)))
                nxt2 = nxt + 1
                if nxt2 < hi and not lines[nxt2].strip():
                    nxt2 += 1
                if nxt2 < hi and lines[nxt2].strip() and not pageparts.ORNAMENT_RE.match(lines[nxt2]):
                    third = re.sub(r"^\s*#{1,6}\s+", "", lines[nxt2].strip()).strip()
                    if len(third.split()) <= 8:
                        spans.append((li, nxt2 + 1, joined2 + " " + third, prev_blank or pageparts._mostly_upper(clean1)))
    return spans


def _choose_title(toc_title, body_text):
    """Prefer the TOC title's casing/punctuation unless the TOC title has more unknown words than the body heading."""
    body_clean = structure.heading_title([body_text.rstrip(".")])
    if toc_title.endswith("?") and not body_clean.endswith("?"):
        body_clean = body_clean.rstrip(".:;!") + "?"
    if not pageparts._mostly_upper(toc_title) and structure.unknown_words(toc_title) <= structure.unknown_words(body_clean):
        return toc_title, False
    return body_clean, True


def build_chapters(units, book):
    """(chapters, warnings) for a treatise book, or (None, []) if the book has no treatise CONTENTS page.
    Raises SystemExit if a treatise CONTENTS page is found (or book['type'] == 'treatise') and validation fails."""
    toc_units, entries = find_toc(units)
    if not entries:
        if book.get("type") == "treatise":
            raise SystemExit("book type is 'treatise' but no CONTENTS page with section entries was found")
        return None, []
    warnings = []
    for t, p, rp in entries:
        if p != rp:
            warnings.append(("toc-page-repaired", "book",
                             "CONTENTS entry '%s' had page %d between %d and %d (OCR digit misread); treated as page %d"
                             % (t, rp, p, p, p)))
    running = pageparts.running_lines(units)
    pages, printed = structure.book_pages(units, book["title"], running)
    after_toc = max(toc_units) + 1
    offset = _unit_offset(units, printed, toc_units, entries)
    matched = []
    prev_ui, prev_line = after_toc, 0
    for idx, (toc_title, print_page, _) in enumerate(entries, start=1):
        exp_ui = _expected_unit(print_page, offset, printed, prev_ui, len(units))
        # Constrain upper search bound using the next entry's expected page so we never jump past the next section.
        if idx < len(entries):
            next_exp = _expected_unit(entries[idx][1], offset, printed, exp_ui, len(units))
            hi_ui = min(len(units) - 1, max(exp_ui + 2, next_exp + 1))
        else:
            hi_ui = min(len(units) - 1, exp_ui + 3)
        lo_ui = max(prev_ui, exp_ui - 2)
        same_page_as_prev = idx > 1 and print_page == entries[idx - 2][1]
        best = None
        for radius, cutoff in ((2, 0.65), (4, 0.60), (1, 0.45)):
            w_lo = max(prev_ui, exp_ui - radius)
            w_hi = min(len(units) - 1, max(hi_ui, exp_ui + radius))
            for ui in range(w_lo, w_hi + 1):
                min_ln = prev_line if ui == prev_ui else 0
                for s_ln, e_ln, cand_text, isolated in _candidates_on_unit(units[ui], ui, min_ln, None, book["title"], running):
                    if cutoff < 0.60 and not isolated:
                        continue
                    sim = _heading_score(cand_text, toc_title)
                    if sim < cutoff:
                        continue
                    same_bonus = 0.06 if (same_page_as_prev and ui == prev_ui) else 0.0
                    rank = sim + (0.04 if ui == exp_ui else 0.0) + (0.02 if isolated else 0.0) + same_bonus - 0.01 * abs(ui - exp_ui)
                    if best is None or rank > best[0]:
                        best = (rank, sim, ui, s_ln, e_ln, cand_text)
            if best is not None and (best[1] >= 0.72 or cutoff < 0.65):
                break
        if best is not None:
            _, sim, ui, s_ln, e_ln, cand_text = best
            title, used_body = _choose_title(toc_title, cand_text)
            if sim < 0.85:
                warnings.append(("treatise-heading-fuzzy", "chapter %d" % idx,
                                 "TOC title '%s' (p. %d) matched body line '%s' on pdf page %d (similarity %.2f)"
                                 % (toc_title, print_page, cand_text, units[ui]["pdf_page"], sim)))
        else:
            # If the heading line was unreadable in OCR on its page, place the section at body_start of exp_ui
            # provided exp_ui advances past prev_ui (never guess a mid-page position without a matched line).
            ui = max(exp_ui, prev_ui + (1 if prev_line > 0 else 0))
            if ui >= len(units):
                raise SystemExit("treatise: TOC entry %d ('%s', page %d) could not be matched to any page"
                                 % (idx, toc_title, print_page))
            lines = units[ui]["text"].split("\n")
            s_ln = structure.body_start(lines, book["title"], running)
            if ui == prev_ui:
                s_ln = max(s_ln, prev_line)
            e_ln = s_ln
            title = toc_title
            warnings.append(("treatise-heading-fallback", "chapter %d" % idx,
                             "TOC title '%s' (p. %d) had no matching heading line near pdf page %d; started at top of pdf page %d"
                             % (toc_title, print_page, units[exp_ui]["pdf_page"], units[ui]["pdf_page"])))
        matched.append({
            "number": idx,
            "title": title,
            "toc_title": toc_title,
            "toc_page": print_page,
            "unit": ui,
            "line": s_ln,
            "title_end": e_ln,
            "appendix": bool(APPENDIX_RE.match(toc_title)),
        })
        prev_ui, prev_line = ui, max(e_ln, s_ln + 1)

    old = {c["number"]: c for c in book.get("chapters", [])}
    chapters = []
    for k, h in enumerate(matched):
        if k + 1 < len(matched):
            nh = matched[k + 1]
            nh_lines = units[nh["unit"]]["text"].split("\n")
            bstart = structure.body_start(nh_lines, book["title"], running)
            above_start = max(bstart, h["title_end"] if nh["unit"] == h["unit"] else bstart)
            has_body_above = any(l.strip() and not pageparts.ORNAMENT_RE.match(l.strip())
                                 for l in nh_lines[above_start:nh["line"]])
            if nh["unit"] == h["unit"] or has_body_above:
                last_unit = nh["unit"]
                last_line = nh["line"]
            else:
                last_unit = max(h["unit"], nh["unit"] - 1)
                last_line = None
        else:
            last_unit = len(units) - 1
            last_line = None
            back = structure.back_matter_unit(units, h["unit"], book["title"], running)
            if back:
                last_unit = back[0] - 1
                warnings.append(("back-matter-excluded", "chapter %d" % h["number"],
                                 "the last section ends before pdf page %d, which opens with '%s' (back matter, not read)"
                                 % (units[back[0]]["pdf_page"], back[1])))
        ch = {
            "number": h["number"],
            "title": h["title"],
            "toc_title": h["toc_title"],
            "toc_page": h["toc_page"],
            "first_unit": h["unit"],
            "first_line": h["line"],
            "title_end": h["title_end"],
            "last_unit": last_unit,
            "last_line": last_line,
            "pdf_pages": [units[h["unit"]]["pdf_page"], units[last_unit]["pdf_page"]],
            "book_pages": [pages[h["unit"]], pages[last_unit]],
            "switches": old.get(h["number"], {}).get("switches", {}),
        }
        if h.get("appendix"):
            ch["appendix"] = True
        chapters.append(ch)
    validate(units, chapters, entries)
    warnings.insert(0, ("treatise-structure", "book",
                        "treatise CONTENTS on pdf page %d with %d sections (pdf pages %d-%d)"
                        % (units[toc_units[0]]["pdf_page"], len(chapters),
                           chapters[0]["pdf_pages"][0], chapters[-1]["pdf_pages"][1])))
    return chapters, warnings


def validate(units, chapters, entries):
    """Fail the run (SystemExit) if treatise sections are missing, out of order, or invert bounds."""
    if len(chapters) != len(entries):
        raise SystemExit("treatise: CONTENTS has %d entries but %d sections were built" % (len(entries), len(chapters)))
    for i, ch in enumerate(chapters):
        u0, l0 = ch["first_unit"], ch["first_line"]
        u1, l1 = ch["last_unit"], ch["last_line"]
        if u1 < u0 or (u1 == u0 and l1 is not None and l1 < ch["title_end"]):
            raise SystemExit("treatise: section %d ('%s') has inverted bounds (unit %d line %s .. unit %d line %s)"
                             % (ch["number"], ch["title"], u0, l0, u1, l1))
        if i > 0:
            prev = chapters[i - 1]
            if (u0, l0) <= (prev["first_unit"], prev["first_line"]):
                raise SystemExit("treatise: section %d ('%s') does not advance past section %d ('%s')"
                                 % (ch["number"], ch["title"], prev["number"], prev["title"]))
