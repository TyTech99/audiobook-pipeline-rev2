"""A source EPUB (for example from Project Gutenberg): kept untouched in the book folder like a PDF, and read into the
same chapter text files a PDF produces, so the same clean-up rules, script and EPUB build follow.

What is read: the author's text and headings in reading order (spine), chapters from the top level of the source's
table of contents, tables, illustrations with their alt text and captions, and footnotes the source marks as notes.
Publisher styling is not carried over: the rebuilt EPUB has its own minimal style sheet.
Copy protection is never removed: a book whose text is encrypted is rejected ("copy-protected EPUB")."""
import hashlib
import html.entities
import json
import os
import posixpath
import re
import shutil
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

from pipeline.book import bookio
from pipeline.text import cleanup
from pipeline.text import pdftext

FONT_OBFUSCATION = {"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"}
NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/",
      "c": "urn:oasis:names:tc:opendocument:xmlns:container", "enc": "http://www.w3.org/2001/04/xmlenc#",
      "ncx": "http://www.daisy.org/z3986/2005/ncx/"}
XHTML = "{http://www.w3.org/1999/xhtml}"
EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"
BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "dt", "dd", "pre", "blockquote", "div", "section", "article",
         "aside", "header", "footer", "figure", "table", "ul", "ol", "dl", "nav", "main", "body", "hr", "figcaption"}
# Publisher pages, not the author's. The rebuilt EPUB has its own title page and tables of contents.
PUBLISHER_TYPES = {"titlepage", "halftitlepage", "imprint", "colophon", "copyright-page", "cover", "toc", "landmarks",
                   "loi", "lot", "seriespage", "imprimatur", "page-list"}
NOTE_TYPES = {"footnote", "endnote", "rearnote", "note"}
NOTE_ROLES = {"doc-footnote", "doc-endnote"}
SKIP = {"script", "style", "head", "nav"}
# Project Gutenberg's own header and licence around the book (not the author's text)
PG_START_RE = re.compile(r"\*\*\*\s*START OF (THE|THIS) PROJECT GUTENBERG", re.I)
PG_END_RE = re.compile(r"\*\*\*\s*END OF (THE|THIS) PROJECT GUTENBERG", re.I)
PG_PRODUCED_RE = re.compile(r"^(Produced|E-text prepared|Transcribed) by\b", re.I)    # the volunteers' credit line
PG_TAIL_RE = re.compile(r"^End of (the )?Project Gutenberg", re.I)
# A copyright notice: the symbol, or "Copyright" or "(c)" followed by a year, or "All rights reserved".
# A statement that a text is free of copyright is not a notice.
COPYRIGHT_RE = re.compile(r"(?:©|(?:\bcopyright|\(c\))\s*(?:©\s*)?(?:\d{4})|\ball rights reserved\b)", re.I)


class EpubSourceError(Exception):
    def __init__(self, msg, kind="permanent"):
        super().__init__(msg)
        self.kind = kind


def local(tag):
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def parse_xml(data):
    """ElementTree of an XHTML file. HTML named entities (EPUB 2 files use &nbsp; and the like) are turned into
    characters first, because XML knows only five."""
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    keep = {"amp", "lt", "gt", "quot", "apos"}
    text = re.sub(r"&([A-Za-z][A-Za-z0-9]*);",
                  lambda m: m.group(0) if m.group(1) in keep or m.group(1) not in html.entities.name2codepoint
                  else chr(html.entities.name2codepoint[m.group(1)]), text)
    text = re.sub(r"<!DOCTYPE[^>]*>", "", text, count=1)
    try:
        return ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as e:
        raise EpubSourceError("the EPUB has a file that is not well-formed XHTML (%s)" % e)


# ---------- the package ----------

