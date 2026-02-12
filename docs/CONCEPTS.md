# Concepts

## Purpose
Define the operating concepts behind FCDRAG (`Fail-Closed Deterministic Corrective RAG`), including fail-closed behavior, deterministic evidence extraction, and strict answer contracts.

## Audience
Pack authors, auditors, and engineers tuning answer quality.

## When to read this
Read after [FCDRAG.md](FCDRAG.md) and [ARCHITECTURE.md](ARCHITECTURE.md), before [PACK_AUTHORING.md](PACK_AUTHORING.md) or [RUNNER_GUIDE.md](RUNNER_GUIDE.md).

## FCDRAG in one paragraph
FCDRAG is not "retrieve once, generate once". It is a contract-driven pipeline that combines deterministic preflight evidence, constrained generation, correction loops, and fail-closed exits.

## Core model: Retrieval -> Augmentation -> Generation -> Validation -> Retry/Fail-closed
In this framework, "RAG" is not just retrieval+generation. Validation and retry logic are first-class.

- Retrieval: preflight commands and/or engine retrieval fetch candidate evidence.
- Augmentation: runner injects evidence blocks and response schema into prompt text.
- Generation: chat call (unless deterministic answer mode is selected).
- Validation: schema, citation provenance, and path gates.
- Retry/Fail-closed: adaptive top-k rerun or contract failure with non-zero exit.

This aligns with the research framing in `RAG_TUNING.md` and the operating model in `RAG_TUNING_MANUAL_RSQT_MDPARSE_v1_0.md`.

## Deterministic extraction vs semantic retrieval

### Deterministic extraction
Deterministic preflights execute explicit tool commands (for example `search`, `entities`, `prod-unwraps`, `defs`) and persist JSON artifacts. This gives stable evidence and cite tokens regardless of LLM behavior.

### Semantic retrieval
Engine chat retrieval (`rag-search` context, chunk/top-k ranking) is still used, but it is intentionally constrained by evidence injection and contract validation.

### Why this split exists
Research notes show that model quality alone does not prevent grounding failures; deterministic extraction plus validation significantly reduces "NOT FOUND despite evidence" and citation drift.

## Grounding mode vs quote-bypass/analyze-only mode

### Grounding mode
- Prompt expects strict grounding behavior.
- If required evidence is missing, model should return `NOT FOUND`.
- Intended for strict auditable behavior with retrieval obligations.

### Analyze-only (quote-bypass)
- Deterministically extracted evidence is treated as authoritative context.
- Model is instructed not to emit `NOT FOUND` when evidence is present; instead use `INSUFFICIENT EVIDENCE` with explicit gaps.
- Triggered by quote-bypass mode (`auto|on|off`) and evidence presence.

Concrete runtime signal:
`run_pack.py:3260`
```python
use_quote_bypass = (args._effective_qb_mode == "on") or (
    args._effective_qb_mode == "auto" and bool(evidence_blocks)
)
```

## Response contracts and validators

### Contract shape
Packs provide a `response_schema` string; typical first lines are:
- `VERDICT=...`
- `CITATIONS=path:line(-line), ...`

The runner enforces this with tolerant parsing (`=` or `:` accepted), then applies optional strict gates.

### Validators and gates
- Schema validator: required lines, allowed verdicts, citation token syntax.
- Citation provenance gate: answer citations must come from evidence tokens when enabled.
- Path Gate A: disallow new file paths not present in evidence.
- Path Gate B: any body-mentioned path must also appear in `CITATIONS`.

### Why this matters
The explicit goal is mechanical fail-closed behavior. "NOT FOUND" and contract failures are treated as pipeline outcomes, not subjective model mood.

## Current execution semantics: deterministic vs llm
`answer_mode=deterministic` and `answer_mode=llm` are materially different pipeline branches.

Concrete implementation excerpt:
`run_pack.py:3539`
```python
if STRICT_FAIL_ON_EMPTY_EVIDENCE and not evidence_blocks:
    # fail-fast abort: no evidence, no model/advice work
if q.answer_mode == "deterministic":
    # model call skipped by design
elif args.evidence_empty_gate and evidence_is_empty:
    # legacy non-strict branch
else:
    # LLM chat branch
```

This means:
- deterministic mode can skip LLM entirely for selected questions.
- LLM mode still runs through validator gates and can rerun on failure.
- with strict evidence gate enabled, empty deterministic evidence aborts the run immediately.

## Strict template + retry semantics
When question config includes strict template + retry settings, the runner injects template constraints and optionally retries.

Concrete code excerpt:
`run_pack.py:3476`
```python
if retry_on_schema_fail and schema_retry_attempts > 0:
    for retry_idx in range(1, schema_retry_attempts + 1):
        probe_issues = _compute_schema_issues_local(ans_probe or "")
```

Concrete run excerpt:
`out/RAQT_MISSION_13_strand_opt/RUN_LOG.txt:38`
```text
event=question.chat.prepare ... strict_response_template=True | retry_on_schema_fail=True | schema_retry_attempts=2
event=question.chat.schema_retry.satisfied ... attempt=0
```

## "NOT FOUND is mechanical"
Research docs attribute `NOT FOUND` primarily to retrieval/windowing/tool-choice mechanics:
- insufficient `top-k`
- chunk strategy mismatches
- wrong command for question class
- evidence/prompt mismatch

Framework mitigations include deterministic preflights, adaptive top-k rerun, and quote-bypass mode.

## Domain-specific determinism via plugins
The runner remains generic. Domain-specific deterministic post-processing is implemented in plugins (`rsqt_guru`), including findings extraction and additional validator logic.

## Related docs
- FCDRAG explainer: [FCDRAG.md](FCDRAG.md)
- Runner operations: [RUNNER_GUIDE.md](RUNNER_GUIDE.md)
- Pack schema: [PACK_AUTHORING.md](PACK_AUTHORING.md)
- Prompt semantics: [PROMPTS.md](PROMPTS.md)
- Tuning outcomes: [RESEARCH_LOG.md](RESEARCH_LOG.md)

## Source anchors
- `run_pack.py:2234`
- `run_pack.py:2370`
- `run_pack.py:2460`
- `run_pack.py:3260`
- `run_pack.py:3308`
- `run_pack.py:3476`
- `run_pack.py:758`
- `run_pack.py:923`
- `run_pack.py:1021`
- `run_pack.py:2658`
- `out/RAQT_MISSION_13_strand_opt/RUN_LOG.txt:38`
- `prompts/RUST_GURU_GROUNDING.md:21`
- `prompts/RUST_GURU_ANALYZE_ONLY.md:15`
- `RAG_TUNING.md:45`
- `RAG_TUNING.md:184`
- `RAG_TUNING_MANUAL_RSQT_MDPARSE_v1_0.md:29`
- `RAG_TUNING_MANUAL_RSQT_MDPARSE_v1_0.md:47`
