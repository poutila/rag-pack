# RAG Tuning Guide for RSQT

> Canonical architecture term in this repository: **FCDRAG (Fail-Closed Deterministic Corrective RAG)**.


Empirical findings from tuning RAG parameters for Rust code queries using `rsqt chat`.

**Test Environment:**
- GPU: NVIDIA GTX 1080 Ti (11GB VRAM)
- Model: Strand-Rust-Coder-14B (Rust-optimized LLM)
- Query Set: 30 questions about a Rust workspace (brave-prof)
- Date: 2026-02-05

---

## Executive Summary

| Parameter | Optimal Value | Impact |
|-----------|---------------|--------|
| **Model Quantization** | Q5_K_M | Best quality/speed balance (84% GPU) |
| **Chunk Strategy** | `hybrid` | +4 substantive answers, enables Cargo.toml context |
| **Prompt Profile** | `grounded` | Enforces citations, honest NOT FOUND |
| **top-k** | 10 | 11.5x more Section citations vs default |

**Recommended command:**
```bash
# Build hybrid index (one-time)
uv run rsqt rag-index RSQT.parquet --output .rsqt.faiss --chunk-strategy hybrid

# Query with optimal settings
uv run rsqt chat "your question" \
  --index .rsqt.faiss \
  --rsqt RSQT.parquet \
  --backend ollama \
  --model "hf.co/Fortytwo-Network/Strand-Rust-Coder-14B-v1-GGUF:Q5_K_M" \
  --prompt-profile grounded \
  --top-k 10
```

---

## 0. Critical: The 3-Phase Protocol

**Key insight**: RAG quality isn't about prompt tuning alone. It's about:
1. Using the **right tool** for each question type
2. Adding **evidence validation** (fail-closed)

### Why "NOT FOUND" Happens (Mechanically)

| Cause | Fix |
|-------|-----|
| `top-k=5` too small | Use `--top-k 10` or `--top-k 15` for boundary questions |
| Anchor window misses `[features]` | Increase `--anchor-window-lines 400` or use `rsqt search` |
| Wrong tool for job | Use deterministic commands first (e.g., `rsqt prod-unwraps`) |

### The 3-Phase Protocol

**Phase A — Locate (No LLM, Deterministic)**

Pick the right command first:

```bash
# Symbol/boundary questions (Q1/Q3)
uv run rsqt rag-search "from_engine_error CliError" --top-k 15
uv run rsqt entities --kind fn --file crates/cli/src/main.rs --limit 50
uv run rsqt search "pub mod" --limit 20  # for lib.rs exports

# Unwrap/expect audit (Q4) - DON'T use chat!
uv run rsqt prod-unwraps --format json

# Cargo features (Q5)
uv run rsqt search "[features]" --include-all-files --limit 50
```

**Phase B — Explain (LLM with Higher top-k)**

```bash
uv run rsqt chat "your question" \
  --index .rsqt.faiss \
  --rsqt RSQT.parquet \
  --prompt-profile grounded \
  --top-k 10
```

**Phase C — Validate (Automatic Fail-Closed)**

Run with `--format json`, then verify:
- Every file path mentioned in `answer` appears in `sources[]`
- Otherwise: **INVALID** - re-run with higher top-k or narrower query

```bash
# Validation check (jq)
uv run rsqt chat "..." --format json | jq '
  .answer as $ans |
  .sources | map(.file) | unique as $files |
  if ($ans | test("crates/[^\"]+\\.rs")) then
    ($ans | capture("crates/[^\"]+\\.rs"; "g") | .[] | IN($files[])) | all
  else true end
'
```

### Question-Type → Tool Mapping

| Question Type | Phase A Tool | Why |
|---------------|--------------|-----|
| Error boundary | `rsqt rag-search "CliError"` | Find definition first |
| Unwrap audit | `rsqt prod-unwraps` | Deterministic, complete |
| Feature flags | `rsqt search "[features]"` | Cargo.toml specific |
| Public API | `rsqt entities --kind fn` | Structured query |
| Cross-module flow | `rsqt rag-search` + higher top-k | Multiple chunks needed |

