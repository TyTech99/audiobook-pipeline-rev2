"""Source language of a book. Decided by code before tesseract reads any page. The first rule that gives an
answer wins, and the answer records its source and scores:

  1. setting      Ty's setting: "language" in the PDF's sidecar file, or "language_setting" in the book's book.json.
  2. text-layer   the language with the highest share of words found in its dictionary, when the text layer has enough
                  words and the winner is clear (share and lead thresholds in settings.yaml, block language:).
  3. sample-read  a fully scanned book: tesseract reads three body pages once with each language pack; the language with
                  the best mean word confidence must also have the best dictionary share.
  4. otherwise    the run stops and asks Ty to set the language."""
import re
import subprocess

from pipeline.text import dicts

NAMES = {"eng": "English", "deu": "German", "spa": "Spanish"}
ALIASES = {"en": "eng", "english": "eng", "de": "deu", "ger": "deu", "german": "deu", "deutsch": "deu",
           "es": "spa", "spanish": "spa", "espanol": "spa", "español": "spa"}


class LanguageError(Exception):
    """No language could be decided, or the language is not supported. A permanent failure: Ty must set it."""
    kind = "permanent"


def normalize(value):
    v = str(value).strip().lower()
    v = ALIASES.get(v, v)
    if v not in dicts.LANGUAGES:
        raise LanguageError("language %r is not supported (supported: English eng, German deu, Spanish spa)" % value)
    return v


def defaults(cfg):
    c = cfg.get("language") or {}
    return {"text_min_words": int(c.get("text_min_words", 200)), "text_min_share": float(c.get("text_min_share", 0.50)),
            "text_min_lead": float(c.get("text_min_lead", 0.30)), "sample_pages": int(c.get("sample_pages", 3)),
            "sample_min_share": float(c.get("sample_min_share", 0.50)), "dpi": int(c.get("sample_dpi", 300))}


def from_text(text, d):
    """(language or None, scores) by rule 2."""
    n = len(dicts.counted(text))
    scores = {k: round(v, 3) for k, v in dicts.shares(text).items()} if n else {}
    if n < d["text_min_words"]:
        return None, {"words": n, "shares": scores, "why": "fewer than %d words in the text layer" % d["text_min_words"]}
    ranked = sorted(scores, key=lambda k: -scores[k])
    best, second = ranked[0], ranked[1]
    lead = round(scores[best] - scores[second], 3)
    info = {"words": n, "shares": scores, "lead": lead}
    if scores[best] >= d["text_min_share"] and lead >= d["text_min_lead"]:
        return best, info
    info["why"] = "no clear winner (best %s %.3f, lead %.3f)" % (best, scores[best], lead)
    return None, info


def tesseract_page(pdf, page, lang, dpi):
    """(mean word confidence, text) of one page read by tesseract with one language pack."""
    png = subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(dpi), "-gray", "-png", pdf],
                         check=True, capture_output=True).stdout
    tsv = subprocess.run(["tesseract", "stdin", "stdout", "-l", lang, "--psm", "3", "tsv"], input=png,
                         check=True, capture_output=True).stdout.decode("utf-8", "replace")
    confs, words = [], []
    for row in tsv.splitlines()[1:]:
        f = row.split("\t")
        if len(f) == 12 and f[11].strip() and float(f[10]) >= 0:
            confs.append(float(f[10]))
            words.append(f[11])
    return (sum(confs) / len(confs) if confs else 0.0), " ".join(words)


def body_pages(n_pages, k):
    """k pages from the middle of the book (front and back matter avoided): evenly spaced over the middle 60%."""
    if n_pages <= k:
        return list(range(1, n_pages + 1))
    lo, hi = max(1, round(n_pages * 0.2)), min(n_pages, round(n_pages * 0.8))
    if hi - lo + 1 < k:
        lo, hi = 1, n_pages
    return sorted({lo + round(i * (hi - lo) / (k - 1)) for i in range(k)}) if k > 1 else [(lo + hi) // 2]


def from_sample(pdf, n_pages, d, read=tesseract_page):
    """(language or None, scores) by rule 3."""
    pages = body_pages(n_pages, d["sample_pages"])
    scores = {}
    for lang in dicts.LANGUAGES:
        confs, text = [], []
        for p in pages:
            c, t = read(pdf, p, lang, d["dpi"])
            confs.append(c)
            text.append(t)
        joined = "\n".join(text)
        scores[lang] = {"conf": round(sum(confs) / len(confs), 2), "share": round(dicts.share(joined, lang), 3)}
    by_conf = max(scores, key=lambda k: scores[k]["conf"])
    by_share = max(scores, key=lambda k: scores[k]["share"])
    info = {"pages": pages, "scores": scores}
    if by_conf == by_share and scores[by_conf]["share"] >= d["sample_min_share"]:
        return by_conf, info
    info["why"] = "confidence picks %s, dictionary share picks %s (best share %.3f)" % (
        by_conf, by_share, scores[by_share]["share"])
    return None, info


def decide(cfg, pdf, sidecar=None, book=None, text=None, n_pages=None, read=tesseract_page, text_source="text-layer"):
    """{'language', 'source', 'scores'} for the book, or LanguageError. text/n_pages default to the PDF's own."""
    d = defaults(cfg)
    for where, value in (("sidecar", (sidecar or {}).get("language")), ("book.json", (book or {}).get("language_setting"))):
        if value:
            return {"language": normalize(value), "source": "setting (%s)" % where, "scores": {}}
    if text is None:
        text = subprocess.run(["pdftotext", pdf, "-"], check=True, capture_output=True, text=True).stdout
    lang, info = from_text(text, d)
    if lang:
        return {"language": lang, "source": text_source, "scores": info}
    tried = {"text-layer": info}
    if info["words"] < d["text_min_words"] and read is not None and pdf:       # a source EPUB has no page to sample
        if n_pages is None:
            out = subprocess.run(["pdfinfo", pdf], check=True, capture_output=True, text=True).stdout
            n_pages = int(re.search(r"^Pages:\s+(\d+)", out, re.M).group(1))
        lang, sinfo = from_sample(pdf, n_pages, d, read)
        if lang:
            return {"language": lang, "source": "sample-read", "scores": sinfo}
        tried["sample-read"] = sinfo
    raise LanguageError("the source language could not be decided (%s). Set it: add \"language\": \"eng\", \"deu\" or "
                        "\"spa\" to the PDF's sidecar .json, or \"language_setting\" to the book's book.json. Scores: %s"
                        % ("; ".join("%s: %s" % (k, v.get("why", "")) for k, v in tried.items()), tried))


def log_line(result):
    return "language: %s (%s) from %s; scores %s" % (result["language"], NAMES[result["language"]], result["source"],
                                                     result["scores"] or "none (set by Ty)")