def protection(z):
    """None, or the reason the book counts as copy-protected: a file other than a font encrypted, or encrypted with
    anything but the font scrambling algorithms."""
    if "META-INF/encryption.xml" not in z.namelist():
        return None
    root = parse_xml(z.read("META-INF/encryption.xml"))
    for ed in root.iter("{%s}EncryptedData" % NS["enc"]):
        m = ed.find("enc:EncryptionMethod", NS)
        alg = m.get("Algorithm") if m is not None else ""
        ref = ed.find(".//enc:CipherReference", NS)
        uri = ref.get("URI") if ref is not None else ""
        is_font = re.search(r"\.(otf|ttf|woff2?)$", uri or "", re.I)
        if alg not in FONT_OBFUSCATION or not is_font:
            return "%s is encrypted (%s)" % (uri or "a file", alg or "unknown method")
    return None


def package(z):
    """{'opf_dir', 'version', 'title', 'authors', 'language', 'rights', 'manifest' {id: (path, media, props)},
    'spine' [path], 'nav', 'ncx', 'cover'} read from the package file."""
    try:
        cont = parse_xml(z.read("META-INF/container.xml"))
    except KeyError:
        raise EpubSourceError("not an EPUB: META-INF/container.xml is missing")
    rf = cont.find(".//c:rootfile", NS)
    if rf is None:
        raise EpubSourceError("not an EPUB: no package file named in META-INF/container.xml")
    opf_path = rf.get("full-path")
    root = parse_xml(z.read(opf_path))
    base = posixpath.dirname(opf_path)
    md = root.find("opf:metadata", NS)
    if md is None:
        raise EpubSourceError("the EPUB package file has no metadata")

    def texts(tag):
        return [" ".join((e.text or "").split()) for e in md.findall(tag, NS) if (e.text or "").strip()]
    roles = {}
    for m in md.findall("opf:meta", NS):
        if m.get("property") == "role" and m.get("refines"):
            roles[m.get("refines").lstrip("#")] = (m.text or "").strip()
    authors = []
    for e in md.findall("dc:creator", NS):
        role = e.get("{%s}role" % NS["opf"]) or roles.get(e.get("id") or "", "aut")
        if role == "aut" and (e.text or "").strip():
            authors.append(" ".join(e.text.split()))
    manifest = {}
    for it in root.findall("opf:manifest/opf:item", NS):
        path = posixpath.normpath(posixpath.join(base, urllib.parse.unquote(it.get("href"))))
        manifest[it.get("id")] = (path, it.get("media-type"), (it.get("properties") or "").split())
    spine_el = root.find("opf:spine", NS)
    spine = [manifest[i.get("idref")][0] for i in spine_el.findall("opf:itemref", NS)
             if i.get("idref") in manifest and i.get("linear") != "no"]
    nav = next((p for p, _, props in manifest.values() if "nav" in props), None)
    ncx = manifest.get(spine_el.get("toc") or "", (None,))[0] or next(
        (p for p, mt, _ in manifest.values() if mt == "application/x-dtbncx+xml"), None)
    cover = next((p for p, _, props in manifest.values() if "cover-image" in props), None)
    if cover is None:
        cm = next((m.get("content") for m in md.findall("opf:meta", NS) if m.get("name") == "cover"), None)
        if cm in manifest and (manifest[cm][1] or "").startswith("image/"):
            cover = manifest[cm][0]
    return {"opf_dir": base, "version": root.get("version", ""), "title": (texts("dc:title") or [""])[0],
            "authors": authors, "language": (texts("dc:language") or [""])[0],
            "description": (texts("dc:description") or [""])[0], "rights": " ".join(texts("dc:rights")),
            "manifest": manifest, "spine": spine, "nav": nav, "ncx": ncx, "cover": cover}


def toc(z, pkg):
    """Top-level entries of the source's table of contents: [(title, path, fragment)]."""
    out = []
    if pkg["nav"]:
        root = parse_xml(z.read(pkg["nav"]))
        navs = [n for n in root.iter() if local(n.tag) == "nav" and "toc" in (n.get(EPUB_TYPE) or "").split()]
        if navs:
            ol = next((c for c in navs[0] if local(c.tag) == "ol"), None)
            for li in ([c for c in ol if local(c.tag) == "li"] if ol is not None else []):
                a = next((c for c in li if local(c.tag) in ("a", "span")), None)
                if a is not None and a.get("href"):
                    out.append((" ".join("".join(a.itertext()).split()),) + split_href(pkg["nav"], a.get("href")))
            if out:
                return out
    if pkg["ncx"]:
        root = parse_xml(z.read(pkg["ncx"]))
        nm = root.find("ncx:navMap", NS)
        for np in (nm.findall("ncx:navPoint", NS) if nm is not None else []):
            label = " ".join("".join(np.find("ncx:navLabel", NS).itertext()).split())
            out.append((label,) + split_href(pkg["ncx"], np.find("ncx:content", NS).get("src")))
    return out