### Scoring Correction

An answer that **cites a file not in sources** is worse than "NOT FOUND":
- Under `grounded` profile, this is a **hard fail**
- Phase C validator catches this automatically

### Concrete Workflows by Question Type

**Q1 — Error boundary (definition-first)**
```bash
# Phase A: Locate definition chunks
uv run rsqt rag-search "CliError from_engine_error run_cli" \
  --index .rsqt.faiss --rsqt RSQT.parquet --top-k 15

# Verify definition files exist
uv run rsqt query --file "crates/cli/src/main.rs" --contains "run_cli" --limit 5
uv run rsqt query --file "crates/cli/src/output.rs" --contains "from_engine_error" --limit 5

# Phase B: Explain with high top-k
uv run rsqt chat "<Q1 text>" --prompt-profile grounded --top-k 15
```

**Q2 — Unsafe audit (deterministic first)**
```bash
# Phase A: Deterministic commands
uv run rsqt search "unsafe_code" --include-all-files --limit 20
uv run rsqt unsafe --format json

# Phase B: LLM interprets results
uv run rsqt chat "<Q2 text>" --prompt-profile grounded --top-k 10
```

**Q3 — Public API inventory (definition-first)**
```bash
# Phase A: Locate lib.rs exports
uv run rsqt rag-search "crates/engine/src/lib.rs pub mod pub use" --top-k 15
uv run rsqt query --file "crates/engine/src/lib.rs" --limit 5

# Phase B: Explain
uv run rsqt chat "<Q3 text>" --prompt-profile grounded --top-k 15
```

**Q4 — Panic safety (prod-unwraps first, NOT chat)**
```bash
# Phase A: Deterministic audit - DO NOT skip this!
uv run rsqt prod-unwraps --format json --limit 20

# Phase B: LLM classifies/suggests replacements
uv run rsqt chat "<Q4 text>" --prompt-profile grounded --top-k 10
```

**Q5 — Feature flags (search first)**
```bash
# Phase A: Find [features] sections
uv run rsqt search "[features]" --include-all-files --limit 200
uv run rsqt query --include-all-files --file "Cargo.toml" --contains "[features]" --limit 20

# Phase B: Explain
uv run rsqt chat "<Q5 text>" --prompt-profile grounded --top-k 15
```

### Reality Check

Even with the best system prompt (`rust-guru`), you still need:

| Question Type | Required First Step | Why |
|---------------|---------------------|-----|
| Error boundary | `rsqt rag-search` + `rsqt query` | Definition chunks must exist |
| Unsafe audit | `rsqt unsafe` | Deterministic, complete |
| Public API | `rsqt query --file lib.rs` | Ensure lib.rs in context |
| Panic safety | `rsqt prod-unwraps` | LLM eyeballing is unreliable |
| Feature flags | `rsqt search "[features]"` | Anchor window may miss it |

**The prompt can be optimal, but the workflow needs definition-first retrieval and deterministic subcommands first.**

### Understanding RAG Architecture: Retrieval → Augmentation → Generation → Validation

The 3-Phase Protocol maps directly to RAG's architecture:

| RAG Component | What It Does | Our Implementation |
|---------------|--------------|-------------------|
| **Retrieval** | Find relevant evidence (chunks, entities, anchors) | Phase A: `rsqt rag-search`, `rsqt entities`, `rsqt search` |
| **Augmentation** | Inject evidence into model context in structured way | Preflight injection: force definition chunks, preflight results into chat |
| **Generation** | LLM produces grounded answer | Phase B: `rsqt chat` with high top-k |
| **Validation** | Verify answer is grounded (NOT standard RAG) | Phase C: Check file citations in sources[] |

**Key insight**: Most RAG systems stop at "Generation". The preflight injection (forcing definition chunks and deterministic outputs before chat) is the **Augmentation** step. Without it, retrieval finds evidence but the model never sees it.

**What we do that's beyond classic RAG:**

1. **Deterministic oracles** before chat (`prod-unwraps`, `unsafe`)
   - This is "tool-based grounding," not semantic retrieval

