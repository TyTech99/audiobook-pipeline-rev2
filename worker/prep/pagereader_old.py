"""Page reading in three tiers, cheapest first. Quality is the first gate.

  1. text-layer   the PDF's own text, when the page has enough characters and its dictionary share in the book's
                  language reaches the text-layer pass mark. CPU only.
  2. tesseract    the page image read with the book's language pack, when its mean word confidence and its dictionary
                  share reach the tesseract pass marks. CPU only.
  3. vision       pages that fail both go to the vision model (textmodel job page_reading, same language hint), which
                  answers in Markdown. The model is loaded only when a page needs it.

Every page gets the same check: its dictionary share in the source language (pages with fewer than check_min_words words
are judged by their character count, and for tesseract by its confidence, alone). The pass marks are in settings.yaml,
block pages:, and come from measurements (see the comments there).

Output: pages.json in the book folder. One record per page with the tier that read it and its scores, plus the text
lines with their position in points for every page not read from the text layer. pdftext.units reads those lines in
place of the text layer, so every later stage works the same whichever tier read a page."""
import json
import os
import re
import subprocess

from pipeline.text import dicts
from pipeline.text import lang as lang_mod
from pipeline.text import pdftext
from pipeline.text import textmodel

PAGES_NAME = "pages.json"
FORMAT = 1


def gate(cfg):
    c = cfg.get("pages") or {}
    return {"min_chars": int(c.get("min_chars", (cfg.get("ocr") or {}).get("min_chars_per_page", 10))),
            "check_min_words": int(c.get("check_min_words", 20)),
            "text_layer_min_share": float(c.get("text_layer_min_share", 0.93)),
            "tesseract_min_conf": float(c.get("tesseract_min_conf", 92)),
            "tesseract_min_share": float(c.get("tesseract_min_share", 0.97)),
            "vision_min_share": float(c.get("vision_min_share", 0.93)),
            "dpi": int(c.get("dpi", 300))}


def judge(text, language, min_share, g, conf=None, min_conf=None):
    """{'chars', 'words', 'share', 'conf', 'passed'} for one page's text."""
    chars = len(re.sub(r"\s", "", text))
    words = len(dicts.counted(text))
    share = round(dicts.share(text, language), 3) if words else 0.0
    ok = chars >= g["min_chars"] and (words < g["check_min_words"] or share >= min_share)
    if min_conf is not None:
        ok = ok and conf is not None and conf >= min_conf
    return {"chars": chars, "words": words, "share": share, "conf": None if conf is None else round(conf, 1), "passed": ok}


def render(pdf, page, dpi):
    return subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(dpi), "-gray", "-png", "-singlefile", pdf],
                          check=True, capture_output=True).stdout


def tesseract(png, language, dpi):
    """(lines with positions in points, mean word confidence, text) from tesseract's TSV output."""
    tsv = subprocess.run(["tesseract", "stdin", "stdout", "-l", language, "--psm", "3", "tsv"], input=png,
                         check=True, capture_output=True).stdout.decode("utf-8", "replace")
    scale = 72.0 / dpi
    lines, order, confs = {}, [], []
    for row in tsv.splitlines()[1:]:
        f = row.split("\t")
        if len(f) != 12:
            continue
        level, key = f[0], tuple(f[1:5])
        left, top, w, h = (int(v) for v in f[6:10])
        if level == "4":
            lines[key] = {"x": left * scale, "y": top * scale, "x2": (left + w) * scale, "y2": (top + h) * scale, "words": []}
            order.append(key)
        elif level == "5" and f[11].strip() and float(f[10]) >= 0 and key in lines:
            lines[key]["words"].append(f[11])
            confs.append(float(f[10]))
    out = []
    for k in order:
        ln = lines[k]
        if ln["words"]:
            out.append({"x": round(ln["x"], 2), "y": round(ln["y"], 2), "x2": round(ln["x2"], 2), "y2": round(ln["y2"], 2),
                        "text": " ".join(ln["words"])})
    out.sort(key=lambda ln: (ln["y"], ln["x"]))
    return out, (sum(confs) / len(confs) if confs else None), "\n".join(ln["text"] for ln in out)


MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
MD_EMPHASIS = (re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1"),                 # pairs only: a lone * or _ is the author's
               re.compile(r"(?<![\w*])(\*)(?=\S)(.+?)(?<=\S)\*(?![\w*])"),
               re.compile(r"(?<!\w)(_)(?=\S)(.+?)(?<=\S)_(?!\w)"))
MD_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def markdown_lines(md, width, height):
    """Text lines with made-up positions for a Markdown page from the vision model. The model's paragraphs (blank-line
    separated) become paragraphs: lines of one paragraph sit at the normal spacing, a new paragraph sits one blank line
    lower, which is how clean-up recognises a paragraph start. Heading marks and emphasis marks are dropped; table rows
    and footnote marks are kept for the EPUB builder."""
    pitch, left = 12.0, 72.0
    y, out, gap = 36.0, [], False
    for raw in md.replace("\r", "").split("\n"):
        line = raw.strip()
        if line.startswith("```"):
            continue
        if not line:
            gap = True
            continue
        if MD_TABLE_RULE.match(line) and not line.startswith("|"):
            continue                       # a horizontal rule; a table's header rule is kept for the EPUB builder
        line = MD_HEADING.sub("", line)
        if not line.startswith("|"):
            for rx in MD_EMPHASIS:
                line = rx.sub(r"\2", line)
        y += pitch * (2 if gap and out else 1)
        gap = False
        out.append({"x": left, "y": round(y, 2), "x2": round(min(width - left, left + 6 * len(line)), 2), "y2": round(y + 10, 2),
                    "text": line})
    return out


