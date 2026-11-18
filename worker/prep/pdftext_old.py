"""Read a PDF's text layer and image positions with poppler. No guessing: coordinates in, text out."""
import os
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from html import unescape


def _run(cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def page_geometry(pdf):
    """[(width, height)] per page as displayed (rotation applied)."""
    n = int(re.search(r"^Pages:\s+(\d+)", _run(["pdfinfo", pdf]), re.M).group(1))
    out = _run(["pdfinfo", "-f", "1", "-l", str(n), pdf])
    sizes = {int(m[0]): (float(m[1]), float(m[2])) for m in re.findall(r"^Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)", out, re.M)}
    rots = {int(m[0]): int(m[1]) for m in re.findall(r"^Page\s+(\d+) rot:\s+(\d+)", out, re.M)}
    geo = []
    for p in range(1, n + 1):
        w, h = sizes[p]
        if rots.get(p, 0) in (90, 270):
            w, h = h, w
        geo.append((w, h))
    return geo


def halves(width, height):
    """A landscape page that holds two book pages is read as two units, left then right."""
    if width > height * 1.2:
        return [("L", 0, width / 2), ("R", width / 2, width / 2)]
    return [(None, 0, width)]


def chars_per_page(pdf):
    """Number of non-space characters in the text layer of each page."""
    n = len(page_geometry(pdf))
    out = []
    for p in range(1, n + 1):
        txt = _run(["pdftotext", "-f", str(p), "-l", str(p), pdf, "-"])
        out.append(len(re.sub(r"\s", "", txt)))
    return out


# Typesetting characters that stand for plain ones: the non-breaking and Unicode hyphens are ordinary hyphens to every
# later rule (hyphen joining, word lists, the voice); a byte-order mark inside the text is not a character at all, and a
# soft hyphen only marks where a word may break, so it is removed.
CHAR_MAP = {"\u2010": "-", "\u2011": "-", "\ufeff": "", "\u00ad": ""}
SOFT_HYPHEN = "\u00ad"     # kept only at a line end, where clean-up joins the word halves without a space


def normalize_chars(text):
    """(text, {character: count}) with CHAR_MAP applied."""
    changed = {}
    text = re.sub(SOFT_HYPHEN + r"(?=[ \t]*\n)", "\x00", text)      # protect line-end soft hyphens
    for a, b in CHAR_MAP.items():
        n = text.count(a)
        if n:
            changed[a] = n
            text = text.replace(a, b)
    n = text.count("\x00")
    if n:
        changed[SOFT_HYPHEN] = changed.get(SOFT_HYPHEN, 0) + n
    return text.replace("\x00", SOFT_HYPHEN), changed


_LINE_RE = re.compile(r'<line xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</line>', re.S)


def page_lines(pdf, page):
    """Text lines of one PDF page with their position in points, top to bottom:
    [{'x', 'y', 'x2', 'y2', 'text'}]. Coordinates are those of the displayed page (poppler bbox-layout)."""
    xml = _run(["pdftotext", "-bbox-layout", "-f", str(page), "-l", str(page), pdf, "-"])
    out = []
    for m in _LINE_RE.finditer(xml):
        words = [unescape(w) for w in re.findall(r">([^<]+)</word>", m.group(5))]
        out.append({"x": float(m.group(1)), "y": float(m.group(2)), "x2": float(m.group(3)),
                    "y2": float(m.group(4)), "text": " ".join(words)})
    return sorted(out, key=lambda ln: (ln["y"], ln["x"]))


def _key(text):
    return re.sub(r"[^A-Za-z0-9]", "", text)


def align(raw_lines, boxes, window=8):
    """Give each line of a layout text (pdftotext -layout) the position of the same line from page_lines.
    Returns a list parallel to raw_lines: the box dict, or None for blank lines and lines that cannot be
    matched one to one (merged columns, small-caps headers). A line that only partly matches a box is left
    as None: its position would be wrong."""
    result = [None] * len(raw_lines)
    j = 0
    for i, line in enumerate(raw_lines):
        k = _key(line)
        if not k:
            continue
        for jj in range(j, min(j + window, len(boxes))):
            b = _key(boxes[jj]["text"])
            if b == k:
                result[i] = boxes[jj]
                j = jj + 1
                break
            if len(b) >= 8 and (k.endswith(b) or k.startswith(b)):
                j = jj + 1
                break
    return result


def read_pages(pdf):
    """{page number: [lines]} for the pages page reading did not take from the text layer (pages.json beside the PDF,
    written by pagereader for this PDF), else {}."""
    import json
    p = os.path.join(os.path.dirname(os.path.abspath(pdf)), "pages.json")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    if d.get("pdf") != os.path.basename(pdf):
        return {}
    return {int(k): v for k, v in (d.get("lines") or {}).items()}


def layout_text(lines):
    """A layout text like pdftotext -layout makes, from positioned lines: one text line per line, indented by its
    distance from the leftmost line in average character widths, with a blank line where the gap between lines is
    larger than one and a half times the usual one. Returns (text, boxes parallel to text.split('\\n'))."""
    if not lines:
        return "", [None]
    widths = [(ln["x2"] - ln["x"]) / len(ln["text"]) for ln in lines if len(ln["text"]) >= 8 and ln["x2"] > ln["x"]]
    cw = sorted(widths)[len(widths) // 2] if widths else 6.0
    left = min(ln["x"] for ln in lines)
    gaps = sorted(b["y"] - a["y"] for a, b in zip(lines, lines[1:]) if b["y"] > a["y"])
    usual = gaps[len(gaps) // 2] if gaps else 12.0
    out, geom = [], []
    for i, ln in enumerate(lines):
        if i and ln["y"] - lines[i - 1]["y"] > usual * 1.5:
            out.append("")
            geom.append(None)
        out.append(" " * max(0, round((ln["x"] - left) / cw)) + ln["text"])
        geom.append(ln)
    return "\n".join(out) + "\n", geom + [None]


def units(pdf, first_pages=None):
    """Reading units in order: [{'pdf_page', 'half', 'text', 'geom'}]. Text keeps the layout (indentation, hyphens).
    'geom' is parallel to text.split('\\n'): the position of each line on the page (see align). Pages that page
    reading read by tesseract or the vision model (pages.json) use those lines instead of the text layer."""
    result = []
    geo = page_geometry(pdf)
    read = read_pages(pdf)
    for p, (w, h) in enumerate(geo[:first_pages] if first_pages else geo, start=1):
        if p in read:
            for half, x, width in halves(w, h):
                mine = [b for b in read[p] if x <= b["x"] < x + width] if half else read[p]
                text, geom = layout_text(sorted(mine, key=lambda ln: (ln["y"], ln["x"])))
                text, changed = normalize_chars(text)
                result.append({"pdf_page": p, "half": half, "text": text, "geom": geom, "normalized": changed})
            continue
        boxes = page_lines(pdf, p)
        for half, x, width in halves(w, h):
            text = _run(["pdftotext", "-layout", "-f", str(p), "-l", str(p),
                         "-x", str(int(x)), "-y", "0", "-W", str(int(width)), "-H", str(int(h)), pdf, "-"])
            text, changed = normalize_chars(text.replace("\f", ""))
            mine = [b for b in boxes if x <= b["x"] < x + width] if half else boxes
            result.append({"pdf_page": p, "half": half, "text": text, "geom": align(text.split("\n"), mine), "normalized": changed})
    return result


def images(pdf):
    """Images with their page, half, position and size in points: [{'pdf_page', 'half', 'x', 'y', 'width', 'height'}]."""
    geo = page_geometry(pdf)
    found = []
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftohtml", "-xml", "-q", pdf, os.path.join(tmp, "o")], check=True, capture_output=True)
        root = ET.parse(os.path.join(tmp, "o.xml")).getroot()
        for page in root.iter("page"):
            p = int(page.get("number"))
            w, h = geo[p - 1]
            scale = float(page.get("width")) / w
            for img in page.iter("image"):
                left, top = float(img.get("left")) / scale, float(img.get("top")) / scale
                iw, ih = float(img.get("width")) / scale, float(img.get("height")) / scale
                # Clip to the page: an image drawn wholly outside the page box is not shown on the page.
                x0, y0, x1, y1 = max(left, 0), max(top, 0), min(left + iw, w), min(top + ih, h)
                if x1 - x0 < 1 or y1 - y0 < 1:
                    continue
                left, top, iw, ih = x0, y0, x1 - x0, y1 - y0
                cx = left + iw / 2
                half = None
                for name, x, width in halves(w, h):
                    if x <= cx < x + width:
                        half = name
                found.append({"pdf_page": p, "half": half, "x": round(left, 2), "y": round(top, 2),
                              "width": round(iw), "height": round(ih)})
    return found