2. **Semantics oracle suite** (trybuild/Miri/Loom for Set B)
   - This is verification, not retrieval augmentation
   - Turns "model claims" into "provable outcomes"

**Mental model for enterprise-grade RAG:**

```
Retrieval → Augmentation → Generation → Validation (→ Retry/Fail-closed)
           ↑                            ↑
     Definition-first              Evidence check
     preflight injection           (file in sources[])
```

Classic RAG stops at "Generation." Adding validation gates is what makes it enterprise-grade.

---

## 1. Model Quantization Comparison

Tested Strand-Rust-Coder-14B at different quantization levels on GTX 1080 Ti (11GB VRAM).

### Results

| Quantization | VRAM | GPU/CPU Split | Time (30q) | Substantive | NOT FOUND | Generic |
|--------------|------|---------------|------------|-------------|-----------|---------|
| **Q4_K_M** | ~9GB | ~100% GPU | ~3.5 min | 47% | 17% | 37% |
| **Q5_K_M** | ~10GB | 84%/16% | ~5.5 min | 40% | 43% | 17% |
| **Q6_K** | ~12GB | 73%/27% | ~7 min | 40% | 47% | 13% |

### Analysis

- **Q4_K_M**: Fastest but highest hallucination risk (37% generic responses)
- **Q5_K_M**: Best balance - good quality, mostly GPU-accelerated, 30% faster than Q6_K
- **Q6_K**: Marginal quality improvement over Q5_K_M, but 27% slower due to CPU offload

### Recommendation

**Use Q5_K_M** for GTX 1080 Ti (11GB). It offers:
- 84% GPU utilization (fast inference)
- Better grounding than Q4_K_M (43% vs 17% proper NOT FOUND)
- Significantly faster than Q6_K with minimal quality loss

### GPU Verification Commands

```bash
# Check GPU memory usage
nvidia-smi

# Check Ollama model loading
ollama ps

# Expected output for Q5_K_M on 11GB GPU:
# SIZE: 10 GB (84% GPU / 16% CPU)
```

---

## 2. Prompt Profile Comparison

Tested `default` vs `grounded` prompt profiles.

### Profile Differences

| Profile | Behavior |
|---------|----------|
| `default` | Standard RAG prompt, may give soft "I don't have information" responses |
| `grounded` | Strict mode: requires `Section:` citations, uses explicit `NOT FOUND` |

### Results (Q5_K_M, top-k=5)

| Profile | NOT FOUND | Section Citations | Quality |
|---------|-----------|-------------------|---------|
| `default` | 15/30 | 2/30 | Baseline |
| `grounded` | 29/30 | 7/30 | Too strict |

### Example Comparison

**Question:** "How does the WebSocket server handle authentication?"

| Profile | Response |
|---------|----------|
| `default` | "I don't have information about that in the documentation" |
| `grounded` | "NOT FOUND - The provided documentation context does not contain any information about WebSocket server authentication handling. The context describes dependency structures (Deps) and trait definitions..." |

### Recommendation

- **Use `grounded`** for audit/verification tasks (explicit NOT FOUND prevents false confidence)
- **Use `default`** for exploratory questions (more permissive)
- **Combine with higher `top-k`** to reduce over-rejection

---

## 3. top-k Parameter Tuning

Tested different numbers of retrieved context chunks.

### Results (Q5_K_M, grounded profile)

| top-k | NOT FOUND | Section Citations | Quality |
|-------|-----------|-------------------|---------|
| 5 (default) | 29/30 | 7/30 | Too strict |
| 10 | 27/30 | 23/30 | Better |

### Key Finding

**Increasing top-k from 5 to 10:**
- Section citations improved **7 → 23** (3.3x improvement)
- NOT FOUND dropped **29 → 27** (-2)

### Recommendation

**Use `--top-k 10`** for complex questions requiring multiple code references.

Trade-off: Higher top-k = more context but potentially more noise. For simple "what is X" questions, default top-k=5 is sufficient.

---

## 4. Chunk Strategy: Entities vs Hybrid

This is the most impactful tuning parameter for crate-level questions.

### Strategies Explained