def split_href(from_path, href):
    path, _, frag = href.partition("#")
    full = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), urllib.parse.unquote(path))) if path else from_path
    return full, frag


# ---------- reading the text ----------

def is_note(el):
    types = set((el.get(EPUB_TYPE) or "").split())
    return bool(types & NOTE_TYPES) or el.get("role") in NOTE_ROLES


def note_ids(z, pkg):
    """{(path, id)} of every element the source marks as a footnote or endnote."""
    found = set()
    for path in pkg["spine"]:
        for el in parse_xml(z.read(path)).iter():
            if is_note(el) and el.get("id"):
                found.add((path, el.get("id")))
    return found


class Reader:
    """Walks one spine file into blocks: ('p', text) ('table', rows, head_rows) ('figure', src, alt, caption)
    ('note', id, text) and ('anchor', id) markers where an element with an id starts."""

    def __init__(self, path, notes_by_id):
        self.path, self.notes_by_id, self.out, self.dropped = path, notes_by_id, [], []

    def inline(self, el):
        parts = [el.text or ""]
        for c in el:
            t = local(c.tag)
            if t == "br":
                parts.append(" ")
            elif t in SKIP:
                pass
            elif t == "a" and self.note_target(c):
                parts.append("[^%s]" % self.note_target(c))
            elif t == "img":
                pass
            else:
                parts.append(self.inline(c))
            parts.append(c.tail or "")
        return "".join(parts)

    def note_target(self, a):
        href = a.get("href") or ""
        if "#" not in href:
            return None
        key = split_href(self.path, href)
        return self.notes_by_id.get(key)

    def walk(self, el):
        t = local(el.tag)
        if t in SKIP or t == "head":
            return
        if el.get("id"):
            self.out.append(("anchor", el.get("id")))
        types = set((el.get(EPUB_TYPE) or "").split())
        if types & PUBLISHER_TYPES:
            self.dropped.append(sorted(types & PUBLISHER_TYPES)[0])
            return
        if is_note(el):
            nid = self.notes_by_id.get((self.path, el.get("id")))
            text = " ".join(self.inline(el).split())
            if nid and text:
                self.out.append(("note", nid, text))
            return
        if t == "table":
            self.out.append(self.table(el))
            return
        if t == "img" or (t == "image" and el.get("{http://www.w3.org/1999/xlink}href")):
            src = el.get("src") or el.get("{http://www.w3.org/1999/xlink}href")
            self.out.append(("figure", split_href(self.path, src)[0], el.get("alt") or "", None))
            return
        if t == "figure":
            cap = next((c for c in el.iter() if local(c.tag) == "figcaption"), None)
            caption = " ".join("".join(cap.itertext()).split()) if cap is not None else None
            imgs = [c for c in el.iter() if local(c.tag) in ("img", "image")]
            for im in imgs:
                src = im.get("src") or im.get("{http://www.w3.org/1999/xlink}href")
                if src:
                    self.out.append(("figure", split_href(self.path, src)[0], im.get("alt") or "", caption))
            if not imgs and caption:
                self.out.append(("p", caption))
            return
        if t == "svg":
            for im in el.iter():
                if local(im.tag) == "image":
                    self.walk(im)
            return
        has_block = any(local(c.tag) in BLOCK or local(c.tag) in ("img", "svg", "table") for c in el)
        if not has_block:
            text = " ".join(self.inline(el).split())
            if text and t not in ("hr",):
                self.out.append(("p", text, t) if re.fullmatch(r"h[1-6]", t) else ("p", text))
            for c in el.iter():
                if c is not el and local(c.tag) in ("img",):
                    self.walk(c)
            return
        if (el.text or "").strip():
            self.out.append(("p", " ".join(el.text.split())))
        for c in el:
            self.walk(c)
            if (c.tail or "").strip():
                self.out.append(("p", " ".join(c.tail.split())))

    def table(self, el):
        head, body = [], []
        for tr in el.iter():
            if local(tr.tag) != "tr":
                continue
            cells = [c for c in tr if local(c.tag) in ("td", "th")]
            row = [" ".join(self.inline(c).split()).replace("|", "/") for c in cells]
            if not row:
                continue
            in_head = any(local(p.tag) == "thead" for p in el.iter() if tr in list(p))
            (head if in_head or (not body and cells and all(local(c.tag) == "th" for c in cells)) else body).append(row)
        return ("table", body, head)


