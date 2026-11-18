"""Word lists for the three supported source languages. One measure, used for language choice and for
the page quality check: the share of a text's words found in a language's dictionary.
The lists are the Debian packages wamerican-large, wngerman and wspanish (see ci/install.sh), the same lists the stage 1
measurements used (tests/stage1/dicts.py)."""
import os
import re
import unicodedata
from functools import lru_cache

LANGUAGES = ("eng", "deu", "spa")
FILES = {"eng": "american-english-large", "deu": "ngerman", "spa": "spanish"}
TESSERACT_PACKS = {"eng": "tesseract-ocr-eng", "deu": "tesseract-ocr-deu", "spa": "tesseract-ocr-spa"}
WORD_RE = re.compile(r"[^\W\d_]+(?:['\u2019][^\W\d_]+)?")


def dict_dir():
    return os.environ.get("AUDIOBOOK_DICT_DIR") or os.environ.get("STAGE1_DICT_DIR") or "/usr/share/dict"


@lru_cache(None)
def _words(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return frozenset(unicodedata.normalize("NFC", w.strip()).lower() for w in f if w.strip())


def words(lang):
    if lang not in FILES:
        raise ValueError("unsupported language %r (supported: %s)" % (lang, ", ".join(LANGUAGES)))
    path = os.path.join(dict_dir(), FILES[lang])
    if not os.path.exists(path):
        raise FileNotFoundError("word list for %s not found at %s (install %s, or set AUDIOBOOK_DICT_DIR)"
                                % (lang, path, {"eng": "wamerican-large", "deu": "wngerman", "spa": "wspanish"}[lang]))
    return _words(path)


def tokens(text):
    return [t.lower().replace("\u2019", "'") for t in WORD_RE.findall(unicodedata.normalize("NFC", text))]


def counted(text):
    """The tokens the share is taken over: words of two letters or more."""
    return [t for t in tokens(text) if len(t) > 1]


def share(text, lang):
    """Fraction of the text's words (two letters or more) found in the language's dictionary. 0.0 for no words.
    A word with an apostrophe also counts when the part before it is a word ("author's")."""
    toks = counted(text)
    if not toks:
        return 0.0
    d = words(lang)
    return sum(t in d or t.split("'")[0] in d for t in toks) / len(toks)


def shares(text):
    return {lang: share(text, lang) for lang in LANGUAGES}
