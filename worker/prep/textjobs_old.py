"""The text model's written jobs, on a textmodel.Session. Every answer goes through spoken.py's code
checks; an answer that fails them is not used and the failure is returned for warnings.log.

  term list     a running list of names and terms, built chunk by chunk. Used only when a chapter takes more than one
                chunk or the book is translated. Summaries use it too.
  summaries     chapter_preview (2 sentences), concept_summary (2 sentences, first half only), chapter_summary
                (3 sentences). A chapter bigger than one chunk is summarized part by part, then from the parts'
                summaries.
  table_takeaway, figure_sentence, book_description: one request each.
  translation   chunk by chunk into English, keeping paragraphs.
"""
import re

from pipeline.text import chunks
from pipeline.voice import spoken
from pipeline.text import textmodel

ANSWER_MARGIN = 500        # tokens kept free beyond the instructions, the term list and the answer limit


class TranslationError(Exception):
    kind = "permanent"


def terms_block(terms):
    if not terms:
        return ""
    return "Term list:\n" + "\n".join("%s = %s" % (k, v) for k, v in terms.items()) + "\n\n"


def parse_terms(out, source, old):
    """The model's 'term = spelling' lines: old entries kept unchanged, a new term kept only if the source text
    contains it (code check: no names the source lacks)."""
    terms = dict(old)
    if out.strip().upper() == "NONE":
        return terms
    for line in out.splitlines():
        m = re.match(r"\s*(?:[-*]\s*)?(.+?)\s*=\s*(.+?)\s*$", line)
        if not m:
            continue
        k, v = m.group(1), m.group(2)
        if k in terms or k not in source or v not in source:
            continue
        terms[k] = v
    return terms


def _reserve(cfg, job, terms, answer_tokens=None):
    e = textmodel.entry(cfg, job)
    system, _ = textmodel.instructions(job)
    answer = answer_tokens if answer_tokens is not None else int(e.get("max_tokens") or 0)
    return e["context"], chunks.tokens(system) + chunks.tokens(terms_block(terms)) + 60 * 20 + answer + ANSWER_MARGIN


def chapter_chunks(cfg, job, paragraphs, terms=None):
    ctx, reserve = _reserve(cfg, job, terms)
    return chunks.split(paragraphs, chunks.budget(ctx, reserve))


def build_terms(ses, paragraphs, terms=None):
    """The running term list over the given text, chunk by chunk."""
    terms = dict(terms or {})
    for ch in chapter_chunks(ses.cfg, "term_list", paragraphs, terms):
        text = "\n\n".join(ch)
        user, _ = textmodel.user_text("term_list", text=text, terms=terms_block(terms))
        terms = parse_terms(ses.ask("term_list", user)["output"], text, terms)
    return terms


def _checked(job, out, source, terms, where, notes):
    c = spoken.check(job, out, source, allowed=list((terms or {}).values()))
    if not c["pass"]:
        notes.append(("model-check-failed", where, "%s answer not used (failed: %s%s): %s" % (
            job, ", ".join(c["failed"]), "; new names " + ", ".join(c["new_names"]) if c["new_names"] else "", out[:200])))
        return None
    return out


def summary(ses, job, paragraphs, where, notes, terms=None, translated=False):
    """(text or None, terms). Multi-pass when the text takes more than one chunk."""
    parts = chapter_chunks(ses.cfg, job, paragraphs, terms)
    source = "\n\n".join(paragraphs)
    if len(parts) > 1 or translated:
        terms = build_terms(ses, paragraphs, terms)
    use_terms = terms if (len(parts) > 1 or translated) else None
    if len(parts) == 1:
        user, _ = textmodel.user_text(job, text=source, terms=terms_block(use_terms))
        return _checked(job, ses.ask(job, user)["output"], source, use_terms, where, notes), terms
    part_summaries = []
    for i, ch in enumerate(parts, 1):
        text = "\n\n".join(ch)
        user, _ = textmodel.user_text(job, text=text, terms=terms_block(use_terms))
        out = _checked(job, ses.ask(job, user)["output"], text, use_terms, "%s part %d" % (where, i), notes)
        if out is None:
            return None, terms
        part_summaries.append(out)
    user, _ = textmodel.user_text(job, template="chapter_summary_parts", text="\n\n".join(part_summaries),
                                  terms=terms_block(use_terms))
    return _checked(job, ses.ask(job, user)["output"], source, use_terms, where, notes), terms


def table_takeaway(ses, table, where, notes):
    user, _ = textmodel.user_text("table_takeaway", text=table)
    return _checked("table_takeaway", ses.ask("table_takeaway", user)["output"], table, None, where, notes)


def figure_sentence(ses, image, mime, caption, nearby, where, notes):
    user, _ = textmodel.user_text("figure_sentence", caption=caption or "(none)", text=nearby)
    out = ses.ask("figure_sentence", user, images=[(image, mime)])["output"]
    return _checked("figure_sentence", out, (caption or "") + "\n" + nearby, None, where, notes)


def book_description(ses, book, chapter_summaries, notes):
    text = "\n\n".join(chapter_summaries)
    user, _ = textmodel.user_text("book_description", title=book["title"], author=book["author"], text=text)
    out = ses.ask("book_description", user)["output"]
    return _checked("book_description", out, text + "\n" + book["title"] + "\n" + book["author"], None, "book", notes)


def translate(ses, paragraphs, language, terms, where, notes):
    """English paragraphs, one for each source paragraph. A chunk whose answer loses or adds paragraphs is done again
    one paragraph per request; a single paragraph's answer is joined into one paragraph."""
    e = textmodel.entry(ses.cfg, "translation")
    system, _ = textmodel.instructions("translation")
    room = chunks.budget(e["context"], chunks.tokens(system) + chunks.tokens(terms_block(terms)) + ANSWER_MARGIN)
    limit = room // 2                       # the answer is about as long as the text: half the room each
    out = []
    for i, ch in enumerate(chunks.split(paragraphs, limit), 1):
        got = _translate_chunk(ses, ch, language, terms, limit, room - limit)
        c = spoken.check_translation("\n\n".join(got), "\n\n".join(ch))
        if not c["pass"]:
            notes.append(("translation-paragraphs", "%s chunk %d" % (where, i),
                          "answer had %d paragraphs for %d; translated again one paragraph per request" % (
                              c["paragraphs_out"], c["paragraphs_in"])))
            got = []
            for p in ch:
                one = _translate_chunk(ses, [p], language, terms, limit, room - limit)
                got.append(" ".join(one))
        out += got
    if len(out) != len(paragraphs):
        raise TranslationError("%s: %d English paragraphs for %d source paragraphs" % (where, len(out), len(paragraphs)))
    return out


def _translate_chunk(ses, ch, language, terms, limit, answer_tokens):
    user, _ = textmodel.user_text("translation", text="\n\n".join(ch), language=language, terms=terms_block(terms))
    return spoken.paragraphs(ses.ask("translation", user, max_tokens=answer_tokens)["output"])
