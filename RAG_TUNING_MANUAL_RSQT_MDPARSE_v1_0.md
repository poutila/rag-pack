# RAG Tuning Manual for RSQT + MDParse

> Canonical architecture term in this repository: **FCDRAG (Fail-Closed Deterministic Corrective RAG)**.

Version: 1.0  
Date: 2026-02-07  

This manual codifies the end-to-end RAG tuning learnings from your RSQT/MDParse optimization work and turns them into a repeatable operating model.

It is based on your current tuning notes and expands them with the missing high-impact learnings discovered during the project. See *Sources* at the end.

---

## Goals and non-goals

### Goals
- Reliable, auditable answers for Rust package audits and code↔docs alignment.
- Fail-closed behavior:
  - Grounding mode: `NOT FOUND` when evidence is missing.
  - Analyze-only mode: `INSUFFICIENT EVIDENCE` when evidence is insufficient.
- Determinism where it matters:
  - deterministic extraction of evidence
  - deterministic decoding for comparable tests (seed + temperature)

### Non-goals
- “One model does everything” (quote + retrieve + reason). This project showed that separating extraction from analysis is the winning architecture.

---

## System architecture

### The canonical pipeline
Retrieval → Augmentation → Generation → Validation → Retry/Fail-closed

Your notes already describe the 3-phase protocol and validation gate pattern; this project adds the missing practical layer: deterministic extraction + LLM analysis as the default for production.

### Two evidence engines (two RAGs)
- RSQT RAG (Rust code): structured entities + anchors → extract code evidence → analyze with LLM
- MDParse RAG (docs): markdown parse output → extract doc evidence → analyze with LLM

Rule: keep two question packs separate and route via a router script:
- rust_pack.yaml → RSQT runner
- docs_pack.yaml → MDParse runner
- rag_router.py orchestrates both (mode: rust/docs/both)

---

## Core learnings

### 1) Deterministic extraction beats “LLM quoting”
A model can be excellent at Rust reasoning yet systematically resist quote-first extraction. The breakthrough was quote-bypass:
- Extract evidence programmatically (query/search/entities/oracles).
- Present evidence to the LLM in analysis-only mode.

Practical consequence: in production, your runner should always be able to operate in QUOTE_BYPASS mode for audit questions.

### 2) Quantization matters less once evidence is controlled
When evidence is deterministic and injected cleanly, most quants converge in accuracy; pick quants for latency/VRAM and instruction-following stability, not “knowledge.”

### 3) “NOT FOUND” is mechanical, not psychological
Your notes explain the mechanical causes (top-k, anchor windows, tool selection). This project added a crucial nuance:
- `NOT FOUND` can also be a model compliance artifact.
- Use mode-specific prompts + deterministic extractors to eliminate “NOT FOUND despite evidence.”

### 4) Validation is not optional
Classic RAG often stops at generation. Your notes already introduce validation; the project strengthened it:
- validate “claims vs evidence”
- log augmented prompts
- if a model contradicts evidence, rerun or bypass the model step

---

## Operating modes and system prompts

### Mode A: GROUNDING (retrieve + cite + explain)
Use when you want the model to search the indexed corpus and cite sections with file+line.
- Missing evidence → output exactly `NOT FOUND`.

Recommended prompt file:
- RUST_GURU_GROUNDING.txt

### Mode B: ANALYZE_ONLY (quote-bypass)
Use when evidence is already provided (deterministically extracted).
- Must not output `NOT FOUND` if evidence is present.
- Uses `INSUFFICIENT EVIDENCE` instead when evidence is incomplete.

Recommended prompt file:
- RUST_GURU_ANALYZE_ONLY.txt

Implementation tip: your runner should auto-select prompt by mode, not by user choice.

---

## Deterministic tools first (the “right tool for the job”)

### RSQT deterministic primitives
Use these before the LLM:
- entities: definition inventory (struct/trait/fn/impl)
- query: definition-site extraction (file + contains + include-entities)
- search: callsite patterns (substring search)
- unsafe and prod-unwraps: audits (oracles)