| Strategy | What it indexes | Chunks | Use Case |
|----------|-----------------|--------|----------|
| `entities` | fn/struct/trait/impl extracted rows | 1386 | Code-level questions |
| `hybrid` | entities + file anchors (Cargo.toml, lib.rs, main.rs, build.rs) | 1402 | Crate-level + code questions |

### Results (Q5_K_M, grounded, top-k=10)

| Strategy | Chunks | NOT FOUND | Cargo.toml refs |
|----------|--------|-----------|-----------------|
| `entities` | 1386 | 27/30 | 0 |
| `hybrid` | 1402 | 23/30 | 22 |

### Dramatic Example: Q01 "List workspace members"

**ENTITIES answer:**
```
NOT FOUND

The provided documentation context does not contain any explicit list of
workspace members or their roles... To answer this question accurately,
we would need access to the actual Cargo.toml file...
```

**HYBRID answer:**
```
The Rust workspace members (crates) are:
- `engine` - Section: Cargo.toml::file anchor 1:27
- `cli` - Section: Cargo.toml::file anchor 1:27
- `gui` - Section: Cargo.toml::file anchor 1:27
- `semantics` - Section: Cargo.toml::file anchor 1:27
```

### Key Finding

**Hybrid chunking enables questions that entities cannot answer:**
- Workspace structure questions
- Feature flag questions
- Build configuration questions
- Crate entrypoint questions

**Cost:** Only +16 chunks (+1.2%) over entities.

### Build Commands

```bash
# Entities only (default)
uv run rsqt rag-index RSQT.parquet --output .rsqt.faiss --chunk-strategy entities

# Hybrid (recommended)
uv run rsqt rag-index RSQT.parquet --output .rsqt.faiss --chunk-strategy hybrid

# Hybrid with extended anchors (for more module context)
uv run rsqt rag-index RSQT.parquet --output .rsqt.faiss \
  --chunk-strategy hybrid \
  --include-anchor-glob "**/src/**/mod.rs"
```

---

## 5. Complete Results Table

All configurations tested with Q5_K_M model:

| Configuration | Chunks | NOT FOUND | Section | Cargo.toml |
|---------------|--------|-----------|---------|------------|
| default, entities, k=5 | 1386 | 15/30 | 2 | 0 |
| grounded, entities, k=5 | 1386 | 29/30 | 7 | 0 |
| grounded, entities, k=10 | 1386 | 27/30 | 23 | 0 |
| **grounded, hybrid, k=10** | 1402 | **23/30** | 17 | **22** |

**Winner:** `grounded, hybrid, k=10` - best combination of honesty, citations, and crate-level context.

---

## 6. When to Use Each Configuration

### For Audit/Compliance

```bash
uv run rsqt chat "question" \
  --prompt-profile grounded \
  --top-k 10
```
- Explicit NOT FOUND for missing info
- Mandatory Section citations
- Auditable responses

### For Exploration/Discovery

```bash
uv run rsqt chat "question" \
  --prompt-profile default \
  --top-k 5
```
- More permissive responses
- Good for general understanding
- Faster (fewer chunks)

### For Crate-Level Questions

```bash
# Build with hybrid first
uv run rsqt rag-index RSQT.parquet --output .rsqt.faiss --chunk-strategy hybrid

# Then query
uv run rsqt chat "What features does this crate expose?" \
  --prompt-profile grounded \
  --top-k 10
```
- Workspace structure
- Feature flags
- Build configuration
- Module layout

---

## 7. Limitations

### What RAG Cannot Answer

1. **Questions about non-indexed files**: If you ask about `docs/internal/SPEC.md` but only RSQT (Rust code) is indexed, the answer will be NOT FOUND.

2. **Deep implementation details**: RAG retrieves chunks, not full files. For algorithm walkthroughs, you may need to read the actual source.

3. **Cross-crate analysis**: Questions like "how do crate A and B interact?" may require multiple targeted queries.

### Grounded Profile Trade-off

The grounded profile is strict - it will say NOT FOUND rather than attempt a partial answer. This is good for accuracy but may feel unhelpful for exploratory questions.

