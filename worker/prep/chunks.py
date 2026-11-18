"""Chunking: the chunk size follows the model's context window, with a floor of 16,000 tokens.

A chunk is a run of whole paragraphs; a paragraph too big for one chunk is split at sentence ends (and a sentence too
big at word breaks). Token counts are estimated from characters with a fixed rate, so the same window size always
gives the same chunks, whatever model is behind it.

CHARS_PER_TOKEN, measured on the stage 1 test set (tests/stage1/results/jobs.csv prompt_tokens against the characters
sent, 2026-10-31): English book prose (8 chapter samples) 4.20 to 4.76 characters per token, the Markdown table sample 2.29.
3.0 is used: below every prose sample by 29 percent or more, so a chunk is never larger than estimated for prose.
"""
import math

from pipeline.voice import spoken
from pipeline.text import textmodel

CHARS_PER_TOKEN = 3.0


def tokens(text):
    return int(math.ceil(len(text) / CHARS_PER_TOKEN))


def budget(context, reserve_tokens):
    """Input tokens a chunk may use: the window (never below the floor) minus what the instructions, the term list
    and the answer need."""
    ctx = max(int(context), textmodel.CONTEXT_FLOOR)
    room = ctx - int(reserve_tokens)
    if room < 1000:
        raise ValueError("a %d-token window leaves %d tokens for text after %d reserved" % (ctx, room, reserve_tokens))
    return room


def _pieces(paragraph, limit):
    """A paragraph too big for one chunk: sentence runs, each at most `limit` tokens."""
    out, cur = [], ""
    units = []
    for s in spoken.sentences(paragraph):
        if tokens(s) <= limit:
            units.append(s)
            continue
        words, w = s.split(" "), ""
        for x in words:
            if w and tokens(w + " " + x) > limit:
                units.append(w)
                w = x
            else:
                w = (w + " " + x).strip()
        if w:
            units.append(w)
    for u in units:
        if cur and tokens(cur + " " + u) > limit:
            out.append(cur)
            cur = u
        else:
            cur = (cur + " " + u).strip()
    if cur:
        out.append(cur)
    return out


def split(paragraphs, limit):
    """Chunks: lists of paragraph strings, each chunk's joined text ('\\n\\n') at most `limit` tokens. Paragraph
    order is kept; a split paragraph's pieces stay together in order."""
    chunks, cur, size = [], [], 0
    sep = tokens("\n\n")
    for p in paragraphs:
        parts = [p] if tokens(p) <= limit else _pieces(p, limit)
        for part in parts:
            t = tokens(part)
            if cur and size + sep + t > limit:
                chunks.append(cur)
                cur, size = [], 0
            size += (sep if cur else 0) + t
            cur.append(part)
    if cur:
        chunks.append(cur)
    return chunks
