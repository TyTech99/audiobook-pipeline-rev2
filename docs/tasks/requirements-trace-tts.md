# TTS Path Requirements Trace (the pre-delivery mechanism)

Rule (operator, 2026-11-22, after two partial-implementation rounds): no
sample ships without this trace completed against the docs — every
documented requirement for the TTS/render path mapped to its implementing
line, gaps named, unruled divergences flagged for decision. Verify gaps in
the OUTPUT (atoms, audio, reports), not in code greps: ffmpeg satisfies
some requirements natively and a code search reports absence falsely.

Docs source: `docs/data-models/voice-recipe.md` (field tables),
`docs/tasks/recipe-schema.md`, `docs/data-models/narrator-script.md`.

| Requirement (docs) | Implementing line / verifier | State |
|---|---|---|
| `tts.sample_rate` (engine native) | run.py TTS_SAMPLE_RATE=24000, mastered to 44.1k | ✅ |
| `tts.bitrate` / `format` | recipe.yaml 192k mp3 | ✅ |
| `tts.chunk_size` 300 | run.py MAX_CHARS_PER_TTS + chunk_text | ✅ |
| `tts.overlap_ms` crossfade between chunks | run.py crossfade_join + declared_overlap_ms | ✅ 2026-11-22 (audible seams fixed) |
| `tts.synthesis_mode` (this recipe's declaration) | run.py synthesis_mode() — zero_shot, canonical prompt shape | ✅ 2026-11-22 |
| `mastering.target_rms_db` ACX -23..-18 | MASTER_TARGET_LUFS -18, measured in qc | ✅ (was -16, fixed #113) |
| `mastering.max_peak_db` / `max_noise_floor_db` | acx_verdict measured | ✅ |
| `mastering.room_tone_ms` head/tail | run.py _room_tone_filter (1000/3000) | ✅ 2026-11-22 |
| `packaging` m4b aac 96k, stik=2, ftyp M4B | build_m4b_from_wav + _patch_stik | ✅ |
| `packaging.chapters.style: both` (quicktime + nero) | **ffmpeg writes chpl natively** — verified in output: 3 markers, correct timestamps; _patch_nero_chapters kept as guarded safety net | ✅ (false gap in the 2026-11-22 trace: verified in output, not code) |
| `packaging.chapters.read_header` (docs example true) | packaging.yaml read_header: false — **unruled divergence, operator decision** | ⚠️ flagged |
| `packaging.metadata.template` (jinja2) | hardcoded metadata.json (same fields) — **structural divergence** | ⚠️ flagged |
| `packaging.cover_art.required: true` | recipe.yaml required: false — samples carry covers; **unruled divergence** | ⚠️ flagged |
| `qc.reference_hash` | run.py sha256 of the voice reference in qc-report | ✅ 2026-11-22 |
| `qc.acx_compliant` | MEASURED (loudnorm + astats) since #113 | ✅ |
| `qc.test_sentence` / `qc.max_distortion` | not implemented — **undefined operationalization, operator decision** | ⚠️ flagged |
| Chunk state passing / chapters as reset points (recipe-schema.md) | recipe/common/annotation.py + annotate_chapter | ✅ (#112) |
| Metadata-only cues, reject-and-retry, declared tags only | recipe/common/annotation.py | ✅ (#112, #114) |
| GPU or fail | worker device request + recipe cuda gate | ✅ (#101, #103) |

Flagged rows are decision items for the operator; none block a sample.
