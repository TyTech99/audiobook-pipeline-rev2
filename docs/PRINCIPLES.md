# Principles

The product and engineering rules this pipeline is built against, stated by the operator. Each is enforced by mechanism wherever a mechanism can enforce it; the rest is review discipline.

## Product principles

1. **Accurate narration is the end goal.** The book must read aloud right. Every other rule serves that.
2. **Nothing the author wrote is altered.** Perfect fidelity is impossible — poor scans, period spellings, print that can't be re-checked — so the enforceable form of this principle is **zero unattributed changes**: every deviation from the raw OCR is either *defect-attributed* (matched to a named, deterministic damage signature) or *declared* an unknown and counted. Unknowns surface for review; they never silently ship.
3. **The narration is the author's reading.** The audiobook must sound like the author reading their own work aloud — their words, their intent, their pronunciations. Anything that compels TTS to deliver a performance short of that reading (unfamiliar words, numerals, references, typography) is handled in the performance layer — the annotated script's pronunciation component — never in the text artifacts. *(Wording locked by the operator, 2026-11-19.)*
4. **Perfect is not the enemy of very good.** The refinement bar is zero unattributed changes with all residuals declared — not zero residuals. Once text-provable repairs are exhausted and every remaining unknown is honestly counted, ship. Instruments that chase the last unknown (e.g. the #66 image pass) are built on real demand, not speculatively.

## Engineering principles

5. **Deterministic first, heuristic second, inference last.** Repairs escalate through those tiers in that order. Never spend inference where a deterministic rule can decide; never let a heuristic do a deterministic rule's job.
6. **Efficiency in all operations.** Cost scales with damage, not book length: cheap tiers run everywhere; expensive tiers (LLM cleanup, image passes) run only on what the cheap tiers flag.
7. **No logic fragmentation.** A rule or keyword table has exactly one definition in one file. Duplicated logic is a defect to consolidate, never something to fix per-copy.

## Where each principle lives in the mechanism

| Principle | Enforcement |
|---|---|
| 2 — zero unattributed | verify-and-revert (#65); word-level declaration (#68); `fidelity-report.json` with `unattributed_changes` |
| 3 — the author's reading | text/speak separation: the annotated script references clean-script text by ID and carries performance cues only in its pronunciation component; cues never reach the EPUB; engine uptake declared per recipe (`engine/capabilities.yaml`); design captured in the pronunciation-component issue |
| 4 — very good is the bar | `cleanup-log.json` + declaration counts as the customer-facing defect statement |
| 5 — tier order | page-quality gate (heuristic) feeds LLM cleanup (inference) whose output is judged deterministically; the repair sweep (#70/#72) is deterministic and pre-declaration |
| 6 — efficiency | LLM cleanup runs only on gate-flagged pages; deterministic rules run at ~ms per book; the #66 image pass would touch only declared/refused sites |
| 7 — one definition | `worker/prep/page_types.py`, `worker/prep/dicts.py`, `worker/prep/ocr_cleanup.py` each own their rules |

The escalation itself (gate → inference → deterministic judge → deterministic sweep → declaration → optional image pass) is diagrammed in `docs/images/ocr-escalation.png` (editable source: `docs/images/ocr-escalation.excalidraw`), drawn at the state of the PR #62→#73 chain.