### MDParse deterministic primitives
Use these before the LLM:
- anchors, links, backlinks, link-check: link integrity and structure
- claims, procedures, contracts, claim-check: governance-style docs
- query/search: deterministic doc excerpt extraction
- rag-search only when a deterministic command does not exist

---

## Extraction patterns (cookbook)

### Definition-first extraction
For symbol questions (struct/trait/fn):
1. Find definition via entities --kind ...
2. Extract the exact block via query --contains "pub struct Foo" (or similar)
3. Inject only the extracted block (10–30 lines), not a whole file.

### Boundary extraction (multi-file synthesis)
For “A ↔ B boundary” questions:
- build a dedicated extractor that returns:
  - conversion definition
  - one callsite
  - minimal supporting context
Then run the LLM in analysis-only mode.

### Unwrap/expect audit
- Prefer prod-unwraps for the list.
- For quoteable callsites:
  - search ".unwrap(" and search ".expect("
  - filter comment/doc hits (///, //!, //, /*, backticks)

### Cargo features
Split into:
- crate-defined [features] blocks (may be none; that is valid)
- dependency feature usage lines (features = [...], default-features = false)
Inject only those sections/lines.

---

## Performance tuning levers (no hardware required)

### Reduce wasted work
- Preflight caching keyed by (pack + parquet + index mtimes)
- Short-circuit preflights: stop when sufficient evidence exists
- Adaptive top_k (grounding mode): start low, rerun high only if validation fails

### Deterministic decoding
For comparable tests, pin:
- temperature=0
- fixed seed
- top_p=1

### Context size management
On limited VRAM, prefer:
- lower num_ctx for larger quants
- larger num_ctx for smaller quants

---

## Reliability gates

### Evidence integrity validator
At minimum:
- In grounding mode: any referenced file must appear in sources.
- In analyze-only mode: evidence is authoritative; do not enforce sources.

### Evidence-empty gate
If deterministic extraction yields “NO EVIDENCE”:
- do not call the LLM
- return NOT FOUND (grounding) or INSUFFICIENT EVIDENCE (analysis-only)

### Prompt logging
Persist per-question:
- augmented prompt
- bypass prompt (if used)
- chat JSON
This is essential for debugging model compliance failures.

---

## Testing and “Guru” certification

### Separate repo audit competence from Rust semantics competence
- Repo audit: RSQT + MDParse packs (definition/boundary/features/unwraps)
- Rust semantics: semantics oracle suite (trybuild + loom + miri)

### Recommended certification protocol
- Core 8: fast smoke test
- Full 15: credible claim
- Run 3 replicates (fixed seeds) and require stability

---

## How to connect to XREF_WORKFLOW_II

### Preflight gate (before STEP_1)
Verify:
- RSQT parquet + index present and fresh
- MD_PARSE parquet + index present and fresh
- semantics oracle (optional but recommended for guru claims)

### STEP routing
- STEP_1 docs audit: MDParse pack
- STEP_2 code audit: RSQT pack
- STEP_3 synthesis: combine both reports

---

## Recommended defaults (practical baseline)

### RSQT baseline (audit)
- chunk strategy: hybrid
- prompt profile: grounded
- top_k: 10–15 for boundary questions
- quote-bypass: ON for production audits

### MDParse baseline (docs audit)
- grounded-style prompt (citations + fail-closed)
- deterministic commands first (anchors/link-check/claims)

---

## Common failure modes and fixes

### NOT FOUND despite evidence
- Root cause: model compliance.
- Fix: quote-bypass (analysis-only).

### Definition not found for a symbol
- Root cause: usage-site retrieval.
- Fix: definition-first extraction via entities + query.

### Search returns doc comments instead of callsites
- Fix: search for call syntax (.unwrap() → .unwrap() is mention; use .unwrap( and filter comment lines).

### Cargo features NOT FOUND
- Fix: treat “no [features]” as valid, and inventory dependency feature usage.

---

## Sources
- RAG_TUNING.md (your original tuning notes)
