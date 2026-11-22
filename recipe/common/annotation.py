"""System annotation layer: builds the LLM annotation prompt for the
subject engine from its declared capabilities (#112).

Documented requirements (docs/data-models/voice-recipe.md:292-302,
docs/tasks/recipe-schema.md:21-43, docs/data-models/narrator-script.md):

- The annotation prompt is ASSEMBLED from: engine capabilities +
  markup examples + style prompt + previous chunk state + chunk text.
  It is never hardcoded per recipe: the recipe declares its engine's
  capabilities (engine/capabilities.yaml, referenced from recipe.yaml),
  and this module builds the prompt FOR that engine. Engine swap =>
  the prompt follows the declaration.
- The clean script is the single source of truth. Annotations are
  METADATA ONLY, keyed by segment ID — no second text copy is stored.
  The LLM's returned chunk is validated against the source (word-exact,
  in order); a mismatch is REJECTED AND RETRIED, never silently edited.
- Chunks (segments) run serially within a chapter with the previous
  chunk's ending state passed to the next call; chapters are emotional
  reset points.
- Only tags DECLARED by the engine's capabilities survive into the TTS
  input; undeclared brackets are dropped and counted (degrade, never
  abort).

This module is pure logic — no engine imports, no LLM client. Recipes
call it; recipe images ship it (Dockerfile: COPY recipe/common
/opt/recipe/common, PYTHONPATH includes /opt/recipe/common).
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any

import yaml


class AnnotationRejected(Exception):
    """The LLM's returned chunk does not preserve the source words."""


@dataclasses.dataclass(frozen=True)
class InlineTag:
    """One declared engine tag, e.g. [breath] with its description/example."""

    tag: str
    description: str = ""
    example: str = ""


@dataclasses.dataclass(frozen=True)
class AnnotationContract:
    """The annotation-relevant subset of an engine's capabilities.yaml.

    Loaded via `from_capabilities` from the recipe's declared
    `engine.capabilities` file. The `annotation_contract` block is the
    normalized declaration the SYSTEM reads; engines that declare no
    contract disable annotation (clean text, no cues) rather than
    inventing a vocabulary.
    """

    engine: str = ""
    inline_tags: tuple[InlineTag, ...] = ()
    instruct_format: str = ""          # e.g. "You are a helpful assistant. {direction}<|endofprompt|>"
    instruct_controls: tuple[str, ...] = ()
    max_direction_words: int = 12     # in-distribution instruct2 directions are SHORT (#114)
    punctuation_rules: tuple[str, ...] = ()
    retries: int = 2

    @property
    def configured(self) -> bool:
        return bool(self.inline_tags or self.instruct_format)

    @property
    def declared_tag_set(self) -> frozenset[str]:
        return frozenset(t.tag for t in self.inline_tags)

    @classmethod
    def from_capabilities(cls, capabilities_path: str | Path) -> "AnnotationContract":
        path = Path(capabilities_path)
        if not path.exists():
            return cls()
        data = yaml.safe_load(path.read_text()) or {}
        block = data.get("annotation_contract") or {}
        tags = tuple(
            InlineTag(
                tag=str(t.get("tag", "")).strip(),
                description=str(t.get("description", "")).strip(),
                example=str(t.get("example", "")).strip(),
            )
            for t in block.get("inline_tags", [])
            if str(t.get("tag", "")).strip()
        )
        instruct = block.get("instruct_channel") or {}
        try:
            max_words = max(1, int(instruct.get("max_direction_words", 12)))
        except (TypeError, ValueError):
            max_words = 12
        return cls(
            engine=str(data.get("engine", "")).strip(),
            inline_tags=tags,
            instruct_format=str(instruct.get("format", "")).strip(),
            instruct_controls=tuple(str(c) for c in instruct.get("controls", [])),
            max_direction_words=max_words,
            punctuation_rules=tuple(str(r) for r in block.get("punctuation_rules", [])),
            retries=max(0, int(block.get("retries", 2))),
        )

    def prompt_vocabulary(self) -> str:
        """The engine-vocabulary section generated FROM the declaration."""
        lines = []
        if self.inline_tags:
            lines.append("## Engine-inline tags (natively interpreted by this TTS engine)")
            for t in self.inline_tags:
                lines.append(f"- {t.tag}: {t.description}" if t.description else f"- {t.tag}")
                if t.example:
                    lines.append(f"  Example: {t.example}")
            lines.append(
                "Use these tags sparingly and only where the text calls for them. "
                "Any bracketed marker NOT in this list will be discarded before synthesis."
            )
        else:
            lines.append("## Engine-inline tags: NONE DECLARED")
            lines.append("Do NOT add any bracketed markers. They would be read aloud.")
        if self.punctuation_rules:
            lines.append("\n## Punctuation adjustments (this engine derives pauses and intonation from punctuation)")
            for rule in self.punctuation_rules:
                lines.append(f"- {rule}")
        if self.instruct_controls:
            lines.append(
                "\n## Delivery direction\n"
                "You may also give a SHORT delivery direction for this chunk (one clause, "
                f"controlling: {', '.join(self.instruct_controls)}). It will be passed to the "
                "engine's instruct channel — never speak it, never put it in the text."
            )
        return "\n".join(lines)

    def output_contract(self) -> str:
        """Machine-parseable response format instructions."""
        return (
            "## Response format (STRICT — each label on its own line)\n"
            "DIRECTION: <one short delivery direction, or none>\n"
            "STATE: <one short clause describing the emotional/pacing state at the END of this chunk>\n"
            "TEXT: <the chunk text, every source word preserved in order, "
            "with only the declared engine tags and punctuation adjustments applied>\n"
            "\n"
            "Rules (STRICT):\n"
            "1. Keep ALL original words in their original order. No additions, no removals, no rephrasing.\n"
            "2. Only the declared engine tags may appear in TEXT. Anything else bracketed is forbidden.\n"
            "3. Return the three labeled lines and nothing else."
        )


