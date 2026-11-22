# CosyVoice 3 Markup Examples

CosyVoice 3 (Fun-CosyVoice3-0.5B-2512) takes direction through two
channels: inline paralinguistic tokens in the text, and a natural-language
instruct attached to the synthesis call. Evidence: model card "Instruct
Support"; CosyVoice 3 paper §2.5 (arXiv 2505.17589).

## Instruct channel (active in this recipe)

The synthesis call is `inference_instruct2(text, instruct, prompt_wav)`.
The instruct format:

    You are a helpful assistant. <delivery direction><|endofprompt|>

The recipe's `styles/*.j2` templates are the delivery direction:

    You are a helpful assistant. Even delivery, neutral tone,
    conversational register. Maintain a steady pace with natural pauses
    at sentence boundaries. ...<|endofprompt|>

The instruct directs delivery (emotion, speed, volume, tone, accent);
the reference clip (`voices/Michael.wav`) supplies the timbre. Per the
paper (§5.6), timbre itself is NOT instruct-controllable.

## Inline tags (engine-native, walk phase)

    He drew a long [breath] and went on.
    She tried not to [laughter] laugh at the absurdity of it.

`[laughter]` and `[breath]` are special tokens in CosyVoice's frontend —
natively interpreted, not read aloud. The annotation layer emits them
per the engine's declared contract (`annotation_contract` in
capabilities.yaml, read by recipe/common/annotation.py, #112); only
these two declared tags survive into the TTS input, and any undeclared
bracket is dropped with a count before synthesis.

## Emphasis

The paper (§2.5) describes a word-emphasis marker wrapping a word in the
input text. Not exercised in the MVP path; validate against the
frontend tokenizer before the annotation layer emits it.

## Pronunciation inpainting (walk phase)

Chinese Pinyin (tone numbers) and English CMU phonemes are supported
in-text for exact pronunciation control — the carrier for the pipeline's
pronunciation-performance layer.

## What is NOT markup

`[pause]`, `[emphasis]`, `[softly]`, `[whisper]`, `[slow]`, `[fast]` are
LLM annotation hints. CosyVoice 3 would read them aloud, so `run.py`
strips them before synthesis.
