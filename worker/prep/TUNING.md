# Text Normalization Tuning

The prep worker normalizes OCR'd text before building the book package: it strips running headers and page numbers, repairs hyphenated line breaks, and reconstructs paragraphs. The thresholds that control these decisions are tunable without code changes.

## How tuning works

There are two levels:

1. **Host-level defaults** — set in `config/worker-prep.yaml` under the `normalization:` section. These apply to all jobs on that node.
2. **Per-job overrides** — passed in the job submission's `metadata.normalization` object. These override the host defaults for that job only.

Agents behind the REST API use per-job overrides. Agents with filesystem access can also edit the host config.

## Per-job override (REST API)

Include a `normalization` object in the job's `metadata`:

```json
{
  "type": "prep",
  "input_format": "pdf",
  "input_url": "file:///var/lib/audiobook/input/book.pdf",
  "metadata": {
    "title": "Book Title",
    "author": "Author Name",
    "normalization": {
      "indent_pt": 6.0,
      "header_title_similarity": 0.85
    }
  }
}
```

Only the keys you include are overridden; the rest use the host defaults.

## Tunable parameters

| Parameter | Type | Default | What it controls |
|-----------|------|---------|------------------|
| `indent_pt` | float | 4.5 | Minimum horizontal indent (points) for a line to be considered a new paragraph. Increase if paragraphs are being split too aggressively; decrease if indented paragraphs are being merged. |
| `gap_pt` | float | 2.5 | Extra vertical gap (points) beyond normal line spacing that signals a paragraph break. Increase to be more conservative about paragraph breaks. |
| `pitch_min_pt` | float | 6.0 | Minimum line spacing (points) considered valid for paragraph geometry. Lower values catch tighter type; raise to ignore noise. |
| `pitch_max_pt` | float | 40.0 | Maximum line spacing (points) considered valid. Raise for large type or spaced-out layouts. |
| `mid_sentence_min_words` | int | 6 | Minimum words in a paragraph before a mid-sentence break is auto-joined to the next paragraph. Shorter fragments are treated as headings/labels. Raise to be more conservative about joins. |
| `running_min_units` | int | 3 | Minimum number of pages a line must appear on (in the same position) to be classified as a running header. Raise to avoid false positives on short books. |
| `running_max_words` | int | 12 | Maximum words in a line to be considered a running header candidate. Raise for longer book titles in headers. |
| `header_title_similarity` | float | 0.9 | Minimum similarity (0-1) between a header line and the book title for it to be recognized as a running header. Lower to catch headers with extra text (e.g., "BOOK TITLE — Chapter 3"); raise to avoid false matches. |

## Host-level config

Edit `config/worker-prep.yaml` on the worker node:

```yaml
normalization:
  indent_pt: 4.5
  gap_pt: 2.5
  pitch_min_pt: 6.0
  pitch_max_pt: 40.0
  mid_sentence_min_words: 6
  running_min_units: 3
  running_max_words: 12
  header_title_similarity: 0.9
```

After editing, restart the prep worker container for changes to take effect.

## Tuning workflow

1. **Run a test job** on a small page range (e.g., pages 1-20) with default settings.
2. **Inspect the output** — check `script.json` in the package for:
   - Remaining running headers (lines that repeat on every page)
   - Unjoined hyphenations (words split across lines)
   - Incorrect paragraph breaks or merges
3. **Adjust one parameter at a time** and re-run.
4. **Check the worker logs** for the normalization summary:
   ```
   Normalization: N changes (H headers, P page numbers, Y hyphens)
   ```

## Known limitations

- **Roman numeral page numbers in headers**: Headers like "HISTORIAN'S TESTAMENT XI" where the roman numeral changes per page are not yet detected as running headers. See issue #29.
- **OCR errors**: Normalization cannot fix OCR misreads (e.g., "slaid" for "laid"). Use the LLM cleanup stage for that.
- **Two-column layouts**: Paragraph detection assumes single-column text. Two-column pages may need manual review.

## Related files

- `worker/prep/cleanup.py` — normalization pipeline implementation
- `worker/prep/pageparts.py` — header/footer detection helpers
- `worker/prep/normalization_settings.py` — settings dataclass
- `config/worker-prep.yaml` — host-level config
- `docs/api/coordinator-api.md` — REST API reference