_WORD_CORE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")
_LABEL = re.compile(r"^(DIRECTION|STATE|TEXT)\s*:\s*(.*)$", re.IGNORECASE | re.DOTALL)


def normalize_words(text: str) -> list[str]:
    """Word cores: lowercase alphanumeric runs with intra-word apostrophes.

    Punctuation the model adds (hint commas, re-quotes) must not fail the
    word check; word-level fidelity is the requirement.
    """
    return _WORD_CORE.findall(text.lower())


def words_in_order(source_words: list[str], candidate_words: list[str]) -> bool:
    """True if every source word appears in candidate as an in-order subsequence."""
    it = iter(candidate_words)
    return all(word in it for word in source_words)


def parse_annotation(raw: str) -> dict[str, str | None]:
    """Parse the labeled response contract. Tolerant: unlabeled body -> TEXT."""
    direction: str | None = None
    state: str | None = None
    text_parts: list[str] = []
    current_label = None
    for line in (raw or "").splitlines():
        match = _LABEL.match(line.strip())
        if match:
            current_label = match.group(1).upper()
            value = match.group(2).strip()
            if current_label == "DIRECTION":
                direction = value or None
            elif current_label == "STATE":
                state = value or None
            elif current_label == "TEXT":
                text_parts.append(value)
        elif current_label == "TEXT":
            text_parts.append(line.strip())
    body = " ".join(part for part in text_parts if part).strip()
    if not body:
        # Tolerant fallback: the whole response is the text.
        body = (raw or "").strip()
        direction = direction or None
    return {"direction": direction, "state": state, "text": body}


def extract_cues(
    clean_text: str,
    returned_text: str,
    contract: AnnotationContract,
) -> dict[str, Any]:
    """Validate the returned chunk against the source and reduce it to cues.

    Raises AnnotationRejected when the returned text does not preserve
    every source word in order — the caller retries, then falls back to
    no-cues (the clean text ships unannotated and the rejection is
    counted in the report).

    The returned text is NEVER kept: cues are {direction, tags anchored to
    source word indexes}; the TTS input is recomposed from the CLEAN text
    plus cues at synthesis time.
    """
    src_words = normalize_words(clean_text)
    returned_words = normalize_words(returned_text)
    if not words_in_order(src_words, returned_words):
        raise AnnotationRejected(
            "returned chunk does not preserve the source words in order"
        )

    # Undeclared brackets: dropped with a count (degrade, never abort).
    dropped = 0
    declared = contract.declared_tag_set

    def _keep(match: re.Match) -> str:
        nonlocal dropped
        inner = match.group(0)
        if inner in declared:
            return inner
        dropped += 1
        return ""

    sanitized = re.sub(r"\[[A-Za-z_]+\]", _keep, returned_text)

    # Anchor each surviving declared tag to the source word it follows.
    tags: list[dict[str, Any]] = []
    pattern = re.compile(r"(\[[A-Za-z_]+\])")
    pieces = pattern.split(sanitized)
    # Walk pieces accumulating word-core count; when a declared tag piece
    # appears, its anchor index is the count of source words seen so far.
    word_count = 0
    for piece in pieces:
        if piece in declared:
            tags.append({"tag": piece, "after_word_index": word_count})
        else:
            word_count += len(_WORD_CORE.findall(piece.lower()))

    return {
        "tags": tags,
        "dropped_undeclared": dropped,
    }