def language_name(code):
    return lang_mod.NAMES[code]


def load(book_dir):
    p = os.path.join(book_dir, PAGES_NAME)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def run(cfg, book_dir, book, pdf, session=None, enabled=True):
    """Reads every page through the tiers and writes pages.json. Returns warnings.
    session: a textmodel.Session, or a callable that makes one (only called if a page reaches tier 3).
    enabled False (setting ocr.enabled): tiers 2 and 3 are off; failing pages keep their text layer and are logged."""
    g = gate(cfg)
    language = book["language"]
    old = load(book_dir)
    sig = {"format": FORMAT, "sha256": book["sha256"], "language": language, "gate": g, "enabled": enabled}
    if old and old.get("signature") == sig:
        print("pages: read before with the same PDF, language and pass marks (pages.json); not read again")
        return [tuple(n) for n in old.get("warnings", [])]
    geo = pdftext.page_geometry(pdf)
    records, lines, notes = [], {}, []
    model = None
    counts = {"text-layer": 0, "tesseract": 0, "vision": 0}
    for p, (w, h) in enumerate(geo, start=1):
        where = "pdf p.%d" % p
        text = subprocess.run(["pdftotext", "-f", str(p), "-l", str(p), pdf, "-"], check=True, capture_output=True,
                              text=True).stdout
        tl = judge(text, language, g["text_layer_min_share"], g)
        rec = {"page": p, "text_layer": tl}
        if tl["passed"] or not enabled:
            rec["tier"] = "text-layer"
            rec["passed"] = tl["passed"]
            if not tl["passed"]:
                notes.append(("page-quality-low", where, "text layer fails the check (%s) and page reading tiers 2 and 3 "
                              "are off (ocr.enabled false)" % _scores(tl)))
            records.append(rec)
            counts["text-layer"] += 1
            continue
        png = render(pdf, p, g["dpi"])
        t_lines, conf, t_text = tesseract(png, language, g["dpi"])
        ts = judge(t_text, language, g["tesseract_min_share"], g, conf, g["tesseract_min_conf"])
        rec["tesseract"] = ts
        if ts["passed"]:
            rec.update(tier="tesseract", passed=True)
            lines[str(p)] = t_lines
            records.append(rec)
            counts["tesseract"] += 1
            notes.append(("page-tesseract", where, "text layer failed (%s); tesseract passed (%s)" % (_scores(tl), _scores(ts))))
            continue
        if model is None:
            model = session() if callable(session) else session
            if model is None:
                raise RuntimeError("page %d needs the vision model but no model session was given" % p)
        user, uv = _user_text(language)
        try:
            # allow_empty: a blank or picture-only page rightly transcribes to nothing; the page check then flags it
            # (page-quality-low) instead of the whole run failing on a correct answer.
            r = model.ask("page_reading", user, images=[(png, "image/png")], extra_inputs={"user_template": uv},
                          allow_empty=True)
        except textmodel.ModelError as e:
            raise textmodel.ModelError("pdf p.%d: %s" % (p, e))
        v_lines = markdown_lines(r["output"], w, h)
        vs = judge("\n".join(ln["text"] for ln in v_lines), language, g["vision_min_share"], g)
        rec["vision"] = dict(vs, output=os.path.relpath(_out(book_dir, r), book_dir), model=r["model"], mode=r["mode"])
        counts["vision"] += 1
        if vs["passed"]:
            rec.update(tier="vision", passed=True)
            lines[str(p)] = v_lines
            notes.append(("page-vision", where, "text layer (%s) and tesseract (%s) failed; vision model passed (%s)"
                          % (_scores(tl), _scores(ts), _scores(vs))))
        else:
            # no tier passed: keep the reading with the best dictionary share, and say so
            best = max((("text-layer", tl), ("tesseract", ts), ("vision", vs)), key=lambda kv: kv[1]["share"])
            rec.update(tier=best[0], passed=False)
            if best[0] == "tesseract":
                lines[str(p)] = t_lines
            elif best[0] == "vision":
                lines[str(p)] = v_lines
            notes.append(("page-quality-low", where, "no tier passed the check (text layer %s; tesseract %s; vision %s); "
                          "kept the %s reading" % (_scores(tl), _scores(ts), _scores(vs), best[0])))
        records.append(rec)
    out = {"signature": sig, "pdf": os.path.basename(pdf), "summary": counts, "pages": records, "lines": lines,
           "warnings": [list(n) for n in notes]}
    with open(os.path.join(book_dir, PAGES_NAME), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print("pages: %d read from the text layer, %d by tesseract, %d sent to the vision model; %d failed every check"
          % (counts["text-layer"], counts["tesseract"], counts["vision"], sum(not r["passed"] for r in records)))
    return notes


def _scores(s):
    parts = ["%d chars" % s["chars"], "%d words" % s["words"], "dictionary share %.3f" % s["share"]]
    if s.get("conf") is not None:
        parts.append("confidence %.1f" % s["conf"])
    return ", ".join(parts)


def _user_text(language):
    from pipeline.text import textmodel
    return textmodel.user_text("page_reading", language=language_name(language))


def _out(book_dir, r):
    from pipeline.text import textmodel
    return textmodel.output_path(book_dir, r["job"], r["key"])