def join_drop_caps(blocks):
    """A drop cap is an image of the first letter (alt text one letter, maybe after an opening quote) before the rest of the word: the letter goes
    back into the text and the image is not an illustration."""
    out = []
    for i, b in enumerate(blocks):
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        if b[0] == "figure" and re.fullmatch(r"[\"“‘']?[^\W\d_]", b[2] or "") and nxt and nxt[0] == "p":
            blocks[i + 1] = (nxt[0], b[2] + nxt[1]) + tuple(nxt[2:])
            continue
        out.append(b)
    return out


def strip_gutenberg(blocks):
    """Blocks between Project Gutenberg's START and END markers, when the source has them."""
    start = next((i for i, b in enumerate(blocks) if b[0] == "p" and PG_START_RE.search(b[1])), None)
    end = next((i for i, b in enumerate(blocks) if b[0] == "p" and PG_END_RE.search(b[1])), None)
    if start is None and end is None:
        return blocks, False
    body = blocks[(start + 1 if start is not None else 0):(end if end is not None else len(blocks))]
    if body and body[0][0] == "p" and PG_PRODUCED_RE.match(body[0][1]):
        body = body[1:]
    if body and body[-1][0] == "p" and PG_TAIL_RE.match(body[-1][1]):
        body = body[:-1]
    return body, True


def read(z, pkg, notes=None):
    """[(path, blocks)] for the spine, with the book's notes numbered in reading order."""
    keys = sorted(note_ids(z, pkg), key=lambda k: (pkg["spine"].index(k[0]), k[1]))
    notes_by_id = {}
    for path in pkg["spine"]:                       # numbered by first appearance in the text
        for el in parse_xml(z.read(path)).iter():
            if el.get("id") and (path, el.get("id")) in set(keys) and (path, el.get("id")) not in notes_by_id:
                notes_by_id[(path, el.get("id"))] = str(len(notes_by_id) + 1)
    out = []
    for path in pkg["spine"]:
        root = parse_xml(z.read(path))
        body = next((c for c in root if local(c.tag) == "body"), None)
        r = Reader(path, notes_by_id)
        if body is not None:
            r.walk(body)
        r.out = join_drop_caps([b for b in r.out])
        for kind in r.dropped:
            if notes is not None:
                notes.append(("epub-source-publisher-page", path, "%s left out (publisher's page, not the author's text)" % kind))
        out.append((path, r.out))
    return out