---

## 8. Files Generated

Test results are stored in `query_runs_rsqt/`:

```
results_*_Q4_K_M_*.jsonl         # Q4 quantization test
results_*_Q5_K_M_*.jsonl         # Q5 quantization test (default profile)
results_*_Q5_K_M_grounded_*.jsonl # Q5 with grounded profile
results_*_Q6_K_*.jsonl           # Q6 quantization test
```

Each JSONL file contains per-query results:
```json
{
  "id": "Q01",
  "category": "workspace",
  "backend": "ollama",
  "model": "hf.co/Fortytwo-Network/Strand-Rust-Coder-14B-v1-GGUF:Q5_K_M",
  "prompt": "List the Rust workspace members...",
  "answer": "The Rust workspace members are...",
  "sources": [...],
  "tokens": 847,
  "ok": true
}
```

---

## 6. System Prompt Tuning

Custom system prompts can significantly improve answer quality for domain-specific questions.

### Available Presets (in query_script_rsqt.py)

| Preset | Focus | Best For |
|--------|-------|----------|
| `default` | General grounding rules | Documentation queries |
| `rust-expert` | Rust terminology + patterns | Code analysis |
| `rust-guru` | **Strictest grounding** + proof obligations | Audit, unsafe analysis |

### Results: default vs rust-expert

| Metric | default | rust-expert | Improvement |
|--------|---------|-------------|-------------|
| Section citations | 17 | 27 | +59% |
| Rust terms used | 17 | 22 | +29% |
| Technical precision | Good | Excellent | Qualitative |

### Example Comparison (Q08: Configuration store)

**DEFAULT:**
```
The configuration store uses a trait abstraction defined in `src/store.rs`.
The production SQLite implementation lives in `src/store/sqlite.rs`.
```

**RUST-EXPERT:**
```
The configuration store abstraction is defined in `crates/engine/src/api.rs`
as a trait object (`Option<Arc<dyn ConfigStore>>`). The production SQLite
implementation lives in `crates/engine/src/config_store_sqlite.rs`.
```

The rust-expert prompt:
- Uses precise Rust terminology (`trait object`, `Arc<dyn>`)
- Provides exact file paths
- Notes what's NOT FOUND explicitly

### rust-guru Preset (Strictest)

The `rust-guru` preset enforces maximum rigor:

**Key Features:**
1. **Definition-first protocol**: Must locate and quote symbol definitions before explaining
2. **Fail-closed for unsafe/concurrency**: Requires proof obligations or says "UNCERTAIN"
3. **Structured output**: Answer → Evidence → Proof obligations
4. **No hallucination**: Missing evidence = "NOT FOUND" + list needed files

**Best for:**
- Security audits
- Unsafe code review
- Concurrency/lifetime analysis
- When you need verifiable answers

**Example usage:**
```bash
uv run query_script_rsqt.py "hf.co/Fortytwo-Network/Strand-Rust-Coder-14B-v1-GGUF:Q5_K_M" \
  --index .rsqt.faiss \
  --rsqt RSQT.parquet \
  --system-preset rust-guru \
  --prompt-profile grounded \
  --top-k 10
```

### Custom System Prompts

You can also use custom system prompts via CLI:

```bash
# Inline
uv run rsqt chat "..." --system-prompt "You are a Rust security auditor."

# From file
uv run rsqt chat "..." --system-prompt-file ./prompts/security_audit.txt
```

### Recommended System Prompt for Rust Code Analysis

```
You are a Rust expert analyzing a Rust codebase. You understand:
- Ownership, borrowing, and lifetimes
- Trait bounds and generic constraints
- Unsafe code and FFI boundaries
- async/await and Pin/Future patterns
- Error handling with Result/Option and the ? operator
- Workspace structure (Cargo.toml, crates, features)

When answering:
1. Use precise Rust terminology (e.g., "moves", "borrows", "impl Trait")
2. Cite specific file paths and function/struct names from the retrieved context
3. If code involves unsafe blocks, note the safety invariants
4. If the answer requires information not in the context, say NOT FOUND
5. Be concise but technically accurate
```

