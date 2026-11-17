# ADR-002: Page Parity Guarantee

## Status

Accepted

## Context

When converting PDFs to EPUBs and audiobooks, content can be silently lost:

- Blank pages skipped without record
- Illustrations dropped
- Frontmatter/backmatter handled inconsistently
- Scanner artifacts removed

For a commercial audiobook service, we need to **prove** that every page in the source was accounted for. This is important for:

- Quality control (nothing accidentally missed)
- Legal/compliance (accounting for all content)
- Debugging (trace missing content back to source)
- Customer trust (transparent conversion process)

## Decision

Every prep job produces a **page ledger** — a structured accounting of what happened to every page in the source document.

### Rules

1. **Completeness**: The ledger has exactly one entry per page (PDF) or item (EPUB)
2. **No gaps**: Pages are numbered sequentially with no missing numbers
3. **Action required**: Every entry has an action (`kept` or `skipped`)
4. **Reason required**: Every entry has a human-readable reason
5. **Section required**: Every entry is assigned to a section (chapter, frontmatter, etc.)
6. **Validation**: The ledger is validated before the job can be marked complete

### Format

See [page-ledger.md](../data-models/page-ledger.md) for the full schema.

## Alternatives Considered

### No Ledger (Trust the Process)

- Simplest, no overhead
- But: no way to prove nothing was lost
- Rejected because we need accountability

### Log-Based Accounting

- Write to a log file during processing
- But: logs are unstructured, hard to validate, easy to miss pages
- Rejected because we need structured, validated data

### Post-Hoc Comparison

- Compare source PDF text to output EPUB text
- But: doesn't account for intentional omissions (blank pages, illustrations)
- Rejected because it can't distinguish "lost" from "intentionally skipped"

## Consequences

### Positive

- Complete accountability for every page
- Clear audit trail for quality control
- Enables debugging and partial reprocessing
- Builds customer trust

### Negative

- Slight overhead in prep processing (page classification)
- More complex validation logic
- Larger output artifacts (ledger JSON)

## Implementation Notes

- Page classification uses heuristics (text density, image detection, position)
- Ambiguous pages are flagged with `section: "unassigned"` for review
- The ledger is immutable once the job is complete
- Validation is a hard gate — jobs with invalid ledgers are marked `failed`