def chapters(z, pkg, files, notes):
    """[{'title', 'blocks'}] split at the top-level table-of-contents entries (one per spine file when the source has
    no table of contents). Text before the first entry joins the first chapter."""
    flat = []
    for path, blocks in files:
        flat.append(("file", path))
        flat += blocks
    flat_p = [b for b in flat if b[0] not in ("file", "anchor")]
    body, had_pg = strip_gutenberg(flat_p)
    if had_pg:
        notes.append(("epub-source-boilerplate", "book", "Project Gutenberg header and licence left out (not the author's text)"))
        keep = set(map(id, body))
        flat = [b for b in flat if b[0] in ("file", "anchor") or id(b) in keep]
    entries = toc(z, pkg)
    pos, cur_file = {}, None
    n = 0
    for i, b in enumerate(flat):
        if b[0] == "file":
            cur_file = b[1]
            pos.setdefault((cur_file, ""), n)
        elif b[0] == "anchor":
            pos.setdefault((cur_file, b[1]), n)
        else:
            n += 1
    content = [b for b in flat if b[0] not in ("file", "anchor")]
    starts = []
    for title, path, frag in entries:
        at = pos.get((path, frag), pos.get((path, "")))
        if at is None:
            notes.append(("epub-source-toc", title, "table-of-contents entry points to %s#%s, which is not in the text" % (path, frag)))
            continue
        if at >= len(content) or (starts and at <= starts[-1][1]):
            continue
        starts.append((title, at))
    covered = {path for _, path, _ in entries}
    for path, blocks in files:                     # a file the contents list never reaches, opening with a heading or short line
        first = next((b for b in blocks if b[0] != "anchor"), None)
        at = pos.get((path, ""))
        if starts and path not in covered and first and first[0] == "p" and at is not None and at < len(content) \
                and content[at] is first and (len(first) == 3 or len(first[1].split()) <= 12):
            starts.append((first[1], at))
            notes.append(("epub-source-toc", first[1], "file not reached by the source's table of contents; a chapter titled by its opening line"))
    starts.sort(key=lambda s: s[1])
    if not starts:
        notes.append(("epub-source-toc", "book", "no usable table of contents: one chapter per file"))
        starts = [(None, pos[(p, "")]) for p, _ in files if pos.get((p, "")) is not None and pos[(p, "")] < len(content)]
        starts = [s for i, s in enumerate(starts) if i == 0 or s[1] > starts[i - 1][1]]
    if starts:
        starts[0] = (starts[0][0], 0)
    out = []
    for i, (title, at) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(content)
        blocks = content[at:end]
        if title is None:
            title = next((b[1] for b in blocks if b[0] == "p"), "Chapter %d" % (i + 1))[:80]
        if blocks and blocks[0][0] == "p" and norm(blocks[0][1]) == norm(title):
            blocks = blocks[1:]                    # the chapter's own heading becomes the chapter title
        if any(b[0] != "note" for b in blocks):
            out.append({"title": " ".join(title.split()), "blocks": blocks})
        else:
            notes.append(("epub-source-empty", title, "table-of-contents entry with no text of its own; left out"))
    return out


def norm(t):
    return re.sub(r"\W+", " ", t).strip().lower()