---

## 8. Advanced Optimization Levers (Beyond Quantization)

Quantization is just one lever. These optimizations can improve quality without more GPU/RAM.

### 1. Use imatrix (i1) Quants

Importance-matrix quants are explicitly tuned to improve quality at a given size.

| Quant | Standard | imatrix Alternative |
|-------|----------|---------------------|
| Q4_K_M | Good balance | **i1-Q4_K_M** - often noticeably better |
| Q5_K_M | Higher quality | **i1-Q5_K_M** - near-Q6 quality |

**Practical recommendation:**
- **Iteration**: Q4_K_M or i1-Q4_K_M (fast)
- **Final audit**: Q6_K or i1-Q5_K_M (rigorous)

### 2. Make Decoding Deterministic

"Q5 contradicts itself" is often sampling noise, not quant issues.

**Ollama Modelfile settings:**
```
PARAMETER temperature 0
PARAMETER seed 42
PARAMETER top_p 1.0
```

**Or via API/CLI:**
```bash
# Create custom modelfile
cat > Modelfile.strand-deterministic << 'EOF'
FROM hf.co/Fortytwo-Network/Strand-Rust-Coder-14B-v1-GGUF:Q5_K_M
PARAMETER temperature 0
PARAMETER seed 42
PARAMETER top_p 1.0
EOF

ollama create strand-deterministic -f Modelfile.strand-deterministic
```

This often makes Q5 behave more consistently than Q4.

### 3. Don't Let "NOT FOUND" Be Decided by Quantization

"NOT FOUND" is usually retrieval/windowing, not model quality.

**Zero-cost fixes:**
- Increase `--top-k` for boundary questions (10-15)
- Use definition-first retrieval (struct/trait/fn definition before usage)
- Run deterministic preflight (`rsqt query`, `rsqt search`) to force-in boundary files

### 4. Improve Speed Without Changing Quant

Reduce memory pressure to fit more layers on GPU:

```bash
# Reduce context if you don't need huge prompts
ollama run model --num-ctx 4096  # default is often 8192+

# llama-server supports KV cache type changes
# (use with caution - can backfire)
--cache-type-k q8_0 --cache-type-v q8_0
```

### 5. Two-Pass Workflow (Best ROI)

This beats "find the perfect quant":

```bash
# Pass 1 (fast): Gather evidence + draft
uv run rsqt chat "question" --model "...Q4_K_M" --top-k 10

# Pass 2 (strict): Verify claims against cited chunks
uv run rsqt chat "Verify: <draft answer> against these sources..." \
  --model "...Q6_K" --top-k 15
```

Makes quality much less sensitive to a single quant.

### Quantization Comparison Summary (with Guru Pack)

| Quant | Speed | Consistency | Quality | Best For |
|-------|-------|-------------|---------|----------|
| Q4_K_M | ⚡ Fast | ✅ High | Good | Iteration, development |
| Q5_K_M | Medium | ⚠️ Variable | Good+ (code examples) | Code generation |
| Q6_K | 🐢 Slow | ✅ Highest | **Best** | Audit, compliance |
| i1-Q5_K_M | Medium | ✅ High | Near-Q6 | **Recommended default** |

---

## Appendix: Quick Reference

### Model Selection (by VRAM)

| VRAM | Recommended Quantization |
|------|-------------------------|
| 8GB | Q4_K_M (may need CPU offload) |
| 11GB | Q5_K_M (84% GPU) |
| 14GB+ | Q6_K or Q8_0 |
| 24GB+ | BF16 (full precision) |

### Parameter Quick Reference

| Parameter | Default | Recommended | Notes |
|-----------|---------|-------------|-------|
| `--chunk-strategy` | entities | **hybrid** | Adds Cargo.toml context |
| `--prompt-profile` | default | **grounded** | For audit tasks |
| `--top-k` | 5 | **10** | For complex questions |
| `--temperature` | 0.0 | 0.0 | Keep deterministic |
| `--max-tokens` | 1024 | 1024 | Increase if answers truncated |

---

*Last updated: 2026-02-05*