def extract_cues_from_response(
    clean_text: str,
    parsed: dict[str, Any],
    contract: AnnotationContract,
) -> dict[str, Any]:
    """Full cue extraction: validate the TEXT field, capture direction."""
    cues = extract_cues(clean_text, parsed.get("text") or "", contract)
    direction = (parsed.get("direction") or "").strip()
    if len(direction) > 200:
        direction = ""
    cues["direction"] = direction or None
    return cues


def compose_tts_text(clean_text: str, cues: dict[str, Any] | None) -> str:
    """Recompose the engine input from the CLEAN text plus cues.

    Pure function of (clean_text, cues): inserts declared tags after
    their anchored source words. No second text copy ever exists.
    """
    if not cues or not cues.get("tags"):
        return clean_text
    words = clean_text.split()
    # after_word_index counts word CORES; map core index -> whitespace
    # word index (both count words the same way: split() on the clean
    # text and normalize_words on it agree for normal prose).
    core_count = 0
    out: list[str] = []
    for word in words:
        out.append(word)
        core_count += len(_WORD_CORE.findall(word.lower()))
        for tag in cues["tags"]:
            if tag.get("after_word_index") == core_count:
                out.append(str(tag["tag"]))
    return " ".join(out)


def sanitize_direction(direction: str, max_words: int) -> str:
    """Instruct2 directions must be SHORT, clean prose (#114).

    Bracketed/angled junk is stripped (it would either be narrated or
    corrupt the instruct), and the phrase is clamped to max_words —
    out-of-distribution long instructs make CosyVoice continue the
    prompt text into speech.
    """
    clean = re.sub(r"\[[^\]]*\]|\{[^}]*\}|<[^>]*>", " ", direction or "")
    clean = re.sub(r"\s+", " ", clean).strip(" ,;")
    words = clean.split()
    if max_words and len(words) > max_words:
        words = words[:max_words]
        clean = " ".join(words).rstrip(",;")
    return clean


def compose_instruct(
    contract: AnnotationContract,
    style_base: str,
    direction: str | None,
) -> str:
    """Per-segment instruct from the style base plus the cue direction,
    wrapped per the engine's declared instruct format.

    The composed direction sentence is kept SHORT and period-joined —
    the trained instruct2 distribution is a brief spoken-style
    direction, not a paragraph (#114).
    """
    clean = sanitize_direction(direction or "", contract.max_direction_words)
    combined = style_base
    if clean:
        combined = f"{style_base.rstrip('. ')} . {clean}"
    if contract.instruct_format and "{direction}" in contract.instruct_format:
        return contract.instruct_format.replace("{direction}", combined)
    return combined


def render_simple_template(template_text: str, variables: dict[str, str]) -> str:
    """{{ var }} substitution without a jinja dependency.

    Missing variables render empty so a template cannot hard-fail a job.
    """
    def _replace(match: re.Match) -> str:
        name = match.group(1).strip()
        return variables.get(name, "")

    return re.sub(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", _replace, template_text)


def build_prompt(
    contract: AnnotationContract,
    prompt_template_path: str | Path,
    markup_examples_path: str | Path,
    *,
    style: str,
    style_description: str,
    content_type: str,
    previous_state: str | None,
    chunk_text: str,
) -> str:
    """Assemble the annotation prompt for the SUBJECT ENGINE.

    Per voice-recipe.md:297: engine capabilities + markup examples +
    style prompt + previous chunk state + chunk text. The vocabulary
    section is generated from the declaration — never hardcoded.
    """
    template = Path(prompt_template_path)
    skeleton = template.read_text() if template.exists() else ""
    examples = Path(markup_examples_path)
    examples_text = examples.read_text() if examples.exists() else ""
    variables = {
        "engine": contract.engine,
        "engine_vocab": contract.prompt_vocabulary(),
        "output_contract": contract.output_contract(),
        "markup_examples": examples_text.strip(),
        "style": style,
        "style_description": style_description,
        "content_type": content_type,
        "previous_state": previous_state or "Start of chapter (emotional reset point)",
        "text": chunk_text,
    }
    return render_simple_template(skeleton, variables)