def place_notes(chs, notes):
    """Each note moves to the chapter that first refers to it, so the link and the note sit in one file."""
    by_id, home = {}, {}
    for c in chs:
        for b in c["blocks"]:
            if b[0] == "note":
                by_id[b[1]] = b
    for i, c in enumerate(chs):
        for b in c["blocks"]:
            if b[0] in ("p", "table"):
                text = b[1] if b[0] == "p" else json.dumps(b[1:])
                for m in re.finditer(r"\[\^([^\]\s]+)\]", text):
                    home.setdefault(m.group(1), i)
    for c in chs:
        c["blocks"] = [b for b in c["blocks"] if b[0] != "note" or b[1] not in home]
    for nid, i in sorted(home.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
        if nid in by_id:
            chs[i]["blocks"].append(by_id[nid])
        else:
            notes.append(("epub-source-note-missing", "chapter %d" % (i + 1), "note mark %s has no note" % nid))


# ---------- the stage ----------

def clean(text, where, notes):
    """The PDF clean-up rules that apply to reflowing text: character fixes (soft hyphens, odd hyphens, byte-order
    marks), quote checks and unknown-word flags. Line, page and layout rules have nothing to do in an EPUB."""
    text, changed = pdftext.normalize_chars(text)
    text = text.replace(pdftext.SOFT_HYPHEN, "")
    if changed:
        notes.append(("characters-normalized", where, ", ".join("%d x U+%04X" % (n, ord(c)) for c, n in sorted(changed.items()))))
    text = cleanup.fix_quotes(text, where, notes)
    cleanup.odd_words(text, where, notes)
    return text


def book_text(chs):
    return "\n".join(b[1] for c in chs for b in c["blocks"] if b[0] == "p")


def rights_flag(pkg, chs):
    """'verify rights' reason when the rights metadata or the book's text carries a copyright notice, else None."""
    if COPYRIGHT_RE.search(pkg["rights"] or ""):
        return "rights metadata: \"%s\"" % pkg["rights"][:120]
    for c in chs:
        for b in c["blocks"]:
            if b[0] == "p":
                m = COPYRIGHT_RE.search(b[1])
                if m:
                    return "book text, chapter '%s': \"...%s...\"" % (c["title"], b[1][max(0, m.start() - 40):m.end() + 40])
    return None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def open_source(path):
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise EpubSourceError("not an EPUB: the file is not a zip archive")
    why = protection(z)
    if why:
        raise EpubSourceError("copy-protected EPUB: %s. The pipeline never removes copy protection." % why)
    return z


def resolve_meta(src, sidecar=None, title=None, author=None):
    """Title and author: the arguments, then the sidecar .json, then the EPUB's own metadata (first listed author;
    the others are co-authors), then the file name. {'title', 'author', 'coauthors', 'title_source', 'author_source', 'log'}."""
    side = sidecar or {}
    pkg = package(open_source(src))
    picks = {}
    for k, given, from_epub in (("title", title, pkg["title"]), ("author", author, (pkg["authors"] or [""])[0])):
        for value, source in ((given, "argument"), (side.get(k), "sidecar-json"), (from_epub, "epub-metadata")):
            if value:
                picks[k], picks[k + "_source"] = value, source
                break
        else:
            picks[k], picks[k + "_source"] = ((os.path.splitext(os.path.basename(src))[0], "file-name") if k == "title"
                                              else ("Unknown", "none found"))
    picks["coauthors"] = pkg["authors"][1:] if picks["author_source"] == "epub-metadata" else []
    picks["log"] = ["metadata: %s %r from %s" % (k, picks[k], picks[k + "_source"]) for k in ("title", "author")]
    return picks


def ingest(cfg, src, meta, language_decide):
    """Copies the source EPUB into its book folder untouched, reads it and writes book.json, text/chapter-NN.txt and
    text/chapter-NN.json (figure places). meta: {'title', 'author', 'title_source', 'author_source', 'warnings', 'sidecar'}
    or None (taken from the EPUB's metadata). language_decide(book, text) -> lang.decide result.
    Returns the book folder."""
    from pipeline.text import ingest as ingest_mod
    z = open_source(src)
    pkg = package(z)
    notes = []
    chs = chapters(z, pkg, read(z, pkg, notes), notes)
    if not chs:
        raise EpubSourceError("the EPUB has no text")
    place_notes(chs, notes)
    meta = dict(meta or {}) if (meta or {}).get("title_source") else resolve_meta(src, None, (meta or {}).get("title"), (meta or {}).get("author"))
    title, author = meta["title"], meta["author"]
    book_dir = ingest_mod.book_folder(cfg, author, title)
    os.makedirs(book_dir, exist_ok=True)
    copy = os.path.join(book_dir, os.path.basename(src))
    if os.path.exists(copy) and sha256(copy) != sha256(src):
        raise EpubSourceError("a different file with this name is already in the book folder: " + copy)
    if not os.path.exists(copy):
        shutil.copyfile(src, copy)
    book = bookio.read_book(book_dir) if os.path.exists(os.path.join(book_dir, "book.json")) else {}
    language = language_decide(book, book_text(chs))
    cleanup.set_language(language["language"])
    rights = rights_flag(pkg, chs)
    book.update({
        "title": title, "author": author,
        "metadata_sources": {"title": meta["title_source"], "author": meta["author_source"]},
        "source_epub": os.path.basename(src), "source_format": "epub", "epub_version": pkg["version"],
        "sha256": sha256(copy), "language": language["language"], "language_source": language["source"],
        "language_scores": language["scores"], "source_rights": pkg["rights"], "verify_rights": rights,
    })
    book["coauthors"] = meta.get("coauthors") or book.get("coauthors") or []
    for k in ("publisher", "edition", "series"):
        book.setdefault(k, "")
    book.setdefault("summaries", {})
    os.makedirs(os.path.join(book_dir, "text"), exist_ok=True)
    book["chapters"] = []
    for n, c in enumerate(chs, start=1):
        texts, figures = [], []
        for b in c["blocks"]:
            where = "ch%d para %d" % (n, len(texts) + 1)
            if b[0] == "p":
                texts.append(clean(b[1], where, notes))
            elif b[0] == "table":
                body, head = b[1], b[2]
                width = max(len(r) for r in head + body)
                rows = ["| " + " | ".join(r + [""] * (width - len(r))) + " |" for r in head]
                if head:
                    rows.append("| " + " | ".join(["---"] * width) + " |")
                rows += ["| " + " | ".join(r + [""] * (width - len(r))) + " |" for r in body]
                texts.append("\n".join(rows))
            elif b[0] == "note":
                texts.append("[^%s]: %s" % (b[1], clean(b[2], where, notes)))
            elif b[0] == "figure":
                figures.append({"before": len(texts), "src": b[1], "alt": b[2], "caption": b[3]})
        name = "chapter-%02d.txt" % n
        with open(os.path.join(book_dir, "text", name), "w", encoding="utf-8") as f:
            f.write("\n\n".join(texts) + "\n")
        with open(os.path.join(book_dir, "text", name[:-4] + ".json"), "w", encoding="utf-8") as f:
            json.dump({"paragraphs": [{"pdf_page": None, "half": None, "y": None} for _ in texts], "figures": figures}, f)
            f.write("\n")
        body = [t for t in texts if not t.startswith("[^")]
        book["chapters"].append({"number": n, "title": c["title"], "text_file": "text/" + name,
                                 "words": sum(len(t.split()) for t in body), "paragraphs": len(texts)})
    bookio.write_book(book_dir, book)
    bookio.write_warnings(book_dir, "ingest-metadata", [("metadata", "book", line) for line in meta.get("log", [])])
    bookio.write_warnings(book_dir, "ingest-language", [("language", "book", "%s from %s" % (language["language"], language["source"]))])
    bookio.write_warnings(book_dir, "epub-source", notes)
    return book_dir


def license_run(cfg, book_dir, book):
    """License status for a source EPUB: the date rule on the first chapters' text, plus 'verify rights' when the
    rights metadata or the text carries a copyright notice."""
    from pipeline.book import license as license_stage
    units = []
    for c in book["chapters"][:8]:
        with open(os.path.join(book_dir, c["text_file"]), encoding="utf-8") as f:
            units.append({"text": f.read(), "pdf_page": c["number"]})
    found = license_stage.find_year(units)
    if found:
        found = (found[0], found[1].replace("pdf p.", "chapter "), found[2])
    license_stage.record(cfg, book_dir, book, found, book.get("verify_rights"))
    if book.get("verify_rights"):
        print("license: verify rights (%s)" % book["verify_rights"])
        return [("verify-rights", "book", "copyright notice found (%s): verify rights" % book["verify_rights"])]
    return []


def figure_blocks(book_dir, book, chapter):
    """The chapter's illustrations from the source EPUB, encoded to the EPUB standard (JPEG photos, PNG line art)."""
    import io
    from PIL import Image
    from pipeline.book import epub
    with open(os.path.join(book_dir, chapter["text_file"][:-4] + ".json"), encoding="utf-8") as f:
        figs = json.load(f).get("figures", [])
    z = open_source(os.path.join(book_dir, book["source_epub"]))
    out = []
    for i, fg in enumerate(figs, start=1):
        try:
            img = Image.open(io.BytesIO(z.read(fg["src"])))
            img.load()
        except (KeyError, OSError):
            continue
        data, media, ext = epub.encode_figure(img)
        alt = fg["alt"] or fg["caption"] or "Illustration"
        out.append(({"type": "figure", "name": "images/ch%02d-fig%02d.%s" % (chapter["number"], i, ext), "data": data,
                     "media": media, "caption": fg["caption"], "alt": alt}, fg["before"]))
    return out


def cover_image(book_dir, book):
    """The source's declared cover image (PIL), or None."""
    import io
    from PIL import Image
    z = open_source(os.path.join(book_dir, book["source_epub"]))
    pkg = package(z)
    if not pkg["cover"]:
        return None
    try:
        img = Image.open(io.BytesIO(z.read(pkg["cover"])))
        img.load()
        return img
    except (KeyError, OSError):
        return None
