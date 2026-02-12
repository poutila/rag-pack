# Data Model: Parquet and Chunking

## Purpose
Explain how parquet corpora and FAISS indexes are used by the FCDRAG runner, and why chunk strategy/top-k/prompt profile materially affect output quality.

## Audience
Operators tuning retrieval quality and engineers integrating new corpus/index builds.

## When to read this
Read before tuning runs in [RUNNER_GUIDE.md](RUNNER_GUIDE.md) and while reviewing defaults in [RESEARCH_LOG.md](RESEARCH_LOG.md).

## What the runner actually uses
`run_pack.py` does not parse parquet directly. It passes `--parquet` and `--index` to engine commands for both preflight and chat.

Implications:
- corpus semantics are engine-owned (`rsqt`/`raqt`/`mdparse`), not runner-owned
- runner enforces only orchestration and evidence contract behavior

`UNKNOWN/NOT FOUND`: exact parquet column schemas are not defined in `run_pack.py`; confirm from engine tool docs/manuals.

## Parquet as corpus in this framework
Operationally, parquet is treated as the searchable source corpus for engine commands:
- preflight commands read from parquet/index to produce deterministic evidence artifacts
- chat commands retrieve chunks from same parquet/index and return answer+sources
- evidence artifacts are then injected back into prompts with explicit `CITE` anchors

## Chunking in practice
Chunk strategy is chosen when building the index (outside runner), then consumed by chat/preflights.

Research logs in this repo report:
- `entities`: better for symbol-level questions
- `hybrid`: entities + file anchors (for example `Cargo.toml`, `lib.rs`, `main.rs`, `build.rs`) and materially better for workspace/Cargo/features questions

The tuning docs show hybrid improved Cargo/workspace evidence coverage while adding only a small chunk count increase.

## Index build knobs (engine CLI)
The RSQT CLI reference documents three chunk strategies at index-build time:
- `entities` (default): entity rows only
- `hybrid`: entities + allowlisted file anchors
- `files`: allowlisted file anchors only (ignores `--entity-kinds`)

Useful anchor controls when tuning hybrid/files indexing:
- `--include-anchor-glob` and `--exclude-anchor-glob`
- `--anchor-allowlist-mode {extend,replace}`
- `--anchor-window-lines` (default `200`)
- `--anchor-overlap-lines` (default `20`)
- `--max-anchor-chars` (default `20000`)

Example commands from repo docs:
```bash
# RSQT: entities-only
uv run rsqt rag-index RSQT.parquet --output .rsqt.faiss --chunk-strategy entities

# RSQT: hybrid (recommended in tuning docs for Cargo/workspace/features)
uv run rsqt rag-index RSQT.parquet --output .rsqt.faiss --chunk-strategy hybrid

# RSQT: hybrid + extra anchors
uv run rsqt rag-index RSQT.parquet --output .rsqt.faiss \
  --chunk-strategy hybrid \
  --include-anchor-glob "**/src/**/mod.rs"

# MDParse: basic FAISS index build
uv run mdparse rag-index MD_PARSE.parquet --output .mdparse.faiss
```

## Why top-k and prompt profile matter
From tuning docs:
- low `top-k` can cause mechanical `NOT FOUND` on boundary/multi-file questions
- `grounded` profile increases strict citation discipline but can over-reject at too-low `top-k`
- increasing `top-k` and using hybrid chunking improved citation density and reduced false `NOT FOUND`

In runner behavior:
- question `top_k` caps max retrieval window
- optional adaptive mode starts low and reruns at max `top_k` if validators fail

## Practical tuning matrix

| Knob | Where set | Typical effect |
|---|---|---|
| Chunk strategy (`entities`/`hybrid`) | index build command, not runner | Changes retrieval surface available to chat/preflight |
| `top_k` | per question in pack (`top_k`) + CLI adaptive controls | Controls retrieval breadth |
| Prompt profile | `--prompt-profile` if engine supports it | Changes retrieval prompt behavior and strictness |
| Quote-bypass mode | runner CLI (`--quote-bypass-mode`) | Switches grounding path vs analyze-only evidence-first path |

## Cargo/workspace/feature questions
Current repo research indicates hybrid chunking is materially better than entities for these question types because anchor chunks expose files like `Cargo.toml` that entity-only indexing can miss.

## Related docs
- FCDRAG explainer: [FCDRAG.md](FCDRAG.md)
- Concepts: [CONCEPTS.md](CONCEPTS.md)
- Runner controls: [RUNNER_GUIDE.md](RUNNER_GUIDE.md)
- Research outcomes: [RESEARCH_LOG.md](RESEARCH_LOG.md)

## Source anchors
- `run_pack.py:707`
- `run_pack.py:730`
- `run_pack.py:2377`
- `run_pack.py:2392`
- `run_pack.py:2460`
- `RAG_TUNING.md:18`
- `RAG_TUNING.md:20`
- `RAG_TUNING.md:319`
- `RAG_TUNING.md:327`
- `RAG_TUNING.md:334`
- `RAG_TUNING.md:359`
- `RAG_TUNING.md:395`
- `RAG_TUNING_MANUAL_RSQT_MDPARSE_v1_0.md:29`
- `RAG_TUNING_MANUAL_RSQT_MDPARSE_v1_0.md:34`
- `RAG_TUNING.md:367`
- `CLI_REFERENCE.md:1209`
- `CLI_REFERENCE.md:1416`
