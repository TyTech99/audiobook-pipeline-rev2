"""Archive.org ABBYY XML ingestion (#81, phase 1).

The Internet Archive publishes the ABBYY FineReader XML derivative for every
scanned item (``{identifier}_abbyy.gz``). It is the upstream source of the
PDF's hidden text layer, and it carries what the layer alone does not: the
recognizing engine's per-character confidence, suspicious flags, word
classifications (dictionary / numeric / identifier), and typography.

This module fetches and verifies it, streams it into per-page word records,
and annotates declared unknowns with the engine's own self-assessment.

Deterministic data only — no model calls, no OCR, no text changes. The
engine's flags are a signal, never proof (the 'almos' lesson: engine
confidence is not ground truth); they annotate the declaration and sharpen
the class split between engine-uncertain damage and engine-confident
author words (names, coinages, foreign terms).
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import os
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

ARCHIVE_METADATA_URL = "https://archive.org/metadata/{identifier}"
ARCHIVE_ABBYY_URL = "https://archive.org/download/{identifier}/{identifier}_abbyy.gz"

# Words at or below this mean charConfidence count as engine-flagged. The
# threshold mirrors the gate's own confidence scale (conf < 85 flags a page);
# 60 catches the 'SPEAJRMAN' class (mean 54, one char at 30) while leaving
# confidently-read names and coinages on the unflagged side of the split.
FLAG_CONFIDENCE = 60


def fetch_abbyy_xml(identifier: str, cache_path: str, http=None, pdf_sha1: str | None = None) -> dict:
    """Download ``{identifier}_abbyy.gz``, verify it, and gunzip to cache_path.

    Every lock from the #81 verification is enforced: the downloaded XML's
    sha1 must match the item manifest, and — when the caller passes the job
    input's sha1 — the manifest's PDF sha1 must match it byte-for-byte,
    proving the XML is the derivative of THIS file. Any mismatch raises so
    the caller can skip ingestion rather than consume an unverified source.
    """
    import httpx

    client = http or httpx.Client(timeout=120)
    manifest_response = client.get(
        ARCHIVE_METADATA_URL.format(identifier=identifier), follow_redirects=True
    )
    manifest_response.raise_for_status()
    files = manifest_response.json().get("files", [])
    abbyy_entry = next((f for f in files if f.get("name", "").endswith("_abbyy.gz")), None)
    if abbyy_entry is None:
        raise FileNotFoundError(f"no _abbyy.gz derivative for item {identifier!r}")
    if pdf_sha1 is not None:
        pdf_entry = next((f for f in files if f.get("name", "").endswith(".pdf")), None)
        if pdf_entry and pdf_entry.get("sha1") and pdf_entry["sha1"].lower() != pdf_sha1.lower():
            raise ValueError(
                "manifest PDF sha1 does not match the job input - the item was "
                "re-derived since this file was captured; refusing unverified XML"
            )
    download_response = client.get(
        ARCHIVE_ABBYY_URL.format(identifier=identifier), follow_redirects=True
    )
    download_response.raise_for_status()
    data = download_response.content
    got = hashlib.sha1(data).hexdigest()
    want = (abbyy_entry.get("sha1") or "").lower()
    if want and got.lower() != want:
        raise ValueError(f"abbyy.gz sha1 mismatch: manifest {want[:12]} vs downloaded {got[:12]}")
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    with open(cache_path, "wb") as fh:
        fh.write(gzip.decompress(data))
    logger.info(
        "ABBYY XML fetched and sha1-verified for item %s (%d bytes gz)", identifier, len(data)
    )
    return abbyy_entry


def parse_abbyy_words(xml_path: str) -> dict[int, list[dict]]:
    """Stream the ABBYY XML into per-page word records.

    The schema stores one ``charParams`` element per character; words are
    ``wordStart="true"`` delimited and NO space characters are stored. Each
    record: ``{text, confidence (mean char), min_confidence, suspicious,
    from_dictionary, numeric, identifier, l, t, r, b}`` — coordinates in the
    original scan space (600dpi for this item class).

    Memory stays bounded: page subtrees are cleared as they close (the
    uncompressed XML for a full book is ~200MB; the word records are ~40MB).
    """
    pages: dict[int, list[dict]] = {}
    page_no = 0
    current: list[tuple[dict, str]] = []

    def flush_word() -> None:
        nonlocal current
        if not current:
            return
        text = "".join(ch for _, ch in current).strip()
        if text:
            confs = [int(a.get("charConfidence", 100)) for a, _ in current]
            first = current[0][0]
            ls = [int(a.get("l", 0)) for a, _ in current]
            ts = [int(a.get("t", 0)) for a, _ in current]
            rs = [int(a.get("r", 0)) for a, _ in current]
            bs = [int(a.get("b", 0)) for a, _ in current]
            pages[page_no].append(
                {
                    "text": text,
                    "confidence": round(sum(confs) / len(confs)),
                    "min_confidence": min(confs),
                    "suspicious": any(a.get("suspicious") == "true" for a, _ in current),
                    "from_dictionary": first.get("wordFromDictionary") == "true",
                    "numeric": first.get("wordNumeric") == "true",
                    "identifier": first.get("wordIdentifier") == "true",
                    "l": min(ls),
                    "t": min(ts),
                    "r": max(rs),
                    "b": max(bs),
                }
            )
        current = []

    for event, elem in ET.iterparse(xml_path, events=("start", "end")):
        tag = elem.tag.split("}")[-1]
        if event == "start":
            if tag == "page":
                page_no += 1
                pages.setdefault(page_no, [])
            continue
        if tag == "charParams":
            if elem.get("wordStart") == "true":
                flush_word()
            current.append((elem.attrib, elem.text or ""))
        elif tag in ("line", "par", "formatting", "text", "block", "page"):
            flush_word()
        if tag == "page":
            elem.clear()
    flush_word()
    return pages


def annotate_declared_sites(sites: list[dict], abbyy_pages: dict[int, list[dict]]) -> list[dict]:
    """Attach the recognizing engine's own self-assessment to declared sites.

    A site matches a page's word by string equality (first occurrence on the
    page; per-instance confidences of the same string rarely differ). Gains:
    ``engine_confidence``, ``engine_suspicious``, ``engine_dictionary``, and
    ``engine_flagged`` (suspicious OR mean confidence at/below the threshold).
    Unmatched sites are left untouched — no signal is not a signal.
    """
    for site in sites:
        page = site.get("page")
        if not isinstance(page, int):
            continue
        for w in (abbyy_pages or {}).get(page) or []:
            if w["text"].lower() == str(site.get("word", "")).lower():
                site["engine_confidence"] = w["confidence"]
                site["engine_suspicious"] = w["suspicious"]
                site["engine_dictionary"] = w["from_dictionary"]
                site["engine_flagged"] = bool(
                    w["suspicious"] or w["confidence"] <= FLAG_CONFIDENCE
                )
                break
    return sites


def page_summaries(abbyy_pages: dict[int, list[dict]], pages: list[int]) -> list[dict]:
    """Per-page summary for the package's abbyy-quality.json."""
    summaries = []
    for page in pages:
        words = abbyy_pages.get(page) or []
        if not words:
            continue
        confs = [w["confidence"] for w in words]
        flagged = [
            {"word": w["text"], "confidence": w["confidence"], "suspicious": w["suspicious"]}
            for w in words
            if w["suspicious"] or w["confidence"] <= FLAG_CONFIDENCE
        ]
        summaries.append(
            {
                "page": page,
                "words": len(words),
                "mean_confidence": round(sum(confs) / len(confs)),
                "flagged_words": len(flagged),
                "flagged_sample": flagged[:12],
            }
        )
    return summaries
