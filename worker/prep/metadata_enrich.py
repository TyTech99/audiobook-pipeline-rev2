"""Metadata enrichment: ISBN scanning and lookup."""

from __future__ import annotations

import re

import httpx

OPEN_LIBRARY_SEARCH = "https://openlibrary.org/search.json"

ISBN10_RE = re.compile(r"\b(\d{9}[\dXx])\b")
ISBN13_RE = re.compile(r"\b(97[89]\d{10})\b")
ISBN_HYPHENATED_RE = re.compile(r"\b(97[89][- ]?\d{1,5}[- ]?\d{1,7}[- ]?\d{1,7}[- ]?[\dXx])\b")
ISBN_CONTEXT_RE = re.compile(r"(?:isbn[-\s]?(?:1[03])?)[:\s]*([0-9Xx][-0-9Xx ]{6,17})", re.IGNORECASE)


def find_isbn_in_text(text: str) -> str | None:
    """Scan text for an ISBN-13 or ISBN-10. Returns canonical form (no hyphens)."""
    for match in ISBN_CONTEXT_RE.finditer(text):
        candidate = match.group(1).strip()
        if _is_valid_isbn(candidate):
            return candidate.replace("-", "").replace(" ", "")
    for match in ISBN13_RE.finditer(text):
        candidate = match.group(1)
        if _is_valid_isbn(candidate):
            return candidate
    for match in ISBN_HYPHENATED_RE.finditer(text):
        candidate = match.group(1)
        if _is_valid_isbn(candidate):
            return candidate.replace("-", "").replace(" ", "")
    for match in ISBN10_RE.finditer(text):
        candidate = match.group(1)
        if _is_valid_isbn(candidate):
            return candidate
    return None


def _is_valid_isbn(candidate: str) -> bool:
    digits = candidate.replace("-", "").replace(" ", "")
    if len(digits) == 13 and digits.isdigit():
        return _isbn13_check(digits)
    if len(digits) == 10 and digits[:9].isdigit() and digits[9] in "0123456789Xx":
        return _isbn10_check(digits)
    return False


def _isbn13_check(digits: str) -> bool:
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits))
    return total % 10 == 0


def _isbn10_check(digits: str) -> bool:
    total = sum((10 - i) * (10 if d in "Xx" else int(d)) for i, d in enumerate(digits))
    return total % 11 == 0


def scan_pages_for_isbn(pages: list[dict], max_pages: int = 15) -> str | None:
    """Scan the first N pages of text for an ISBN."""
    for page in pages[:max_pages]:
        text = page.get("text", "")
        isbn = find_isbn_in_text(text)
        if isbn:
            return isbn
    return None


def lookup_isbn(isbn: str) -> dict | None:
    """Best-effort ISBN lookup via isbnlib if available."""
    try:
        import isbnlib

        cleaned = isbnlib.canonical(isbn)
        if not cleaned:
            return None
        data = isbnlib.meta(cleaned)
        if not data:
            return None
        return {
            "title": data.get("title") or "",
            "author": data.get("author") or "",
            "year": str(data.get("year") or ""),
            "publisher": data.get("publisher") or "",
        }
    except Exception:
        return None


def search_open_library(title: str, author: str, http: httpx.Client | None = None) -> dict | None:
    """Fallback metadata search by title+author via Open Library."""
    client = http or httpx.Client(timeout=15)
    try:
        response = client.get(
            OPEN_LIBRARY_SEARCH,
            params={"title": title, "author": author, "limit": 1},
        )
        response.raise_for_status()
        docs = response.json().get("docs", [])
        if not docs:
            return None
        doc = docs[0]
        first_publish_year = doc.get("first_publish_year") or (doc.get("first_publish_year") or [None])[0] if isinstance(doc.get("first_publish_year"), list) else doc.get("first_publish_year")
        return {
            "title": doc.get("title") or "",
            "author": (doc.get("author_name") or [""])[0],
            "year": str(first_publish_year or ""),
            "publisher": "",
        }
    except Exception:
        return None


def enrich_metadata(
    metadata: dict,
    pages: list[dict],
    http: httpx.Client | None = None,
) -> dict:
    """Enrich metadata with ISBN and lookup data without wiping good fields."""
    result = dict(metadata)
    isbn = result.get("isbn") or scan_pages_for_isbn(pages)
    if isbn:
        result["isbn"] = isbn
        lookup = lookup_isbn(isbn)
        if lookup:
            for key in ("title", "author", "year", "publisher"):
                if lookup.get(key) and not result.get(key):
                    result[key] = lookup[key]
    else:
        title = result.get("title") or ""
        author = result.get("author") or ""
        if title and author and title != "Unknown Title" and author != "Unknown Author":
            lookup = search_open_library(title, author, http=http)
            if lookup:
                for key in ("title", "author", "year", "publisher"):
                    if lookup.get(key) and not result.get(key):
                        result[key] = lookup[key]
    return result
