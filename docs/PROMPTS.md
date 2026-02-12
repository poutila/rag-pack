# Prompts

## Purpose
Document prompt files, prompt selection logic, and semantics expected by FCDRAG grounding vs analyze-only modes.

## Audience
Operators tuning answer behavior and pack authors overriding prompt paths.

## When to read this
Read when changing prompt files or investigating behavior differences between standard and quote-bypass runs.

## Prompt files in this repo

| File | Intended use |
|---|---|
| `XREF_WORKFLOW_II_new/tools/rag_packs/prompts/RUST_GURU_SYSTEM.md` | Base system policy language (grounding discipline, definition-first, fail-closed claims) |
| `XREF_WORKFLOW_II_new/tools/rag_packs/prompts/RUST_GURU_GROUNDING.md` | Standard grounding mode contract and citation behavior |
| `XREF_WORKFLOW_II_new/tools/rag_packs/prompts/RUST_GURU_ANALYZE_ONLY.md` | Analyze-only (quote-bypass) mode where deterministic evidence is authoritative |

Runner defaults point to these files via `runner_policy.yaml`.

## Prompt selection precedence
Selection is implemented in `run_pack.py` and follows this order:
1. `--system-prompt-file`: single legacy prompt for both modes.
2. `--system-prompt-grounding-file` / `--system-prompt-analyze-file`.
3. pack override via `runner.prompts.grounding` / `runner.prompts.analyze`.
4. fallback defaults from `runner_policy.yaml`.

Missing prompt paths are handled fail-open at resolution time for optional prompt flags.

## Grounding vs analyze-only semantics

### Grounding mode
- model is expected to ground claims in provided sources/evidence
- schema lines (for example `VERDICT=` and `CITATIONS=`) must be emitted exactly as required
- missing required evidence should yield `NOT FOUND`

### Analyze-only (quote-bypass)
- deterministic evidence is treated as authoritative input
- prompt explicitly forbids `NOT FOUND` when evidence is present
- if evidence is incomplete, model should emit `INSUFFICIENT EVIDENCE` with missing items

## Definition-first and fail-closed guidance
Both prompt families encode a definition-first protocol and fail-closed handling for high-risk Rust semantics (unsafe, lifetime/variance, atomics, object safety):
- provide evidence/proof obligations
- otherwise mark uncertainty explicitly

## How response schema is injected
Beyond system prompts, runner also injects the pack `response_schema` into generated question prompts and adds citation instructions (including `CITE=` token usage). This means output contract enforcement is a shared responsibility between:
- prompt text
- injected response schema
- post-answer validators

## Debugging prompt behavior
Inspect these artifacts in run output:
- `<QID>_augmented_prompt.md`
- `<QID>_bypass_prompt.md` (if quote-bypass path was used)
- `<QID>_chat.json`

This lets you compare exact prompt content sent to engine chat.

## UNKNOWN/NOT FOUND boundaries
- Runner does not inspect model internals; differences from model quantization/backend behavior are external to this module.
- Backend-specific prompt-template behavior (for non-Ollama engines) is `UNKNOWN/NOT FOUND` in this repo unless implemented in engine CLI tooling.

## Related docs
- FCDRAG explainer: [FCDRAG.md](FCDRAG.md)
- Concepts: [CONCEPTS.md](CONCEPTS.md)
- Runtime controls: [RUNNER_GUIDE.md](RUNNER_GUIDE.md)
- Extension points: [EXTENDING_AND_PORTING.md](EXTENDING_AND_PORTING.md)

## Source anchors
- `XREF_WORKFLOW_II_new/tools/rag_packs/runner_policy.yaml:28`
- `XREF_WORKFLOW_II_new/tools/rag_packs/runner_policy.yaml:29`
- `XREF_WORKFLOW_II_new/tools/rag_packs/runner_policy.yaml:30`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:1839`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:1847`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:1862`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:1912`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2377`
- `XREF_WORKFLOW_II_new/tools/rag_packs/prompts/RUST_GURU_SYSTEM.md:9`
- `XREF_WORKFLOW_II_new/tools/rag_packs/prompts/RUST_GURU_SYSTEM.md:19`
- `XREF_WORKFLOW_II_new/tools/rag_packs/prompts/RUST_GURU_GROUNDING.md:3`
- `XREF_WORKFLOW_II_new/tools/rag_packs/prompts/RUST_GURU_GROUNDING.md:31`
- `XREF_WORKFLOW_II_new/tools/rag_packs/prompts/RUST_GURU_ANALYZE_ONLY.md:3`
- `XREF_WORKFLOW_II_new/tools/rag_packs/prompts/RUST_GURU_ANALYZE_ONLY.md:15`
